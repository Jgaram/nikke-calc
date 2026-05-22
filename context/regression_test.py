import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from calculator.timeline import simulate

# ── 기준값: 2026-05-22, 30회 평균 ±3σ ────────────────────────────────────────
# 기준값(총 딜 평균, ±3σ 허용 오차)과 캐릭터별 딜 평균은 이 파일이 단일 출처다.
# 갱신 시: 30회 재측정 → expected / char_avg 모두 이 파일만 수정.

SQUADS = {
    "스쿼드1": {
        "members": ["리틀 머메이드", "크라운", "라피 : 레드 후드", "미하라", "헬름"],
        "config":  {},
        "expected": (2_130_489_617, 35_011_962),   # (총 딜 평균, ±3σ)
        "char_avg": {
            "리틀 머메이드":    647_306_357,
            "크라운":          217_505_381,
            "라피 : 레드 후드": 938_137_479,
            "미하라":          142_953_033,
            "헬름":            184_587_364,
        },
    },
    "스쿼드2": {
        # 리틀 머메이드 버스트 미사용
        "members": ["츠바이", "나유타", "프리바티", "스노우 화이트 : 헤비암즈", "리틀 머메이드"],
        "config":  {"no_burst_char": "리틀 머메이드"},
        "expected": (3_335_447_089, 62_298_888),
        "char_avg": {
            "츠바이":                  135_048_894,
            "나유타":                  381_904_017,
            "프리바티":                277_797_542,
            "스노우 화이트 : 헤비암즈": 2_138_033_038,
            "리틀 머메이드":            402_663_596,
        },
    },
    "스쿼드3": {
        # 솔린 : 프로스트 티켓 버스트 미사용
        "members": ["토브", "아르카나 : 포츈 메이트", "도로시 : 세렌디피티", "드레이크", "솔린 : 프로스트 티켓"],
        "config":  {"no_burst_char": "솔린 : 프로스트 티켓"},
        "expected": (3_463_952_844, 29_462_296),
        "char_avg": {
            "토브":                  52_881_834,
            "아르카나 : 포츈 메이트": 702_088_506,
            "도로시 : 세렌디피티":   1_318_584_785,
            "드레이크":               906_512_952,
            "솔린 : 프로스트 티켓":   483_884_766,
        },
    },
    "스쿼드4": {
        "members": ["목단", "마스트 : 로망틱 메이드", "홍련 : 흑영", "리버렐리오", "앵커 : 이노센트 메이드"],
        "config":  {},
        "expected": (2_384_781_660, 70_271_258),
        "char_avg": {
            "목단":                    19_881_366,
            "마스트 : 로망틱 메이드":   195_906_776,
            "홍련 : 흑영":            1_113_514_871,
            "리버렐리오":               959_993_837,
            "앵커 : 이노센트 메이드":    95_484_808,
        },
    },
    "스쿼드5": {
        "members": ["아니스 : 스타", "아르카나", "이사벨", "신데렐라", "크라운"],
        "config":  {},
        "expected": (4_255_341_450, 47_045_765),
        "char_avg": {
            "아니스 : 스타": 861_908_155,
            "아르카나":      218_420_209,
            "이사벨":        985_758_544,
            "신데렐라":    1_955_154_242,
            "크라운":        234_100_298,
        },
    },
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def make_char(name, **overrides):
    char = {
        "name": name,
        "level": 400, "breakthrough": 3, "core_enhancement": 0,
        "affinity": 30, "skill_level": 10, "burst_regen_time": 2.0,
        "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
        "equip_skills": {"atk_pct": 20, "max_ammo_pct": 120},
        "cube": {"name": "재장", "level": 15},
        "console": {"common_level": 180, "class_level": 100, "company_level": 100},
        "collection_stage": "SR15",
    }
    char.update(overrides)
    return char


def check_squad(squad_name: str, info: dict) -> bool:
    members = info["members"]
    config  = info["config"] or None
    exp_avg, tol = info["expected"]
    sigma = tol / 3

    squad  = [make_char(n) for n in members]
    result = simulate(squad, config=config)

    char_totals = {n: result.char_total.get(n, 0) for n in members}
    total = sum(char_totals.values())
    diff  = total - exp_avg
    ok    = abs(diff) <= tol
    n_sigma = abs(diff) / sigma

    print(f"[{PASS if ok else FAIL}] {squad_name}  총 {total:,}  "
          f"(기대 {exp_avg:,} ±{tol:,}  차이 {diff:+,}  {n_sigma:.1f}σ)")

    if not ok:
        print(f"  → 기준 대비 {'초과' if diff > 0 else '미달'} {abs(diff):,} ({abs(diff) / exp_avg * 100:.2f}%)")
        print("  캐릭터별 대미지:")
        for n in members:
            ref   = info["char_avg"].get(n, 0)
            cdiff = char_totals[n] - ref
            print(f"    {n}: {char_totals[n]:,}  (기준 {ref:,}  {cdiff:+,})")

    return ok


def main():
    print("=== 회귀 테스트 (기준: 2026-05-22, 30회 평균 ±3σ) ===\n")
    results = [check_squad(name, info) for name, info in SQUADS.items()]
    n_pass  = sum(results)
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} 통과")
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
