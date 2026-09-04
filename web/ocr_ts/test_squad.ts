// 파이썬 정본 기준값(squad_ref/*.ref.json)과 단계별 대조 — 서명 → 속성 → 후보 점수 → 배정 → 최종.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { assign, loadDb, pickAlign, read, scoreCell, signatures, readElement, type Tile } from "./squad_ocr.ts";

const DIR = join(import.meta.dir, "ref_squad");
const db = loadDb(readFileSync(join(import.meta.dir, "..", "..", "data", "face_sig.json"), "utf8"));
const b64 = (s: string) => Uint8Array.from(Buffer.from(s, "base64"));
const close = (a: number[], b: number[], eps = 1e-9) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= eps);
const t = { align: 0, alignOk: 0, cells: 0, sigOk: 0, elemOk: 0, scoreOk: 0, assignOk: 0, assignN: 0, resultOk: 0, resultN: 0 };
const bad: string[] = [];
for (const f of readdirSync(DIR).filter((x) => x.endsWith(".ref.json")).sort()) {
  const ref = JSON.parse(readFileSync(join(DIR, f), "utf8").replace(/"phash":\s*(\d+)/g, '"phash": "$1"'));   // 64비트 정수는 문자열로
  const req = JSON.parse(readFileSync(join(DIR, f.replace(".ref.json", ".json")), "utf8"));
  if (ref.mode === "align") {
    t.align++;
    const [i] = pickAlign(db, req.samples.map((row: string[]) => row.map(b64)));
    if (i === ref.align_index) t.alignOk++; else bad.push(`${f}: align ${i} vs py ${ref.align_index}`);
    continue;
  }
  const tiles: Tile[] = req.tiles.map((x: any) => ({ c12: b64(x.c12), c24: b64(x.c24), c32: b64(x.c32), badge: b64(x.badge) }));
  const locked: Record<number, string> = {}; for (const [k, v] of Object.entries(ref.locked || {})) locked[Number(k)] = v as string;
  const cells = tiles.map((tile, k) => {
    t.cells++;
    const st = ref.stages[k];
    const [co, fi, ph, cl] = signatures(db, ...(function () { const p = (b: Uint8Array, g: number) => { const o: [number, number, number][] = []; for (let i = 0; i < g * g; i++) o.push([b[i * 3], b[i * 3 + 1], b[i * 3 + 2]]); return o; }; return [p(tile.c12, db.C), p(tile.c24, db.F), p(tile.c32, db.PH)] as const; })());
    const sigOk = close(co, st.coarse) && close(fi, st.fine) && ph === BigInt(st.phash) && close(cl, st.color);
    if (sigOk) t.sigOk++; else bad.push(`${f}#${k} 서명 다름 (coarse ${close(co, st.coarse)} fine ${close(fi, st.fine)} phash ${ph === BigInt(st.phash)} color ${close(cl, st.color)})`);
    const [el, ec] = readElement((function () { const o: [number, number, number][] = []; for (let i = 0; i < 256; i++) o.push([tile.badge[i * 3], tile.badge[i * 3 + 1], tile.badge[i * 3 + 2]]); return o; })());
    if (el === st.element && Math.abs(ec - st.element_conf) < 1e-9) t.elemOk++; else bad.push(`${f}#${k} 속성 ${el}/${ec.toFixed(4)} vs py ${st.element}/${st.element_conf.toFixed(4)}`);
    const [scores] = scoreCell(db, tile);
    const names = Object.keys(st.scores), mine = [...scores.keys()];
    const ok = names.length === mine.length && names.every((nm, i) => nm === mine[i] && Math.abs(scores.get(nm)![0] - st.scores[nm][0]) < 1e-9);
    if (ok) t.scoreOk++; else bad.push(`${f}#${k} 점수 다름 (py ${names.length}명 / ts ${mine.length}명, 첫 ${names[0]}=${st.scores[names[0]]?.[0]?.toFixed(4)} vs ${scores.get(names[0])?.[0]?.toFixed(4)})`);
    return scores;
  });
  t.assignN++;
  const pick = assign(cells, locked);
  if (pick.join("|") === ref.assign.map((x: any) => x ?? "").join("|")) t.assignOk++; else bad.push(`${f} 배정 다름`);
  t.resultN++;
  const got = read(db, tiles, locked);
  if (JSON.stringify(got) === JSON.stringify(ref.result)) t.resultOk++;
  else { const idx = got.findIndex((g, i) => JSON.stringify(g) !== JSON.stringify(ref.result[i])); bad.push(`${f} 최종 결과 다름 (첫 칸 #${idx}: ts ${JSON.stringify(got[idx]).slice(0, 120)} vs py ${JSON.stringify(ref.result[idx]).slice(0, 120)})`); }
}
console.log(`align ${t.alignOk}/${t.align} · 칸 ${t.cells}: 서명 ${t.sigOk} · 속성 ${t.elemOk} · 점수 ${t.scoreOk} · 배정 ${t.assignOk}/${t.assignN} · 최종 ${t.resultOk}/${t.resultN}`);
for (const b of bad.slice(0, 12)) console.log("  " + b);
