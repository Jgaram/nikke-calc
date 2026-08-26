"""CDN 무기 메카닉 파싱 회귀. ``python -m unittest scraper.test_parse_nikke``."""

from __future__ import annotations

import json
import unittest

from scraper.parse_nikke import OUT, SRC, parse_fire_mechanics


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


if __name__ == "__main__":
    unittest.main()
