"""`runner.sim --json`·`--batch` 계약 검사 — 형식의 정본은 docs/SIM-JSON.md.

    python -m unittest discover -s tests -v

핵심은 하나다: **같은 입력이면 JSON 총딜이 텍스트 출력의 총딜과 정확히 같다.** 다른 프로그램
(`Jgaram/nikke-opt`)이 이 레포를 평가기로 쓰므로 둘이 갈리면 그쪽 최적화가 조용히 틀린다.
시뮬 한 번이 수 초라 서브프로세스를 한꺼번에 띄워 놓고 결과를 모은다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQUAD = ["리틀 머메이드", "크라운", "라피 : 레드 후드", "미하라", "헬름"]
OTHER = ["리틀 머메이드", "크라운"]
BOSS = "솔로 레이드 S40"
# 랜덤 모드는 시드를 고정해 짧게 돈다 — 기대값 모드와 다른 경로(난수열)도 같은지 본다
RANDOM_ARGS = ["--seed", "7", "--duration", "60"]

# 인라인 육성 프로필 (docs/SIM-JSON.md §육성). 빈 항목·base=default는 기본 스펙과 딜이 같아야 하고,
# 줄 수 표기(`overload`·"7/7/7")는 계산기 표기(합산 퍼센트)와 딜이 같아야 한다.
EMPTY_PROFILE = {"base": "default", "chars": {}}
MID_PCT = {"skill_levels": {"1": 7, "2": 7, "3": 7}, "equip_skills": {"atk_pct": 11.11, "element_bonus": 44.3}}
MID_SHORT = {"skill_levels": "7/7/7", "overload": {"공격력": 1, "우월 코드 대미지": 2}}

_TOTAL = re.compile(r"스쿼드 총 딜: ([\d,]+)")
_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _start(args: list[str], stdin: str | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "runner.sim", *args], cwd=ROOT, env=_ENV,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def _finish(p: subprocess.Popen, stdin: str | None = None) -> tuple[int, str, str]:
    out, err = p.communicate(stdin, timeout=600)
    return p.returncode, out, err


def _text_total(out: str) -> int:
    m = _TOTAL.search(out)
    assert m, f"텍스트 출력에 총딜 줄이 없다:\n{out}"
    return int(m.group(1).replace(",", ""))


class SimJsonContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        squad = ",".join(SQUAD)
        batch_in = "\n".join(json.dumps(x, ensure_ascii=False) for x in [
            # 다른 스쿼드를 먼저 돌려 한 프로세스 안에서 상태가 새지 않는지도 본다
            {"id": "other", "squad": OTHER, "expected": True},
            {"id": "bad", "squad": ["없는 니케"], "expected": True},
            "not an object",
            {"id": "random", "squad": SQUAD, "seed": 7, "duration": 60},
        ]) + "\nnot json\n"
        prof_in = "\n".join(json.dumps(x, ensure_ascii=False) for x in [
            {"id": "empty", "squad": OTHER, "expected": True, "profile": EMPTY_PROFILE},
            {"id": "pct", "squad": OTHER, "expected": True,
             "profile": {"base": "default", "chars": {"크라운": MID_PCT}}},
            {"id": "short", "squad": OTHER, "expected": True,
             "profile": {"base": "default", "chars": {"크라운": MID_SHORT}}},
            {"id": "typo", "squad": OTHER, "expected": True, "profile": {"chars": {"없는 니케": {}}}},
        ]) + "\n"
        jobs = {
            "text": (_start([squad, "--expected", "--boss", BOSS]), None),
            "json_inline": (_start([squad, "--expected", "--boss", BOSS, "--json",
                                    "--profile", json.dumps(EMPTY_PROFILE)]), None),
            "batch_profile": (_start(["--batch"], prof_in), prof_in),
            "json": (_start([squad, "--expected", "--boss", BOSS, "--json"]), None),
            "text_random": (_start([squad, *RANDOM_ARGS]), None),
            "text_other": (_start([",".join(OTHER), "--expected"]), None),
            "batch": (_start(["--batch"], batch_in), batch_in),
            "error": (_start(["없는 니케,크라운", "--expected", "--json"]), None),
        }
        cls.res = {k: _finish(p, s) for k, (p, s) in jobs.items()}

    def test_json_total_equals_text_total(self):
        code, out, err = self.res["text"]
        self.assertEqual(code, 0, err)
        code, jout, err = self.res["json"]
        self.assertEqual(code, 0, err)
        lines = jout.splitlines()
        self.assertEqual(len(lines), 1, "stdout에는 JSON 객체 하나만 나가야 한다")
        obj = json.loads(lines[0])
        self.assertEqual(obj["total_damage"], _text_total(out))

        self.assertEqual(obj["schema_version"], 1)
        self.assertEqual([m["name"] for m in obj["members"]], SQUAD)
        self.assertEqual(sum(m["damage"] for m in obj["members"]), obj["total_damage"])
        self.assertTrue(obj["expected"])
        self.assertIsNone(obj["seed"])
        self.assertEqual(obj["boss"]["def"], 31784)
        self.assertEqual(obj["boss"]["code"], "풍압")
        self.assertEqual(obj["evaluator"]["repo"], "Jgaram/nikke-calc")
        # 헬름은 장탄 옵션 0% 레이어가 붙는다 (data/char_defaults.json) — 이탈 보고가 JSON에 실려야 한다
        self.assertIn("헬름", obj["spec"]["deviated"])
        self.assertIn("헬름", obj["spec"]["char_defaults"]["applied"])
        self.assertTrue(any("이탈" in w for w in obj["warnings"]))

    def test_batch_lines(self):
        code, out, err = self.res["batch"]
        self.assertEqual(code, 0, err)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["line"] for r in rows], [1, 2, 3, 4, 5])

        other, bad, not_obj, rand, not_json = rows
        self.assertEqual(other["id"], "other")
        self.assertEqual(other["total_damage"], _text_total(self.res["text_other"][1]))
        self.assertEqual(bad["id"], "bad")
        self.assertEqual(bad["error"]["type"], "invalid_input")
        self.assertIn("error", not_obj)
        self.assertIn("error", not_json)
        # 실패한 줄 뒤에도 계속 처리하고, 결과는 단발 텍스트 실행과 같다
        self.assertEqual(rand["id"], "random")
        self.assertEqual(rand["total_damage"], _text_total(self.res["text_random"][1]))
        self.assertEqual(rand["seed"], 7)
        self.assertFalse(rand["expected"])

    def test_inline_profile_default_base(self):
        # 빈 인라인 프로필 + base=default = 기본 스펙. 파일 없이 육성을 넘기는 길이 딜을 바꾸지 않는다
        code, out, err = self.res["json_inline"]
        self.assertEqual(code, 0, err)
        obj = json.loads(out)
        self.assertEqual(obj["total_damage"], _text_total(self.res["text"][1]))
        self.assertEqual(obj["spec"]["baseline"], "profile")
        prof = obj["spec"]["profile"]
        self.assertEqual((prof["source"], prof["base"], prof["ungrown"]), ("inline", "default", []))

    def test_inline_profile_batch(self):
        code, out, err = self.res["batch_profile"]
        self.assertEqual(code, 0, err)
        empty, pct, short, typo = (json.loads(line) for line in out.splitlines())
        self.assertEqual(empty["total_damage"], _text_total(self.res["text_other"][1]))
        self.assertEqual(short["total_damage"], pct["total_damage"])
        self.assertLess(pct["total_damage"], empty["total_damage"])
        self.assertEqual(typo["error"]["type"], "invalid_input")
        self.assertIn("없는 니케", typo["error"]["message"])

    def test_error_object(self):
        code, out, err = self.res["error"]
        self.assertNotEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, "오류도 stdout에는 JSON 객체 하나만")
        e = json.loads(lines[0])["error"]
        self.assertEqual(e["type"], "invalid_input")
        self.assertIn("없는 니케", e["message"])


if __name__ == "__main__":
    unittest.main()
