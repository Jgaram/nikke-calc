"""cdn_locale 병합 규칙 — 사전의 다른 절·열쇠 보존, 없는 열쇠만 보태기, 보스는 언제나 보태기만."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scraper.cdn_locale import merge_dictionary, write_merged


class MergeDictionaryTest(unittest.TestCase):
    def test_add_only_keeps_foreign_sections_and_keys(self) -> None:
        existing = {
            "names": {"라피": "Rapi"},
            "skills": {"1more": "One More", "소원": "Wish"},
            "bosses": {"툼스톤": "トゥームストーン", "H.S.T.A.": "デュアルリング"},
            "_buff_names": {"취기": "Tipsy"},
        }
        generated = {
            "names": {"라피": "Rapi", "드레이크 : 그레이트 빌런": "Drake: Great Villain"},
            "skills": {"소원": "Wish!", "오버 오버 드라이브": "Super Duper Overdrive"},
            "bosses": {"H.S.T.A.": "Dual Ring", "새 보스": "New Boss"},
            "tpls": {"■ x": "■ y"},
        }
        report = merge_dictionary(existing, generated)
        self.assertEqual(existing["names"], {"라피": "Rapi", "드레이크 : 그레이트 빌런": "Drake: Great Villain"})
        self.assertEqual(existing["skills"], {"1more": "One More", "소원": "Wish", "오버 오버 드라이브": "Super Duper Overdrive"})
        self.assertEqual(existing["bosses"], {"툼스톤": "トゥームストーン", "H.S.T.A.": "デュアルリング", "새 보스": "New Boss"})
        self.assertEqual(existing["_buff_names"], {"취기": "Tipsy"})
        self.assertEqual(existing["tpls"], {"■ x": "■ y"})
        self.assertEqual(report["skills"], (1, 0, 1, 1))     # 보탬 1 · 같음 0 · 값 다름 1(소원) · 사전만 1(1more)
        self.assertEqual(report["bosses"], (1, 0, 1, 1))

    def test_overwrite_replaces_except_bosses(self) -> None:
        existing = {"skills": {"소원": "Wish"}, "bosses": {"H.S.T.A.": "デュアルリング"}}
        generated = {"skills": {"소원": "Wish!"}, "bosses": {"H.S.T.A.": "Dual Ring"}}
        merge_dictionary(existing, generated, overwrite=True)
        self.assertEqual(existing["skills"]["소원"], "Wish!")
        self.assertEqual(existing["bosses"]["H.S.T.A."], "デュアルリング")

    def test_write_merged_preserves_format_and_check_does_not_write(self) -> None:
        existing = {"names": {"라피": "Rapi"}, "_buff_names": {"취기": "Tipsy"}}
        text = json.dumps(existing, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n"
        generated = {"names": {"라피": "Rapi", "네온": "Neon"}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "game.en.json"
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            write_merged(p, generated, check=True, overwrite=False)
            with open(p, encoding="utf-8", newline="") as f:
                self.assertEqual(f.read(), text)                       # --check는 쓰지 않는다
            write_merged(p, generated, check=False, overwrite=False)
            with open(p, encoding="utf-8", newline="") as f:
                out = f.read()
            self.assertTrue(out.startswith("{\r\n  \"names\""))     # 들여쓰기 2·CRLF 보존
            self.assertTrue(out.endswith("\r\n"))
            got = json.loads(out)
            self.assertEqual(got["names"], {"라피": "Rapi", "네온": "Neon"})
            self.assertEqual(got["_buff_names"], {"취기": "Tipsy"})


if __name__ == "__main__":
    unittest.main()
