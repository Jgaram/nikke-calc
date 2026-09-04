// 스쿼드 캡처 얼굴 서명 대조 — 파이썬 `web/squad_ocr.py`의 TS 재구현. 의존 없음.
//
// 서명표 `data/face_sig.json`(scraper/face_sig.py가 만든다)과 규칙이 같아야 한다:
// coarse 12×12 밝기 z-점수 · fine 24×24 RGB z-점수 · phash 32×32 DCT 저주파 64비트 · color 12×4 HSV 히스토그램.
// 파이썬 정수 셈법(`//`는 바닥 나눗셈, `%`는 양수 나머지)과 정렬의 안정성(동률은 앞선 것)을 그대로 따른다.

export const BADGE = 16;
export const ELEM_GATE = 0.25, ELEM_BONUS = 0.20, SHORTLIST = 32, TOPN = 5;
export const W_NCC = 0.50, W_PH = 0.26, W_COL = 0.24;
export const ALIGN: Array<[number, number, number]> = [];
for (const dx of [-0.03, 0.0, 0.03]) for (const dy of [-0.03, 0.0, 0.03]) for (const sc of [0.94, 1.0, 1.06]) ALIGN.push([dx, dy, sc]);
export const ELEM_HUE: Record<string, number> = { 작열: 254, 수냉: 150, 풍압: 97, 전격: 214, 철갑: 22 };
export const HUE_WIN = 14;

type RGB = [number, number, number];
export type Card = { n: string; c: number; e: string; f: string; co: number[]; fi: number[]; ph: bigint; cl: number[] };
export type Db = { cards: Card[]; C: number; F: number; PH: number; HB: number; SB: number; mc: number[]; mf: number[]; mp: number[] };

const floorDiv = (a: number, b: number) => Math.floor(a / b);
const mod = (a: number, m: number) => ((a % m) + m) % m;

/** face_sig.json 본문 → DB. `ph`는 2^53을 넘으므로 JSON.parse 전에 문자열로 감싼다. */
export function loadDb(text: string): Db {
  const doc = JSON.parse(text.replace(/"ph":\s*(\d+)/g, '"ph": "$1"'));
  const g = doc.grid, m = doc.mask;
  const cards: Card[] = doc.cards.map((c: any) => ({ ...c, ph: BigInt(c.ph) }));
  return { cards, C: g.coarse, F: g.fine, PH: g.phash, HB: g.hb, SB: g.sb,
    mc: pool(m.mask, g.coarse, m.g), mf: pool(m.mask, g.fine, m.g), mp: pool(m.mask, g.phash, m.g) };
}

function pool(mask: number[], g: number, src: number): number[] {
  const step = src / g, out: number[] = [];
  for (let y = 0; y < g; y++) for (let x = 0; x < g; x++) {
    const y0 = Math.trunc(y * step), y1 = Math.max(Math.trunc(y * step) + 1, Math.trunc((y + 1) * step));
    const x0 = Math.trunc(x * step), x1 = Math.max(Math.trunc(x * step) + 1, Math.trunc((x + 1) * step));
    let s = 0, n = 0;
    for (let yy = y0; yy < y1; yy++) for (let xx = x0; xx < x1; xx++) { s += mask[yy * src + xx]; n++; }
    out.push(s * 2 >= n ? 1 : 0);
  }
  return out;
}
function norm(v: number[]): number[] {
  const m = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, x) => a + (x - m) * (x - m), 0) / v.length) || 1.0;
  return v.map((x) => (x - m) / sd);
}
const COS = new Map<number, number[][]>();
function dct1(v: number[]): number[] {
  const n = v.length;
  let cs = COS.get(n);
  if (!cs) { cs = []; for (let k = 0; k < n; k++) { const row: number[] = []; for (let x = 0; x < n; x++) row.push(Math.cos((Math.PI * (2 * x + 1) * k) / (2 * n))); cs.push(row); } COS.set(n, cs); }
  const out: number[] = [];
  for (let k = 0; k < n; k++) { let s = 0; for (let x = 0; x < n; x++) s += v[x] * cs[k][x]; out.push(s); }
  return out;
}
const lum = (p: RGB) => floorDiv(p[0] * 299 + p[1] * 587 + p[2] * 114, 1000);
function rgb2hsv(p: RGB): [number, number, number] {
  const [r, g, b] = p, mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h: number;
  if (d === 0) h = 0;
  else if (mx === r) h = mod(floorDiv(43 * (g - b), d), 256);
  else if (mx === g) h = mod(85 + floorDiv(43 * (b - r), d), 256);
  else h = mod(171 + floorDiv(43 * (r - g), d), 256);
  return [h, mx === 0 ? 0 : floorDiv(255 * d, mx), mx];
}
function planes(db: Db, tile: Tile): [RGB[], RGB[], RGB[], RGB[]] {
  const rd = (b: Uint8Array, g: number, key: string): RGB[] => {
    if (b.length !== g * g * 3) throw new Error(`${key}는 ${g}x${g} RGB여야 한다 (받은 길이 ${b.length})`);
    const out: RGB[] = []; for (let i = 0; i < g * g; i++) out.push([b[i * 3], b[i * 3 + 1], b[i * 3 + 2]]); return out;
  };
  return [rd(tile.c12, db.C, "c12"), rd(tile.c24, db.F, "c24"), rd(tile.c32, db.PH, "c32"), rd(tile.badge, BADGE, "badge")];
}

/** 줄여 온 화소 → (coarse, fine, phash, color). */
export function signatures(db: Db, bc: RGB[], bf: RGB[], bp32: RGB[]): [number[], number[], bigint, number[]] {
  const PH = db.PH;
  const coarse = norm(bc.filter((_, i) => db.mc[i]).map(lum));
  let fine: number[] = [];
  for (let ch = 0; ch < 3; ch++) fine = fine.concat(norm(bf.filter((_, i) => db.mf[i]).map((p) => p[ch])));
  let bp = bp32.map(lum);
  const live = bp.filter((_, i) => db.mp[i]);
  const fill = live.length ? floorDiv(live.reduce((a, b) => a + b, 0), live.length) : 128;
  bp = bp.map((v, i) => (db.mp[i] ? v : fill));
  const rows: number[][] = []; for (let y = 0; y < PH; y++) rows.push(dct1(bp.slice(y * PH, (y + 1) * PH)));
  const cols: number[][] = []; for (let k = 0; k < 8; k++) cols.push(dct1(rows.map((r) => r[k])));
  const vals: number[] = []; for (let k = 0; k < 8; k++) for (let y = 0; y < 8; y++) vals.push(cols[k][y]);
  vals.shift();
  const med = [...vals].sort((a, b) => a - b)[Math.floor(vals.length / 2)];
  let phash = 0n; for (const v of vals) phash = (phash << 1n) | (v > med ? 1n : 0n);
  const { HB, SB } = db, hist = new Array<number>(HB * SB).fill(0);
  bf.forEach((p, i) => {
    if (!db.mf[i]) return;
    const [h, s, v] = rgb2hsv(p);
    if (v < 30) return;
    hist[floorDiv(h * HB, 256) * SB + Math.min(SB - 1, floorDiv(s * SB, 256))] += 1;
  });
  const tot = hist.reduce((a, b) => a + b, 0) || 1.0;
  return [coarse, fine, phash, hist.map((x) => x / tot)];
}

/** 속성 원 조각의 색 → (속성, 확신도). 가장 진한 화소만 골라 투표. */
export function readElement(badge: RGB[]): [string | null, number] {
  const px = badge.map(rgb2hsv).filter((t) => t[2] > 45);
  if (px.length < 10) return [null, 0.0];
  const sorted = px.map((t, i) => [t, i] as const).sort((a, b) => (b[0][1] - a[0][1]) || (a[1] - b[1])).map((x) => x[0]);   // 안정 정렬
  const keep = sorted.slice(0, Math.max(8, Math.floor(px.length / 3))).filter((t) => t[1] > 70);
  if (keep.length < 6) return [null, 0.0];
  const votes: Record<string, number> = {}; for (const nm of Object.keys(ELEM_HUE)) votes[nm] = 0;
  for (const [h, s] of keep) for (const [nm, hv] of Object.entries(ELEM_HUE)) {
    const d = Math.min(Math.abs(h - hv), 256 - Math.abs(h - hv));
    if (d < HUE_WIN) votes[nm] += (1 - d / HUE_WIN) * (s / 255);
  }
  const rank = Object.entries(votes).map((kv, i) => [kv, i] as const).sort((a, b) => (b[0][1] - a[0][1]) || (a[1] - b[1])).map((x) => x[0]);
  if (rank[0][1] <= 0) return [null, 0.0];
  const tot = Object.values(votes).reduce((a, b) => a + b, 0) || 1.0;
  return [rank[0][0], (rank[0][1] - rank[1][1]) / tot];
}

const dot = (a: number[], b: number[]) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s / a.length; };
function popcount64(x: bigint): number { let n = 0; while (x) { n += Number(x & 1n); x >>= 1n; } return n; }
const ham = (a: bigint, b: bigint) => ((63 - popcount64(a ^ b)) / 63) * 2 - 1;
const inter = (a: number[], b: number[]) => { let s = 0; for (let i = 0; i < a.length; i++) s += Math.min(a[i], b[i]); return 2 * s - 1; };

export type Tile = { c12: Uint8Array; c24: Uint8Array; c32: Uint8Array; badge: Uint8Array };
export type CellScores = Map<string, [number, number, string]>;   // 이름 → (점수, 코스튬, 파일) — 삽입 순서 유지

/** 칸 하나 → (후보별 점수, (속성, 확신도)). */
export function scoreCell(db: Db, tile: Tile): [CellScores, [string | null, number]] {
  const [bc, bf, bp, badge] = planes(db, tile);
  const [co, fi, ph, cl] = signatures(db, bc, bf, bp);
  const [el, ec] = readElement(badge);
  let sub = db.cards, bonus: string | null = null;
  if (el && ec >= ELEM_GATE) { const f = db.cards.filter((c) => c.e === el); if (f.length >= 3) sub = f; }
  else if (el) bonus = el;
  const best = new Map<string, [number, Card]>();
  for (const c of sub) { const s = dot(co, c.co); const cur = best.get(c.n); if (!cur || s > cur[0]) best.set(c.n, [s, c]); }
  const short = [...best.values()].map((v, i) => [v, i] as const).sort((a, b) => (b[0][0] - a[0][0]) || (a[1] - b[1])).map((x) => x[0]).slice(0, SHORTLIST);
  const raw: CellScores = new Map();
  for (const [, c] of short) {
    let v = W_NCC * dot(fi, c.fi) + W_PH * ham(ph, c.ph) + W_COL * inter(cl, c.cl);
    if (bonus && c.e === bonus) v += ELEM_BONUS * ec;
    raw.set(c.n, [v, c.c, c.f]);
  }
  const vs = [...raw.values()].map((v) => v[0]);
  const m = vs.reduce((a, b) => a + b, 0) / vs.length;
  const sd = Math.sqrt(vs.reduce((a, x) => a + (x - m) * (x - m), 0) / vs.length) || 1.0;
  const out: CellScores = new Map();
  for (const [nm, v] of raw) out.set(nm, [(v[0] - m) / sd, v[1], v[2]]);
  return [out, [el, ec]];
}

/** 한 니케는 한 번만. locked(사람이 확정한 칸)은 건드리지 않는다. */
export function assign(cells: CellScores[], locked: Record<number, string> = {}): Array<string | null> {
  const taken = new Map<string, number>();
  const pick: Array<string | null> = new Array(cells.length).fill(null);
  for (const [i, nm] of Object.entries(locked)) { taken.set(nm, Number(i)); pick[Number(i)] = nm; }
  const free = cells.map((_, i) => i).filter((i) => !(i in locked));
  const maxOf = (k: number) => { let mx = 0, any = false; for (const v of cells[k].values()) { if (!any || v[0] > mx) { mx = v[0]; any = true; } } return any ? mx : 0; };
  const order = free.map((i, idx) => [i, idx] as const).sort((a, b) => (maxOf(b[0]) - maxOf(a[0])) || (a[1] - b[1])).map((x) => x[0]);
  for (const i of order) {
    const ranked = [...cells[i].entries()].map((kv, idx) => [kv, idx] as const).sort((a, b) => (b[0][1][0] - a[0][1][0]) || (a[1] - b[1])).map((x) => x[0]);
    for (const [nm] of ranked) if (!taken.has(nm)) { taken.set(nm, i); pick[i] = nm; break; }
  }
  for (let it = 0; it < 6; it++) {
    let moved = false;
    for (let ai = 0; ai < free.length; ai++) for (let bi = ai + 1; bi < free.length; bi++) {
      const i = free[ai], j = free[bi], a = pick[i], b = pick[j];
      if (!a || !b || a === b || !cells[i].has(b) || !cells[j].has(a)) continue;
      if (cells[i].get(b)![0] + cells[j].get(a)![0] > cells[i].get(a)![0] + cells[j].get(b)![0] + 1e-9) { pick[i] = b; pick[j] = a; moved = true; }
    }
    if (!moved) break;
  }
  return pick;
}

/** samples = [[12×12 RGB × ALIGN 25]...] → 가장 잘 맞는 틀. */
export function pickAlign(db: Db, samples: Uint8Array[][]): [number, [number, number, number]] {
  const C = db.C, score = new Array<number>(ALIGN.length).fill(0);
  for (const views of samples) views.forEach((raw, k) => {
    if (raw.length !== C * C * 3) throw new Error(`표본은 ${C}x${C} RGB여야 한다 (받은 길이 ${raw.length})`);
    const px: RGB[] = []; for (let i = 0; i < C * C; i++) px.push([raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]]);
    const co = norm(px.filter((_, i) => db.mc[i]).map(lum));
    let mx = -Infinity; for (const c of db.cards) mx = Math.max(mx, dot(co, c.co));
    score[k] += mx;
  });
  let best = 0; for (let k = 1; k < score.length; k++) if (score[k] > score[best]) best = k;
  return [best, ALIGN[best]];
}

export type CellResult = { pick: string | null; element: string | null; element_conf: number; margin: number; sure: boolean;
  candidates: Array<{ name: string; cos: number; file: string; score: number }> };
const round = (x: number, d: number) => { const p = 10 ** d; return Math.round(x * p) / p; };   // 파이썬 round와 .5 처리는 다를 수 있다 — 대조에서 확인
export function read(db: Db, tiles: Tile[], locked: Record<number, string> = {}): CellResult[] {
  const cells: CellScores[] = [], elems: Array<[string | null, number]> = [];
  for (const t of tiles) { const [c, e] = scoreCell(db, t); cells.push(c); elems.push(e); }
  const pick = assign(cells, locked);
  return cells.map((cell, i) => {
    const rank = [...cell.entries()].map((kv, idx) => [kv, idx] as const).sort((a, b) => (b[0][1][0] - a[0][1][0]) || (a[1] - b[1])).map((x) => x[0]).slice(0, TOPN);
    const chosen = pick[i], top = rank.length ? rank[0][1][0] : 0.0, second = rank.length > 1 ? rank[1][1][0] : 0.0;
    return { pick: chosen, element: elems[i][0], element_conf: round(elems[i][1], 3), margin: round(top - second, 2),
      sure: chosen === (rank.length ? rank[0][0] : null) && top - second >= 0.6,
      candidates: rank.map(([nm, v]) => ({ name: nm, cos: v[1], file: v[2], score: round(v[0], 2) })) };
  });
}
