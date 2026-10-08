"""니케별 운용(`chars`) 입력 검사 — 컨트롤·큐브 (docs/SIM-JSON.md §요청). 시뮬은 돌리지 않는다.

    python -m unittest discover -s tests -v

`prepare()`까지만 돌려 **조립된 캐릭터 dict**를 본다 — 같은 컨트롤을 CLI 문자열 옵션과 요청 `chars`로 주면
같은 dict가 되는가, 레이어 위에 어떻게 얹히는가, 잘못 주면 끊기는가. 딜이 같은지는
`test_sim_json.py`가 시뮬로 본다.
"""

from __future__ import annotations

import unittest

from runner import sim
from runner import spec
from runner.request import RequestError

SQUAD = ["앨리스", "프리카", "크라운"]
AP = sim.build_parser()


def by_cli(*argv: str) -> dict[str, dict]:
    """CLI 인자 → 조립된 스쿼드 {이름: 캐릭터 dict}."""
    return {c["name"]: c for c in sim.prepare(sim.base_request(AP.parse_args([",".join(SQUAD), *argv]))).squad}


def by_req(**req) -> dict[str, dict]:
    """요청 칸 → 조립된 스쿼드 {이름: 캐릭터 dict}."""
    return {c["name"]: c for c in sim.prepare({"squad": SQUAD, **req}).squad}


def chars(**per) -> dict:
    return {"chars": per}


class Controls(unittest.TestCase):
    def test_request_equals_string_options(self):
        cli = by_cli("--tap", "프리카:4.0:0.03:0:burst_charge", "--reload-ctrl", "프리카:finish_by_fb_end")
        ctrl = {"tap_fire": {"rate": 4.0, "release": 0.03, "window": "burst_charge"},
                "reload": {"policy": "finish_by_fb_end"}}
        self.assertEqual(by_req(**chars(프리카={"control": ctrl})), cli)

    def test_merges_onto_layer(self):
        # 앨리스의 레이어 톡톡이는 남고 장전컨만 더해진다
        got = by_req(**chars(앨리스={"control": {"reload": {"policy": "into_fb"}}}))["앨리스"]["control"]
        self.assertEqual(got["reload"], {"policy": "into_fb"})
        self.assertEqual(got["tap_fire"], by_req()["앨리스"]["control"]["tap_fire"])

    def test_false_turns_axis_off(self):
        # false = 레이어 값까지 끈다. null은 RFC 7386대로 「적지 않음」 = 레이어 그대로
        off = by_req(**chars(앨리스={"control": {"tap_fire": False}}))["앨리스"]
        self.assertIsNone(off["control"]["tap_fire"])
        self.assertEqual(by_req(**chars(앨리스={"control": {"tap_fire": None}})), by_req())

    def test_click_replaces_layer_legacy_left_click(self):
        click = [{"window": "always", "mode": "tap", "rate": 4.0}]
        for c in (by_req(**chars(앨리스={"control": {"click": click}}))["앨리스"],
                  by_cli("--click", "앨리스:always:tap:rate=4.0")["앨리스"]):
            self.assertEqual(c["control"], {"click": click})
        # 조건부 규칙(에이다 홀드 — 미란다 동석)이 얹은 종전 키도 같다
        ada = spec.build_squad(["에이다", "미란다"], {"에이다": {"control": {"click": click}}})[0]
        self.assertEqual(ada["control"], {"click": click})

    def test_explicit_beats_tactic(self):
        # 택틱은 다발 기본값이라 니케별로 명시한 값이 그 위에 병합돼 이긴다(dict는 재귀 병합 — 택틱의
        # 등급 high는 남는다). 다른 축은 택틱 값 그대로다
        tac = {"operation": {"tactics": [{"name": "버충", "target": "프리카"}]}}
        base = by_req(**tac)["프리카"]["control"]
        got = by_req(**tac, **chars(프리카={"control": {"reload": {"policy": "into_fb"}}}))["프리카"]["control"]
        self.assertEqual(got["reload"], {**base["reload"], "policy": "into_fb"})
        self.assertEqual(got["tap_fire"], base["tap_fire"])

    def test_burst_pattern(self):
        who = "마스트 : 로망틱 메이드"
        squad = [who, "크라운"]
        by_name = sim.prepare({"squad": squad, "chars": {who: {"control": {"burst": {"pattern": "1,3,5,9,11,14"}}}}})
        by_list = sim.prepare({"squad": squad, "chars": {who: {"control": {"burst": {"pattern": [1, 3, 5, 9, 11, 14]}}}}})
        self.assertEqual(by_name.config["burst_pattern"], by_list.config["burst_pattern"])
        with self.assertRaises(RequestError) as e:
            sim.prepare({"squad": squad, "chars": {who: {"control": {"burst": {"pattern": "없는 패턴"}}}}})
        self.assertEqual(e.exception.path, f"/chars/{who}/control/burst/pattern")

    def test_bad_input(self):
        for req, path in [
            (chars(헬름={}), "/chars/헬름"),                                                # 스쿼드에 없다
            (chars(프리카={"control": {"tapp": {}}}), "/chars/프리카/control/tapp"),         # 모르는 축
            (chars(프리카={"control": {"reload": {"policy": "nope"}}}), "/chars/프리카/control"),  # 어휘
            (chars(프리카={"control": {"reload": {"margin": "x"}}}), "/chars/프리카/control/reload/margin"),
            (chars(프리카={"control": {"click": [{"window": "always", "mode": "tap",
                                                "gate": {"burst_stage": "3", "burst_user": "헬름"}}]}}),
             "/chars/프리카/control/click/0/gate/burst_user"),
        ]:
            with self.subTest(req=req), self.assertRaises(RequestError) as e:
                by_req(**req)
            self.assertEqual(e.exception.path, path)


class Cube(unittest.TestCase):
    def test_forms(self):
        want = {"name": "렐릭 어설트 큐브", "level": 15}
        for got in (by_cli("--cube", "크라운:렐릭 어설트 큐브"),
                    by_cli("--cube", "크라운:렐릭 어설트 큐브:15"),
                    by_req(**chars(크라운={"cube": "렐릭 어설트 큐브"})),
                    by_req(**chars(크라운={"cube": {"name": "렐릭 어설트 큐브", "level": 15}}))):
            self.assertEqual(got["크라운"]["cube"], want)
        self.assertEqual(by_req(**chars(크라운={"cube": {"name": "렐릭 베어 큐브", "level": 7}}))["크라운"]["cube"]["level"], 7)

    def test_rejected(self):
        for cube in ("없는 큐브", "렐릭 힐링 큐브", "공통", {"name": "렐릭 베어 큐브", "level": 16},
                     {"name": "렐릭 베어 큐브", "level": 15.0}):
            with self.subTest(cube=cube), self.assertRaises(RequestError) as e:
                by_req(**chars(크라운={"cube": cube}))
            self.assertTrue(e.exception.path.startswith("/chars/크라운/cube"))
        # 인라인 육성 프로필의 cube 키로 들어와도 같은 검사를 지나고, 같은 두 표기를 받는다
        with self.assertRaises(ValueError):
            by_req(profile={"base": "default", "chars": {"크라운": {"cube": {"name": "없는 큐브"}}}})
        got = by_req(profile={"base": "default", "chars": {"크라운": {"cube": "렐릭 어설트 큐브"}}})
        self.assertEqual(got["크라운"]["cube"], {"name": "렐릭 어설트 큐브", "level": 15})


if __name__ == "__main__":
    unittest.main()
