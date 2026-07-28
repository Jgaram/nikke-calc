"""
Phase 4: 단일 히트 대미지 계산기

DealForm:
  대미지 = ① 계수 × ② 공방차이 × ③ 보너스 × ④ 차지 × ⑤ 유형별 버프 × ⑥ 적 받는 × ⑦ 우월 코드

사용법:
  result = calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)
  # result = {"damage": int, "is_crit": bool}

hit_type 딕셔너리:
  {
    "is_core":                      False,  # 코어 히트 (코어 대미지 가산. 스킬 공격은 is_core_damage와 함께여야 적용)
    "is_core_damage":               False,  # core_damage 스킬 (확정 코어 명중 — is_normal_atk=False라도 ③ 코어 배율 적용)
    "is_part":                      False,  # 파츠 명중 (part_dmg_pct 가산). 파츠를 명시한 스킬 + enemy.has_parts=True일 때만
    "is_full_burst":                False,  # 풀버스트 타임 (+50%)
    "is_optimal_range":             False,  # 적정거리 (+30%, 스킬에는 미적용)
    "is_full_charge":               False,  # SR/RL 풀 차지 (④ 적용)
    "is_burst_damage":              False,  # burst_damage 스킬 (burst_dmg 가산)
    "is_aoe_burst":                 False,  # 그 버스트 대미지의 대상이 '적 전체'인 경우
                                            # (burst_dmg_aoe_pct 추가 가산. is_burst_damage 전제)
    "is_pierce_damage":             False,  # pierce_damage 스킬 (pierce_dmg 가산)
    "is_armor_break_damage":        False,  # armor_break_damage 스킬 (armor_break_dmg_pct 가산, ②에서 적 방어력 0)
    "is_dot":                       False,  # dot_damage 스킬 (dot_dmg 가산)
    "is_projectile_explosion":      False,  # projectile_explosion_damage (projectile_explosion_dmg 가산)
    "is_projectile_attachment":     False,  # projectile_attachment_damage (projectile_attachment_dmg 가산)
    "is_sequential":                False,  # sequential_damage (sequential_dmg 가산)
    "is_normal_atk":                True,   # 기본 무기 일반 공격 여부
                                            # True  → ① normal_atk_dmg_pct 적용
                                            #         ③ 적정거리/코어 가산 허용
                                            #         크리 시 normal_atk_crit 적용
                                            # False → 스킬 공격 (위 항목들 미적용)
    "coeff":                        None,   # 계수 override (None이면 weapon["damage_coeff"] 사용)
    "is_final_atk":                 False,  # "최종 공격력 X% 대미지" 스킬이면 True
                                            # 차이 없음(공식 동일), 향후 구분용으로 보존
  }
"""

from __future__ import annotations

import random

DEFAULT_ENEMY_DEF = 31784.0

# ── 코드 상성 ─────────────────────────────────────────────────────────────

_CODE_ADVANTAGE: dict[str, str] = {
    "전격": "수냉",
    "수냉": "작열",
    "작열": "풍압",
    "풍압": "철갑",
    "철갑": "전격",
}

def is_element_match(char_code: str, enemy_code: str) -> bool:
    """캐릭터 코드가 적 코드에 우월한지 반환."""
    return _CODE_ADVANTAGE.get(char_code, "") == enemy_code

# ── 기본 hit_type ─────────────────────────────────────────────────────────

def default_hit_type(**overrides) -> dict:
    ht = {
        "is_core":          False,
        "is_full_burst":    False,
        "is_optimal_range": False,
        "is_full_charge":   False,
        "is_burst_damage":              False,
        "is_aoe_burst":                 False,
        "is_pierce_damage":             False,
        "is_armor_break_damage":        False,
        "is_dot":                       False,
        "is_projectile_explosion":      False,
        "is_projectile_attachment":     False,
        "is_sequential":                False,
        "is_split":                     False,
        "is_part":                      False,
        "is_core_damage":               False,
        "is_normal_atk":                True,
        "coeff":            None,
        "is_final_atk":     False,
    }
    ht.update(overrides)
    return ht


# ── DealForm 각 항목 ──────────────────────────────────────────────────────

def _factor1(weapon: dict, buffs: dict, hit_type: dict) -> float:
    """① 계수 × (1 + 일반 공격 대미지 배율 %▲)"""
    coeff = hit_type["coeff"]
    if coeff is None:
        coeff = weapon["damage_coeff"]

    # normal_atk_dmg_pct는 기본 무기 일반 공격에만 적용
    if hit_type["is_normal_atk"]:
        normal_bonus = buffs.get("normal_atk_dmg_pct", 0.0) / 100.0
    else:
        normal_bonus = 0.0

    return coeff * (1.0 + normal_bonus)


def _factor2(base_atk: float, enemy_def: float, buffs: dict, hit_type: dict) -> float:
    """② {기본공격력 × (1 + atk_pct%) + atk_flat}
       – {적방어력 × (1 + enemy_def_down_pct%) × (1 – def_ignore_pct%)}
    enemy_def_down_pct: 적 방어력 감소 버프 합(음수). armor_break_damage는 적 방어력을 0으로 계산."""
    atk_term = base_atk * (1.0 + buffs.get("atk_pct", 0.0) / 100.0) \
               + buffs.get("atk_flat", 0.0)
    if hit_type.get("is_armor_break_damage"):
        def_term = 0.0
    else:
        eff_def = max(enemy_def * (1.0 + buffs.get("enemy_def_down_pct", 0.0) / 100.0), 0.0)
        def_term = eff_def * (1.0 - buffs.get("def_ignore_pct", 0.0) / 100.0)
    return max(atk_term - def_term, 0.0)


def _factor3(weapon: dict, buffs: dict, hit_type: dict) -> tuple[float, bool]:
    """
    ③ 보너스 배율 반환 및 크리티컬 판정.
    반환: (factor3, is_crit)

    가산 항목:
      크리티컬: (0.5 + crit_dmg%) — 확률 판정
      풀버스트 타임: +0.5
      적정거리: +0.3 (is_normal_atk=True인 경우만)
      코어 대미지: (core_dmg_mult% − 100%) + core_dmg% (is_normal_atk=True인 경우만)
    """
    bonus = 1.0
    is_crit = False

    # 크리티컬 확률 판정
    # normal_atk_crit_rate는 is_normal_atk=True일 때만 가산
    base_crit_rate = buffs.get("crit_rate", 0.15)  # get_buffs에서 이미 0.15 포함
    if not hit_type["is_normal_atk"]:
        # 스킬 공격: normal_atk_crit_rate 제외한 순수 crit_rate만 사용
        # (buff_manager가 normal_atk_crit_rate와 crit_rate를 모두 crit_rate에 합성함)
        # → 스킬 히트의 크리확률은 별도 인자로 넘기는 방식이 정확하지만,
        #   현재 parsed_skills에 normal_atk_crit_rate를 분리 추적하지 않으므로
        #   스킬 히트도 buffs["crit_rate"]를 그대로 사용 (보수적 근사)
        crit_rate = base_crit_rate
    else:
        crit_rate = base_crit_rate

    if random.random() < crit_rate:
        is_crit = True
        bonus += 0.5 + buffs.get("crit_dmg", 0.0) / 100.0

    # 풀버스트 타임
    if hit_type["is_full_burst"]:
        bonus += 0.5

    # 적정거리 (일반 공격에만)
    if hit_type["is_optimal_range"] and hit_type["is_normal_atk"]:
        bonus += 0.3

    # 코어 대미지 (일반 공격 + core_damage 스킬)
    # core_damage 스킬은 "코어 명중 대미지"가 명시된 확정 코어 히트라 is_normal_atk=False라도 태운다.
    if hit_type["is_core"] and (hit_type["is_normal_atk"] or hit_type.get("is_core_damage")):
        # 무기 코어 대미지(예: 200%)는 비코어 기본 100% 대비 추가분 → -100%
        core_base = (weapon.get("core_dmg_mult", 200.0) - 100.0) / 100.0
        core_extra = buffs.get("core_dmg_pct", 0.0) / 100.0
        bonus += core_base + core_extra

    return bonus, is_crit


def _factor4(weapon: dict, buffs: dict, hit_type: dict) -> float:
    """
    ④ 차지 배율.
    풀 차지가 아니면 1.0.
    charge_dmg_mag_pct가 있으면: (1 + charge_dmg_mag_pct%) × full_charge_mult% × (1 + charge_dmg_pct%)
    없으면: full_charge_mult% × (1 + charge_dmg_pct%)
    """
    if not hit_type["is_full_charge"]:
        return 1.0

    full_charge_mult = weapon.get("full_charge_mult", 100.0) / 100.0
    charge_dmg_pct = buffs.get("charge_dmg_pct", 0.0) / 100.0
    charge_dmg_mag_pct = buffs.get("charge_dmg_mag_pct", 0.0) / 100.0

    if charge_dmg_mag_pct:
        return (1.0 + charge_dmg_mag_pct) * full_charge_mult * (1.0 + charge_dmg_pct)
    else:
        return full_charge_mult * (1.0 + charge_dmg_pct)


def _factor5(buffs: dict, hit_type: dict) -> float:
    """
    ⑤ 유형별 버프.
    100% + 공격대미지▲ [+ 대미지 유형별 버프 선택 합산]
    분배 대미지(split_dmg)는 ⑥에 합산 — 여기서는 제외.
    """
    val = 1.0 + buffs.get("atk_dmg_pct", 0.0) / 100.0

    if hit_type.get("is_burst_damage"):
        val += buffs.get("burst_dmg_pct", 0.0) / 100.0
        # 대상이 '적 전체'인 버스트 대미지에만 추가 가산 (트리나 뻗은 뿌리).
        # 같은 clause의 bonus_damage·dot_damage는 대상 아님 → is_burst_damage 안에 둔다.
        if hit_type.get("is_aoe_burst"):
            val += buffs.get("burst_dmg_aoe_pct", 0.0) / 100.0
    if hit_type.get("is_pierce_damage"):
        val += buffs.get("pierce_dmg_pct", 0.0) / 100.0
    if hit_type.get("is_armor_break_damage"):
        val += buffs.get("armor_break_dmg_pct", 0.0) / 100.0
    if hit_type.get("is_dot"):
        val += buffs.get("dot_dmg_pct", 0.0) / 100.0
    if hit_type.get("is_projectile_explosion"):
        val += buffs.get("projectile_explosion_dmg", 0.0) / 100.0
    if hit_type.get("is_projectile_attachment"):
        val += buffs.get("projectile_attachment_dmg", 0.0) / 100.0
    if hit_type.get("is_sequential"):
        val += buffs.get("sequential_dmg_pct", 0.0) / 100.0

    # 파츠 대미지 — hit_type["is_part"]로 제어
    if hit_type.get("is_part"):
        val += buffs.get("part_dmg_pct", 0.0) / 100.0

    return val


def _factor6(buffs: dict, hit_type: dict) -> float:
    """
    ⑥ 적 받는 대미지.
    100% + received_dmg▲ [+ split_dmg▲]
    분배 대미지(split_dmg)는 received_dmg와 함께 ⑥에서 합산.
    """
    val = 1.0 + buffs.get("received_dmg", 0.0) / 100.0

    if hit_type.get("is_split"):
        val += buffs.get("split_dmg_pct", 0.0) / 100.0

    return val


def _factor7(buffs: dict) -> float:
    """⑦ 우월 코드. 100% [+ 10% + element_bonus%▲]"""
    if not buffs.get("is_element_match", False):
        return 1.0
    return 1.0 + 0.1 + buffs.get("element_bonus_pct", 0.0) / 100.0


# ── 메인 함수 ─────────────────────────────────────────────────────────────

def calc_damage(
    base_atk: float,
    buffs: dict,
    weapon: dict,
    hit_type: dict | None = None,
    enemy_def: float = DEFAULT_ENEMY_DEF,
) -> dict:
    """
    단일 히트 대미지 계산.

    Parameters
    ----------
    base_atk  : calc_base_stats()["atk"]
    buffs     : buff_manager.get_buffs() 결과
    weapon    : parsed_nikke.json의 캐릭터 항목
    hit_type  : default_hit_type(**overrides) 로 생성. None이면 기본값 사용.
    enemy_def : 적 방어력 (기본 31784)

    Returns
    -------
    {"damage": int, "is_crit": bool}
    """
    if hit_type is None:
        hit_type = default_hit_type()

    f1 = _factor1(weapon, buffs, hit_type)
    f2 = _factor2(base_atk, enemy_def, buffs, hit_type)
    f3, is_crit = _factor3(weapon, buffs, hit_type)
    f4 = _factor4(weapon, buffs, hit_type)
    f5 = _factor5(buffs, hit_type)
    f6 = _factor6(buffs, hit_type)
    f7 = _factor7(buffs)

    # ① × ② × ③ × ④ × ⑤ × ⑥ × ⑦
    # ①의 계수는 %이므로 /100
    damage = (f1 / 100.0) * f2 * f3 * f4 * f5 * f6 * f7

    if hit_type.get("_debug_factors"):
        print(
            f"  ①계수={f1:.4f}%  ②공방차={f2:,.1f}  ③보너스={f3:.4f}(크리={is_crit})"
            f"  ④차지={f4:.4f}  ⑤유형={f5:.4f}  ⑥받는={f6:.4f}  ⑦코드={f7:.4f}"
            f"  → {max(round(damage), 1):,}"
        )

    # 공격력 < 방어력이면 f2=0 → 최소 1 보장
    return {"damage": max(round(damage), 1), "is_crit": is_crit}


def calc_damage_avg(
    base_atk: float,
    buffs: dict,
    weapon: dict,
    hit_type: dict | None = None,
    enemy_def: float = DEFAULT_ENEMY_DEF,
) -> float:
    """
    크리티컬 확률을 기댓값으로 처리한 평균 대미지.
    시뮬레이션 없이 기댓값을 빠르게 검산할 때 사용.
    """
    if hit_type is None:
        hit_type = default_hit_type()

    f1 = _factor1(weapon, buffs, hit_type)
    f2 = _factor2(base_atk, enemy_def, buffs, hit_type)
    f4 = _factor4(weapon, buffs, hit_type)
    f5 = _factor5(buffs, hit_type)
    f6 = _factor6(buffs, hit_type)
    f7 = _factor7(buffs)

    # ③ 기댓값: 크리 기여분 = crit_rate × (0.5 + crit_dmg%)
    crit_rate = buffs.get("crit_rate", 0.15)
    crit_dmg = buffs.get("crit_dmg", 0.0) / 100.0
    f3_base = 1.0
    if hit_type["is_full_burst"]:
        f3_base += 0.5
    if hit_type["is_optimal_range"] and hit_type["is_normal_atk"]:
        f3_base += 0.3
    if hit_type["is_core"] and (hit_type["is_normal_atk"] or hit_type.get("is_core_damage")):
        # 무기 코어 대미지는 비코어 기본 100% 대비 추가분 → -100%
        core_base = (weapon.get("core_dmg_mult", 200.0) - 100.0) / 100.0
        core_extra = buffs.get("core_dmg_pct", 0.0) / 100.0
        f3_base += core_base + core_extra
    f3_avg = f3_base + crit_rate * (0.5 + crit_dmg)

    return max((f1 / 100.0) * f2 * f3_avg * f4 * f5 * f6 * f7, 1.0)


# ── 단위 테스트 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 버프 없는 기본 상태 (buff_manager._BUFFS_ZERO 모방)
    zero_buffs = {
        "atk_pct": 0.0, "atk_flat": 0.0, "def_ignore_pct": 0.0,
        "crit_rate": 0.15, "crit_dmg": 0.0, "core_dmg_pct": 0.0,
        "atk_dmg_pct": 0.0, "burst_dmg_pct": 0.0, "pierce_dmg_pct": 0.0,
        "dot_dmg_pct": 0.0, "armor_break_dmg_pct": 0.0,
        "projectile_explosion_dmg": 0.0, "projectile_attachment_dmg": 0.0,
        "sequential_dmg_pct": 0.0,
        "charge_dmg_pct": 0.0, "charge_dmg_mag_pct": 0.0,
        "received_dmg": 0.0, "split_dmg_pct": 0.0,
        "element_bonus_pct": 0.0, "is_element_match": False,
        "def_pct": 0.0, "charge_speed_pct": 0.0, "max_ammo_pct": 0.0,
        "accuracy_pct": 0.0, "normal_atk_dmg_pct": 0.0, "reload_speed_pct": 0.0,
        "part_dmg_pct": 0.0, "burst_dmg_aoe_pct": 0.0,
    }

    # 라피 AR 기본 스펙 (parsed_nikke.json 값 대신 임시값)
    weapon_ar = {
        "weapon_type": "AR",
        "damage_coeff": 13.65,
        "core_dmg_mult": 200.0,
    }

    base_atk = 50000  # 임의 공격력

    # ── 검산 1: 버프 없음, 크리 없음 (avg으로 검산)
    avg = calc_damage_avg(base_atk, zero_buffs, weapon_ar, enemy_def=DEFAULT_ENEMY_DEF)
    # 수작업: (13.65/100) × (50000 - 31784) × (1 + 0.15×0.5) = 0.1365 × 18216 × 1.075
    expected = (13.65 / 100) * (50000 - 31784) * (1 + 0.15 * 0.5)
    print(f"검산 1 — 평균 대미지: {avg:.2f}  (수작업: {expected:.2f})")
    assert abs(avg - expected) < 1.0, f"불일치: {avg} vs {expected}"

    # ── 검산 2: atk_pct +100%, 크리 없음
    buffs2 = dict(zero_buffs)
    buffs2["atk_pct"] = 100.0
    avg2 = calc_damage_avg(base_atk, buffs2, weapon_ar, enemy_def=DEFAULT_ENEMY_DEF)
    expected2 = (13.65 / 100) * (50000 * 2.0 - 31784) * (1 + 0.15 * 0.5)
    print(f"검산 2 — atk +100%: {avg2:.2f}  (수작업: {expected2:.2f})")
    assert abs(avg2 - expected2) < 1.0, f"불일치: {avg2} vs {expected2}"

    # ── 검산 3: 코어 히트 (일반 공격), 크리 없음
    buffs3 = dict(zero_buffs)
    buffs3["crit_rate"] = 0.0
    ht3 = default_hit_type(is_core=True)
    avg3 = calc_damage_avg(base_atk, buffs3, weapon_ar, hit_type=ht3, enemy_def=DEFAULT_ENEMY_DEF)
    # f3 = 1.0 + 1.0(core 200% → +100% 추가분) + 0(crit) = 2.0
    expected3 = (13.65 / 100) * (50000 - 31784) * 2.0
    print(f"검산 3 — 코어 히트 (crit_rate=0): {avg3:.2f}  (수작업: {expected3:.2f})")
    assert abs(avg3 - expected3) < 1.0, f"불일치: {avg3} vs {expected3}"

    # ── 검산 4: SR 풀 차지, full_charge_mult=250%
    weapon_sr = {"weapon_type": "SR", "damage_coeff": 50.0,
                 "core_dmg_mult": 200.0, "full_charge_mult": 250.0}
    buffs4 = dict(zero_buffs)
    buffs4["crit_rate"] = 0.0
    ht4 = default_hit_type(is_full_charge=True)
    avg4 = calc_damage_avg(base_atk, buffs4, weapon_sr, hit_type=ht4, enemy_def=DEFAULT_ENEMY_DEF)
    # f1=50, f2=(50000-31784), f3=1.0, f4=2.5, f5=1.0, f6=1.0, f7=1.0
    expected4 = (50.0 / 100) * (50000 - 31784) * 1.0 * 2.5
    print(f"검산 4 — SR 풀 차지: {avg4:.2f}  (수작업: {expected4:.2f})")
    assert abs(avg4 - expected4) < 1.0, f"불일치: {avg4} vs {expected4}"

    # ── 검산 5: 풀버스트 + 우월코드
    buffs5 = dict(zero_buffs)
    buffs5["crit_rate"] = 0.0
    buffs5["is_element_match"] = True
    buffs5["element_bonus_pct"] = 10.0
    ht5 = default_hit_type(is_full_burst=True)
    avg5 = calc_damage_avg(base_atk, buffs5, weapon_ar, hit_type=ht5, enemy_def=DEFAULT_ENEMY_DEF)
    # f3=1.5, f7=1.2(10%+10%)
    expected5 = (13.65 / 100) * (50000 - 31784) * 1.5 * 1.0 * 1.0 * 1.2
    print(f"검산 5 — 풀버스트 + 우월코드: {avg5:.2f}  (수작업: {expected5:.2f})")
    assert abs(avg5 - expected5) < 1.0, f"불일치: {avg5} vs {expected5}"

    # ── 검산 6: core_damage 스킬 (is_normal_atk=False인데도 코어 배율이 실려야 함)
    buffs6 = dict(zero_buffs)
    buffs6["crit_rate"] = 0.0
    buffs6["core_dmg_pct"] = 26.0
    ht6 = default_hit_type(is_normal_atk=False, is_core=True, is_core_damage=True, coeff=833.79)
    avg6 = calc_damage_avg(base_atk, buffs6, weapon_ar, hit_type=ht6, enemy_def=DEFAULT_ENEMY_DEF)
    # f3 = 1.0 + (200-100)/100 + 26/100 = 2.26
    expected6 = (833.79 / 100) * (50000 - 31784) * 2.26
    print(f"검산 6 — core_damage 스킬: {avg6:.2f}  (수작업: {expected6:.2f})")
    assert abs(avg6 - expected6) < 1.0, f"불일치: {avg6} vs {expected6}"

    # 같은 히트에서 is_core_damage를 빼면 코어 배율이 빠져야 한다 (스킬은 기본적으로 코어 미적용)
    ht6b = default_hit_type(is_normal_atk=False, is_core=True, coeff=833.79)
    avg6b = calc_damage_avg(base_atk, buffs6, weapon_ar, hit_type=ht6b, enemy_def=DEFAULT_ENEMY_DEF)
    expected6b = (833.79 / 100) * (50000 - 31784) * 1.0
    assert abs(avg6b - expected6b) < 1.0, f"불일치: {avg6b} vs {expected6b}"

    # ── 검산 7: part_dmg_pct는 is_part 히트에만 ⑤로 가산
    buffs7 = dict(zero_buffs)
    buffs7["crit_rate"] = 0.0
    buffs7["part_dmg_pct"] = 26.21
    ht7 = default_hit_type(is_normal_atk=False, is_part=True, coeff=1189.66)
    avg7 = calc_damage_avg(base_atk, buffs7, weapon_ar, hit_type=ht7, enemy_def=DEFAULT_ENEMY_DEF)
    expected7 = (1189.66 / 100) * (50000 - 31784) * (1.0 + 26.21 / 100)
    print(f"검산 7 — part_dmg_pct (is_part): {avg7:.2f}  (수작업: {expected7:.2f})")
    assert abs(avg7 - expected7) < 1.0, f"불일치: {avg7} vs {expected7}"

    ht7b = default_hit_type(is_normal_atk=False, coeff=1189.66)
    avg7b = calc_damage_avg(base_atk, buffs7, weapon_ar, hit_type=ht7b, enemy_def=DEFAULT_ENEMY_DEF)
    expected7b = (1189.66 / 100) * (50000 - 31784)
    assert abs(avg7b - expected7b) < 1.0, f"불일치: {avg7b} vs {expected7b}"

    # ── 검산 8: burst_dmg_aoe_pct — '적 전체' 버스트에만 가산 (트리나 뻗은 뿌리)
    buffs8 = dict(zero_buffs)
    buffs8["crit_rate"] = 0.0
    buffs8["burst_dmg_pct"] = 50.0
    buffs8["burst_dmg_aoe_pct"] = 435.6
    ht8_aoe = default_hit_type(is_normal_atk=False, is_burst_damage=True, is_aoe_burst=True)
    ht8_st = default_hit_type(is_normal_atk=False, is_burst_damage=True)          # 단일 대상 버스트
    ht8_bonus = default_hit_type(is_normal_atk=False, is_aoe_burst=True)          # bonus_damage 취급
    a8 = calc_damage_avg(base_atk, buffs8, weapon_ar, hit_type=ht8_aoe, enemy_def=DEFAULT_ENEMY_DEF)
    b8 = calc_damage_avg(base_atk, buffs8, weapon_ar, hit_type=ht8_st, enemy_def=DEFAULT_ENEMY_DEF)
    c8 = calc_damage_avg(base_atk, buffs8, weapon_ar, hit_type=ht8_bonus, enemy_def=DEFAULT_ENEMY_DEF)
    base8 = (13.65 / 100) * (50000 - 31784)
    print(f"검산 8 — AoE 버스트: {a8:.2f} (수작업 {base8 * (1 + 0.5 + 4.356):.2f}) / "
          f"단일 버스트: {b8:.2f} (수작업 {base8 * 1.5:.2f}) / bonus: {c8:.2f} (수작업 {base8:.2f})")
    assert abs(a8 - base8 * (1 + 0.5 + 4.356)) < 1.0, f"불일치: {a8}"
    assert abs(b8 - base8 * 1.5) < 1.0, f"불일치: {b8}"          # aoe 미적용
    assert abs(c8 - base8) < 1.0, f"불일치: {c8}"                # is_burst_damage 아니면 둘 다 미적용

    print("\n모든 검산 통과.")
