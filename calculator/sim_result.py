"""
전투 시뮬레이션 로그 및 결과 데이터클래스.

simulate() 함수가 반환하는 SimResult와,
verbose=True 시 함께 채워지는 SimLog를 정의한다.

클래스 구성:
  HitEvent        — 단일 피격 이벤트 (시각, 피해량, 크리 여부 등)
  BurstLogEntry   — 버스트 단계 사용/풀버스트 시작·종료 이벤트
  BuffEntry       — 스냅샷에 기록된 활성 버프 1건
  BuffSnapshot    — 풀버스트 진입 시각의 전체 버프 상태 스냅샷
  ReloadLogEntry  — 재장전 시작/완료 이벤트
  AmmoLogEntry    — 탄환 수 변화 이벤트
  SimLog          — 위 이벤트들의 컨테이너 + 요약 출력 메서드
  CategoryStat    — 딜량·히트수 묶음 (DamageBreakdown 구성 요소)
  DamageBreakdown — 캐릭터 대미지 분석 결과 (유형별·버스트 구간별)
  SimResult       — 전체 시뮬 결과 (히트 목록, 딜 합산) + 분석 출력 메서드

분석 함수:
  analyze_damage(result, char_name) → DamageBreakdown
  analyze_team(result)              → list[DamageBreakdown]
  print_team_analysis(result, chars)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────

_NORMAL_ATK_TAGS = frozenset({
    "normal", "core", "full_charge_hit", "core+full_charge_hit",
})

def _is_normal(ev: "HitEvent") -> bool:
    """HitEvent가 일반공격 계열이면 True.

    기본 발사 루프(CharState)에서 생성된 히트를 판별한다.
    skill_name == "기본 공격", pellet:N / core:pellet:N 태그,
    normal / core / full_charge_hit / core+full_charge_hit 태그를 포함한다.
    스킬 대미지(burst_damage, dot_damage, bonus_damage 등)는 False.
    """
    if ev.skill_name == "기본 공격":
        return True
    tag = ev.hit_tag
    if tag.startswith("pellet:") or tag.startswith("core:pellet:"):
        return True
    return tag in _NORMAL_ATK_TAGS


# ── HitEvent ──────────────────────────────────────────────────────────────

@dataclass
class HitEvent:
    """단일 피격 이벤트. simulate()가 생성하는 가장 기본 단위."""
    t: float          # 발생 시각 (초)
    caster: str       # 딜러 캐릭터명
    damage: int       # 최종 피해량 (방어력 계산 후 정수)
    is_crit: bool     # 크리티컬 여부 (기대값 모드에서는 **항상 False** — crit_frac을 보라)
    crit_frac: float = 0.0
                      # 이 히트가 낸 크리 "횟수". 확률 판정 모드에서는 0.0/1.0이고,
                      # 기대값 모드에서는 **크리 확률 그 자체**(0~1)다.
                      # `damage.py _factor3`가 계산해 결과 dict로 넘기던 값을 그대로 싣는다 —
                      # 기대값 모드에서는 `is_crit`이 늘 False라 이 값이 유일한 크리 정보다.
    hit_tag: str = ""  # 피격 종류 식별자:
                      #   "normal"            — 일반 자동 발사
                      #   "core"              — 코어 명중
                      #   "full_charge_hit"   — 풀차지 명중 (SR/RL)
                      #   "core+full_charge_hit" — 코어+풀차지
                      #   "pellet:N"          — SG N번째 산탄
                      #   "normal_skill"      — 일반공격 공식 스킬
                      #   "bonus_damage"      — 버스트 스킬 추가 대미지
                      #   "burst_damage" 등   — 그 외 스킬 stat 이름
    skill_name: str = "기본 공격"  # 스킬명 (일반공격은 "기본 공격")


# ── SimLog 구성 엔트리들 ──────────────────────────────────────────────────

@dataclass
class BurstLogEntry:
    """버스트 흐름에서 발생한 단일 이벤트."""
    t: float    # 발생 시각 (초)
    event: str  # 이벤트 종류:
                #   "stage:N 사용"    — N단계 버스트 스킬 사용
                #   "reenter:N 사용"  — N단계 reenter 재사용
                #   "full_burst 시작" — 풀버스트 진입
                #   "full_burst 종료" — 풀버스트 종료
    caster: str # 스킬 사용자 캐릭터명 ("full_burst 시작/종료"는 빈 문자열)


@dataclass
class BuffEntry:
    """BuffSnapshot에 기록된 활성 버프 1건."""
    name: str           # 버프 이름 (parsed_skills의 effect.name)
    caster: str         # 버프를 건 캐릭터명
    expires_at: float   # 만료 시각 (초). math.inf = 영구 지속


@dataclass
class BuffSnapshot:
    """풀버스트 진입 시각에 찍은 스쿼드 전체 버프 상태 스냅샷.

    BurstController가 "full_burst 시작" 이벤트 발생 시 생성한다.
    """
    t: float                              # 스냅샷 시각 (초)
    buffs_by_char: dict[str, list[BuffEntry]]  # 캐릭터명 → 해당 캐릭터에 적용된 버프 목록


@dataclass
class BuffEvent:
    """버프 1건의 활성/갱신/만료 이벤트."""
    t: float            # 발생 시각 (초)
    kind: str           # "activate" | "expire"
    name: str           # 버프 이름
    caster: str         # 버프를 건 캐릭터명
    target: str         # 버프를 받은 캐릭터명
    expires_at: float   # 활성화 시 예정 만료 시각 (expire 이벤트에서는 실제 만료 시각)
    value: float | None = None  # 버프 수치 (스킬 레벨 기준); expire 이벤트는 None
    stat: str | None = None     # 버프 stat 종류 (e.g. "atk_pct", "charge_speed_pct")


@dataclass
class InstantEvent:
    """인스턴트 효과 발동 이벤트."""
    t: float            # 발생 시각 (초)
    name: str           # 효과 이름
    caster: str         # 시전자
    target: str         # 대상 캐릭터명
    stat: str           # stat 종류 (e.g. "ammo_charge_pct")
    value: float | None # 수치


@dataclass
class ReloadLogEntry:
    """재장전 시작 또는 완료 이벤트."""
    t: float      # 발생 시각 (초)
    caster: str   # 재장전 캐릭터명
    event: str    # "재장전 시작" 또는 "재장전 완료"


@dataclass
class AmmoLogEntry:
    """탄환 수 변화 이벤트."""
    t: float    # 발생 시각 (초)
    caster: str # 캐릭터명
    ammo: int   # 변화 후 남은 탄환 수


# ── SimLog ────────────────────────────────────────────────────────────────

@dataclass
class SimLog:
    """verbose=True 시뮬 시 채워지는 전투 이벤트 로그 컨테이너.

    simulate()에서 SimResult.log 필드로 반환된다.
    verbose=False이면 SimResult.log는 None.
    """
    burst_log: list[BurstLogEntry] = field(default_factory=list)
    # 버스트 단계 사용, 풀버스트 시작/종료 이벤트 시간순 목록

    buff_snapshots: list[BuffSnapshot] = field(default_factory=list)
    # 풀버스트 진입마다 찍힌 버프 스냅샷 목록 (풀버스트 횟수만큼 쌓임)

    buff_events: list[BuffEvent] = field(default_factory=list)
    # 버프 활성/만료 이벤트 전체 목록 — 전투 전 구간 버프 타임라인 재구성용

    instant_events: list[InstantEvent] = field(default_factory=list)
    # 인스턴트 효과 발동 이벤트 목록

    reload_log: list[ReloadLogEntry] = field(default_factory=list)
    # 전 캐릭터의 재장전 시작/완료 이벤트 시간순 목록

    ammo_log: list[AmmoLogEntry] = field(default_factory=list)
    # 탄환 수 변화 이벤트 목록 (발사·재장전 완료·탄환 충전)

    def burst_summary(self, chars: list[str] | None = None) -> str:
        """버스트 흐름 전체를 시간순으로 출력한다.

        chars 지정 시 해당 캐릭터가 사용자인 이벤트만 표시.
        full_burst 시작/종료처럼 caster가 없는 이벤트는 항상 포함.
        """
        lines = ["[버스트 사이클]"]
        for e in self.burst_log:
            if chars and e.caster and e.caster not in chars:
                continue
            caster_str = f"  {e.caster}" if e.caster else ""
            lines.append(f"  t={e.t:7.3f}s  {e.event}{caster_str}")
        return "\n".join(lines)

    def buff_summary(self, chars: list[str] | None = None) -> str:
        """풀버스트 진입마다 찍힌 버프 스냅샷을 출력한다.

        각 스냅샷은 해당 시각에 활성화된 버프 목록을 캐릭터별로 보여준다.
        chars 지정 시 해당 캐릭터의 버프만 표시.
        """
        lines = ["[버프 스냅샷 — 풀버스트 진입 시]"]
        for snap in self.buff_snapshots:
            lines.append(f"\n  ── t={snap.t:.3f}s ──")
            for char, buffs in snap.buffs_by_char.items():
                if chars and char not in chars:
                    continue
                if not buffs:
                    continue
                lines.append(f"  {char}:")
                for b in buffs:
                    exp = "영구" if b.expires_at == math.inf else f"{b.expires_at:.2f}s까지"
                    lines.append(f"    [{b.name}] by {b.caster}  ({exp})")
        return "\n".join(lines)


# ── SimResult ─────────────────────────────────────────────────────────────

@dataclass
class SimResult:
    """simulate() 반환값. 히트 목록과 딜 합산, 분석 출력 메서드를 포함한다."""
    hits: list[HitEvent] = field(default_factory=list)
    # 전투 중 발생한 모든 HitEvent. simulate() 종료 시 시각순 정렬.

    char_total: dict[str, int] = field(default_factory=dict)
    # 캐릭터명 → 전투 전체 누적 피해량

    squad_total: int = 0
    # 스쿼드 전체 누적 피해량 (char_total 합산)

    duration: float = 0.0
    # 시뮬레이션 지속 시간 (초)

    log: SimLog | None = None
    # verbose=True 시 채워지는 전투 이벤트 로그. False이면 None.

    burst_casts: list = field(default_factory=list)
    # (시각, 단계, 시전자). **verbose와 무관하게** 늘 채워진다 — 최공 대상 진단이
    # 「이 풀버스트의 3버는 누구였나」를 알아야 하는데, 그것 하나로 전체 로그를 켤 수는 없다.

    full_bursts: list = field(default_factory=list)
    # [시작, 종료] 시각 쌍. 위와 같은 이유로 늘 채워진다.

    top_atk_picks: list = field(default_factory=list)
    # 「최종 공격력이 가장 높은 아군」 대상 버프가 갈린 순간들.
    # `{t, caster, buff, stat, spec, chosen, ranking:[{name, atk, base}]}`
    # 미란다 애장품처럼 **대상이 갈리면 딜이 통째로 달라지는** 버프를 눈으로 확인하는 근거다.

    def summary(self, chars: list[str] | None = None) -> str:
        """스쿼드 총 딜과 캐릭터별 딜량·비율을 출력한다.

        chars 지정 시 해당 캐릭터만 표시 (스쿼드 총 딜 기준 비율은 유지).
        딜량 내림차순 정렬.
        """
        lines = [
            f"시뮬레이션 {self.duration:.1f}초  "
            f"스쿼드 총 딜: {self.squad_total:,}"
        ]
        for name, dmg in sorted(self.char_total.items(), key=lambda x: -x[1]):
            if chars and name not in chars:
                continue
            pct = dmg / self.squad_total * 100 if self.squad_total else 0
            lines.append(f"  {name}: {dmg:,} ({pct:.1f}%)")
        return "\n".join(lines)

    def dmg_breakdown(self, chars: list[str] | None = None) -> str:
        """캐릭터별 기본공격/스킬 딜 비율을 한 줄 요약으로 출력한다.

        chars 지정 시 해당 캐릭터만 표시. 딜량 내림차순 정렬.
        스킬별 세부 내역이 필요하면 skill_breakdown_by_cycle() 사용.
        """
        lines = [f"{'캐릭터':<28} {'캐릭터 딜':>14} {'기본공격':>9} {'스킬':>9}"]
        lines.append("-" * 64)
        for name, total in sorted(self.char_total.items(), key=lambda x: -x[1]):
            if chars and name not in chars:
                continue
            normal = sum(e.damage for e in self.hits if e.caster == name and _is_normal(e))
            skill  = sum(e.damage for e in self.hits if e.caster == name and not _is_normal(e))
            n_pct = normal / total * 100 if total else 0
            s_pct = skill  / total * 100 if total else 0
            lines.append(f"{name:<28} {total:>14,} {n_pct:>8.1f}% {s_pct:>8.1f}%")
        return "\n".join(lines)

    def skill_breakdown_by_cycle(self, chars: list[str] | None = None) -> str:
        """버스트 사이클별 스킬 딜량·히트수를 집계해 출력한다.

        사이클 구분:
          사이클 0 — 전투 시작 ~ 첫 풀버스트 직전
          사이클 N — N번째 풀버스트 시작 ~ 다음 풀버스트 직전 (마지막은 전투 종료까지)

        verbose=False(log=None)이면 버스트 경계 정보가 없으므로
        전체 전투를 사이클 0 단일 구간으로 집계한다.

        chars 지정 시 해당 캐릭터만 표시. 각 캐릭터 내 스킬은 딜량 내림차순.
        """
        def skill_key(ev: HitEvent) -> str:
            return "일반공격" if _is_normal(ev) else ev.skill_name

        # 풀버스트 시작 시각으로 사이클 경계 수집
        burst_starts: list[float] = []
        if self.log:
            burst_starts = [e.t for e in self.log.burst_log if e.event == "full_burst 시작"]

        # 구간 정의: (label, t_start, t_end)
        boundaries: list[tuple[str, float, float]] = []
        all_bounds = [0.0] + burst_starts + [math.inf]
        for i in range(len(all_bounds) - 1):
            t0, t1 = all_bounds[i], all_bounds[i + 1]
            label = f"사이클 {i}  (t={t0:.3f}s" + (f" ~ {t1:.3f}s)" if t1 != math.inf else " ~ 종료)")
            boundaries.append((label, t0, t1))

        # 출력 대상 캐릭터 목록 (딜 내림차순)
        target_chars = [
            name for name, _ in sorted(self.char_total.items(), key=lambda x: -x[1])
            if not chars or name in chars
        ]

        lines = ["[버스트 사이클별 스킬 딜 집계]"]

        for label, t0, t1 in boundaries:
            cycle_hits = [e for e in self.hits if t0 <= e.t < t1]
            if not cycle_hits:
                continue

            lines.append(f"\n── {label} ──")

            for name in target_chars:
                char_hits = [e for e in cycle_hits if e.caster == name]
                if not char_hits:
                    continue

                dmg_by_skill: dict[str, int] = {}
                cnt_by_skill: dict[str, int] = {}
                for ev in char_hits:
                    key = skill_key(ev)
                    dmg_by_skill[key] = dmg_by_skill.get(key, 0) + ev.damage
                    cnt_by_skill[key] = cnt_by_skill.get(key, 0) + 1

                char_total = sum(dmg_by_skill.values())
                lines.append(f"  {name}")
                lines.append(f"    {'스킬명':<28} {'딜량':>14} {'히트수':>7} {'비율':>7}")
                lines.append(f"    {'-'*60}")
                for skill, dmg in sorted(dmg_by_skill.items(), key=lambda x: -x[1]):
                    pct = dmg / char_total * 100 if char_total else 0
                    cnt = cnt_by_skill[skill]
                    lines.append(f"    {skill:<28} {dmg:>14,} {cnt:>7} {pct:>6.1f}%")
                lines.append(f"    {'합계':<28} {char_total:>14,}")

        return "\n".join(lines)

    def hit_summary(self, chars: list[str] | None = None) -> str:
        """히트 목록을 시간순으로 출력한다. 재장전·버스트 이벤트를 인터리브한다.

        각 히트 행: 시각 / 캐릭터명 / 스킬명 / 피해량 / 크리·코어 플래그
        인터리브 행: 재장전 시작·완료, 버스트 단계 사용, 풀버스트 시작·종료

        chars 지정 시 해당 캐릭터의 히트만 표시.
        인터리브 이벤트는 chars 필터 적용 대상이지만,
        full_burst 시작/종료(caster 없음)는 항상 출력.
        verbose=False(log=None)이면 인터리브 없이 히트만 출력.
        """
        lines = ["[히트 목록]"]

        # 재장전 + 버스트 이벤트를 하나의 인터리브 목록으로 합산
        inline_events: list[tuple[float, str]] = []
        for e in (self.log.reload_log if self.log else []):
            if not chars or e.caster in chars:
                inline_events.append((e.t, f"{e.caster:<18} [{e.event}]"))
        for e in (self.log.burst_log if self.log else []):
            caster_str = e.caster if e.caster else "스쿼드"
            if not chars or not e.caster or e.caster in chars:
                inline_events.append((e.t, f"{caster_str:<18} [버스트: {e.event}]"))
        inline_events.sort(key=lambda x: x[0])
        inline_idx = 0

        for ev in self.hits:
            while inline_idx < len(inline_events) and inline_events[inline_idx][0] <= ev.t:
                it, msg = inline_events[inline_idx]
                lines.append(f"  t={it:7.3f}s  {msg}")
                inline_idx += 1

            if chars and ev.caster not in chars:
                continue
            flags = ""
            if ev.is_crit:
                flags += " 크리"
            if "core" in ev.hit_tag:
                flags += " 코어"
            lines.append(
                f"  t={ev.t:7.3f}s  {ev.caster:<18} {ev.skill_name:<20}"
                f"  {ev.damage:>12,}{flags}"
            )

        while inline_idx < len(inline_events):
            it, msg = inline_events[inline_idx]
            lines.append(f"  t={it:7.3f}s  {msg}")
            inline_idx += 1

        return "\n".join(lines)


# ── 버스트 구간 분류 분석 ──────────────────────────────────────────────────

@dataclass
class CategoryStat:
    """딜량·히트수 묶음. DamageBreakdown의 각 카테고리를 나타낸다."""
    damage: int = 0
    hits: int = 0

    def pct(self, total: int) -> float:
        """total 대비 이 카테고리의 딜 비율(%)."""
        return self.damage / total * 100 if total else 0.0


@dataclass
class DamageBreakdown:
    """캐릭터 1명의 대미지 분석 결과.

    analyze_damage()가 반환한다. 두 축으로 분류된다:

    피해 유형:
      normal_atk — 기본 발사 루프에서 발생한 피해
      skill      — 스킬 효과(버스트·패시브·DoT 등)에서 발생한 피해

    버스트 구간:
      fb_self  — 본인이 버스트를 사용한 풀버스트 10초 구간
      fb_other — 타인 버스트로 진입한 풀버스트 10초 구간
      non_fb   — 풀버스트 밖 구간

    verbose=False(log=None)로 생성된 SimResult에서는
    버스트 구간 통계(fb_self / fb_other / non_fb)가 모두 0으로 남는다.
    """
    char_name: str

    normal_atk: CategoryStat = field(default_factory=CategoryStat)
    skill: CategoryStat = field(default_factory=CategoryStat)

    fb_self: CategoryStat = field(default_factory=CategoryStat)
    fb_other: CategoryStat = field(default_factory=CategoryStat)
    non_fb: CategoryStat = field(default_factory=CategoryStat)

    _skill_detail: dict[str, CategoryStat] = field(default_factory=dict)
    # 스킬명 → CategoryStat. skill 카테고리의 세부 내역.

    @property
    def total(self) -> int:
        return self.normal_atk.damage + self.skill.damage

    def summary(self) -> str:
        """피해 유형별·버스트 구간별 분석 결과를 출력한다."""
        total = self.total
        fb_total = self.fb_self.damage + self.fb_other.damage + self.non_fb.damage

        lines = [f"=== [{self.char_name}] 대미지 분석 ===", ""]

        lines.append("─ 피해 유형별 비중 ─")
        lines.append(f"  총 대미지  : {total:>15,}")
        lines.append(
            f"  기본 공격  : {self.normal_atk.damage:>15,}  "
            f"({self.normal_atk.pct(total):5.1f}%)  [{self.normal_atk.hits}회]"
        )
        lines.append(
            f"  스킬       : {self.skill.damage:>15,}  "
            f"({self.skill.pct(total):5.1f}%)  [{self.skill.hits}회]"
        )

        if self._skill_detail:
            lines.append("")
            lines.append("  ┌ 스킬 상세")
            for sname, stat in sorted(self._skill_detail.items(), key=lambda x: -x[1].damage):
                lines.append(
                    f"  │  {sname:<30} {stat.damage:>14,}  "
                    f"({stat.pct(total):5.1f}%)  [{stat.hits}회]"
                )
            lines.append("  └")

        lines.append("")
        lines.append("─ 버스트 구간별 비중 ─")
        lines.append(f"  총 대미지      : {fb_total:>15,}")
        lines.append(
            f"  풀버스트 (본인) : {self.fb_self.damage:>15,}  "
            f"({self.fb_self.pct(fb_total):5.1f}%)  [{self.fb_self.hits}회]"
        )
        lines.append(
            f"  풀버스트 (타인) : {self.fb_other.damage:>15,}  "
            f"({self.fb_other.pct(fb_total):5.1f}%)  [{self.fb_other.hits}회]"
        )
        lines.append(
            f"  비풀버스트      : {self.non_fb.damage:>15,}  "
            f"({self.non_fb.pct(fb_total):5.1f}%)  [{self.non_fb.hits}회]"
        )

        return "\n".join(lines)


def analyze_damage(result: SimResult, char_name: str) -> DamageBreakdown:
    """특정 캐릭터의 대미지를 피해 유형별·버스트 구간별로 분석한다.

    버스트 구간 분류는 verbose=True로 생성된 result(result.log 존재) 에서만 동작한다.
    log가 없으면 fb_self / fb_other / non_fb 통계는 모두 0으로 남는다.
    """
    bd = DamageBreakdown(char_name=char_name)

    # burst_log를 순회해 각 풀버스트 구간의 (start, end, is_self) 목록 구성.
    # "stage:N 사용" / "reenter:N 사용" 이벤트의 caster 집합으로
    # 해당 사이클에 본인이 버스트를 사용했는지 판별한다.
    fb_intervals: list[tuple[float, float, bool]] = []

    if result.log is not None:
        pending_casters: set[str] = set()
        fb_start: float | None = None

        for entry in result.log.burst_log:
            if entry.event.endswith("사용"):
                if entry.caster:
                    pending_casters.add(entry.caster)
            elif entry.event == "full_burst 시작":
                fb_start = entry.t
            elif entry.event == "full_burst 종료":
                if fb_start is not None:
                    fb_intervals.append((fb_start, entry.t, char_name in pending_casters))
                fb_start = None
                pending_casters = set()

        # 전투 종료 전 마지막 풀버스트가 닫히지 않은 경우
        if fb_start is not None:
            fb_intervals.append((fb_start, result.duration, char_name in pending_casters))

    def _burst_category(t: float) -> str:
        for start, end, is_self in fb_intervals:
            if start <= t < end:
                return "fb_self" if is_self else "fb_other"
        return "non_fb"

    for ev in result.hits:
        if ev.caster != char_name:
            continue

        burst_stat: CategoryStat = getattr(bd, _burst_category(ev.t))
        burst_stat.damage += ev.damage
        burst_stat.hits += 1

        if _is_normal(ev):
            bd.normal_atk.damage += ev.damage
            bd.normal_atk.hits += 1
        else:
            bd.skill.damage += ev.damage
            bd.skill.hits += 1
            if ev.skill_name not in bd._skill_detail:
                bd._skill_detail[ev.skill_name] = CategoryStat()
            bd._skill_detail[ev.skill_name].damage += ev.damage
            bd._skill_detail[ev.skill_name].hits += 1

    return bd



# ── 최공 대상 버프 진단 ────────────────────────────────────────────────────
# 「자신을 제외한 최종 공격력이 가장 높은 아군 N기에게」 계열 버프는 **대상이 갈리면
# 딜이 통째로 달라진다.** 미란다 애장품이 대표다:
#
#   미란다 버스트(파워 업!)로 공격력이 먼저 뿌려지고 → 그것까지 포함한 최종 공격력으로
#   풀버스트 시작 시점에 웨이크업!의 «1발 크리티컬 확률»이 한 명에게 붙는다.
#   그 한 명이 **그 사이클에 3단계 버스트를 쓴 딜러**여야 값이 산다.
#
# 총딜 숫자만 보고는 누가 받았는지 알 수 없어서, 갈린 순간의 순위를 그대로 들고 와
# 「누가 받았나 · 3버가 받았나 · 못 받은 사람은 공격력 증가가 몇 %p 더 필요한가」를 낸다.
#
# 필요 공증은 소지 공격력이 분모다:
#     Δ(%p) = (커트라인 최종공격력 − 내 최종공격력) ÷ 내 소지 공격력 × 100
# 오버로드 공격력 증가가 소지 공격력에 곱으로 붙기 때문이다 (`_effective_atk`와 같은 식).

_PICK_LEAD = 1.5   # 풀버스트 시작 직전 몇 초까지를 «그 사이클»로 볼 것인가 (버스트 3연타 폭)


def analyze_top_atk(result: "SimResult") -> list[dict]:
    """최공 대상 버프가 갈린 순간들을 사이클·3버와 엮어 돌려준다.

    `verbose`와 무관하게 동작한다 — 필요한 것(`top_atk_picks`·`burst_casts`·
    `full_bursts`)은 시뮬이 늘 채운다.
    """
    fbs = list(result.full_bursts)
    out = []
    for p in result.top_atk_picks:
        t = p["t"]
        cycle = None
        dealer = None
        for i, (s, e) in enumerate(fbs):
            if s - _PICK_LEAD <= t <= e:
                cycle = i + 1
                # 그 풀버스트를 연 3단계 버스트 시전자
                cands = [nm for (bt, stage, nm) in result.burst_casts
                         if stage == "3" and s - _PICK_LEAD <= bt <= s + 1e-6]
                dealer = cands[-1] if cands else None
                break

        chosen = list(p["chosen"])
        rank = p["ranking"]
        kind = p.get("kind", "top")
        by_name = {r["name"]: r for r in rank}
        # 커트라인 = 뽑힌 사람의 경계값. 최공은 그걸 «넘어야», 최저공은 «내려가야» 뺏는다.
        vals = [by_name[c]["atk"] for c in chosen if c in by_name]
        cut = (max(vals) if kind == "low" else min(vals)) if vals else 0.0
        rows = []
        for r in rank:
            got = r["name"] in chosen
            need = None
            if not got and r["base"] > 0:
                # 최저공은 부호가 반대다 — 내려야 하는 양을 양수로 담는다
                need = ((r["atk"] - cut) if kind == "low" else (cut - r["atk"])) \
                    / r["base"] * 100.0
            # 최종 공격력이 같아도 한 명만 뽑힌다(순서로 갈린다). 그때 «+0.0%p 필요»라고
            # 적으면 「이미 충분한데 왜 못 받지」가 된다 — 동점이라고 말해 준다.
            rows.append({**r, "got": got, "need_atk_pct": need,
                         "tie": (not got) and need is not None and need <= 0.0})

        out.append({
            "t": round(t, 3),
            "kind": kind,
            "cycle": cycle,
            "caster": p["caster"],
            "buff": p["buff"],
            "stat": p["stat"],
            "spec": p["spec"],
            "slots": int(str(p["spec"]).rsplit(":", 1)[-1] or 1),
            "chosen": chosen,
            "dealer": dealer,
            # 3버가 못 받은 사이클만 «놓쳤다»로 본다. 3버를 못 찾으면 판정하지 않는다.
            "dealer_got": (dealer in chosen) if dealer else None,
            "ranking": rows,
        })
    return out


def summarize_top_atk(result: "SimResult") -> list[dict]:
    """`analyze_top_atk()`를 **경우별로 묶어** 작게 만든다.

    사이클마다 같은 일이 반복되는 편성이 대부분이라(미란다가 매번 같은 둘에게 걸고,
    3버만 에이다↔헬름으로 교대), 18건을 그대로 보내면 화면도 저장 공간도 낭비다.
    「같은 버프가 같은 사람에게, 같은 3버 아래서」를 한 줄로 접고 사이클 번호만 모은다.

    웹이 이걸 계산 결과에 실어 보낸다 — **별도 시뮬이 필요 없다.**
    """
    rows = analyze_top_atk(result)
    cases: dict = {}
    for r in rows:
        key = (r["buff"], r["stat"], r["spec"], tuple(r["chosen"]),
               r["dealer"], r["dealer_got"])
        c = cases.get(key)
        if c is None:
            c = {
                "kind": r.get("kind", "top"),
                "caster": r["caster"], "buff": r["buff"], "stat": r["stat"],
                "slots": r["slots"], "chosen": r["chosen"],
                "dealer": r["dealer"], "dealer_got": r["dealer_got"],
                "cycles": [],
                "ranking": [{
                    "name": e["name"],
                    "atk": round(e["atk"]),
                    "base": round(e["base"]),
                    "got": e["got"],
                    # 반올림해도 «몇 %p 모자라나»를 읽는 데는 충분하다
                    "need": (None if e["need_atk_pct"] is None
                             else round(e["need_atk_pct"], 1)),
                    "tie": e["tie"],
                } for e in r["ranking"]],
            }
            cases[key] = c
        if r["cycle"]:
            c["cycles"].append(r["cycle"])
    return list(cases.values())


# ── 결과 화면 타임라인 (확인용) ───────────────────────────────────────────
# 웹 결과 탭 맨 아래 «버스트 타임라인»이 쓴다. 저장은 안 된다 — 계산할 때마다
# 다시 실려 오는 확인용 부가 정보다(collectDecks()가 기록에는 안 담는다).

_TL_TARGET_BUCKETS = 60   # 전투 시간과 무관하게 막대 수를 이 근처로 맞춘다


def dps_timeline(result: "SimResult") -> dict:
    """구간별(«버킷») 캐릭터별 딜 합. 결과 화면의 딜 막대(시간 축)가 이 값을 쓴다.

    버킷 폭은 `duration / _TL_TARGET_BUCKETS`(최소 1초) — 짧은 전투도 긴 전투도
    막대 수가 비슷해 폭이 극단으로 가늘거나 굵어지지 않는다.
    """
    duration = result.duration or 1.0
    bucket_sec = max(1.0, duration / _TL_TARGET_BUCKETS)
    n = max(1, math.ceil(duration / bucket_sec))
    buckets: list[dict] = [{} for _ in range(n)]
    for ev in result.hits:
        idx = min(n - 1, int(ev.t // bucket_sec))
        b = buckets[idx]
        b[ev.caster] = b.get(ev.caster, 0) + ev.damage
    return {"bucket_sec": bucket_sec, "buckets": buckets}


def burst_cycles(result: "SimResult") -> list[dict]:
    """풀버스트 사이클 하나하나 — 1·2·3버가 **몇 분의 1초 간격**으로 몰려 있어서
    그대로 찍으면 시간 축에서 겹쳐 뭉개진다(실측). 그래서 풀버스트 구간(`full_bursts`)
    하나당 그걸 준비한 발동을 단계별로 묶어 하나로 낸다.

    **고정 리드타임으로 묶지 않는다.** 처음엔 `analyze_top_atk`의 `_PICK_LEAD`(1.5초)를
    빌려 썼는데, 실측(미란다 덱)에서 1버가 그 풀버스트보다 **20초 가까이 먼저** 켜지는
    편성이 나와 전부 놓쳤다 — 1버 쿨이 남는 동안 미리 켜 두고 2·3버가 뒤늦게 따라붙는
    조합이 실전에 있다는 뜻이다. 대신 **각 발동이 속하는 풀버스트를 "그 발동 이후
    끝나는 가장 이른 구간"** 으로 정한다: 리드타임을 얼마나 잡든 놓치는 조합이 생기지만,
    "다음 풀버스트가 끝나기 전"은 항상 참이기 때문이다.

    구간에 못 묶인 발동(어떤 풀버스트보다도 뒤에 남은 것 — 전투 막판에 못 이어진
    버스트)은 `strays`로 따로 낸다. 사라뜨리지 않는다: 못 이어진 버스트도 «무엇을
    놓쳤는지»의 증거다.
    """
    cycles = [{"start": s, "end": e, "casts": {}} for s, e in sorted(result.full_bursts)]
    strays = []
    for t, stage, name in result.burst_casts:
        hit = next((c for c in cycles if t <= c["end"]), None)
        if hit is None:
            strays.append({"t": t, "stage": stage, "name": name})
        else:
            hit["casts"][stage] = {"t": t, "name": name}
    return {"cycles": cycles, "strays": strays}


def format_top_atk(result: "SimResult", indent: str = "") -> str:
    """`analyze_top_atk()`를 사람이 읽는 블록으로. CLI(`--view topatk`)가 쓴다."""
    rows = analyze_top_atk(result)
    if not rows:
        return f"{indent}최종 공격력 최상위 대상 버프가 없는 편성입니다."
    lines = [f"{indent}=== 최공 대상 버프 ({len(rows)}건) ==="]
    for r in rows:
        mark = "" if r["dealer_got"] is None else ("  ✔" if r["dealer_got"] else "  ✘ 3버가 못 받음")
        cyc = f"사이클 {r['cycle']}" if r["cycle"] else "사이클 밖"
        lines.append(f"{indent}[{cyc}] t={r['t']:<7} {r['caster']} 「{r['buff']}」"
                     f" {r['stat']} · 상위 {r['slots']}기{mark}")
        lines.append(f"{indent}   받은 사람: {' · '.join(r['chosen'])}"
                     + (f"   (이 사이클 3버: {r['dealer']})" if r["dealer"] else ""))
        for e in r["ranking"]:
            tag = "◀ 받음" if e["got"] else ""
            need = ("" if e["need_atk_pct"] is None
                    else ("  동점 — 순서로 밀림 (아주 조금만 더)" if e["tie"]
                          else f"  공증 +{e['need_atk_pct']:.1f}%p 필요"))
            lines.append(f"{indent}     {e['name']:<18} 최종 {e['atk']:>11,.0f}"
                         f"  소지 {e['base']:>9,.0f} {tag}{need}")
    return "\n".join(lines)

def analyze_team(result: SimResult) -> list[DamageBreakdown]:
    """스쿼드 전원을 분석해 DamageBreakdown 목록으로 반환한다. 딜량 내림차순 정렬."""
    breakdowns = [analyze_damage(result, name) for name in result.char_total]
    breakdowns.sort(key=lambda b: -b.total)
    return breakdowns


def print_team_analysis(result: SimResult, chars: list[str] | None = None) -> None:
    """스쿼드 전체 분석 결과를 콘솔에 출력한다.

    result.summary() 출력 후 각 캐릭터의 DamageBreakdown.summary()를 출력한다.
    chars 지정 시 해당 캐릭터만 출력.
    """
    print(result.summary(chars))
    print()
    for bd in analyze_team(result):
        if chars and bd.char_name not in chars:
            continue
        print(bd.summary())
        print()
