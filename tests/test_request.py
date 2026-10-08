"""요청 형식 검사 — 스키마 · 의미 · 병합 (runner/request.py, docs/SIM-JSON.md §요청). 시뮬은 돌리지 않는다.

    python -m unittest discover -s tests -v

요청이 입력의 정본이다. 여기서는 그 계약 — 잘못된 칸은 **JSON Pointer 경로**와 함께 끊기는가, null·false의
뜻, RFC 7386 병합, CLI 표기가 같은 요청으로 펴지는가 — 를 본다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from runner import request as rq
from runner import sim
from runner.request import RequestError

ROOT = Path(__file__).resolve().parent.parent
AP = sim.build_parser()
OK = {"squad": ["크라운", "헬름"]}


def path_of(req) -> str:
    try:
        rq.validate(req)
    except RequestError as e:
        return e.path
    raise AssertionError(f"통과하면 안 된다: {req}")


class Schema(unittest.TestCase):
    def test_minimal_and_full(self):
        rq.validate(OK)
        rq.validate({
            "schema_version": 2, "id": {"anything": [1]}, "squad": ["프리카", "크라운"],
            "sim": {"duration": 60, "expected": True, "seed": 7, "first_burst": 3, "burst_gauge_mode": "accumulate",
                    "allow_unparsed": False},
            "boss": {"preset": "솔로 레이드 S40"},
            "enemy": {"def": 20000, "code": "철갑", "core_px": 10, "distance": 30, "has_parts": True,
                      "part_break_interval": 10, "optimal_range_weapons": ["SR"]},
            "operation": {"camera": "프리카", "camera_mode": "single", "control_mode": "solo", "no_burst": "크라운",
                          "auto": ["크라운"], "tactics": [{"name": "버충", "target": "프리카"}]},
            "profile": {"base": "default", "_meta": {"name": "가상"}, "_account": {"console": {"common_level": 180}},
                        "chars": {"크라운": {"skill_levels": "7/7/7", "overload": {"공격력": 2},
                                           "equipment": {"머리": {"tier": "T9", "corp": "테트라", "level": 3}}}}},
            "chars": {"프리카": {"control": {"tap_fire": {"rate": 4.0, "window": "burst_charge"},
                                            "burst": {"delay": 1.0, "pattern": [1]}},
                                "cube": {"name": "렐릭 어설트 큐브", "level": 10},
                                "weapon_mode_swap": False, "favorite_stage": 2}},
        })

    def test_errors_point_at_the_field(self):
        cases = [
            ({}, ""),                                                             # squad 없음
            ({"squad": []}, "/squad"),
            ({"squad": ["크라운"] * 6}, "/squad"),
            ({"squad": ["없는 니케"]}, "/squad/0"),
            ({**OK, "schema_version": 1}, "/schema_version"),
            ({**OK, "sim": {"duration": 0}}, "/sim/duration"),
            ({**OK, "sim": {"duration": -5}}, "/sim/duration"),
            ({**OK, "sim": {"seed": 7.5}}, "/sim/seed"),
            ({**OK, "sim": {"expected": "yes"}}, "/sim/expected"),
            ({**OK, "enemy": {"code": "불"}}, "/enemy/code"),
            ({**OK, "enemy": {"deff": 1}}, "/enemy/deff"),
            ({**OK, "operation": {"no_burst": "앨리스"}}, "/operation/no_burst"),
            ({**OK, "operation": {"camera": "앨리스"}}, "/operation/camera"),
            ({**OK, "operation": {"auto": ["앨리스"]}}, "/operation/auto/0"),
            ({**OK, "operation": {"tactics": [{"name": "없는 택틱"}]}}, "/operation/tactics/0/name"),
            ({**OK, "profile_level": "sync"}, "/profile_level"),                  # profile 없이
            ({**OK, "profile": {"chars": {"크라운": {"level": 9999}}}}, "/profile/chars/크라운/level"),
            ({**OK, "profile": {"chars": {"크라운": {"equip_skills": {"atk_pctt": 3}}}}},
             "/profile/chars/크라운/equip_skills/atk_pctt"),
            ({**OK, "profile": {"chars": {"크라운": {"equipment": {"머리": {"tier": "T99"}}}}}},
             "/profile/chars/크라운/equipment/머리/tier"),
            ({**OK, "profile": {"chars": {"크라운": {"control": {}}}}}, "/profile/chars/크라운/control"),
            ({**OK, "profile": {"_account": {"console": {"이상": 1}}}}, "/profile/_account/console/이상"),
            ({**OK, "chars": {"크라운": {"auto": True}}}, "/chars/크라운/auto"),
        ]
        for req, path in cases:
            with self.subTest(req=req):
                self.assertEqual(path_of(req), path)

    def test_v1_keys_point_to_new_place(self):
        with self.assertRaises(RequestError) as e:
            rq.validate({**OK, "no-burst": "크라운"})
        self.assertIn("/operation/no_burst", e.exception.detail)
        with self.assertRaises(RequestError) as e:
            rq.validate({**OK, "tap": ["크라운:4.0"]})
        self.assertIn("/chars/<니케>/control", e.exception.detail)

    def test_duplicate_squad_member_is_allowed(self):
        rq.validate({"squad": ["크라운", "크라운"]})        # 판단은 입력하는 쪽 몫이다

    def test_null_means_absent(self):
        self.assertEqual(rq.validate({**OK, "sim": {"seed": None}, "boss": None}), {**OK, "sim": {}})

    def test_schema_is_json(self):
        out = subprocess.run([sys.executable, "-m", "runner.request"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout
        self.assertEqual(json.loads(out)["$schema"], "https://json-schema.org/draft/2020-12/schema")


class MergeAndRead(unittest.TestCase):
    def test_merge_patch_rfc7386(self):
        base = {"sim": {"expected": True, "seed": 1}, "chars": {"A": {"control": {"x": 1}}}, "squad": ["A"]}
        got = rq.merge_patch(base, {"sim": {"seed": None}, "chars": {"A": {"control": {"y": 2}}}, "squad": ["B"]})
        self.assertEqual(got, {"sim": {"expected": True}, "chars": {"A": {"control": {"x": 1, "y": 2}}}, "squad": ["B"]})

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(RequestError):
            rq.loads('{"squad": ["크라운"], "squad": ["헬름"]}')


class CliIsSugar(unittest.TestCase):
    """CLI 옵션은 요청의 사람용 표기다 — 준 옵션만 칸이 되고, 같은 요청으로 펴진다."""

    def req_of(self, *argv: str) -> dict:
        return sim.base_request(AP.parse_args(list(argv)))

    def test_only_given_options(self):
        self.assertEqual(self.req_of("크라운,헬름"), {"squad": ["크라운", "헬름"]})

    def test_strings_become_structure(self):
        got = self.req_of("크라운,헬름,앨리스", "--expected", "--seed", "3", "--no-burst", "크라운", "--auto", "헬름",
                          "--tactic", "버충:앨리스", "--tap", "앨리스:4.0", "--cube", "크라운:렐릭 어설트 큐브:10",
                          "--favorite", "크라운:2", "--burst-delay", "크라운:1.5", "--enemy-code", "철갑")
        self.assertEqual(got, {
            "squad": ["크라운", "헬름", "앨리스"],
            "sim": {"seed": 3, "expected": True},
            "enemy": {"code": "철갑"},
            "operation": {"no_burst": "크라운", "auto": ["헬름"], "tactics": [{"name": "버충", "target": "앨리스"}]},
            "chars": {"앨리스": {"control": {"tap_fire": {"rate": 4.0}}},
                      "크라운": {"control": {"burst": {"delay": 1.5}}, "favorite_stage": 2,
                               "cube": {"name": "렐릭 어설트 큐브", "level": 10}}},
        })

    def test_flags_override_request(self):
        req = json.dumps({"squad": ["크라운"], "sim": {"expected": True, "duration": 60}}, ensure_ascii=False)
        got = self.req_of("--request", req, "--duration", "30")
        self.assertEqual(got, {"squad": ["크라운"], "sim": {"expected": True, "duration": 30.0}})


if __name__ == "__main__":
    unittest.main()
