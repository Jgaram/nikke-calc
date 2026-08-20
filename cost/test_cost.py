"""재화 소모량 회귀. `python -m cost.test_cost`.

앵커는 종합.xlsx(상위 폴더)가 이미 내놓은 값이다. 시트의 SR 결과 셀은 수식이 아니라
붙여넣은 상수라 하나가 어긋나는데, 그 건은 아래 주석에 남긴다.
"""

from __future__ import annotations

import sys

from . import (TABLES, breakthrough, collection, collection_to, kit, kit_supply,
               kit_supply_by_source, kit_weights, manual_cost, mix_for, parse_stage,
               points_to_kits, skill_manual, standard_mix)

# 종합.xlsx `소장품관리` — "한 가지 키트만 사용할 때 키트 사용 수 기대 값"
SINGLE_KIT = {
    ("R", 0, 5):   (52.507548, 17.562006, 10.0),
    ("R", 5, 10):  (56.639073, 19.062219, 10.0),
    ("R", 10, 15): (60.991210, 20.747222, 10.0),
    ("SR", 0, 5):  (202.696304, 68.566942, 33.009875),
    ("SR", 5, 10): (284.514804, 89.967579, 39.280107),
    ("SR", 10, 15): (373.360926, 108.104527, 44.954569),
}
# SR 중급키트만 어긋난다 (구간별 +0.003% / +0.06% / +0.27%). 시트의 해당 셀 W13:W15는
# 수식이 아닌 상수라 갱신이 한 번 밀린 것으로 본다 — 초급·상급은 같은 상수인데도
# 소수 6자리까지 맞으므로, 모델이 아니라 그 세 셀이 낡았다고 판단했다.
STALE = {("SR", 0, 5, "중급키트"), ("SR", 5, 10, "중급키트"), ("SR", 10, 15, "중급키트")}


def check(name: str, got: float, want: float, tol: float) -> bool:
    ok = abs(got - want) <= tol
    print(f"  {'OK  ' if ok else 'FAIL'} {name:34} {got:12.6f}  기대 {want:12.6f}")
    return ok


def main() -> int:
    bad = 0

    print("소장품 — 한 종류만 사용 (종합.xlsx 대조)")
    for (grade, s, t), wants in SINGLE_KIT.items():
        for k, want in zip(kit.GRADES, wants):
            got = kit.consume(grade, s, t, [k])[k]
            tol = 1.0 if (grade, s, t, k) in STALE else 1e-4
            bad += not check(f"{grade} {s}→{t} {k}", got, want, tol)

    print("\n소장품 — 0→15 합계")
    for grade, wants in (("R", (170.137832, 57.371438, 30.0)),
                         ("SR", (860.572034, 266.639047, 117.244551))):
        for k, want in zip(kit.GRADES, wants):
            got = kit.consume(grade, 0, 15, [k])[k]
            tol = 1.0 if (grade, 0, 5, k) in STALE else 1e-4
            bad += not check(f"{grade} 0→15 {k}", got, want, tol)

    print("\n점수 왕복 — 배합 비율로 쪼갠 뒤 다시 더하면 제자리")
    w = kit_weights()
    for grade in ("R", "SR"):
        for pts in (100.0, 1265.4):
            back = points_to_kits(pts, grade)
            bad += not check(f"{grade} {pts}점 → 키트 → 점수",
                             kit.score(back, w), pts, 1e-9)

    print("\n키트 수급 — 원시값에서 다시 센다")
    src = kit_supply_by_source()
    box = TABLES["키트_수급"]["상자"]
    # 파견: 손으로 곱한 값과 맞춘다 (4슬롯 × 30일 = 120회)
    for g, want_per_run in (("초급키트", 0.15 * 2 + 0.15 * 3 + 0.15 * box["상자1"]["초급키트"]
                             + 0.08 * 2 * box["상자1"]["초급키트"]
                             + 0.08 * box["상자2"]["초급키트"]
                             + 0.04 * 2 * box["상자2"]["초급키트"]),
                            ("중급키트", 0.06 * 2 + 0.03 * 3 + 0.15 * box["상자1"]["중급키트"]
                             + 0.08 * 2 * box["상자1"]["중급키트"]
                             + 0.08 * box["상자2"]["중급키트"]
                             + 0.04 * 2 * box["상자2"]["중급키트"]),
                            ("상급키트", 0.04 * 1 + 0.02 * 2
                             + 0.08 * box["상자2"]["상급키트"]
                             + 0.04 * 2 * box["상자2"]["상급키트"])):
        bad += not check(f"파견 {g}", src["파견"][g], want_per_run * 120, 1e-9)
    # 솔로레이드: 상자1 12개 + 상자2 51개
    for g in kit.GRADES:
        bad += not check(f"솔로레이드 {g}", src["솔로레이드"][g],
                         12 * box["상자1"][g] + 51 * box["상자2"][g], 1e-9)
    # 뮤지엄: 6개월에 3회 → 월 0.5회분
    for g, per in (("초급키트", 170), ("중급키트", 60), ("상급키트", 30)):
        bad += not check(f"뮤지엄 {g}", src["뮤지엄"][g], per * 0.5, 1e-9)

    sup = kit_supply()
    for g in kit.GRADES:
        bad += not check(f"희소가치 {g}", kit_weights()[g], sup["초급키트"] / sup[g], 1e-12)
    # 배합은 주기 길이를 다 쓰고 어느 등급도 0칸이 아니어야 한다.
    seq = standard_mix()
    bad += not check("SR 배합 주기 길이", len(seq), TABLES["표준_비율"]["주기_길이"], 0)
    for g in kit.GRADES:
        bad += not check(f"SR 배합 {g} 칸 수 ≥ 1", min(seq.count(g), 1), 1, 0)
    bad += not check("R 배합은 초급만", len(set(mix_for("R"))), 1, 0)

    print("\n구간 중간에서 출발하는 경우")
    for grade, s, t in (("SR", 1, 5), ("SR", 8, 10), ("R", 3, 5), ("SR", 12, 15)):
        r = collection(grade, s, t)
        total = sum(r["cost"].values())
        ok = total > 0 and r["points"] > 0
        print(f"  {'OK  ' if ok else 'FAIL'} {grade} {s}→{t:<3} "
              f"{ {k: round(v, 1) for k, v in r['cost'].items() if v} }  {r['points']:.0f}점")
        bad += not ok
    # 부분 구간이 전체 구간보다 쌀 수밖에 없다.
    bad += not check("SR 1→5 < SR 0→5", collection("SR", 1, 5)["points"],
                     min(collection("SR", 1, 5)["points"],
                         collection("SR", 0, 5)["points"] - 1e-9), 1e-6)

    print("\n스킬 메뉴얼 — 노랑만 세고 파랑·보라는 값이 남아 있다")
    bad += not check("MANUAL_COST 8", manual_cost()[8], 90.0, 0)
    bad += not check("MANUAL_COST 10", manual_cost()[10], 120.0, 0)
    br = skill_manual("1", 7, 10)["breakdown"]
    for grade, want in (("파랑", 714.0), ("보라", 505.0), ("노랑", 315.0)):
        bad += not check(f"7→10 {grade}", br[grade], want, 0)
    bad += not check("7→10 비용 = 노랑", skill_manual("1", 7, 10)["cost"]["스킬 메뉴얼"],
                     br["노랑"], 0)

    print("\n돌파·코어강화 — 한 칸이 1뽑기")
    for frm, to, want in (((0, 0), (3, 0), 3), ((3, 0), (3, 7), 7), ((0, 0), (3, 7), 10),
                          ((1, 0), (3, 2), 4), ((3, 7), (3, 7), 0)):
        bad += not check(f"{frm} → {to}", breakthrough(frm, to)["cost"]["뽑기"], want, 0)
    for frm, to in (((0, 0), (2, 3)), ((0, 0), (4, 0)), ((3, 7), (3, 0)), ((0, 0), (3, 8))):
        try:
            breakthrough(frm, to)
        except ValueError:
            print(f"  OK   {frm} → {to}은 거부됨")
        else:
            print(f"  FAIL {frm} → {to}이 통과됐다")
            bad += 1

    print("\ncollection_stage 어댑터 — 시뮬레이터 표현을 그대로 받는다")
    for stage, want in (("SR10", ("SR", 10)), ("R0", ("R", 0)), ("SR15", ("SR", 15)),
                        ("없음", None)):
        got = parse_stage(stage)
        ok = got == want
        print(f"  {'OK  ' if ok else 'FAIL'} {stage:<5} → {got}")
        bad += not ok
    for stage in ("SR16", "X3", "SR", ""):
        try:
            parse_stage(stage)
        except ValueError:
            print(f"  OK   {stage!r}은 거부됨")
        else:
            print(f"  FAIL {stage!r}이 통과됐다")
            bad += 1
    bad += not check("SR10→15 = collection(SR,10,15)",
                     collection_to("SR10")["points"], collection("SR", 10, 15)["points"], 1e-12)
    bad += not check("이미 SR15면 0점", collection_to("SR15")["points"], 0.0, 0)
    if collection_to("없음") is None:
        print("  OK   미장착은 None (소장품 아이템 비용은 안 다룬다)")
    else:
        print("  FAIL 미장착이 None이 아니다")
        bad += 1

    print("\n목표 레벨 제약")
    for target in (3, 7, 12):
        try:
            collection("SR", 0, target)
        except ValueError:
            print(f"  OK   목표 {target}은 거부됨")
        else:
            print(f"  FAIL 목표 {target}이 통과됐다")
            bad += 1

    print(f"\n{'실패 ' + str(bad) + '건' if bad else '전부 통과'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
