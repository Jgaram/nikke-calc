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
    is_crit: bool     # 크리티컬 여부
    hit_tag: str      # 피격 종류 식별자:
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
    """풀버스트 진입 시각에 찍은 팀 전체 버프 상태 스냅샷.

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

    team_total: int = 0
    # 팀 전체 누적 피해량 (char_total 합산)

    duration: float = 0.0
    # 시뮬레이션 지속 시간 (초)

    log: SimLog | None = None
    # verbose=True 시 채워지는 전투 이벤트 로그. False이면 None.

    def summary(self, chars: list[str] | None = None) -> str:
        """팀 총 딜과 캐릭터별 딜량·비율을 출력한다.

        chars 지정 시 해당 캐릭터만 표시 (팀 총 딜 기준 비율은 유지).
        딜량 내림차순 정렬.
        """
        lines = [
            f"시뮬레이션 {self.duration:.1f}초  "
            f"팀 총 딜: {self.team_total:,}"
        ]
        for name, dmg in sorted(self.char_total.items(), key=lambda x: -x[1]):
            if chars and name not in chars:
                continue
            pct = dmg / self.team_total * 100 if self.team_total else 0
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
            caster_str = e.caster if e.caster else "팀"
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


def analyze_team(result: SimResult) -> list[DamageBreakdown]:
    """팀 전원을 분석해 DamageBreakdown 목록으로 반환한다. 딜량 내림차순 정렬."""
    breakdowns = [analyze_damage(result, name) for name in result.char_total]
    breakdowns.sort(key=lambda b: -b.total)
    return breakdowns


def print_team_analysis(result: SimResult, chars: list[str] | None = None) -> None:
    """팀 전체 분석 결과를 콘솔에 출력한다.

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
