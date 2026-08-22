"""클립 재장전 운용 회귀. ``python -m unittest calculator.test_clip_reload``."""

from __future__ import annotations

import unittest

from calculator.timeline import CharState


class _BuffStub:
    """CharState 재장전 단위 테스트에 필요한 BuffManager 최소 표면."""

    def __init__(self, buffs: dict | None = None, weapon_change: dict | None = None):
        self._buffs = buffs or {}
        self._weapon_change = weapon_change
        self._active: list = []
        self.state = {"charging": {}}
        self.events: list[tuple[str, float, str]] = []

    def get_buffs(self, caster: str, target: str, t: float) -> dict:
        return dict(self._buffs)

    def get_weapon_change(self, caster: str) -> dict | None:
        return self._weapon_change

    def is_stunned(self, caster: str) -> bool:
        return False

    def notify(self, event: str, t: float, caster: str) -> None:
        self.events.append((event, t, caster))

    def _invalidate_buffs_cache(self) -> None:
        pass


def _state(name: str = "드레이크", clip_policy: str | None = None) -> CharState:
    reload_control = {} if clip_policy is None else {"clip_policy": clip_policy}
    return CharState(
        {"name": name, "control": {"reload": reload_control}},
        base_atk=1.0,
        enemy_code="",
    )


def _finish_all(state: CharState, bm: _BuffStub) -> None:
    """진행 중인 연속 클립을 마지막까지 완료한다."""
    while state.reloading_until > 0:
        state._finish_reload(state.reloading_until, bm)


class ClipReloadPolicyTests(unittest.TestCase):
    def test_default_full_chains_clips_and_notifies_once_at_max(self) -> None:
        bm = _BuffStub()
        state = _state()
        full = state._full_ammo(bm, 0.0)

        state.ammo = 0
        state._start_reload(0.0, bm)
        first_end = state.reloading_until
        state._finish_reload(first_end, bm)

        self.assertEqual(state.ammo, state._clip_gain(full))
        self.assertGreater(state.reloading_until, first_end)
        self.assertEqual(state._post_reload_end_t, -1.0)
        self.assertEqual(bm.events, [])

        _finish_all(state, bm)
        self.assertEqual(state.ammo, full)
        self.assertEqual(state.reloading_until, -1.0)
        self.assertGreater(state._post_reload_end_t, 0.0)
        self.assertEqual([e[0] for e in bm.events], ["event:full_reload"])

    def test_one_clip_stops_partial_with_delay_but_without_full_reload_event(self) -> None:
        bm = _BuffStub()
        state = _state(clip_policy="one_clip")
        full = state._full_ammo(bm, 0.0)
        state.post_reload_delay = 0.5
        state._post_reload_end_t = 99.0

        state.ammo = 0
        state._start_reload(0.0, bm)
        end = state.reloading_until
        state._finish_reload(end, bm)

        self.assertEqual(state.ammo, state._clip_gain(full))
        self.assertEqual(state.reloading_until, -1.0)
        self.assertEqual(state._post_reload_end_t, end + 0.5)
        self.assertEqual(bm.events, [])

    def test_one_clip_still_notifies_when_that_clip_reaches_max(self) -> None:
        bm = _BuffStub()
        state = _state(clip_policy="one_clip")
        full = state._full_ammo(bm, 0.0)
        state.post_reload_delay = 0.5
        state.ammo = full - state._clip_gain(full)

        state._start_reload(0.0, bm)
        end = state.reloading_until
        _finish_all(state, bm)

        self.assertEqual(state.ammo, full)
        self.assertEqual([e[0] for e in bm.events], ["event:full_reload"])
        self.assertEqual(state._post_reload_end_t, end + 0.5)

    def test_one_clip_uses_current_buffed_max_ammo_for_clip_size(self) -> None:
        state = _state(clip_policy="one_clip")
        bm = _BuffStub({"max_ammo_flat": 31 - state.weapon["max_ammo"]})

        state.ammo = 0
        state._start_reload(0.0, bm)
        _finish_all(state, bm)

        self.assertEqual(state._full_ammo(bm, 0.0), 31)
        self.assertEqual(state.ammo, 10)  # round_half_up(31 / 3)
        self.assertEqual(bm.events, [])

    def test_explicit_cover_keeps_loading_while_firing_is_impossible(self) -> None:
        bm = _BuffStub()
        state = _state(clip_policy="one_clip")
        full = state._full_ammo(bm, 0.0)
        state._cover_until = 10.0
        state.ammo = 0

        state._start_reload(0.0, bm)
        _finish_all(state, bm)

        self.assertEqual(state.ammo, full)
        self.assertEqual([e[0] for e in bm.events], ["event:full_reload"])

    def test_weapon_change_reload_ignores_original_weapon_clip_policy(self) -> None:
        bm = _BuffStub(weapon_change={"max_ammo": 5})
        state = _state(clip_policy="one_clip")
        state.ammo = 0

        state._start_reload(0.0, bm)
        _finish_all(state, bm)

        self.assertEqual(state.ammo, 5)
        self.assertEqual([e[0] for e in bm.events], ["event:full_reload"])

    def test_into_fb_uses_time_until_one_clip_is_ready(self) -> None:
        bm = _BuffStub()
        full_state = _state()
        one_clip_state = _state(clip_policy="one_clip")
        full_state.ammo = one_clip_state.ammo = 0

        one = one_clip_state._reload_duration(bm, 0.0)
        self.assertEqual(
            one_clip_state._reload_until_fire_duration(bm, 0.0),
            one + one_clip_state.post_reload_delay,
        )
        self.assertGreater(full_state._reload_until_fire_duration(bm, 0.0), one)

    def test_sg_weapon_group_has_measured_point_two_reload_delay(self) -> None:
        # 클립 여부와 무관한 SG 공통값.
        for name in (
            "드레이크", "소다 : 트윙클링 바니", "페퍼", "누아르",
            "레오나", "나가", "도로시 : 세렌디피티", "아니스 : 스파클링 서머",
        ):
            with self.subTest(name=name):
                state = _state(name=name)
                self.assertEqual(state.reload_start_delay, 0.2)
                self.assertEqual(state.post_reload_delay, 0.2)

        # 비교 촬영한 RL에는 일반화하지 않는다.
        rl_state = _state(name="센티")
        self.assertIsNone(rl_state.reload_start_delay)
        self.assertEqual(rl_state.post_reload_delay, 0.0)

    def test_sg_empty_magazine_schedules_reload_point_two_after_last_shot(self) -> None:
        bm = _BuffStub()
        state = _state(name="나가")
        last_shot = 10.083
        state.ammo = 1
        state.next_fire_time = last_shot

        def fake_fire(t, _bm, _enemy, _cfg):
            state.ammo = 0
            return []

        state._fire = fake_fire
        state._current_fire_rate = lambda _bm, _t: 1.5
        state._tick_auto(last_shot, bm, {}, {})

        self.assertAlmostEqual(state.next_fire_time, last_shot + 1 / 1.5)
        self.assertAlmostEqual(
            state.reloading_until,
            last_shot + state.reload_start_delay + state.weapon["reload_time"],
        )
        state._finish_reload(state.reloading_until, bm)
        self.assertAlmostEqual(
            max(state.next_fire_time, state._post_reload_end_t),
            last_shot + 1.9,  # 나가 영상 10.083 → 11.983
        )

    def test_post_reload_gate_overlaps_sg_fire_interval(self) -> None:
        # 아니스 : 스파클링 서머 실측: 즉시 재장전도 기본 40프레임 간격 유지.
        bm = _BuffStub({"reload_speed_pct": 100.0})
        state = _state(name="아니스 : 스파클링 서머")
        last_shot = 17.433
        state.ammo = 0
        state.next_fire_time = last_shot + 1 / state.fire_rate

        state._start_reload(last_shot + state.reload_start_delay, bm)
        reload_end = state.reloading_until
        state._finish_reload(reload_end, bm)
        post_end = state._post_reload_end_t
        self.assertAlmostEqual(post_end, last_shot + 0.4)

        # 복귀 하한이 기존 발사 시계를 덮지 않는다.
        state._tick_auto = lambda _t, _bm, _enemy, _cfg: []
        state.tick(post_end, bm, {}, {})
        self.assertAlmostEqual(state.next_fire_time, last_shot + 1 / state.fire_rate)

    def test_invalid_policy_and_non_clip_target_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "full.*one_clip"):
            _state(clip_policy="two_clips")
        with self.assertRaisesRegex(ValueError, "클립 무기가 아니므로"):
            _state(name="라피 : 레드 후드", clip_policy="one_clip")


if __name__ == "__main__":
    unittest.main()
