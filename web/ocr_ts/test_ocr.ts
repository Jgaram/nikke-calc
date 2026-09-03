// 파이썬 정본(ocr_ref/*.json)과 단계별 대조 — 조각 상자 → 캔버스 → 증강 → HOG → 라벨 → 최종값.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Svm, augment, hogOf, normalizeDigit, readRegions, segmentDigits } from "./power_ocr.ts";

const DIR = join(import.meta.dir, "ref");
const svm = new Svm(JSON.parse(readFileSync(join(import.meta.dir, "power_svm.json"), "utf8")));
const verbose = process.argv.includes("--verbose");

const tally = { regions: 0, boxOk: 0, canvasOk: 0, augExact: 0, augClose: 0, augTotal: 0, hogMax: 0, labelOk: 0, labelTotal: 0, valueOk: 0, sureOk: 0 };
const bad: string[] = [];
for (const f of readdirSync(DIR).filter((x) => x.endsWith(".json")).sort()) {
  const ref = JSON.parse(readFileSync(join(DIR, f), "utf8"));
  for (let ci = 0; ci < ref.cases.length; ci++) {
    const c = ref.cases[ci];
    const rgb = Uint8Array.from(Buffer.from(c.rgb_b64, "base64"));
    tally.regions++;
    // 1) 조각 상자
    const digs = segmentDigits(rgb, c.w, c.h);
    const boxes = digs.map((d) => [d.x, d.y, d.w, d.h].join(","));
    const refBoxes = c.digits.map((d: any) => d.box.join(","));
    const boxOk = boxes.join("|") === refBoxes.join("|");
    if (boxOk) tally.boxOk++; else bad.push(`${f}#${ci} 상자 다름: ts ${boxes.length}개 vs py ${refBoxes.length}개`);
    // 2~5) 조각마다 (상자가 맞을 때만 의미 있음)
    if (boxOk) {
      digs.forEach((d, k) => {
        const r = c.digits[k];
        const canvas = normalizeDigit(d);
        const refCanvas = Uint8Array.from((r.canvas as number[][]).flat());
        const cEq = canvas.every((v, i) => v === refCanvas[i]);
        if (cEq) tally.canvasOk++; else if (verbose) bad.push(`${f}#${ci}.${k} 캔버스 다름`);
        const augs = augment(canvas);
        augs.forEach((a, ai) => {
          const ra = Uint8Array.from((r.augs[ai] as number[][]).flat());
          let maxd = 0; for (let i = 0; i < a.length; i++) maxd = Math.max(maxd, Math.abs(a[i] - ra[i]));
          tally.augTotal++;
          if (maxd === 0) tally.augExact++; else if (maxd <= 2) tally.augClose++;
          else if (verbose) bad.push(`${f}#${ci}.${k} 증강${ai} 차이 ${maxd}`);
        });
        const hog = hogOf(canvas);
        let hmax = 0; for (let i = 0; i < 540; i++) hmax = Math.max(hmax, Math.abs(hog[i] - r.hog[i]));
        tally.hogMax = Math.max(tally.hogMax, hmax);
        augs.forEach((a, ai) => {
          tally.labelTotal++;
          if (svm.predict(hogOf(a)) === r.labels[ai]) tally.labelOk++;
        });
      });
    }
    // 6) 최종값
    const got = readRegions([{ w: c.w, h: c.h, rgb }], svm)[0];
    if (got.value === c.expected.value) tally.valueOk++; else bad.push(`${f}#${ci} 값 다름: ts ${got.value} vs py ${c.expected.value} (정답 ${ref.truth?.[ci] ?? "?"})`);
    if (got.sure === c.expected.sure) tally.sureOk++;
  }
}
console.log(`영역 ${tally.regions}: 상자 일치 ${tally.boxOk} · 최종값 일치 ${tally.valueOk} · sure 일치 ${tally.sureOk}`);
console.log(`캔버스 일치 ${tally.canvasOk} · 증강 정확 ${tally.augExact}/${tally.augTotal} (±2 이내 ${tally.augClose}) · HOG 최대차 ${tally.hogMax.toExponential(2)} · 라벨 일치 ${tally.labelOk}/${tally.labelTotal}`);
for (const b of bad.slice(0, 25)) console.log("  " + b);
