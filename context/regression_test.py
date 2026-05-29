import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from calculator.timeline import simulate

# ── 기준값: 2026-05-29, 30회 평균 ±3σ (스쿼드3: 클립 SG 드레이크 반영) ──
# 기준값(총 딜 평균, ±3σ 허용 오차)과 캐릭터별 딜 평균은 이 파일이 단일 출처다.
# 갱신 시: 30회 재측정 → expected / char_avg 모두 이 파일만 수정.
# 조건: equip_skills={}, first_burst_time=3.0
# 2026-05-28: RL/SR post_fire_delay 기본값 0.215→0.38, 엄폐 자동재장전 모델링 반영.

SQUADS = {
    "스쿼드1": {
        "members": ["리틀 머메이드", "크라운", "라피 : 레드 후드", "미하라", "헬름"],
        "config":  {"first_burst_time": 3.0},
        "expected": (1_866_164_300, 29_306_951),   # (총 딜 평균, ±3σ)
        "char_avg": {
            "리틀 머메이드":    573_918_226,
            "크라운":          194_534_838,
            "라피 : 레드 후드": 859_116_920,
            "미하라":          113_663_651,
            "헬름":            124_930_664,
        },
    },
    "스쿼드2": {
        # 리틀 머메이드 버스트 미사용
        "members": ["츠바이", "나유타", "프리바티", "스노우 화이트 : 헤비암즈", "리틀 머메이드"],
        "config":  {"no_burst_char": "리틀 머메이드", "first_burst_time": 3.0},
        "expected": (2_648_656_174, 56_041_121),
        "char_avg": {
            "츠바이":                  108_872_317,
            "나유타":                  328_270_032,
            "프리바티":                397_239_976,
            "스노우 화이트 : 헤비암즈": 1_477_460_327,
            "리틀 머메이드":            336_813_520,
        },
    },
    "스쿼드3": {
        # 솔린 : 프로스트 티켓 버스트 미사용
        "members": ["토브", "아르카나 : 포츈 메이트", "도로시 : 세렌디피티", "드레이크", "솔린 : 프로스트 티켓"],
        "config":  {"no_burst_char": "솔린 : 프로스트 티켓", "first_burst_time": 3.0},
        "expected": (3_151_748_793, 31_688_804),
        "char_avg": {
            "토브":                  40_146_737,
            "아르카나 : 포츈 메이트": 642_376_615,
            "도로시 : 세렌디피티":   1_221_124_006,
            "드레이크":               819_212_172,
            "솔린 : 프로스트 티켓":   428_889_261,
        },
    },
    "스쿼드4": {
        "members": ["목단", "마스트 : 로망틱 메이드", "홍련 : 흑영", "리버렐리오", "앵커 : 이노센트 메이드"],
        "config":  {"first_burst_time": 3.0},
        "expected": (2_302_660_897, 48_381_855),
        "char_avg": {
            "목단":                    17_788_849,
            "마스트 : 로망틱 메이드":   191_580_634,
            "홍련 : 흑영":            1_158_824_652,
            "리버렐리오":               860_568_934,
            "앵커 : 이노센트 메이드":    73_897_830,
        },
    },
    "스쿼드5": {
        "members": ["아니스 : 스타", "아르카나", "이사벨", "신데렐라", "크라운"],
        "config":  {"first_burst_time": 3.0},
        "expected": (3_716_628_062, 53_149_513),
        "char_avg": {
            "아니스 : 스타": 760_307_155,
            "아르카나":      162_328_785,
            "이사벨":        872_553_961,
            "신데렐라":    1_701_789_103,
            "크라운":        219_649_056,
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
        "equip_skills": {},
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
    print("=== 회귀 테스트 (기준: 2026-05-23, 30회 평균 ±3σ) ===\n")
    results = [check_squad(name, info) for name, info in SQUADS.items()]
    n_pass  = sum(results)
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} 통과")
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
