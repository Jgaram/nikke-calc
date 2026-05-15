import sys
sys.stdout.reconfigure(encoding='utf-8')
from calculator.timeline import simulate

def make_char(name, **overrides):
    char = {
        'name': name,
        'level': 400, 'breakthrough': 3, 'core_enhancement': 0,
        'affinity': 30, 'skill_level': 10, 'burst_regen_time': 2.0,
        'equipment': {p: {'level': 5, 'skills': []} for p in ['머리', '몸통', '팔', '다리']},
        'equip_skills': {'atk_pct': 20, 'max_ammo_pct': 120},
        'cube': {'name': '재장', 'level': 15},
        'console': {'common_level': 180, 'class_level': 100, 'company_level': 100},
        'collection_stage': 'SR15',
    }
    char.update(overrides)
    return char

team = [make_char(n) for n in ['아니스 : 스타', '크라운', '미하라 : 본딩 체인', 'B3']]
r = simulate(team)
print(r.dmg_breakdown())
