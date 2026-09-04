// TS 판독기 쪽 끊기 진단 — ocr_ref 덤프(JSON)의 한 줄에 대해 문턱별 조각·쉼표, 쉼표 기준 순서, 후보 순위와 읽은 값.
//   bun run diag_ts.ts <ref.json> <줄 번호>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Svm, OFFSETS, UNIT_SCALES, STABLE_GATE, toGray, segmentCandidates, normalizeDigit, augment, hogOf } from "./power_ocr.ts";
import * as M from "./power_ocr.ts";

const ref = JSON.parse(readFileSync(process.argv[2], "utf8"));
const li = Number(process.argv[3] ?? 0);
const c = ref.cases[li];
const rgb = Uint8Array.from(Buffer.from(c.rgb_b64, "base64"));
const svm = new Svm(JSON.parse(readFileSync(join(import.meta.dir, "power_svm.json"), "utf8")));
const g = toGray(rgb, c.w, c.h);
const internal = M as any;
console.log(`${ref.shot} 줄 ${li}: ${c.w}x${c.h} 정답 ${ref.truth?.[li]} · 옛 ${c.expected.value}`);
for (const off of OFFSETS) {
  const comps = internal.components(g, off);
  const got = internal.pickDigits(comps, true);
  if (!Array.isArray(got[0])) { console.log(`  문턱 ${off}: 조각 ${comps.length} → 없음`); continue; }
  const [keep, commas] = got;
  console.log(`  문턱 ${off}: 조각 ${comps.length} → 숫자 ${keep.length} ${keep.map((d: any) => `${d.x}+${d.w}`).join(" ")} · 쉼표 ${commas.map((d: any) => d.x).join(",")}`);
}
const cands = segmentCandidates(rgb, c.w, c.h);
console.log(`  후보 ${cands.length}개:`);
for (const digs of cands.slice(0, 6)) {
  let txt = "", stable = 1;
  for (const d of digs) {
    const votes = new Map<number, number>();
    for (const a of augment(normalizeDigit(d))) { const v = svm.predict(hogOf(a)); votes.set(v, (votes.get(v) ?? 0) + 1); }
    let lab = -1, top = -1, tot = 0; for (const [v, n] of votes) { tot += n; if (n > top) { top = n; lab = v; } }
    txt += lab; stable = Math.min(stable, top / tot);
  }
  console.log(`    ${digs.length}자리 ${digs.map((d) => `${d.x}+${d.w}`).join(" ")} → ${txt} (안정 ${stable.toFixed(2)})`);
}
