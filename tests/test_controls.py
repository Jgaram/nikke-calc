"""구조화 컨트롤·큐브 입력 검사 (docs/SIM-JSON.md §컨트롤·큐브). 시뮬은 돌리지 않는다.

    python -m unittest discover -s tests -v

`prepare()`까지만 돌려 **조립된 캐릭터 dict**를 본다 — 같은 컨트롤을 문자열 옵션과 dict로 주면
같은 dict가 되는가, 레이어 위에 어떻게 얹히는가, 잘못 주면 끊기는가. 딜이 같은지는
`test_sim_json.py`가 시뮬로 본다.
"""

from __future__ import annotations

import json
import unittest

from runner import sim
from runner import spec

SQUAD = "앨리스,프리카,크라운"
AP = sim.build_parser()


def squad_of(*argv: str, **line) -> dict[str, dict]:
    """CLI 인자(+ 배치 줄 dict) → 조립된 스쿼드 {이름: 캐릭터 dict}."""
    args = AP.parse_args([SQUAD, *argv] if not line else list(argv))
    if line:
        args = sim._line_namespace(AP, args, {"squad": SQUAD, **line})
    return {c["name"]: c for c in sim.prepare(args).squad}


class StructuredControls(unittest.TestCase):
    def test_dict_equals_string_options(self):
        by_str = squad_of("--tap", "프리카:4.0:0.03:0:burst_charge", "--reload-ctrl", "프리카:finish_by_fb_end")
        ctrl = {"프리카": {"tap_fire": {"rate": 4.0, "release": 0.03, "window": "burst_charge"},
                         "reload": {"policy": "finish_by_fb_end"}}}
        self.assertEqual(squad_of("--controls", json.dumps(ctrl, ensure_ascii=False)), by_str)
        self.assertEqual(squad_of(controls=ctrl), by_str)        # 배치 dict

    def test_merges_onto_layer(self):
        # 앨리스의 레이어 톡톡이는 남고 장전컨만 더해진다
        got = squad_of(controls={"앨리스": {"reload": {"policy": "into_fb"}}})["앨리스"]["control"]
        self.assertEqual(got["reload"], {"policy": "into_fb"})
        self.assertEqual(got["tap_fire"], squad_of()["앨리스"]["control"]["tap_fire"])

    def test_click_replaces_layer_legacy_left_click(self):
        click = [{"window": "always", "mode": "tap", "rate": 4.0}]
        for c in (squad_of(controls={"앨리스": {"click": click}})["앨리스"],
                  squad_of("--click", "앨리스:always:tap:rate=4.0")["앨리스"]):
            self.assertEqual(c["control"], {"click": click})
        # 조건부 규칙(에이다 홀드 — 미란다 동석)이 얹은 종전 키도 같다
        ada = spec.build_squad(["에이다", "미란다"], {"에이다": {"control": {"click": click}}})[0]
        self.assertEqual(ada["control"], {"click": click})

    def test_clash_with_string_options(self):
        for argv, ctrl in [
            (["--tap", "프리카:4.0"], {"프리카": {"tap_fire": {"rate": 3.6}}}),
            (["--tap", "프리카:4.0"], {"프리카": {"click": [{"window": "always", "mode": "tap"}]}}),  # 같은 좌클릭
            (["--burst-delay", "프리카:1.0"], {"프리카": {"burst": {"delay": 2.0}}}),
        ]:
            with self.subTest(argv=argv), self.assertRaises(sim.UsageError):
                squad_of(*argv, "--controls", json.dumps(ctrl, ensure_ascii=False))
        # 다른 축이면 함께 줘도 된다
        c = squad_of("--tap", "프리카:4.0", controls={"프리카": {"reload": {"policy": "into_fb"}}})["프리카"]
        self.assertEqual(set(c["control"]), {"tap_fire", "reload"})

    def test_bad_input(self):
        with self.assertRaises(sim.UsageError):
            squad_of(controls={"헬름": {}})                     # 스쿼드에 없다
        with self.assertRaises(sim.UsageError):
            squad_of("--controls", "{not json")
        with self.assertRaises(ValueError):
            squad_of(controls={"프리카": {"tapp": {}}})          # 모르는 컨트롤 키


class Cube(unittest.TestCase):
    def test_forms(self):
        want = {"name": "렐릭 어설트 큐브", "level": 15}
        for got in (squad_of("--cube", "크라운:렐릭 어설트 큐브"),
                    squad_of("--cube", "크라운:렐릭 어설트 큐브:15"),
                    squad_of(cube={"크라운": "렐릭 어설트 큐브"}),
                    squad_of(cube={"크라운": {"name": "렐릭 어설트 큐브", "level": 15}}),
                    squad_of(cube="크라운:렐릭 어설트 큐브")):
            self.assertEqual(got["크라운"]["cube"], want)
        self.assertEqual(squad_of(cube={"크라운": {"name": "렐릭 베어 큐브", "level": 7}})["크라운"]["cube"]["level"], 7)

    def test_rejected(self):
        for cube in ("없는 큐브", "렐릭 힐링 큐브", "공통", {"name": "렐릭 베어 큐브", "level": 16}):
            with self.subTest(cube=cube), self.assertRaises(ValueError):
                squad_of(cube={"크라운": cube})
        with self.assertRaises(sim.UsageError):
            squad_of(cube={"헬름": "렐릭 베어 큐브"})
        # 인라인 육성 프로필의 cube 키로 들어와도 같은 검사를 지난다
        with self.assertRaises(ValueError):
            squad_of(profile={"base": "default", "chars": {"크라운": {"cube": {"name": "없는 큐브"}}}})


if __name__ == "__main__":
    unittest.main()
