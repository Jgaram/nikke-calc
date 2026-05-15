#%%

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from calculator.timeline import simulate


def make_char(name, **overrides):
    char = {
        "name": name,
        "level": 400, "breakthrough": 3, "core_enhancement": 0,
        "affinity": 30, "skill_level": 10, "burst_regen_time": 2.0,
        "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
        "equip_skills": {"atk_pct": 20, "max_ammo_pct": 120},  # 장비 옵션 합산값 (% 단위). 9종: atk_pct, element_bonus, max_ammo_pct, crit_rate, crit_dmg, charge_speed_pct, charge_dmg_pct, accuracy_pct, def_pct
        "cube": {"name": "재장", "level": 15},
        "console": {"common_level": 180, "class_level": 100, "company_level": 100},
        "collection_stage": "SR15",
    }
    char.update(overrides)
    return char


#%%
# 셀 1: 팀/버스트 사이클 확인 — 여기서 TARGET과 팀 구성 설정

TARGET = "라피"
team_names = ["아니스 : 스타", "크라운", TARGET, "B3"]
team = [make_char(n) for n in team_names]

r0 = simulate(team, verbose=True)

burst_times = [e.t for e in r0.log.burst_log if e.event == "full_burst 시작"]

print("풀버스트 횟수:", len(burst_times))

DEBUG_T0 = burst_times[4]
DEBUG_T1 = burst_times[4]+2

print(f"검토 구간: {DEBUG_T0:.3f}~{DEBUG_T1:.3f}s\n")
simulate(team, config={"_debug_char": TARGET, "_debug_t0": DEBUG_T0, "_debug_t1": DEBUG_T1})


#%%
FIXED = ["아니스 : 스타", "크라운"]
CANDIDATES = [
    "라피 : 레드 후드",
    "스노우 화이트 : 헤비암즈",
    "신데렐라",
    "리버렐리오",
    "홍련 : 흑영",
    "네온 : 비전 아이",
    "미하라 : 본딩 체인",
    "도로시 : 세렌디피티",
    "디젤 : 윈터 스위츠"
]

results = []
for candidate in CANDIDATES:
    names = FIXED + [candidate, "B3"]
    team = [make_char(n) for n in names]
    result = simulate(team)
    results.append((candidate, result))

for candidate, r in results:
    print(r.dmg_breakdown(chars=FIXED + [candidate]))

# %%

names=["아니스 : 스타", "크라운", "B3", "디젤 : 윈터 스위츠"]
team = [make_char(n) for n in names]
result = simulate(team)

print(result.dmg_breakdown(chars=names))

# %%
