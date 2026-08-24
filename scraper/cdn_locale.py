#!/usr/bin/env python3
"""
cdn_locale.py
blablalink CDN에서 **다른 언어의** 니케 이름·스킬 텍스트를 받아 웹 사전으로 굽는다.

한국어 수집(`cdn_fetch.py`)은 건드리지 않는다 — 계산·데이터의 정본은 그대로
한국어 이름이고, 여기서 만드는 것은 **화면에 보일 이름**뿐이다. 그래서 산출물은
「한국어 원문 → 그 언어」의 납작한 표다. 웹의 `T()`가 UI 문자열과 같은 사전에
섞어 쓴다(`web/src/i18n.js`).

    python scraper/cdn_locale.py                 # en·ja·zh-TW 전부
    python scraper/cdn_locale.py --lang en       # 하나만
    python scraper/cdn_locale.py --offline       # 받아 둔 원본만으로 다시 굽기

받은 원본은 `research/blablalink/json/roledata/<locale>/<rid>.json`에 남겨
두 번째부터는 다시 받지 않는다. 요청은 **1초에 하나**다 — 남의 CDN이다.

산출물: `web/src/i18n/game.<lang>.json`
    {"names": {한국어 이름: 현지 이름},
     "skills": {한국어 스킬명: 현지 스킬명},
     "tpls": {한국어 설명 템플릿: 현지 설명 템플릿}}

템플릿의 `{0}`·`{1}` 번호는 언어마다 따로 매겨진다(숫자가 나오는 순서). 어순이
달라 자리 수가 안 맞으면 그 항목은 **버린다** — 값이 엇갈려 끼워지느니 한국어로
보이는 편이 낫다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import cdn_path  # noqa: E402
from cdn_fetch import ROLEDATA_PATH, render_skill  # noqa: E402

ROOT = Path(__file__).parent.parent
SCRAPED = Path(__file__).parent / "nikke_scraped.json"
RAW_DIR = ROOT / "research" / "blablalink" / "json" / "roledata"
OUT_DIR = ROOT / "web" / "src" / "i18n"

# CDN 로케일 → 웹 언어 코드. 중국어는 번체만 있다(zh-CN은 404) — NIKKE 공식과 같다.
LOCALES = {"en": "en", "ja": "ja", "zh-tw": "zh"}   # roledata는 소문자 zh-tw다(nikke_list는 zh-TW)
PAUSE = 1.0
SKILL_KEYS = ("skill1_detail", "skill2_detail", "ulti_skill_detail")
_PH = re.compile(r"\{(\d+)\}")


def _suffix(ko_key: str) -> str:
    """`사쿠라 (SR)`처럼 동명이인을 가른 꼬리. 현지 이름에도 똑같이 붙인다 —
    안 붙이면 화면에 «Sakura»가 둘 나와 어느 쪽인지 알 수 없다."""
    m = re.search(r" \([^)]+\)$", ko_key)
    return m.group(0) if m else ""


def fetch_raw(client: httpx.Client, rid: int, locale: str) -> dict | None:
    dst = RAW_DIR / locale / f"{rid}.json"
    if dst.exists():
        return json.loads(dst.read_text(encoding="utf-8"))
    r = client.get(cdn_path.url(ROLEDATA_PATH.format(rid=rid, locale=locale)))
    time.sleep(PAUSE)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = json.loads(r.content.decode("utf-8-sig"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def build(locale: str, lang: str, offline: bool) -> None:
    scraped = json.loads(SCRAPED.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    skills: dict[str, str] = {}
    tpls: dict[str, str] = {}
    missing, dropped = [], 0

    client = None if offline else httpx.Client(timeout=30)
    try:
        for ko_name, rec in scraped.items():
            rid = rec.get("id")
            if not rid:
                continue
            if offline:
                p = RAW_DIR / locale / f"{rid}.json"
                role = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            else:
                role = fetch_raw(client, rid, locale)
            if not role:
                missing.append(ko_name)
                continue

            loc_name = (role.get("name_localkey") or "").strip()
            if loc_name:
                names[ko_name] = loc_name + _suffix(ko_name)

            # 한국어 스킬 순서(스킬1·스킬2·버스트)와 같은 키 순서로 짝을 짓는다
            ko_skills = list((rec.get("스킬") or {}).items())
            for (ko_sk, ko_info), key in zip(ko_skills, SKILL_KEYS):
                detail = role.get(key)
                if not detail:
                    continue
                loc_sk = (detail.get("name_localkey") or "").strip()
                if loc_sk:
                    skills.setdefault(ko_sk, loc_sk)
                ko_tpl = ko_info.get("template") or ""
                loc_tpl = render_skill(detail).get("template") or ""
                if not ko_tpl or not loc_tpl:
                    continue
                if len(_PH.findall(ko_tpl)) != len(_PH.findall(loc_tpl)):
                    dropped += 1
                    continue
                tpls.setdefault(ko_tpl, loc_tpl)
    finally:
        if client:
            client.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"game.{lang}.json"
    out.write_text(json.dumps({"names": names, "skills": skills, "tpls": tpls},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{lang:3s} 이름 {len(names)} · 스킬명 {len(skills)} · 설명 {len(tpls)} "
          f"(자리 수 불일치로 버림 {dropped}) · 없음 {len(missing)} → {out.relative_to(ROOT)}")
    if missing:
        print("    없음:", ", ".join(missing[:8]), "…" if len(missing) > 8 else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=list(LOCALES.values()))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    for locale, lang in LOCALES.items():
        if a.lang and lang != a.lang:
            continue
        build(locale, lang, a.offline)


if __name__ == "__main__":
    main()
