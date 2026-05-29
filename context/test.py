#%%
# test.py — 단일 캐릭터 디버그 및 스쿼드 동작 수동 확인용
# regression_test.py(기준값 자동 비교)와 달리 탐색·검증 목적

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from calculator.timeline import simulate


def make_char(name, **overrides):
    char = {
        "name": name,
        "level": 400, "breakthrough": 3, "core_enhancement": 0,
        "affinity": 30, "skill_levels": {"1": 10, "2": 10, "3": 10},
        "burst_regen_time": 2.0,
        "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
        "equip_skills": {"atk_pct": 20, "max_ammo_pct": 120},
        "cube": {"name": "재장", "level": 15},
        "console": {"common_level": 180, "class_level": 100, "company_level": 100},
        "collection_stage": "SR15",
    }
    char.update(overrides)
    return char


#%%
# ── 셀 1: 스쿼드 설정 ─────────────────────────────────────────────────────────
# 스쿼드 구성 템플릿 및 버스트 간격 확인 절차 → context/CALCULATOR.md §9 참고

TARGET = "아스카 : WILLE"  # 아스카 : WILLE 검증

# 아스카 : WILLE 검증 스쿼드 (시나리오 표준)
squad = [make_char(n) for n in ["리틀 머메이드", "크라운", "아스카 : WILLE", "test_B3"]]

r = simulate(squad, verbose=True)
print(r.summary())

#%%
# ── 셀 2: 버스트 사이클 확인 ───────────────────────────────────────────────────

burst_times = [e.t for e in r.log.burst_log if "full_burst" in e.event]
print(f"풀버스트 횟수: {len(burst_times)}")
if len(burst_times) > 1:
    gaps = [f"{burst_times[i+1]-burst_times[i]:.2f}s" for i in range(min(5, len(burst_times)-1))]
    print(f"사이클 간격 (처음 5회): {gaps}")

#%%
# ── 셀 3: 특정 구간 상세 디버그 ───────────────────────────────────────────────

DEBUG_BURST_IDX = 4          # 몇 번째 버스트 구간을 볼지
DEBUG_WINDOW    = 2.0        # 구간 길이(초)

t0 = burst_times[DEBUG_BURST_IDX]
t1 = t0 + DEBUG_WINDOW
print(f"검토 구간: {t0:.3f}~{t1:.3f}s")
simulate(squad, config={"_debug_char": TARGET, "_debug_t0": t0, "_debug_t1": t1})

#%%
# ── 셀 4: 버프 스냅샷 요약 ────────────────────────────────────────────────────

print(r.log.buff_summary(chars=[TARGET]))
