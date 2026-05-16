import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from calculator.timeline import simulate
from calculator.sim_result import _is_normal

# 기준값: 평균 딜, 허용 오차(±3σ)
# 10회 반복 측정 기준 (2026-05-16). MAINTENANCE.md ## 회귀 테스트 기준점 참조.
EXPECTED = {
    #                              평균 딜         ±허용오차(3σ)
    "라피 : 레드 후드":        (930_363_525,  33_058_455),
    "스노우 화이트 : 헤비암즈": (1_021_407_534, 11_437_152),
    "신데렐라":                (1_110_701_455, 12_510_492),
    "리버렐리오":              (956_935_208,   19_861_056),
    "홍련 : 흑영":             (994_991_354,   28_349_745),
    "네온 : 비전 아이":        (1_017_420_818, 21_442_971),
    "미하라 : 본딩 체인":      (831_573_692,   20_839_743),
    "도로시 : 세렌디피티":     (1_272_138_309, 19_375_341),
    "디젤 : 윈터 스위츠":      (994_496_904,   28_505_046),
}

FIXED = ["아니스 : 스타", "크라운"]

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
    if candidate == "디젤 : 윈터 스위츠":
        names = FIXED + ["B3", candidate]
    else:
        names = FIXED + [candidate, "B3"]
    team = [make_char(n) for n in names]
    result = simulate(team)
    return result.char_total.get(candidate, 0)


def check(candidate: str) -> bool:
    exp_avg, tol = EXPECTED[candidate]
    sigma = tol / 3  # tol = 3σ
    got = run_candidate(candidate)

    diff = got - exp_avg
    ok = abs(diff) <= tol
    print(f"[{PASS if ok else FAIL}] {candidate}")
    if not ok:
        n_sigma = abs(diff) / sigma
        print(f"       딜: 기대 {exp_avg:,} ±{tol:,}  실제 {got:,}  차이 {diff:+,}  ({n_sigma:.1f}σ)")
    return ok


def main():
    print("=== 회귀 테스트 (기준: 2026-05-16, 10회 평균 ±3σ) ===\n")
    results = [check(c) for c in EXPECTED]
    n_pass = sum(results)
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} 통과", "" if n_pass == n_total else "<- 실패 항목 확인 필요")
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
