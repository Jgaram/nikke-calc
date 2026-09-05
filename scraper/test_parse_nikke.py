"""CDN 무기 메카닉 파싱 회귀. ``python -m unittest scraper.test_parse_nikke``."""

from __future__ import annotations

import json
import unittest

from scraper.parse_nikke import OUT, SRC, merge_into, parse_fire_mechanics


class ReloadDelayParsingTests(unittest.TestCase):
    def test_spot_delays_are_converted_from_centiseconds(self) -> None:
        parsed = parse_fire_mechanics({
            "spot_last_delay": 20,
            "spot_first_delay": 13,
        })

        self.assertEqual(parsed["reload_start_delay"], 0.2)
        self.assertEqual(parsed["post_reload_delay"], 0.13)

    def test_missing_spot_delays_do_not_mask_fallback(self) -> None:
        parsed = parse_fire_mechanics({})

        self.assertNotIn("reload_start_delay", parsed)
        self.assertNotIn("post_reload_delay", parsed)

    def test_zero_spot_delay_is_preserved(self) -> None:
        parsed = parse_fire_mechanics({
            "spot_last_delay": 0,
            "spot_first_delay": 0,
        })

        self.assertEqual(parsed["reload_start_delay"], 0.0)
        self.assertEqual(parsed["post_reload_delay"], 0.0)

    def test_all_scraped_spot_delays_match_parsed_seconds(self) -> None:
        raw = json.loads(SRC.read_text(encoding="utf-8"))
        parsed = json.loads(OUT.read_text(encoding="utf-8"))
        checked = 0

        for name, char in raw.items():
            if name.startswith("_"):
                continue
            weapon = char["무기상세"]
            with self.subTest(name=name):
                self.assertEqual(
                    parsed[name]["reload_start_delay"],
                    round(float(weapon["spot_last_delay"]) / 100, 4),
                )
                self.assertEqual(
                    parsed[name]["post_reload_delay"],
                    round(float(weapon["spot_first_delay"]) / 100, 4),
                )
            checked += 1

        self.assertGreater(checked, 0)


class MergeIntoTest(unittest.TestCase):
    """정본 병합 — 손수 관리 키·형식 보존, 파서 키만 교체, 사라진 파서 키 제거."""

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def test_merge_preserves_manual_keys_format_and_drops_stale_parser_keys(self) -> None:
        import tempfile
        from pathlib import Path

        existing = {
            "라피": {"element_code": "작열", "max_ammo": 60, "burst_energy": 0.1, "rare": "SSR", "clip_fill": 1.0},
            "신인": {"element_code": "풍압", "max_ammo": 9, "preview": True, "rare": "SSR", "clip_fill": 1.0},
            "옛날": {"element_code": "철갑", "max_ammo": 6, "charge_time": 1.0, "rare": "SR", "clip_fill": 0.5},
        }
        text = json.dumps(existing, ensure_ascii=False, indent=1).replace("\n", "\r\n")   # 정본 형식: 1칸·CRLF·끝 개행 없음
        parsed = {
            "라피": {"element_code": "작열", "max_ammo": 60},                    # 변화 없음
            "신인": {"element_code": "풍압", "max_ammo": 9},                     # 출시 → preview 소멸
            "옛날": {"element_code": "철갑", "max_ammo": 6},                     # charge_time 사라짐
            "새로": {"element_code": "전격", "max_ammo": 120},                   # 신규
            "test_B1": {"element_code": "철갑", "max_ammo": 60},
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "parsed_nikke.json"
            self._write(p, text)
            changed = merge_into(p, parsed, only=["신인", "옛날", "새로"])
            self.assertEqual(changed, ["신인", "옛날", "새로"])
            with open(p, encoding="utf-8", newline="") as f:
                out = f.read()
            self.assertIn("\r\n", out)
            self.assertFalse(out.endswith("\n"))
            self.assertTrue(out.startswith("{\r\n \"라피\": {"))       # 들여쓰기 1칸·키 순서 보존
            got = json.loads(out)
            self.assertEqual(got["라피"], existing["라피"])                # only 밖 → 손대지 않음
            self.assertEqual(got["신인"], {"element_code": "풍압", "max_ammo": 9, "rare": "SSR", "clip_fill": 1.0})
            self.assertEqual(got["옛날"], {"element_code": "철갑", "max_ammo": 6, "rare": "SR", "clip_fill": 0.5})
            self.assertEqual(got["새로"], {"element_code": "전격", "max_ammo": 120})
            self.assertNotIn("test_B1", got)                              # only 밖의 더미도 안 들어간다
            # --check는 쓰지 않는다
            before = out
            merge_into(p, {"라피": {"element_code": "수냉", "max_ammo": 60}}, check=True)
            with open(p, encoding="utf-8", newline="") as f:
                self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
