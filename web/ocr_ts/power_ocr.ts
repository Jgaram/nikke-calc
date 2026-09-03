// 전투력 숫자 판독 — 파이썬 `web/power_ocr.py`(OpenCV)의 TS 재구현. 의존 없음.
//
// 흐름(파이썬과 같다): 영역 RGB → 회색 → 여러 문턱으로 조각 나누기 → 숫자 줄 고르기(왕관·쉼표·잡티 제거)
// → 붙은 조각 가르기 → 쉼표 관문·고름 점수로 최선 선택 → 조각을 16×24 캔버스로 → 증강 7종 →
// HOG(540) → RBF-SVM 10클래스 → 다수결. 각 단계는 OpenCV의 셈법(고정소수점·경계·반올림)을 따라 맞췄고,
// 파이썬이 만든 기준값(ocr_ref)과 단계별로 대조해 확인한다(test_ocr.ts).

export const DIGIT_W = 16, DIGIT_H = 24;
export const OFFSETS = [60, 75, 90, 105, 120, 135];
export const UNIT_SCALES = [0.86, 0.93, 1.0, 1.08, 1.16];
export const STABLE_GATE = 0.72;

export type Comp = { x: number; y: number; w: number; h: number; area: number; mask: Uint8Array };
export type Gray = { w: number; h: number; d: Uint8Array };

// ── 숫자 도우미 (파이썬·numpy 셈법) ──────────────────────────────────────
/** 파이썬 round() — 짝수로 붙인다(.5는 짝수 쪽). */
export function pyRound(x: number): number {
  const f = Math.floor(x), r = x - f;
  if (r > 0.5) return f + 1;
  if (r < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}
function median(a: number[]): number {
  const s = [...a].sort((p, q) => p - q), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}
function mean(a: number[]): number { return a.reduce((p, q) => p + q, 0) / a.length; }
function std(a: number[]): number {            // numpy 기본 = 모집단 표준편차
  const m = mean(a);
  return Math.sqrt(a.reduce((p, q) => p + (q - m) * (q - m), 0) / a.length);
}
/** numpy.percentile(선형 보간) */
function percentile(d: Uint8Array, q: number): number {
  const s = Array.from(d).sort((p, r) => p - r), pos = (q / 100) * (s.length - 1);
  const lo = Math.floor(pos), fr = pos - lo;
  return lo + 1 < s.length ? s[lo] + fr * (s[lo + 1] - s[lo]) : s[lo];
}

// ── 영상 기본 연산 (OpenCV 셈법) ─────────────────────────────────────────
/** RGB → 회색. OpenCV 8비트 고정소수점(R 4899 · G 9617 · B 1868, >>14). */
export function toGray(rgb: Uint8Array, w: number, h: number): Gray {
  const d = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < w * h; i++, p += 3) {
    d[i] = (rgb[p] * 4899 + rgb[p + 1] * 9617 + rgb[p + 2] * 1868 + (1 << 13)) >> 14;
  }
  return { w, h, d };
}

/** 8-연결 성분 + 통계. 라벨은 처음 만나는 화소 순(래스터). */
function connectedComponents(bw: Uint8Array, w: number, h: number): Comp[] {
  const lab = new Int32Array(w * h);
  const out: Comp[] = [];
  const stack: number[] = [];
  let next = 1;
  for (let s = 0; s < w * h; s++) {
    if (!bw[s] || lab[s]) continue;
    const id = next++;
    let minx = w, miny = h, maxx = -1, maxy = -1, area = 0;
    lab[s] = id; stack.push(s);
    while (stack.length) {
      const p = stack.pop()!;
      const px = p % w, py = (p - px) / w;
      area++;
      if (px < minx) minx = px; if (px > maxx) maxx = px;
      if (py < miny) miny = py; if (py > maxy) maxy = py;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = px + dx, ny = py + dy;
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        const q = ny * w + nx;
        if (bw[q] && !lab[q]) { lab[q] = id; stack.push(q); }
      }
    }
    const cw = maxx - minx + 1, ch = maxy - miny + 1;
    const mask = new Uint8Array(cw * ch);
    for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
      if (lab[(miny + y) * w + minx + x] === id) mask[y * cw + x] = 1;
    }
    out.push({ x: minx, y: miny, w: cw, h: ch, area, mask });
  }
  return out;
}

/** 배경 밝기(90분위)에서 offset만큼 어두운 것을 글자로 본다 → 조각 목록(x순). */
function components(g: Gray, offset: number): Comp[] {
  const bg = percentile(g.d, 90), thr = Math.max(10, bg - offset);
  const bw = new Uint8Array(g.w * g.h);
  for (let i = 0; i < bw.length; i++) bw[i] = g.d[i] < thr ? 1 : 0;
  return connectedComponents(bw, g.w, g.h).filter((c) => c.area >= 2).sort((a, b) => a.x - b.x);
}

// ── 숫자 줄 고르기 (파이썬 _pick_digits) ────────────────────────────────────
function pickDigits(comps: Comp[], wantCommas: false): Comp[];
function pickDigits(comps: Comp[], wantCommas: true): Comp[] | [Comp[], Comp[]];
function pickDigits(comps: Comp[], wantCommas: boolean): Comp[] | [Comp[], Comp[]] {
  if (comps.length < 3) return [];
  const medH = median(comps.map((c) => c.h));
  let keep = comps.filter((c) => 0.65 * medH <= c.h && c.h <= 1.35 * medH);
  if (keep.length < 3) return [];
  const base = median(keep.map((c) => c.y + c.h));
  keep = keep.filter((c) => Math.abs(c.y + c.h - base) <= medH * 0.28);
  if (keep.length < 3) return [];
  keep.sort((a, b) => a.x - b.x);
  for (let it = 0; it < 4; it++) {
    if (keep.length < 5) break;
    const gaps: number[] = [];
    for (let i = 0; i < keep.length - 1; i++) gaps.push(keep[i + 1].x - (keep[i].x + keep[i].w));
    if (gaps[0] >= 1.5 * Math.max(...gaps.slice(1)) && gaps[0] > 1) { keep = keep.slice(1); continue; }
    if (gaps[gaps.length - 1] >= 1.5 * Math.max(...gaps.slice(0, -1)) && gaps[gaps.length - 1] > 1) {
      keep = keep.slice(0, -1); continue;
    }
    break;
  }
  if (!wantCommas) return keep;
  const lo = keep[0].x, hi = keep[keep.length - 1].x + keep[keep.length - 1].w;
  const base2 = median(keep.map((c) => c.y + c.h));
  const top2 = median(keep.map((c) => c.y));
  const spans = keep.map((c) => [c.x, c.x + c.w] as const);
  const inGap = (cx: number) => !spans.some(([a, b]) => a - 1 <= cx && cx <= b + 1);
  const commas = comps.filter((c) => {
    const cx = c.x + c.w / 2;
    return lo < cx && cx < hi && c.h <= medH * 0.5 && c.y > top2 + medH * 0.45
      && c.y + c.h <= base2 + medH * 0.45 && inGap(cx);
  }).sort((a, b) => a.x - b.x);
  return [keep, commas];
}

// ── 붙은 조각 가르기 (파이썬 _split_wide) ─────────────────────────────────
function splitWide(comps: Comp[], unit: number): Comp[] {
  const out: Comp[] = [];
  for (const c of comps) {
    const n = Math.max(1, pyRound(c.w / unit));
    if (n === 1) { out.push(c); continue; }
    const col = new Array<number>(c.w).fill(0);
    for (let y = 0; y < c.h; y++) for (let x = 0; x < c.w; x++) col[x] += c.mask[y * c.w + x];
    const cuts = [0];
    for (let k = 1; k < n; k++) {
      const mid = Math.floor((k * col.length) / n);
      const lo = Math.max(1, mid - 2), hi = Math.min(col.length - 1, mid + 3);
      if (hi > lo) {
        let best = lo;
        for (let t = lo; t < hi; t++) {          // (col[t], |t-mid|) 최소 — 파이썬 min(key)와 같이 앞선 것 우선
          if (col[t] < col[best] || (col[t] === col[best] && Math.abs(t - mid) < Math.abs(best - mid))) best = t;
        }
        cuts.push(best);
      } else cuts.push(mid);
    }
    cuts.push(col.length);
    for (let k = 0; k < n; k++) {
      const a = cuts[k], b = cuts[k + 1];
      if (b - a < 1) continue;
      let y0 = -1, y1 = -1, area = 0;
      for (let y = 0; y < c.h; y++) {
        let any = 0;
        for (let x = a; x < b; x++) { const v = c.mask[y * c.w + x]; any |= v; area += v; }
        if (any) { if (y0 < 0) y0 = y; y1 = y; }
      }
      if (y0 < 0) continue;
      const sw = b - a, sh = y1 - y0 + 1, mask = new Uint8Array(sw * sh);
      for (let y = 0; y < sh; y++) for (let x = 0; x < sw; x++) mask[y * sw + x] = c.mask[(y0 + y) * c.w + a + x];
      out.push({ x: c.x + a, y: c.y + y0, w: sw, h: sh, area, mask });
    }
  }
  return out;
}

function scoreSegmentation(digs: Comp[], medH: number): number {
  const hs = digs.map((d) => d.h), ws = digs.map((d) => d.w);
  const gaps: number[] = [];
  for (let i = 0; i < digs.length - 1; i++) gaps.push(digs[i + 1].x - (digs[i].x + digs[i].w));
  let s = 0;
  s -= (std(hs) / (mean(hs) + 1e-6)) * 2.0;
  s -= (std(ws) / (mean(ws) + 1e-6)) * 1.2;
  if (gaps.length) s -= (std(gaps) / (medH + 1e-6)) * 0.8;
  return s;
}

/** 쉼표로 나눈 묶음이 «1~3자리 + 3자리×n»인가. 쉼표가 2개 미만이면 판단 보류(null). */
function commaOk(digs: Comp[], commas: Comp[]): boolean | null {
  const nc = commas.length;
  if (nc < 2) return null;
  const cen = digs.map((d) => d.x + d.w / 2);
  const cuts = commas.map((c) => cen.filter((t) => t < c.x + c.w / 2).length);
  const uniq = [...new Set(cuts)].sort((a, b) => a - b);
  if (uniq.length !== cuts.length || uniq.some((v, i) => v !== cuts[i])) return false;
  const groups = [cuts[0]];
  for (let k = 1; k < nc; k++) groups.push(cuts[k] - cuts[k - 1]);
  groups.push(digs.length - cuts[nc - 1]);
  return 1 <= groups[0] && groups[0] <= 3 && groups.slice(1).every((g) => g === 3);
}

/** 여러 문턱·칸폭으로 끊어 보고 가장 그럴듯한 숫자 조각들을 고른다. */
export function segmentDigits(rgb: Uint8Array, w: number, h: number): Comp[] {
  const g = toGray(rgb, w, h);
  const passes: Array<[Comp[], Comp[]]> = [];
  for (const off of OFFSETS) {
    const got = pickDigits(components(g, off), true);
    if (!Array.isArray(got[0]) || (got as [Comp[], Comp[]])[0].length < 4) continue;
    passes.push(got as [Comp[], Comp[]]);
  }
  if (!passes.length) return [];
  const counts = passes.map(([, c]) => c.length).filter((n) => n > 0);
  let ref: Comp[] = [];
  if (counts.length) {
    const uniq = [...new Set(counts)].sort((a, b) => a - b);
    const key = (v: number) => [counts.filter((x) => x === v).length, -Math.abs(v - 3)];
    let mode = uniq[0];
    for (const v of uniq) {                         // 파이썬 max(set, key) — 동률이면 먼저 본 값
      const a = key(v), b = key(mode);
      if (a[0] > b[0] || (a[0] === b[0] && a[1] > b[1])) mode = v;
    }
    for (const [, c] of passes) if (c.length === mode) { ref = c; break; }
  }
  let best: Comp[] | null = null, bestKey: [number, number] = [-1, -9e9];
  for (const [base] of passes) {
    const ws = base.map((d) => d.w).sort((a, b) => a - b);
    const midW = ws[Math.floor(ws.length / 2)];
    let solo = ws.filter((x) => x <= midW * 1.4);
    if (!solo.length) solo = ws;
    const unit0 = solo[Math.floor(solo.length / 2)];
    for (const us of UNIT_SCALES) {
      const digs = splitWide(base, Math.max(1.0, unit0 * us)).sort((a, b) => a.x - b.x);
      if (digs.length < 4) continue;
      const ok = commaOk(digs, ref);
      if (ok === false) continue;
      const medH = median(digs.map((d) => d.h));
      const key: [number, number] = [ok ? 1 : 0, scoreSegmentation(digs, medH)];
      if (key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) { best = digs; bestKey = key; }
    }
  }
  return best ?? [];
}

// ── 크기 조절 (OpenCV resize) ──────────────────────────────────────────────
/** INTER_AREA 축소(면적 가중 평균). 축(가로/세로)별 분리. */
function resizeArea(src: Uint8Array, sw: number, sh: number, dw: number, dh: number): Uint8Array {
  const tab = (s: number, d: number) => {
    const scale = s / d, rows: Array<Array<[number, number]>> = [];
    for (let i = 0; i < d; i++) {
      const a = i * scale, b = Math.min(s, a + scale), ws: Array<[number, number]> = [];
      for (let k = Math.floor(a); k < b; k++) ws.push([k, (Math.min(b, k + 1) - Math.max(a, k)) / scale]);
      rows.push(ws);
    }
    return rows;
  };
  const tx = tab(sw, dw), ty = tab(sh, dh), tmp = new Float64Array(sh * dw), out = new Uint8Array(dw * dh);
  for (let y = 0; y < sh; y++) for (let x = 0; x < dw; x++) {
    let v = 0; for (const [k, wgt] of tx[x]) v += src[y * sw + k] * wgt; tmp[y * dw + x] = v;
  }
  for (let y = 0; y < dh; y++) for (let x = 0; x < dw; x++) {
    let v = 0; for (const [k, wgt] of ty[y]) v += tmp[k * dw + x] * wgt;
    out[y * dw + x] = Math.max(0, Math.min(255, Math.round(v)));
  }
  return out;
}
/** INTER_LINEAR 확대·축소(반화소 중심, 경계 복제, 1/32 양자화 없이 실수). */
function resizeLinear(src: Uint8Array, sw: number, sh: number, dw: number, dh: number): Uint8Array {
  const out = new Uint8Array(dw * dh), sx = sw / dw, sy = sh / dh;
  const at = (x: number, y: number) => src[Math.min(sh - 1, Math.max(0, y)) * sw + Math.min(sw - 1, Math.max(0, x))];
  for (let y = 0; y < dh; y++) {
    let fy = (y + 0.5) * sy - 0.5; let y0 = Math.floor(fy); fy -= y0;
    if (y0 < 0) { y0 = 0; fy = 0; } if (y0 >= sh - 1) { y0 = sh - 1; fy = 0; }
    for (let x = 0; x < dw; x++) {
      let fx = (x + 0.5) * sx - 0.5; let x0 = Math.floor(fx); fx -= x0;
      if (x0 < 0) { x0 = 0; fx = 0; } if (x0 >= sw - 1) { x0 = sw - 1; fx = 0; }
      const v = (1 - fy) * ((1 - fx) * at(x0, y0) + fx * at(x0 + 1, y0)) + fy * ((1 - fx) * at(x0, y0 + 1) + fx * at(x0 + 1, y0 + 1));
      out[y * dw + x] = Math.max(0, Math.min(255, Math.round(v)));
    }
  }
  return out;
}
/** INTER_CUBIC (A = −0.75, 경계 복제). */
function resizeCubic(src: Uint8Array, sw: number, sh: number, dw: number, dh: number): Uint8Array {
  const A = -0.75;
  const coef = (t: number) => {
    const w0 = ((A * (t + 1) - 5 * A) * (t + 1) + 8 * A) * (t + 1) - 4 * A;
    const w1 = ((A + 2) * t - (A + 3)) * t * t + 1;
    const w2 = ((A + 2) * (1 - t) - (A + 3)) * (1 - t) * (1 - t) + 1;
    return [w0, w1, w2, 1 - w0 - w1 - w2];
  };
  const out = new Uint8Array(dw * dh), sx = sw / dw, sy = sh / dh;
  const at = (x: number, y: number) => src[Math.min(sh - 1, Math.max(0, y)) * sw + Math.min(sw - 1, Math.max(0, x))];
  for (let y = 0; y < dh; y++) {
    const fy = (y + 0.5) * sy - 0.5, y0 = Math.floor(fy), cy = coef(fy - y0);
    for (let x = 0; x < dw; x++) {
      const fx = (x + 0.5) * sx - 0.5, x0 = Math.floor(fx), cx = coef(fx - x0);
      let v = 0;
      for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) v += cy[j] * cx[i] * at(x0 - 1 + i, y0 - 1 + j);
      out[y * dw + x] = Math.max(0, Math.min(255, Math.round(v)));
    }
  }
  return out;
}
/** INTER_NEAREST — OpenCV resizeNN: sx = floor(dx·src/dst). */
function resizeNearest(src: Uint8Array, sw: number, sh: number, dw: number, dh: number): Uint8Array {
  const out = new Uint8Array(dw * dh);
  for (let y = 0; y < dh; y++) {
    const sy = Math.min(sh - 1, Math.floor((y * sh) / dh));
    for (let x = 0; x < dw; x++) out[y * dw + x] = src[sy * sw + Math.min(sw - 1, Math.floor((x * sw) / dw))];
  }
  return out;
}
/** cv2.resize(INTER_AREA): 두 축 다 축소면 면적 평균, 확대가 끼면 최근접(OpenCV 문서: «확대 시 INTER_NEAREST와 비슷»). */
export function resizeAreaLike(src: Uint8Array, sw: number, sh: number, dw: number, dh: number): Uint8Array {
  return (dw <= sw && dh <= sh) ? resizeArea(src, sw, sh, dw, dh) : resizeNearest(src, sw, sh, dw, dh);
}

/** 조각 → DIGIT_W×DIGIT_H 캔버스(비율 유지·가운데). */
export function normalizeDigit(c: Comp): Uint8Array {
  const m = new Uint8Array(c.w * c.h);
  for (let i = 0; i < m.length; i++) m[i] = c.mask[i] ? 255 : 0;
  const sc = Math.min((DIGIT_H - 4) / c.h, (DIGIT_W - 4) / c.w);
  const nw = Math.max(1, pyRound(c.w * sc)), nh = Math.max(1, pyRound(c.h * sc));
  const r = resizeAreaLike(m, c.w, c.h, nw, nh);
  const canvas = new Uint8Array(DIGIT_W * DIGIT_H);
  const y0 = Math.floor((DIGIT_H - nh) / 2), x0 = Math.floor((DIGIT_W - nw) / 2);
  for (let y = 0; y < nh; y++) for (let x = 0; x < nw; x++) canvas[(y0 + y) * DIGIT_W + x0 + x] = r[y * nw + x];
  return canvas;
}

// ── 증강 7종 (파이썬 augment) ───────────────────────────────────────────────
/** GaussianBlur 3×3 σ=0.8 — OpenCV 8비트 비트정확 커널 [61,134,61]/256, 경계 REFLECT_101. */
function gaussian3(src: Uint8Array, w: number, h: number): Uint8Array {
  const k = [61, 134, 61];
  const refl = (i: number, n: number) => (i < 0 ? -i : i >= n ? 2 * n - i - 2 : i);
  const tmp = new Int32Array(w * h), out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    tmp[y * w + x] = k[0] * src[y * w + refl(x - 1, w)] + k[1] * src[y * w + x] + k[2] * src[y * w + refl(x + 1, w)];
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const v = k[0] * tmp[refl(y - 1, h) * w + x] + k[1] * tmp[y * w + x] + k[2] * tmp[refl(y + 1, h) * w + x];
    out[y * w + x] = Math.min(255, (v + (1 << 15)) >> 16);
  }
  return out;
}
/** warpAffine 가로 이동(dst(x) = src(x − shift)), 양선형, 경계 0. 분수부는 OpenCV처럼 1/32로 양자화. */
function shiftX(src: Uint8Array, w: number, h: number, shift: number): Uint8Array {
  const out = new Uint8Array(w * h);
  const at = (x: number, y: number) => (x < 0 || x >= w || y < 0 || y >= h ? 0 : src[y * w + x]);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = x - shift, X = Math.round(sx * 1024), X0 = Math.floor((X + 16) / 1024);
    const fr = (((X + 16) >> 5) & 31) / 32;
    const v = (1 - fr) * at(X0, y) + fr * at(X0 + 1, y);
    out[y * w + x] = Math.max(0, Math.min(255, Math.round(v)));
  }
  return out;
}
/** 2×2 사각 커널 팽창/침식(앵커 (1,1)) — 자기와 왼쪽·위·왼위 화소 중 max/min. */
function morph(src: Uint8Array, w: number, h: number, dilate: boolean): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = dilate ? 0 : 255;
    for (let dy = -1; dy <= 0; dy++) for (let dx = -1; dx <= 0; dx++) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0) continue;
      const s = src[ny * w + nx];
      v = dilate ? Math.max(v, s) : Math.min(v, s);
    }
    out[y * w + x] = v;
  }
  return out;
}
export function augment(canvas: Uint8Array): Uint8Array[] {
  const W = DIGIT_W, H = DIGIT_H;
  const small = resizeArea(canvas, W, H, W / 2, H / 2);
  return [
    canvas,
    resizeCubic(small, W / 2, H / 2, W, H),
    gaussian3(canvas, W, H),
    shiftX(canvas, W, H, 0.6),
    shiftX(canvas, W, H, -0.6),
    morph(canvas, W, H, true),
    morph(canvas, W, H, false),
  ];
}

// ── HOG (OpenCV HOGDescriptor 16×24 · 블록 8×8 · 보폭 4 · 셀 4 · 9구간) ──────
const HOG = { winW: 16, winH: 24, block: 8, stride: 4, cell: 4, nbins: 9, sigma: 2.0, thresh: 0.2 };
export function hogOf(img: Uint8Array): Float32Array {
  const { winW: W, winH: H, block: B, stride: S, cell: C, nbins: N } = HOG;
  // 1) 화소별 기울기 (경계 REFLECT_101) → 크기·각도 → 두 구간에 나눠 담을 무게
  const refl = (i: number, n: number) => (i < 0 ? -i : i >= n ? 2 * n - i - 2 : i);
  const mag0 = new Float32Array(W * H), mag1 = new Float32Array(W * H);
  const bin0 = new Int32Array(W * H), bin1 = new Int32Array(W * H);
  const scale = N / Math.PI;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const dx = img[y * W + refl(x + 1, W)] - img[y * W + refl(x - 1, W)];
    const dy = img[refl(y + 1, H) * W + x] - img[refl(y - 1, H) * W + x];
    const mag = Math.sqrt(dx * dx + dy * dy);
    let ang = Math.atan2(dy, dx); if (ang < 0) ang += 2 * Math.PI;       // [0, 2π)
    let a = ang * scale - 0.5;
    let h0 = Math.floor(a); a -= h0;
    if (h0 < 0) h0 += N; else if (h0 >= N) h0 -= N;
    let h1 = h0 + 1; if (h1 >= N) h1 = 0;
    const i = y * W + x;
    mag0[i] = mag * (1 - a); mag1[i] = mag * a; bin0[i] = h0; bin1[i] = h1;
  }
  // 2) 블록 가중치(가우시안)와 셀 보간표
  const nbx = (W - B) / S + 1, nby = (H - B) / S + 1, ncell = B / C, bh = ncell * ncell * N;
  const gw = new Float32Array(B * B);
  const gs = 1 / (HOG.sigma * HOG.sigma * 2);
  for (let i = 0; i < B; i++) for (let j = 0; j < B; j++) {
    const di = i - B * 0.5, dj = j - B * 0.5;             // OpenCV HOGCache 블록 가중치 — +0.5 없음(기준값 대조로 확인)
    gw[i * B + j] = Math.exp(-(di * di + dj * dj) * gs);
  }
  const out = new Float32Array(nbx * nby * bh);
  let o = 0;
  for (let bx = 0; bx < nbx; bx++) for (let by = 0; by < nby; by++) {   // x 우선(OpenCV blockData 순서)
    const hist = new Float32Array(bh);
    const px0 = bx * S, py0 = by * S;
    for (let i = 0; i < B; i++) for (let j = 0; j < B; j++) {
      const pi = (py0 + i) * W + px0 + j, wgt = gw[i * B + j];
      const cx = (j + 0.5) / C - 0.5, cy = (i + 0.5) / C - 0.5;
      const ix0 = Math.floor(cx), iy0 = Math.floor(cy), fx = cx - ix0, fy = cy - iy0;
      const add = (icx: number, icy: number, ww: number) => {
        if (icx < 0 || icy < 0 || icx >= ncell || icy >= ncell) return;
        const ofs = (icx * ncell + icy) * N;      // OpenCV HOGCache: 셀은 x 우선(열 우선)으로 놓인다
        hist[ofs + bin0[pi]] += mag0[pi] * ww; hist[ofs + bin1[pi]] += mag1[pi] * ww;
      };
      add(ix0, iy0, wgt * (1 - fx) * (1 - fy)); add(ix0 + 1, iy0, wgt * fx * (1 - fy));
      add(ix0, iy0 + 1, wgt * (1 - fx) * fy); add(ix0 + 1, iy0 + 1, wgt * fx * fy);
    }
    // 3) L2-Hys 정규화
    let sum = 0; for (let k = 0; k < bh; k++) sum += hist[k] * hist[k];
    let sc = 1 / (Math.sqrt(sum) + bh * 0.1);
    sum = 0;
    for (let k = 0; k < bh; k++) { hist[k] = Math.min(hist[k] * sc, HOG.thresh); sum += hist[k] * hist[k]; }
    sc = 1 / (Math.sqrt(sum) + 1e-3);
    for (let k = 0; k < bh; k++) out[o++] = hist[k] * sc;
  }
  return out;
}

// ── SVM (OpenCV C_SVC · RBF · 일대일 투표) ────────────────────────────────
export type SvmModel = { gamma: number; var_count: number; labels: number[]; sv: number[][]; dfs: Array<{ rho: number; alpha: number[]; index: number[] }> };
export class Svm {
  private sv: Float32Array[];
  constructor(private m: SvmModel) { this.sv = m.sv.map((v) => Float32Array.from(v)); }
  predict(x: Float32Array): number {
    const { gamma, labels, dfs } = this.m, n = labels.length;
    const kern = new Map<number, number>();
    const K = (i: number) => {
      let v = kern.get(i);
      if (v === undefined) {
        const s = this.sv[i]; let d = 0;
        for (let k = 0; k < x.length; k++) { const t = x[k] - s[k]; d += t * t; }
        v = Math.exp(-gamma * d); kern.set(i, v);
      }
      return v;
    };
    const votes = new Array<number>(n).fill(0);
    let df = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++, df++) {
      const f = dfs[df]; let s = -f.rho;
      for (let k = 0; k < f.alpha.length; k++) s += f.alpha[k] * K(f.index[k]);
      votes[s > 0 ? i : j]++;
    }
    let best = 0; for (let i = 1; i < n; i++) if (votes[i] > votes[best]) best = i;
    return labels[best];
  }
}

// ── 서버 진입점 (파이썬 read_regions와 같은 모양) ───────────────────────────
export type Region = { w: number; h: number; rgb: Uint8Array };
export type Reading = { value: number | null; text: string; digits: number; stable: number; sure: boolean; box: number[] | null; rw?: number; rh?: number };
export function readRegions(regions: Region[], svm: Svm): Reading[] {
  const out: Reading[] = [];
  for (const r of regions) {
    const { w, h, rgb } = r;
    if (w < 8 || h < 6 || rgb.length !== w * h * 3) { out.push({ value: null, text: '', digits: 0, stable: 0, sure: false, box: null }); continue; }
    const digs = segmentDigits(rgb, w, h);
    if (!digs.length) { out.push({ value: null, text: '', digits: 0, stable: 0, sure: false, box: null }); continue; }
    let txt = '', stable = 1.0;
    for (const d of digs) {
      const votes = new Map<number, number>();
      for (const a of augment(normalizeDigit(d))) { const v = svm.predict(hogOf(a)); votes.set(v, (votes.get(v) ?? 0) + 1); }
      let lab = -1, top = -1, total = 0;
      for (const [v, c] of votes) { total += c; if (c > top) { top = c; lab = v; } }
      txt += String(lab); stable = Math.min(stable, top / total);
    }
    const x0 = Math.min(...digs.map((d) => d.x)), x1 = Math.max(...digs.map((d) => d.x + d.w));
    const y0 = Math.min(...digs.map((d) => d.y)), y1 = Math.max(...digs.map((d) => d.y + d.h));
    const value = /^\d+$/.test(txt) ? Number(txt) : null;
    out.push({ value, text: txt, digits: digs.length, stable: Math.round(stable * 1000) / 1000, sure: stable >= STABLE_GATE, box: [x0, y0, x1, y1], rw: w, rh: h });
  }
  return out;
}
