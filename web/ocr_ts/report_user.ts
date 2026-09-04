// 전투력 숫자 판독 — 폴더 인자판: 정답(내가 읽은 값) / 옛(파이썬) / 지금(TS)
//   bun run report_user.ts <ocr_ref 폴더>
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Svm, readRegions } from "./power_ocr.ts";
const svm = new Svm(JSON.parse(readFileSync(join(import.meta.dir, "power_svm.json"), "utf8")));
const DIR = process.argv[2];
let tot = 0, pyOk = 0, tsOk = 0;
for (const f of readdirSync(DIR).filter((x) => x.endsWith(".json")).sort()) {
  const ref = JSON.parse(readFileSync(join(DIR, f), "utf8"));
  const truth: string[] | null = ref.truth;
  const rows = ref.cases.map((c: any) => {
    const r = readRegions([{ w: c.w, h: c.h, rgb: Uint8Array.from(Buffer.from(c.rgb_b64, "base64")) }], svm)[0];
    return { py: c.expected.value, ts: r.value, conf: (r as any).confidence ?? (r as any).conf };
  });
  const line = rows.map((r: any, i: number) => {
    const t = truth?.[i]; if (t) { tot++; if (String(r.py) === t) pyOk++; if (String(r.ts) === t) tsOk++; }
    const mark = t ? (String(r.ts) === t ? "✓" : `✗(정답 ${t})`) : "·";
    return `${mark}${r.ts}${String(r.py) !== String(r.ts) ? `(py ${r.py})` : ""}`;
  }).join("  ");
  console.log(`${ref.shot.padEnd(28)} 영역 ${rows.length}  ${line}`);
}
console.log(`정답 있는 줄 ${tot}: 옛 ${pyOk}/${tot} · 지금 ${tsOk}/${tot}`);
