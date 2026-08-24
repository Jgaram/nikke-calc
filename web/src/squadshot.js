/* 스쿼드 캡처 판독 — 브라우저 몫.
 *
 * 캡처에서 니케 카드 격자를 찾아 칸을 잘라 **줄인 화소만** 서버로 보낸다.
 * 판독(대조군 대조·전역 배정)은 서버가 한다 — 대조군 서명표를 내보내지 않기 위해서고,
 * 덕분에 캡처 원본도 서버에 올라가지 않는다.
 *
 * 두 번 오간다:
 *   1) /api/squad/align — 표본 4칸을 여러 «틀»로 잘라 12x12만 보낸다(15KB).
 *      격자 상자가 카드 테두리를 6% 물고 있어 이 보정이 없으면 정확도가 떨어진다.
 *   2) /api/squad/read  — 정해진 틀로 25칸을 세 크기 + 속성 뱃지로 보낸다(150KB).
 */

const OCR_C = [12, 24, 32];              // 서버가 기대하는 세 크기
const OCR_BADGE = 16;
const OCR_ELEM_BOX = [0.78, 0.755, 0.99, 0.965];   // 칸 안 속성 원 자리
// 서버의 squad_ocr.ALIGN과 **순서가 같아야 한다**
const OCR_ALIGN = [];
for (const dx of [-0.03, 0, 0.03]) {
  for (const dy of [-0.03, 0, 0.03]) {
    for (const sc of [0.94, 1.0, 1.06]) OCR_ALIGN.push([dx, dy, sc]);
  }
}

/** 이미지 → 캔버스 화소. 붙여넣기·드롭·파일 첨부가 모두 여기로 모인다. */
async function shotPixels(src) {
  const bmp = await createImageBitmap(src);
  const cv = document.createElement("canvas");
  cv.width = bmp.width;
  cv.height = bmp.height;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  cx.drawImage(bmp, 0, 0);
  bmp.close?.();
  return { cv, cx, w: cv.width, h: cv.height,
           data: cx.getImageData(0, 0, cv.width, cv.height).data };
}

/** 문턱을 넘는 구간 뽑기 — 세로·가로 투영에서 카드 띠를 찾는 데 쓴다. */
function shotBands(prof, thr, minLen) {
  const out = [];
  let st = null;
  for (let i = 0; i < prof.length; i++) {
    if (prof[i] >= thr && st === null) st = i;
    else if (prof[i] < thr && st !== null) {
      if (i - st >= minLen) out.push([st, i]);
      st = null;
    }
  }
  if (st !== null && prof.length - st >= minLen) out.push([st, prof.length]);
  return out;
}

/** 문턱을 넘는 **가장 긴** 구간 하나. 흰 판의 범위를 잡는 데 쓴다. */
function shotLongest(prof, thr) {
  let best = [0, 0], st = null;
  for (let i = 0; i < prof.length; i++) {
    if (prof[i] >= thr && st === null) st = i;
    else if (prof[i] < thr && st !== null) {
      if (i - st > best[1] - best[0]) best = [st, i];
      st = null;
    }
  }
  if (st !== null && prof.length - st > best[1] - best[0]) best = [st, prof.length];
  return best;
}

/** 폭도 간격도 고른 **가장 긴 사슬**만 카드 열로 인정한다.
 *
 *  카드 열은 같은 폭으로 같은 간격을 두고 늘어선다. 왼쪽 보스 그림, 오른쪽 링크
 *  단추처럼 «혼자 다른» 띠는 이 사슬에 못 들어온다. 중앙값 폭만 보던 예전 방식은
 *  보스 그림 옆 잔재(폭이 우연히 비슷한 것)를 못 떨궈서, 그 잔재가 줄 투영을
 *  오염시키고 → 줄이 보스 그림 높이만큼 부풀고 → 잘라 낸 칸에 흰 여백이 섞였다.
 *  (실측: 캡처 10장에서 열 6~7개 → 전부 5개, 줄 높이도 고르게 잡힌다.)
 */
function shotChain(bs) {
  if (bs.length <= 2) return bs;
  let best = [];
  for (let i = 0; i < bs.length; i++) {
    for (let j = i + 1; j < bs.length; j++) {
      const wi = bs[i][1] - bs[i][0], wj = bs[j][1] - bs[j][0];
      if (Math.abs(wi - wj) > Math.max(3, wi * 0.25)) continue;
      const pitch = bs[j][0] - bs[i][0];
      if (pitch < wi * 0.8) continue;
      const seq = [bs[i], bs[j]];
      let nx = bs[j][0] + pitch;
      for (let k = j + 1; k < bs.length; k++) {
        const wk = bs[k][1] - bs[k][0];
        if (Math.abs(bs[k][0] - nx) <= Math.max(4, pitch * 0.2)
            && Math.abs(wk - wi) <= Math.max(3, wi * 0.25)) {
          seq.push(bs[k]);
          nx = bs[k][0] + pitch;
        }
      }
      const sum = (a) => a.reduce((t, b) => t + (b[1] - b[0]), 0);
      if (seq.length > best.length
          || (seq.length === best.length && sum(seq) > sum(best))) best = seq;
    }
  }
  return best.length >= 3 ? best : bs;
}

// 게임 UI는 고정 배치다. 파란 SQUAD 라벨 크기 하나로 카드 자리가 전부 정해진다.
// 표본 8장 실측(괄호는 편차): 카드높이 1.47(1.45~1.51) · 카드폭 1.40(1.38~1.43) ·
// 칸간격 1.73(1.69~1.77) · 윗변 +1.92(1.88~1.94) — 모두 «라벨 높이» 배수다.
// 왼쪽은 «라벨 폭» 1.41배(1.39~1.42).
const CARD_H = 1.47, CARD_W = 1.40, CARD_PITCH = 1.73, CARD_TOP = 1.92, CARD_LEFT = 1.41;
const CARD_N = 5;                        // 스쿼드 한 줄은 다섯 명이다

/** 칸 안은 값이 크고 칸 사이는 작다 — 그 차이가 가장 큰 «밀기 양»을 고른다.
 *
 *  칸을 하나씩 경계에 붙이면 폭이 34~54로 들쭉날쭉해진다(실측). 카드 안에도 밝은
 *  경계가 있어 엉뚱한 데 붙기 때문이다. 크기·간격은 계산값으로 **고정**하고
 *  격자 전체를 몇 픽셀 밀지만 찾으면 균일함이 깨지지 않는다. */
function shotBestShift(prof, starts, size, lo, hi) {
  const n = prof.length;
  const cum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) cum[i + 1] = cum[i] + prof[i];
  const tot = (a, b) => {
    a = Math.max(0, Math.min(n, Math.round(a)));
    b = Math.max(0, Math.min(n, Math.round(b)));
    return b > a ? cum[b] - cum[a] : 0;
  };
  let best = 0, bv = null;
  for (let d = lo; d <= hi; d++) {
    let inside = 0, gap = 0;
    for (const s0 of starts) {
      const s1 = s0 + d;
      inside += tot(s1, s1 + size);
      gap += tot(s1 - size * 0.22, s1) + tot(s1 + size, s1 + size * 1.22);
    }
    const v = inside - gap;
    if (bv === null || v > bv) { bv = v; best = d; }
  }
  return best;
}

/** 라벨에서 격자를 **계산한다.** 자르는 방식이 달라도 흔들리지 않는다
 *  (실측: 넓게·창틀째·딱 맞게·0.6배·1.6배·아래 잘림 등 8종 × 캡처 10장 = 79/80.
 *   전체 투영으로 찾던 예전 방식은 같은 시험에서 60~83/100이었다). */
function shotGridFromLabels(shot, labs) {
  const { w, h, data } = shot;
  const med = (a) => a.slice().sort((p, q) => p - q)[a.length >> 1];
  const lh = med(labs.map((c) => c.h));
  const lw = med(labs.map((c) => c.w));
  const lx = med(labs.map((c) => c.x));
  if (lh < 5) return null;

  const cw = Math.round(lh * CARD_W);
  const ch = Math.round(lh * CARD_H);
  const pitch = lh * CARD_PITCH;
  const xs = [];
  for (let i = 0; i < CARD_N; i++) xs.push(Math.round(lx + lw * CARD_LEFT + pitch * i));
  const ys = labs.map((lb) => Math.round(lb.y + lh * CARD_TOP));

  const sat = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    if (Math.max(r, g, b) - Math.min(r, g, b) > 28) sat[p] = 1;
  }
  // 세로 투영은 «카드가 있을 y»에서만 잰다 — 화면 전체 높이로 재면 창틀·배경 띠가
  // 모든 열에 더해져 «열 하나(전체 폭)»가 된다(실측).
  const colp = new Int32Array(w);
  for (const t of ys) {
    for (let y = Math.max(0, t); y < Math.min(h, t + ch); y++) {
      const base = y * w;
      for (let x = 0; x < w; x++) if (sat[base + x]) colp[x]++;
    }
  }
  const dx = shotBestShift(colp, xs, cw, -Math.round(cw * 0.35), Math.round(cw * 0.35));
  const cols = [];
  for (const x of xs) {
    const a = Math.max(0, x + dx), b = Math.min(w, x + dx + cw);
    if (b - a >= cw * 0.9) cols.push([a, b]);
  }
  if (cols.length < 3) return null;

  const rowp = new Int32Array(h);
  for (let y = 0; y < h; y++) {
    const base = y * w;
    let n = 0;
    for (let x = cols[0][0]; x < cols[cols.length - 1][1]; x++) if (sat[base + x]) n++;
    rowp[y] = n;
  }
  const dy = shotBestShift(rowp, ys, ch, -Math.round(ch * 0.35), Math.round(ch * 0.35));
  const rows = [];
  for (const t of ys) {
    const a = t + dy;
    // 화면 밖으로 크게 잘린 줄은 버린다 — 반쪽 얼굴은 못 맞춘다
    if (a < -ch * 0.15 || a + ch > h + ch * 0.15) continue;
    rows.push([Math.max(0, a), Math.min(h, a + ch)]);
  }
  return rows.length ? { rows, cols, byLabel: true } : null;
}

/** 카드 격자. **라벨로 계산하는 게 본줄기**고, 라벨을 못 찾을 때만 투영으로 더듬는다. */
function shotGrid(shot) {
  const labs = shotSquadLabels(shot).slice(0, SQUAD_MAX);
  if (labs.length >= 2) {
    const g = shotGridFromLabels(shot, labs);
    if (g) return g;
  }
  return shotGridByProjection(shot);
}

/** 라벨이 없을 때 — 카드는 «채도 있는 사각형»이라 흰 배경과 갈린다. */
function shotGridByProjection({ w, h, data }) {
  const sat = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    if (Math.max(r, g, b) - Math.min(r, g, b) > 28) sat[p] = 1;
  }

  /** y0~y1 구간에서 세로 투영으로 열을 찾는다. */
  const colsIn = (y0, y1) => {
    const p = new Int32Array(w);
    for (let y = y0; y < y1; y++) {
      const base = y * w;
      for (let x = 0; x < w; x++) if (sat[base + x]) p[x]++;
    }
    return shotChain(shotBands(p, Math.max(6, (y1 - y0) / 46), Math.max(8, w / 40)));
  };

  /** 줄은 **카드 열 안에서만** 센다. 전체 폭으로 세면 왼쪽 보스 그림(카드보다
   *  세로로 길다)이 줄을 위아래로 부풀려, 잘라 낸 칸에 흰 여백이 섞인다. */
  const rowsIn = (cs) => {
    if (!cs.length) return [];
    const xa = cs[0][0], xb = cs[cs.length - 1][1], ww = xb - xa;
    const p = new Int32Array(h);
    for (let y = 0; y < h; y++) {
      const base = y * w;
      let n = 0;
      for (let x = xa; x < xb; x++) if (sat[base + x]) n++;
      p[y] = n;
    }
    let raw = shotBands(p, Math.max(6, ww / 40), Math.max(8, h / 60));
    // 한 줄이 둘로 쪼개질 때가 있다(카드 안 가로 경계선). 바짝 붙은 띠는 합친다 —
    // 안 합치면 14px짜리 조각을 줄로 집어 얼굴이 뭉개진다(실측).
    if (raw.length) {
      const hs = raw.map((b) => b[1] - b[0]).sort((a, b) => a - b);
      const mh = hs[hs.length >> 1];
      const mg = [];
      for (const b of raw) {
        const last = mg[mg.length - 1];
        if (last && b[0] - last[1] <= Math.max(3, mh * 0.30)) last[1] = b[1];
        else mg.push([b[0], b[1]]);
      }
      raw = mg;
    }
    const out = [];
    for (const [y0, y1] of raw) {
      let live = 0;
      for (const [x0, x1] of cs) {
        let n = 0;
        for (let y = y0; y < y1; y++) {
          const base = y * w;
          for (let x = x0; x < x1; x += 2) if (sat[base + x]) n++;
        }
        if (n > (y1 - y0) * (x1 - x0) / 2 * 0.12) live++;
      }
      if (live >= Math.max(3, cs.length - 1)) out.push([y0, y1]);
    }
    return out;
  };

  let cols = colsIn(0, h);
  let rows = rowsIn(cols);
  if (rows.length) {
    // 줄을 찾았으면 **줄 안에서** 열을 다시 잡는다 — SQUAD 라벨·총딜 숫자가
    // 세로 투영에 섞이지 않게 된다
    const c2 = colsIn(rows[0][0], rows[rows.length - 1][1]);
    if (c2.length >= 3) { cols = c2; rows = rowsIn(cols); }
  }
  return { rows, cols };
}

/** 캡처에서 **흰 기록 판만** 잘라 낸다.
 *
 *  휴대폰 캡처는 파란 창틀과 어두운 배경을 통째로 달고 온다. 그 색이 세로·가로
 *  투영을 전부 채워서 «열이 하나(전체 폭)»로 잡히고 격자를 못 찾는다(실측 2/10).
 *  판은 흰색이라 «흰 화소가 많은 가장 긴 구간»으로 잘라 낼 수 있다.
 *  격자를 못 찾았을 때만 쓰는 **대비책**이다 — 늘 자르면 멀쩡하던 캡처가 망가진다
 *  (실측: 어떤 캡처는 자르면 5줄 → 2줄이 된다).
 */
function shotCropPanel(shot) {
  const { w, h, data } = shot;
  const cx = new Int32Array(w), cy = new Int32Array(h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (Math.min(data[i], data[i + 1], data[i + 2]) > 185) { cx[x]++; cy[y]++; }
    }
  }
  const [x0, x1] = shotLongest(cx, h * 0.25);
  const [y0, y1] = shotLongest(cy, w * 0.25);
  if (x1 - x0 < w * 0.4 || y1 - y0 < h * 0.3) return null;
  const cv = document.createElement("canvas");
  cv.width = x1 - x0;
  cv.height = y1 - y0;
  const g = cv.getContext("2d", { willReadFrequently: true });
  g.drawImage(shot.cv, x0, y0, cv.width, cv.height, 0, 0, cv.width, cv.height);
  return { cv, cx: g, w: cv.width, h: cv.height, ox: x0, oy: y0,
           data: g.getImageData(0, 0, cv.width, cv.height).data };
}

/** 칸 하나를 틀(dx, dy, 배율)만큼 흔들어 g×g RGB로 줄인다. */
function shotTile(shot, box, align, g) {
  const [bx0, by0, bx1, by1] = box;
  const bw = bx1 - bx0, bh = by1 - by0;
  const [dx, dy, sc] = align;
  const pad = (sc - 1) / 2;
  const sx = bx0 + bw * (-pad + dx), sy = by0 + bh * (-pad + dy);
  const sw = bw * (1 + 2 * pad), sh = bh * (1 + 2 * pad);
  const cv = document.createElement("canvas");
  cv.width = cv.height = g;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";
  cx.drawImage(shot.cv, sx, sy, sw, sh, 0, 0, g, g);
  const d = cx.getImageData(0, 0, g, g).data;
  const out = new Uint8Array(g * g * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) {
    out[j++] = d[i]; out[j++] = d[i + 1]; out[j++] = d[i + 2];
  }
  return out;
}

/** 칸의 속성 뱃지 조각. 칸 안 비율은 서버의 ELEM_BOX와 같아야 한다. */
function shotBadge(shot, box, align) {
  const [bx0, by0, bx1, by1] = box;
  const bw = bx1 - bx0, bh = by1 - by0;
  const [dx, dy, sc] = align;
  const pad = (sc - 1) / 2;
  const tx = bx0 + bw * (-pad + dx), ty = by0 + bh * (-pad + dy);
  const tw = bw * (1 + 2 * pad), th = bh * (1 + 2 * pad);
  const [ex0, ey0, ex1, ey1] = OCR_ELEM_BOX;
  const cv = document.createElement("canvas");
  cv.width = cv.height = OCR_BADGE;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";
  cx.drawImage(shot.cv, tx + tw * ex0, ty + th * ey0,
               tw * (ex1 - ex0), th * (ey1 - ey0), 0, 0, OCR_BADGE, OCR_BADGE);
  const d = cx.getImageData(0, 0, OCR_BADGE, OCR_BADGE).data;
  const out = new Uint8Array(OCR_BADGE * OCR_BADGE * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) {
    out[j++] = d[i]; out[j++] = d[i + 1]; out[j++] = d[i + 2];
  }
  return out;
}

function shotB64(u8) {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

/** 솔로레이드는 **스쿼드가 다섯 개다.** 그보다 많이 잡히면 격자가 헛것을 문 것이다. */
const SQUAD_MAX = 5;

/** 찾은 카드 줄을 «SQUAD 라벨»로 검증·정정한다.
 *
 *  채도 투영은 줄을 하나 더 찾거나 한 줄을 둘로 쪼갤 때가 있다. 그러면 전체가 한 칸씩
 *  밀려 4번 자리에 3번 얼굴이 들어간다(실측). 파란 라벨은 스쿼드마다 정확히 하나라
 *  **개수의 정답**을 알고 있으니, 라벨마다 «바로 아래 가장 가까운 줄»을 하나씩만 고른다.
 */
function shotAlignRows(shot, rows) {
  const labs = shotSquadLabels(shot);
  if (!labs.length) return rows.slice(0, SQUAD_MAX);
  const used = new Set();
  const out = [];
  for (const lab of labs) {
    let best = -1, bestD = Infinity;
    for (let i = 0; i < rows.length; i++) {
      if (used.has(i)) continue;
      const d = rows[i][0] - lab.y;            // 카드 줄은 라벨보다 아래에 있다
      if (d < 0 || d > lab.h * 6) continue;
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best >= 0) { used.add(best); out.push(rows[best]); }
  }
  // 라벨과 짝지은 줄이 하나도 없으면 판단을 보류하고 원래 것을 쓴다
  if (!out.length) return rows.slice(0, SQUAD_MAX);
  out.sort((a, b) => a[0] - b[0]);
  // **줄 높이는 다 같아야 한다.** 채도 투영이 어떤 줄에서 얇은 띠만 잡을 때가 있다
  // (실측: 다른 줄이 53~57px인데 한 줄만 14px — 그 줄 얼굴이 통째로 깨졌다).
  // 가운데값에서 크게 벗어난 줄은 윗변만 믿고 높이를 가운데값으로 맞춘다.
  const hs = out.map((r) => r[1] - r[0]).sort((a, b) => a - b);
  const medH = hs[hs.length >> 1];
  return out.map(([y0, y1]) =>
    Math.abs((y1 - y0) - medH) > medH * 0.25 ? [y0, y0 + medH] : [y0, y1]
  ).slice(0, SQUAD_MAX);
}

/** 캡처 한 장 → 서버 판독 결과. 격자를 못 찾으면 무엇이 문제인지 알려 준다. */
async function shotRead(src, locked) {
  const shot = await shotPixels(src);
  let grid = shotGrid(shot);
  if (grid.rows.length < 1 || grid.cols.length < 3) {
    // 창틀·배경이 붙은 캡처다. 흰 판만 잘라 다시 해 본다 — 사람에게 «다시 잘라
    // 오세요»라고 떠넘길 일이 아니다.
    //
    // 잘라 낸 판은 **격자를 찾는 데만** 쓰고 좌표를 원본으로 되돌린다. 자른 걸
    // 그대로 쓰면 왼쪽 SQUAD 라벨이 반쯤 잘려 총딜을 통째로 못 읽는다(실측 2/2).
    const cut = shotCropPanel(shot);
    if (cut) {
      const g2 = shotGrid(cut);
      if (g2.rows.length >= 1 && g2.cols.length >= 3) {
        grid = {
          rows: g2.rows.map(([p, q]) => [p + cut.oy, q + cut.oy]),
          cols: g2.cols.map(([p, q]) => [p + cut.ox, q + cut.ox]),
        };
      }
    }
  }
  const rows = grid.byLabel ? grid.rows : shotAlignRows(shot, grid.rows);
  const cols = grid.cols;
  if (rows.length < 1 || cols.length < 3) {
    throw new Error(`니케 카드 격자를 못 찾았습니다 (${rows.length}줄 ${cols.length}칸). `
      + "스쿼드 목록이 통째로 담기게, 잘리지 않은 화면을 넣어 주세요.");
  }
  const boxes = [];
  for (const [y0, y1] of rows) for (const [x0, x1] of cols) boxes.push([x0, y0, x1, y1]);

  // 1라운드 — 표본 몇 칸으로 틀을 정한다
  const step = Math.max(1, Math.floor(boxes.length / 4));
  const samples = [];
  for (let i = 0; i < boxes.length && samples.length < 4; i += step) {
    samples.push(OCR_ALIGN.map((a) => shotB64(shotTile(shot, boxes[i], a, OCR_C[0]))));
  }
  const a = await postJSON("/api/squad/align", { samples });
  const align = a.align;

  // 2라운드 — 정해진 틀로 본 판독
  const tiles = boxes.map((b) => ({
    c12: shotB64(shotTile(shot, b, align, OCR_C[0])),
    c24: shotB64(shotTile(shot, b, align, OCR_C[1])),
    c32: shotB64(shotTile(shot, b, align, OCR_C[2])),
    badge: shotB64(shotBadge(shot, b, align)),
  }));
  const r = await postJSON("/api/squad/read", { tiles, locked: locked || {} });
  // 전투력은 실패해도 니케 판독을 막지 않는다 — 사람이 채울 수 있는 값이다
  let powers = [], powerThumbs = [], powerSure = [];
  try {
    if (HEALTH.power_ocr) {
      const pw = await shotPowers(shot);
      powers = pw.values;
      powerThumbs = pw.thumbs;
      powerSure = pw.sure;
    }
  } catch { /* 숫자만 비운다 — 니케 판독은 막지 않는다 */ }
  return { cells: r.cells, rows: rows.length, cols: cols.length, boxes, shot, align,
           powers, powerThumbs, powerSure };
}

/** 확인 화면에서 한 칸을 고쳤을 때 — 고친 칸을 고정하고 나머지를 다시 배정한다. */
async function shotRelock(state, locked) {
  const tiles = state.boxes.map((b) => ({
    c12: shotB64(shotTile(state.shot, b, state.align, OCR_C[0])),
    c24: shotB64(shotTile(state.shot, b, state.align, OCR_C[1])),
    c32: shotB64(shotTile(state.shot, b, state.align, OCR_C[2])),
    badge: shotB64(shotBadge(state.shot, b, state.align)),
  }));
  const r = await postJSON("/api/squad/read", { tiles, locked });
  return r.cells;
}

/** 칸 하나를 미리보기 그림(data URL)으로 — 확인 화면에서 얼굴을 보여 준다. */
function shotThumb(state, i, px) {
  const [x0, y0, x1, y1] = state.boxes[i];
  const cv = document.createElement("canvas");
  cv.width = cv.height = px || 64;
  const cx = cv.getContext("2d");
  cx.imageSmoothingQuality = "high";
  cx.drawImage(state.shot.cv, x0, y0, x1 - x0, y1 - y0, 0, 0, cv.width, cv.height);
  return cv.toDataURL("image/png");
}


/* ── 전투력 숫자 ────────────────────────────────────────────────────────────
 * 니케 얼굴과 **기준점이 다른 문제**다. 얼굴은 카드 격자가 앵커이고, 숫자는
 * 파란 SQUAD 라벨이 앵커다. 그래서 따로 찾고 따로 보낸다.
 * 문서: web/docs/캡처판독-전투력숫자.md
 */
const PW_STD_W = 96;                       // 서버 power_ocr.STD_SQUAD_W와 같아야 한다
const PW_X0 = 1.15, PW_X1 = 6.2;           // SQUAD 라벨 폭 기준 전투력 영역
// 세로 창은 «넓히는» 게 아니라 «내린다» — 라벨 기준으로 잡으면 위에 흰 공간이 남고
// 아래에서 글자가 잘려 쉼표와 숫자가 구분되지 않는다(실측 37/40 → 40/40).
// 서버 power_ocr.POWER_Y0/Y1과 **같은 값이어야 한다**.
const PW_Y0 = 0.00, PW_Y1 = 1.40;

/** 파란 SQUAD 라벨을 찾는다 → [{x, y, w, h}] 위에서 아래로. */
function shotSquadLabels(shot) {
  const { w, h, data } = shot;
  const on = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    if (mx < 90 || mx - mn < 60) continue;
    if (b !== mx) continue;                            // 파란 계열만
    // 색조를 OpenCV와 같은 0~180 눈금으로 낸다 (서버 detect_squads와 같은 범위).
    // b가 최대일 때 h = 4 + (r-g)/d — 여기에 4를 곱하는 실수를 했었다.
    const d6 = mx - mn;
    let hue = (4 + (r - g) / d6) * 30;                 // 0~180
    if (hue < 0) hue += 180;
    if (hue < 95 || hue > 115) continue;
    on[p] = 1;
  }
  // 이어진 덩어리 (너비 우선 탐색)
  const seen = new Uint8Array(w * h);
  const out = [];
  const qx = new Int32Array(w * h);
  for (let p0 = 0; p0 < on.length; p0++) {
    if (!on[p0] || seen[p0]) continue;
    let head = 0, tail = 0;
    qx[tail++] = p0; seen[p0] = 1;
    let x0 = p0 % w, x1 = x0, y0 = (p0 / w) | 0, y1 = y0, n = 0;
    while (head < tail) {
      const p = qx[head++]; n++;
      const x = p % w, y = (p / w) | 0;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      for (const q of [p - 1, p + 1, p - w, p + w]) {
        if (q < 0 || q >= on.length || seen[q] || !on[q]) continue;
        if ((q === p - 1 && x === 0) || (q === p + 1 && x === w - 1)) continue;
        seen[q] = 1; qx[tail++] = q;
      }
    }
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    if (bw < 12 || bh < 6) continue;
    if (bw / bh < 1.6 || bw / bh > 6.0) continue;
    if (n < bw * bh * 0.45) continue;                  // 속이 찬 덩어리
    out.push({ x: x0, y: y0, w: bw, h: bh });
  }
  if (!out.length) return [];
  // 비슷한 크기가 비슷한 x에 세로로 반복되는 무리만 남긴다
  const med = (a) => a.slice().sort((p, q) => p - q)[a.length >> 1];
  const mw = med(out.map((c) => c.w));
  let keep = out.filter((c) => Math.abs(c.w - mw) <= mw * 0.35);
  const mx = med(keep.map((c) => c.x));
  keep = keep.filter((c) => Math.abs(c.x - mx) <= mw * 0.8);
  return keep.sort((a, b) => a.y - b.y);
}

/** 전투력 영역을 잘라 배율까지 맞춘다 (서버가 기대하는 형태 + 눈으로 볼 그림). */
function shotPowerRegion(shot, lab) {
  const sc = Math.max(1, PW_STD_W / lab.w);            // 줄이지는 않는다
  const sx = Math.max(0, Math.round(lab.x + lab.w * PW_X0));
  const ex = Math.min(shot.w, Math.round(lab.x + lab.w * PW_X1));
  const sy = Math.max(0, Math.round(lab.y + lab.h * PW_Y0));
  const ey = Math.min(shot.h, Math.round(lab.y + lab.h * PW_Y1));
  if (ex - sx < 8 || ey - sy < 6) return null;
  const ow = Math.max(1, Math.round((ex - sx) * sc));
  const oh = Math.max(1, Math.round((ey - sy) * sc));
  const cv = document.createElement("canvas");
  cv.width = ow; cv.height = oh;
  const cx = cv.getContext("2d", { willReadFrequently: true });
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";
  cx.drawImage(shot.cv, sx, sy, ex - sx, ey - sy, 0, 0, ow, oh);
  const d = cx.getImageData(0, 0, ow, oh).data;
  const rgb = new Uint8Array(ow * oh * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) {
    rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2];
  }
  // 사람이 «읽은 숫자»와 «실제 그림»을 대조할 수 있어야 고칠 수 있다
  return { w: ow, h: oh, rgb: shotB64(rgb), canvas: cv,
           thumb: cv.toDataURL("image/png") };
}

/** 캡처 한 장 → 스쿼드별 전투력. 못 읽으면 null이 온다(사람이 채우면 된다). */
async function shotPowers(shot) {
  const labs = shotSquadLabels(shot).slice(0, SQUAD_MAX);
  if (!labs.length) return { values: [], thumbs: [] };
  const regions = labs.map((l) => shotPowerRegion(shot, l)).filter(Boolean);
  if (!regions.length) return { values: [], thumbs: [] };
  const send = regions.map(({ w, h, rgb }) => ({ w, h, rgb }));
  const r = await postJSON("/api/squad/power", { regions: send });
  // 서버가 «실제로 읽은 구간»을 알려 주니 그 자리만 크게 오려 보여 준다
  const thumbs = r.powers.map((p, i) => {
    const src = regions[i];
    if (!src) return null;
    if (!p.box) return src.thumb;
    const [x0, y0, x1, y1] = p.box;
    const pad = Math.round((y1 - y0) * 0.3);
    const sx = Math.max(0, x0 - pad), sy = Math.max(0, y0 - pad);
    const sw = Math.min(src.w - sx, x1 - x0 + pad * 2);
    const sh = Math.min(src.h - sy, y1 - y0 + pad * 2);
    const H = 40;                                    // 눈으로 읽히는 크기
    const cv = document.createElement("canvas");
    cv.height = H;
    cv.width = Math.max(1, Math.round(sw * H / sh));
    const cx = cv.getContext("2d");
    cx.imageSmoothingQuality = "high";
    cx.drawImage(src.canvas, sx, sy, sw, sh, 0, 0, cv.width, cv.height);
    return cv.toDataURL("image/png");
  });
  return { values: r.powers.map((p) => p.value),
           sure: r.powers.map((p) => !!p.sure), thumbs };
}
