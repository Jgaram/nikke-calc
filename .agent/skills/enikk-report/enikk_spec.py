"""enikk 덤프 → 보고서 스펙 + 기준값 JSON.


브라우저에서 긁은 덱 목록(`SKILL.md §2`의 스니펫 출력)을 받아 두 파일을 만든다.

  reports/specs/<슬러그>.json   — `/report` 러너 입력 (계산 가능한 덱만)
  reports/refs/<슬러그>.json    — `report_ref.py` 입력 (enikk 평균딜)

캐릭터는 **이름이 아니라 id로 조인한다.** enikk 썸네일 URL의 `si_c{id}_`가
`scraper/nikke_scraped.json`의 `id`와 같은 체계다. 한국 서버 명칭은 영문명을 그대로
음차하지 않아(Liter=리타, Moran=목단) 이름 매칭은 반드시 틀린다.

    python .agent/skills/enikk-report/enikk_spec.py <덤프.txt> \
        --slug sr35-enikk-teams --min-uses 3 \
        --title "..." --note "..." --code 풍압 --runs 5

덤프 형식 — 한 줄에 한 덱, 공백/줄바꿈 구분:

    192,583,234,101,074=867|8.31|5.84
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]

# 한국어 캐릭터명을 그대로 찍는다 (윈도우 기본 cp949에서 깨진다).
sys.stdout.reconfigure(encoding="utf-8")


def load_decks(text: str) -> list[tuple[list[int], int, float, float]]:
    out = []
    for tok in text.split():
        ids, rest = tok.split("=")
        n, mx, av = rest.split("|")
        out.append(([int(x) for x in ids.split(",")], int(n), float(mx), float(av)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="enikk 덱 덤프 → 스펙 + 기준값")
    ap.add_argument("dump", help="덱 덤프 텍스트 파일")
    ap.add_argument("--slug", required=True, help="출력 파일명 (영문 슬러그)")
    ap.add_argument("--min-uses", type=int, default=3, help="이 횟수 이상 사용된 덱만 (기본 3)")
    ap.add_argument("--title", default="enikk 실사용 조합 딜량")
    ap.add_argument("--note", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--code", default=None,
                    help="랩쳐 코드. 보스 속성을 그대로 적는다 (약점이 아니다)")
    ap.add_argument("--core-px", type=int, default=0)
    ap.add_argument("--has-parts", action="store_true")
    ap.add_argument("--ref-label", default="enikk 평균")
    a = ap.parse_args()

    sc = json.loads((ROOT / "scraper/nikke_scraped.json").read_text(encoding="utf-8"))
    sk = json.loads((ROOT / "data/parsed_skills.json").read_text(encoding="utf-8"))
    byid = {v.get("id"): k for k, v in sc.items()}

    decks = [d for d in load_decks(pathlib.Path(a.dump).read_text(encoding="utf-8"))
             if d[1] >= a.min_uses]

    cases, ref, skipped, unknown = [], {}, [], set()
    for ids, n, _mx, av in decks:
        names = [byid.get(i) for i in ids]
        for i, nm in zip(ids, names):
            if nm is None:
                unknown.add(i)
        bad = [nm for nm in names if nm is None or nm not in sk]
        if bad:
            skipped.append((names, n, bad))
            continue
        key = " · ".join(names)
        cases.append({"name": f"{n}회 · {key}", "squad": names})
        ref[key] = av

    if not cases:
        sys.exit("계산 가능한 덱이 없다 — 덤프나 --min-uses를 확인하라")

    enemy: dict = {"core_px": a.core_px, "has_parts": a.has_parts}
    if a.code:
        enemy["code"] = a.code
    spec = {"title": a.title, "note": a.note, "runs": a.runs,
            "enemy": enemy, "cases": cases}

    sp = ROOT / f"reports/specs/{a.slug}.json"
    rp = ROOT / f"reports/refs/{a.slug}.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    rp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    rp.write_text(json.dumps(
        {"label": a.ref_label, "unit": "B", "scale": 1e9, "by_squad": ref},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{a.min_uses}회 이상 {len(decks)}개 → 계산가능 {len(cases)}개 / 제외 {len(skipped)}개")
    for names, n, bad in skipped:
        shown = [nm or "?" for nm in names]
        print(f"  제외 ({n}회): {' · '.join(shown)}   ← {', '.join(str(b) for b in bad if b)}")
    if unknown:
        print(f"  ⚠ 스크랩 데이터에 없는 id: {sorted(unknown)} — cdn_fetch 갱신이 필요할 수 있다")
    print(f"\n{sp}\n{rp}")


if __name__ == "__main__":
    main()
