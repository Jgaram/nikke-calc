"""육성 프로필 입력 검사 — 인라인 프로필·줄 수 표기 (docs/SIM-JSON.md §육성). 시뮬은 돌리지 않는다.

    python -m unittest discover -s tests -v

딜이 같은지는 `test_sim_json.py`가 시뮬로 본다. 여기서는 그 앞단 — 같은 육성을 다른 표기로
적으면 **조립된 캐릭터 dict가 같은가**, 잘못 적으면 조용히 넘어가지 않고 끊기는가를 본다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner import spec

NAME = "크라운"


class ShortNotation(unittest.TestCase):
    def test_skill_levels_forms(self):
        want = {"1": 7, "2": 7, "3": 7}
        for form in ("7/7/7", [7, 7, 7], 7, want):
            self.assertEqual(spec.normalize_growth(NAME, {"skill_levels": form}, "t")["skill_levels"], want)

    def test_overload_lines_match_table(self):
        got = spec.normalize_growth(NAME, {"overload": {"공격력": 2, "우월 코드 대미지": 4}}, "t")
        self.assertEqual(got["equip_skills"], {"atk_pct": spec.overload("atk_pct", 2),
                                               "element_bonus": spec.overload("element_bonus", 4)})
        # 줄 수만 주면 기본 스펙 레벨이다 — 기본 스펙의 equip_skills 값과 같다
        self.assertEqual(got["equip_skills"]["element_bonus"], spec.DEFAULT_CHAR["equip_skills"]["element_bonus"])

    def test_overload_names(self):
        for name in ("element_bonus", "우월 코드 대미지", "우월코드대미지", "우월코드"):
            got = spec.normalize_growth(NAME, {"overload": {name: 1}}, "t")["equip_skills"]
            self.assertEqual(list(got), ["element_bonus"], name)

    def test_overload_mixed_levels_are_per_line(self):
        got = spec.normalize_growth(NAME, {"overload": {"최대 장탄 수": [10, 15, 10]}}, "t")
        self.assertEqual(got["equip_skills"]["max_ammo_pct"],
                         spec.overload_lines("max_ammo_pct", 1, 15) + spec.overload_lines("max_ammo_pct", 2))
        same = spec.normalize_growth(NAME, {"overload": {"공격력": [15, 15]}}, "t")
        self.assertEqual(same["equip_skills"]["atk_pct"], spec.overload("atk_pct", 2, 15))

    def test_overload_zero_clears(self):
        got = spec.normalize_growth(NAME, {"overload": {"공격력": 0, "우월코드": []}}, "t")
        self.assertEqual(got["equip_skills"], {"atk_pct": 0, "element_bonus": 0})


class InlineProfile(unittest.TestCase):
    def test_default_base_is_default_spec(self):
        prof = spec.load_profile({"base": "default", "chars": {"헬름": {}}})
        for name in (NAME, "헬름"):
            self.assertEqual(spec.build_char(name, profile=prof), spec.build_char(name))
        self.assertEqual(prof.ungrown, [])

    def test_ungrown_base_is_default(self):
        prof = spec.load_profile({"chars": {}})
        self.assertEqual(prof.base, "ungrown")
        c = spec.build_char(NAME, profile=prof)
        self.assertEqual(c["skill_levels"], spec.UNGROWN["skill_levels"])
        self.assertEqual(prof.ungrown, [NAME])

    def test_json_string_equals_dict(self):
        data = {"base": "default", "chars": {NAME: {"skill_levels": "4/4/4", "overload": {"공격력": 1}}}}
        a = spec.load_profile(data)
        b = spec.load_profile(json.dumps(data, ensure_ascii=False))
        self.assertEqual(spec.build_char(NAME, profile=a), spec.build_char(NAME, profile=b))
        self.assertEqual((a.source, a.name), ("inline", "인라인"))

    def test_file_and_inline_build_the_same(self):
        chars = {NAME: {"skill_levels": "7/7/7", "overload": {"공격력": 1, "우월코드": 2}}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(spec, "PROFILE_DIR", Path(d)):
            (Path(d) / "t.json").write_text(json.dumps({"chars": chars}, ensure_ascii=False), encoding="utf-8")
            file_prof = spec.load_profile("t")
        inline = spec.load_profile({"chars": chars})
        self.assertEqual(file_prof.source, "file")
        for name in (NAME, "헬름"):     # 헬름은 둘 다 미육성
            self.assertEqual(spec.build_char(name, profile=file_prof), spec.build_char(name, profile=inline))

    def test_mistakes_are_rejected(self):
        bad = [
            {"char": {}},                                            # 최상위 키 오타
            {"base": "max", "chars": {}},
            {"chars": {"없는 니케": {}}},                            # 정식 명칭이 아님
            {"chars": {NAME: {"control": {}}}},                      # 육성이 아니다
            {"chars": {NAME: {"skill_levels": 11}}},
            {"chars": {NAME: {"skill_levels": "7/7"}}},
            {"chars": {NAME: {"overload": {"크리티컬": 1}}}},        # 확률·대미지 둘 다와 맞는다
            {"chars": {NAME: {"overload": {"공격력": [16]}}}},
            {"chars": {NAME: {"overload": {"공격력": 1}, "equip_skills": {"atk_pct": 3}}}},
            {"chars": {NAME: {"breakthrough": 4}}},
            {"chars": {NAME: {"collection_stage": "SR16"}}},
            "{not json",
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(SystemExit):
                spec.load_profile(data)


if __name__ == "__main__":
    unittest.main()
