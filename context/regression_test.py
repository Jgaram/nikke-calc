import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from calculator.timeline import simulate
from calculator.sim_result import _is_normal

EXPECTED = {
    #                              평균 딜          ±허용오차(3σ)
    "라피 : 레드 후드":        (879_716_435,   31_348_560),
    "스노우 화이트 : 헤비암즈": (406_184_557,   16_661_145),
    "신데렐라":                (892_112_704,   11_376_222),
    "리버렐리오":              (880_361_999,   34_058_454),
    "홍련 : 흑영":             (965_246_467,   20_143_002),
    "네온 : 비전 아이":        (1_132_869_949, 23_894_652),
    "미하라 : 본딩 체인":      (953_550_945,   21_551_994),
    "도로시 : 세렌디피티":     (904_475_167,   18_180_000),
    "디젤 : 윈터 스위츠":      (958_008_102,   36_142_593),
}

FIXED = ["아니스 : 스타", "크라운", "B3"]

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


def run_candidate(candidate: str) -> int:
    names = FIXED + [candidate]
    team = [make_char(n) for n in names]
    result = simulate(team)
    return result.char_total.get(candidate, 0)


def check(candidate: str) -> bool:
    exp_avg, tol = EXPECTED[candidate]
    sigma = tol / 3  # tol = 3σ
    got = run_candidate(candidate)

    diff = got - exp_avg
    ok = abs(diff) <= tol
    n_sigma = abs(diff) / sigma
    print(f"[{PASS if ok else FAIL}] {candidate}  실제 {got:,}  (기대 {exp_avg:,} ±{tol:,}  차이 {diff:+,}  {n_sigma:.1f}σ)")
    return ok


def main():
    print("=== 회귀 테스트 (기준: 2026-05-17, 10회 평균 ±3σ) ===\n")
    n_total = len(EXPECTED)
    for i, c in enumerate(EXPECTED, 1):
        ok = check(c)
        if not ok:
            print(f"\n{i-1}/{n_total} 통과 후 FAIL — 위 수치를 확인하고 의도한 변경인지 판단하세요.")
            sys.exit(1)
    print(f"\n{n_total}/{n_total} 통과")
    sys.exit(0)


if __name__ == "__main__":
    main()
