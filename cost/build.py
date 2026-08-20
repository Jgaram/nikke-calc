"""`data/cost_expected.json`을 굽는다. `python -m cost.build`

소장품 기대값만 실계산이고 나머지는 `tables.json`을 그대로 옮긴다. 이 산출물은
**손으로 고치지 않는다** — 표를 고쳤으면 이 스크립트를 다시 돌린다.

웹앱은 `cost/`를 번들에 넣지 않고 이 JSON만 읽는다 (`web/build.py`의 `data/*.json`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import TABLES, daily_points, kit_supply, kit_supply_by_source, kit_weights, mix_for
from . import kit as kit_mod

OUT = Path(__file__).resolve().parent.parent / "data" / "cost_expected.json"


def build() -> dict:
    w = kit_weights()

    collection: dict[str, dict[str, dict]] = {}
    mixes: dict[str, dict[str, int]] = {}
    for grade in ("R", "SR"):
        mix = mix_for(grade)
        mixes[grade] = {k: mix.count(k) for k in kit_mod.GRADES}
        rows: dict[str, dict] = {}
        for target in (5, 10, 15):
            # 시작은 0~14 전부. 1→5·8→10처럼 구간 중간에서 출발하는 것도 들어간다.
            for start in range(min(target, kit_mod.MAX_START + 1)):
                kits = kit_mod.consume(grade, start, target, mix)
                pts = kit_mod.score(kits, w)
                rows[f"{start}->{target}"] = {
                    "kits": {k: round(v, 3) for k, v in kits.items() if v},
                    "points": round(pts, 2),
                }
        collection[grade] = rows

    return {
        "_생성": "python -m cost.build — 손으로 고치지 않는다",
        "_단위": "관리키트는 개수(회 수 아님). points는 초급키트 1개 = 1점.",
        "kit_supply_by_source": {s: {k: round(v, 3) for k, v in p.items()}
                                 for s, p in kit_supply_by_source().items()},
        "kit_supply_30d": {k: round(v, 3) for k, v in kit_supply().items()},
        "kit_weights": {k: round(v, 4) for k, v in w.items()},
        "kit_daily_points": round(daily_points(), 3),
        "kit_mix": mixes,
        "collection": collection,
        "skill_manual": TABLES["스킬_메뉴얼"],
        "gear": TABLES["장비강화"],
        "breakthrough": TABLES["돌파코강"],
        "overload_module": TABLES["오버로드_모듈"],
        "missing": TABLES["_미수집"],
        "excluded": TABLES["_안다룸"],
    }


def main() -> int:
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for v in data["collection"].values())
    print(f"{OUT.relative_to(OUT.parent.parent)} — 소장품 {n}줄, "
          f"하루 수급 {data['kit_daily_points']}점")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
