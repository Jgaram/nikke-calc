"""재장전 지연·클립 운용 회귀. ``python -m unittest calculator.test_clip_reload``."""

from __future__ import annotations

import unittest

from calculator.timeline import CharState, _NIKKE


class _BuffStub:
    """CharState 재장전 단위 테스트에 필요한 BuffManager 최소 표면."""

    def __init__(self, buffs: dict | None = None, weapon_change: dict | None = None):
        self._buffs = buffs or {}
        self._weapon_change = weapon_change
        self._active: list = []
        self.state = {"charging": {}, "rng_acc": {}}
        self.events: list[tuple[str, float, str]] = []

    def get_buffs(self, caster: str, target: str, t: float) -> dict:
        return dict(self._buffs)

    def get_weapon_change(self, caster: str) -> dict | None:
        return self._weapon_change

    def is_stunned(self, caster: str) -> bool:
        return False

    def element_override_match(self, caster: str, enemy_code: str) -> bool:
        return False

    def notify_team_hit(self, event: str, t: float, caster: str) -> None:
        pass

    def consume_bullet_buffs(self, caster: str, t: float) -> None:
        pass

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

    def test_reload_delays_use_cdn_values_before_weapon_group_fallback(self) -> None:
        for name, expected in {
            "페퍼": (0.2, 0.2),
            "라피": (0.2, 0.2),
            "네로": (0.2, 0.13),
            "토브": (0.2, 0.33),
            "센티": (0.2, 0.2),
            "아니스 : 스타": (0.2, 0.2),
        }.items():
            with self.subTest(name=name):
                state = _state(name=name)
                self.assertEqual(
                    (state.reload_start_delay, state.post_reload_delay), expected)

    def test_normal_reload_applies_post_delay_for_each_weapon_type(self) -> None:
        representatives = {
            "AR": "라피",
            "MG": "크라운",
            "SG": "나가",
            "SMG": "네로",
            "SR": "앨리스",
            "RL": "센티",
        }

        for weapon_type, name in representatives.items():
            with self.subTest(weapon_type=weapon_type, name=name):
                bm = _BuffStub()
                state = _state(name=name)
                state.ammo = 0
                state._start_reload(1.0, bm)

                while state.reloading_until > 0:
                    completed_at = state.reloading_until
                    state._finish_reload(completed_at, bm)

                self.assertEqual(state.weapon_type, weapon_type)
                self.assertAlmostEqual(
                    state._post_reload_end_t,
                    completed_at + state.post_reload_delay,
                )

    def test_charge_last_shot_starts_reload_after_cdn_delay(self) -> None:
        for expected_type, name in (
                ("SR", "델타"),
                ("RL", "벨로타"),
                ("RL", "아니스 : 스타")):
            with self.subTest(weapon_type=expected_type, name=name):
                bm = _BuffStub()
                state = _state(name=name)
                last_shot = 5.0
                state.ammo = 1
                state._charge_phase = "charging"

                state._charge_fire(
                    last_shot, bm, {}, {"rng_mode": "expected"}, True)

                self.assertEqual(state.weapon_type, expected_type)
                self.assertAlmostEqual(
                    state.reloading_until,
                    last_shot
                    + state.reload_start_delay
                    + state._reload_duration(bm, last_shot),
                )
                self.assertAlmostEqual(
                    state._post_delay_end_t,
                    last_shot + state.post_fire_delay,
                )

    def test_charge_reload_preserves_post_fire_lower_bound(self) -> None:
        bm = _BuffStub()
        state = _state(name="센티")
        state.ammo = 1
        state._charge_phase = "ready"
        state._post_delay_end_t = 10.0

        self.assertEqual(state._tick_charge(9.9, bm, {}, {}), [])
        self.assertEqual(state._charge_phase, "ready")

        self.assertEqual(state._tick_charge(10.0, bm, {}, {}), [])
        self.assertEqual(state._charge_phase, "charging")

    def test_sg_empty_magazine_starts_reload_point_two_after_last_shot(self) -> None:
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
            last_shot + 1.9,
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

    def test_empty_clip_sg_first_gain_takes_two_reload_durations(self) -> None:
        last_shot = 25.133

        for reload_speed_pct in (0.0, 29.69):
            with self.subTest(reload_speed_pct=reload_speed_pct):
                state = _state(name="페퍼", clip_policy="one_clip")
                bm = _BuffStub({"reload_speed_pct": reload_speed_pct})
                state.ammo = 1
                state.next_fire_time = last_shot
                state.reloading_until = -1.0

                def fake_fire(t, _bm, _enemy, _cfg):
                    state.ammo = 0
                    return []

                state._fire = fake_fire
                state._current_fire_rate = lambda _bm, _t: 1.5
                state._tick_auto(last_shot, bm, {}, {})

                one = state.weapon["reload_time"] * (1 - reload_speed_pct / 100)
                expected_gain = last_shot + state.reload_start_delay + 2 * one
                self.assertAlmostEqual(state.reloading_until, expected_gain)

                state._finish_reload(expected_gain, bm)
                self.assertAlmostEqual(
                    state._post_reload_end_t,
                    expected_gain + state.post_reload_delay,
                )

    def test_all_non_charge_weapons_with_cdn_delay_take_last_shot_path(self) -> None:
        checked = 0
        for name, weapon in _NIKKE.items():
            if (weapon["weapon_type"] in ("SR", "RL")
                    or weapon.get("reload_start_delay") is None):
                continue
            state = _state(name=name)
            with self.subTest(name=name):
                bm = _BuffStub()
                state.ammo = 1
                state.next_fire_time = 0.0

                def fake_fire(t, _bm, _enemy, _cfg, current=state):
                    current.ammo = 0
                    return []

                state._fire = fake_fire
                state._tick_auto(0.0, bm, {}, {})
                multiplier = 2 if state.weapon_type == "SG" and state.is_clip else 1
                self.assertAlmostEqual(
                    state.reloading_until,
                    state.reload_start_delay
                    + multiplier * state.weapon["reload_time"],
                )
                checked += 1

        self.assertGreater(checked, 0)

    def test_clip_rl_does_not_inherit_sg_empty_entry_extra_reload(self) -> None:
        bm = _BuffStub()
        state = _state(name="센티", clip_policy="one_clip")
        one = state._reload_duration(bm, 0.0)

        state._start_reload(0.0, bm, empty_clip_entry=True)

        self.assertAlmostEqual(state.reloading_until, one)

    def test_empty_clip_sg_full_reload_only_doubles_first_clip(self) -> None:
        bm = _BuffStub()
        state = _state(name="페퍼")
        state.ammo = 0
        one = state._reload_duration(bm, 0.0)

        state._start_reload(0.0, bm, empty_clip_entry=True)
        first_end = state.reloading_until
        self.assertAlmostEqual(first_end, 2 * one)

        state._finish_reload(first_end, bm)
        self.assertAlmostEqual(state.reloading_until, first_end + one)
        _finish_all(state, bm)

        self.assertAlmostEqual(state._post_reload_end_t, 4 * one + 0.2)
        self.assertEqual([event[0] for event in bm.events], ["event:full_reload"])

    def test_manual_clip_reload_does_not_get_empty_entry_extra_time(self) -> None:
        bm = _BuffStub()
        state = _state(name="페퍼", clip_policy="one_clip")
        state.ammo = 0

        state._start_reload(0.0, bm)

        self.assertAlmostEqual(state.reloading_until, state._reload_duration(bm, 0.0))

    def test_invalid_policy_and_non_clip_target_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "full.*one_clip"):
            _state(clip_policy="two_clips")
        with self.assertRaisesRegex(ValueError, "클립 무기가 아니므로"):
            _state(name="라피 : 레드 후드", clip_policy="one_clip")


if __name__ == "__main__":
    unittest.main()
