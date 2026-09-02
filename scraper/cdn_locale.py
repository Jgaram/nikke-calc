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
from cdn_fetch import (FAVORITE_PATH, FAVORITE_RARE_MAP_PATH, ICON_RID_RE,  # noqa: E402
                       ROLEDATA_PATH, render_skill)

ROOT = Path(__file__).parent.parent
SCRAPED = Path(__file__).parent / "nikke_scraped.json"
RAW_DIR = ROOT / "research" / "blablalink" / "json" / "roledata"
FAV_RAW_DIR = ROOT / "research" / "blablalink" / "json" / "favorite"
OUT_DIR = ROOT / "web" / "src" / "i18n"

# CDN 로케일 → 웹 언어 코드. 중국어는 번체만 있다(zh-CN은 404) — NIKKE 공식과 같다.
LOCALES = {"en": "en", "ja": "ja", "zh-tw": "zh"}   # roledata는 소문자 zh-tw다(nikke_list는 zh-TW)
PAUSE = 1.0
SKILL_KEYS = ("skill1_detail", "skill2_detail", "ulti_skill_detail")
_PH = re.compile(r"\{(\d+)\}")


def _suffix(ko_key: str) -> str:
    """`사쿠라 (SR)`처럼 동명이인을 가른 꼬리(등급·id). 현지 이름에도 똑같이 붙인다 —
    안 붙이면 화면에 «Sakura»가 둘 나와 어느 쪽인지 알 수 없다.
    «레이 (가칭)» 같은 것은 이름의 일부라 현지 이름이 이미 제 말로 들고 있다 — 안 붙인다."""
    m = re.search(r" \((SSR|SR|R|\d+)\)$", ko_key)
    return m.group(0) if m else ""


def build_cubes(offline: bool) -> dict[str, dict[str, str]]:
    """큐브 이름·스킬명·설명 템플릿을 언어별로. 반환: {lang: {한국어: 현지어}}.

    한국어 템플릿은 `data/cube.json`(scraper/cdn_tables.py)의 것과 같은 절차로 만든
    문구라 그대로 키가 된다. 큐브 17종 × 3언어 = 51요청, 역시 1초에 하나."""
    from cdn_tables import CUBE_MAP_PATH, CUBE_PATH, clean_template, render_levels  # noqa: E402
    from cdn_fetch import build_template  # noqa: E402

    def tmpl(info: dict) -> str:
        return clean_template(build_template(render_levels(info))["template"])

    raw_dir = RAW_DIR / "cube"
    out = {lang: {} for lang in LOCALES.values()}
    client = None if offline else httpx.Client(timeout=30)
    try:
        def get(path: str, cache: Path) -> dict | list | None:
            if cache.exists():
                return json.loads(cache.read_text(encoding="utf-8"))
            if offline:
                return None
            r = client.get(cdn_path.url(path))
            time.sleep(PAUSE)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = json.loads(r.content.decode("utf-8-sig"))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        cube_map = get(CUBE_MAP_PATH, raw_dir / "cube_rare_map.json") or []
        for c in cube_map:
            cid = c["id"]
            ko = get(CUBE_PATH.format(locale="ko", cid=cid), raw_dir / "ko" / f"{cid}.json")
            if not ko:
                continue
            ko_skills = {s["name_localkey"]: s for s in (ko.get("harmonycube_skill_group") or []) if s}
            for locale, lang in LOCALES.items():
                loc = get(CUBE_PATH.format(locale=locale, cid=cid), raw_dir / locale / f"{cid}.json")
                if not loc:
                    continue
                d = out[lang]
                if ko.get("name_localkey") and loc.get("name_localkey"):
                    d.setdefault(ko["name_localkey"], loc["name_localkey"])
                loc_skills = [s for s in (loc.get("harmonycube_skill_group") or []) if s]
                # 스킬은 같은 자리(순서)끼리 짝이다 — 이름으로는 언어가 달라 못 맞춘다
                for ko_s, loc_s in zip([s for s in (ko.get("harmonycube_skill_group") or []) if s], loc_skills):
                    if ko_s.get("name_localkey") and loc_s.get("name_localkey"):
                        d.setdefault(ko_s["name_localkey"], loc_s["name_localkey"])
                    kt, lt = tmpl(ko_s), tmpl(loc_s)
                    if kt and lt and len(_PH.findall(kt)) == len(_PH.findall(lt)):
                        d.setdefault(kt, lt)
    finally:
        if client:
            client.close()
    return out


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


def _fetch_fav(client: httpx.Client | None, path: str, dst: Path) -> dict | None:
    """애장품 원본 하나. 받아 둔 것이 있으면 다시 받지 않는다(요청은 1초에 하나 — 남의 CDN)."""
    if dst.exists():
        return json.loads(dst.read_text(encoding="utf-8"))
    if client is None:
        return None
    r = client.get(cdn_path.url(path))
    time.sleep(PAUSE)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = json.loads(r.content.decode("utf-8-sig"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def add_favorites(client: httpx.Client | None, locale: str, scraped: dict,
                  skills: dict, tpls: dict) -> tuple[int, int]:
    """애장품 단계별 교체 스킬의 현지 이름·설명을 `skills`·`tpls`에 보탠다.

    애장품 원본은 로케일이 경로에 있다(`/equip/{locale}/favorite_{fid}.json`). 한국어 수집본의
    `애장품.단계별`과 **같은 순서·같은 교체 슬롯**이라 자리로 짝지으면 된다 — 이름으로 맞추면
    현지 이름이 달라 못 찾는다. 자리 수(`{0}`·`{1}`)가 어긋나는 항목은 기본 스킬과 같은 이유로 버린다.
    """
    rid_ko = {rec["id"]: ko for ko, rec in scraped.items() if rec.get("id") and rec.get("애장품")}
    if not rid_ko:
        return 0, 0
    rare = _fetch_fav(client, FAVORITE_RARE_MAP_PATH, FAV_RAW_DIR / "favorite_rare_map.json")
    if not rare:
        print("    애장품: favorite_rare_map 없음 — 건너뜀", file=sys.stderr)
        return 0, 0
    added = dropped = 0
    for fid in rare.get("SSR", []):
        fav = _fetch_fav(client, FAVORITE_PATH.format(locale=locale, fid=fid),
                         FAV_RAW_DIR / locale / f"{fid}.json")
        if not fav:
            continue
        m = ICON_RID_RE.search(fav.get("icon_resource_id", ""))
        if not m:
            continue
        ko_name = rid_ko.get(int(m.group(1)))
        if not ko_name:
            continue
        ko_stages = (scraped[ko_name].get("애장품") or {}).get("단계별") or []
        loc_stages = [x for x in (fav.get("favoriteitem_skill_group_data") or []) if x]
        for ko_st, loc_item in zip(ko_stages, loc_stages):
            info = loc_item.get("info", loc_item)
            if ko_st.get("교체슬롯") != loc_item.get("skill_change_slot"):
                dropped += 1          # 순서가 어긋나면 짝을 못 믿는다
                continue
            loc_sk = (info.get("name_localkey") or "").strip()
            if ko_st.get("스킬명") and loc_sk:
                skills.setdefault(ko_st["스킬명"], loc_sk)
            ko_tpl, loc_tpl = ko_st.get("template") or "", render_skill(info).get("template") or ""
            if not ko_tpl or not loc_tpl:
                continue
            if len(_PH.findall(ko_tpl)) != len(_PH.findall(loc_tpl)):
                dropped += 1
                continue
            if tpls.setdefault(ko_tpl, loc_tpl) == loc_tpl:
                added += 1
    return added, dropped


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

        # 애장품 단계별 교체 스킬 — 기본 3종과 같은 사전에 보탠다
        fav_added, fav_dropped = add_favorites(client, locale, scraped, skills, tpls)
        dropped += fav_dropped
    finally:
        if client:
            client.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"game.{lang}.json"
    out.write_text(json.dumps({"names": names, "skills": skills, "tpls": tpls},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{lang:3s} 이름 {len(names)} · 스킬명 {len(skills)} · 설명 {len(tpls)}(애장품 {fav_added}) "
          f"(자리 수 불일치로 버림 {dropped}) · 없음 {len(missing)} → {out.relative_to(ROOT)}")
    if missing:
        print("    없음:", ", ".join(missing[:8]), "…" if len(missing) > 8 else "")


def build_bosses() -> dict[str, dict[str, str]]:
    """유니온 레이드 보스 이름. 받아 둔 시즌 API 응답(`research/blablalink/api/unionraid_*_level.json`)에
    네 언어가 다 들어 있다(`name_localvalues`). 반환: {lang: {한국어: 현지어}}."""
    out = {lang: {} for lang in LOCALES.values()}
    key_of = {"en": "en", "ja": "ja", "zh": "zh-tw"}
    # 같은 랩처가 회차마다 다른 코드(속성)를 달고 나온다 — «리빌드 핑거즈 [A.N.M.I]»와
    # «리빌드 핑거즈 [D.M.T.R]»는 이름은 같고 코드만 다르다. API 응답에 없는 조합은
    # **이름 부분**만 현지어로 바꾸고 코드는 그 언어의 괄호 모양으로 다시 붙인다.
    strip = {"ko": r"\s*\[[^\]]*\]\.?$", "en": r"\s+[A-Z][A-Z.]*$", "ja": r"「[^」]*」$", "zh": r"\s*\[[^\]]*\]$"}
    base_of = {lang: {} for lang in LOCALES.values()}
    for f in sorted((ROOT / "research" / "blablalink" / "api").glob("unionraid_*_level.json")):
        txt = f.read_text(encoding="utf-8")
        for m in re.finditer(r'"name_localvalues":\s*(\{[^{}]*\})', txt):
            vals = json.loads(m.group(1))
            ko = vals.get("ko")
            if not ko:
                continue
            ko_base = re.sub(strip["ko"], "", ko).strip()
            for lang, k in key_of.items():
                if vals.get(k):
                    out[lang].setdefault(ko, vals[k])
                    base_of[lang].setdefault(ko_base, re.sub(strip[lang], "", vals[k]).strip())
    seasons = ROOT / "research" / "blablalink" / "union_raid_seasons.json"
    if seasons.exists():
        data = json.loads(seasons.read_text(encoding="utf-8"))
        for season in (data if isinstance(data, list) else data.values()):
            for b in season.get("bosses", []):
                ko, en = b.get("name_ko"), b.get("name_en")
                if not ko:
                    continue
                m = re.search(r"\[([^\]]*)\]", ko)
                code = m.group(1) if m else ""
                ko_base = re.sub(strip["ko"], "", ko).strip()
                if en:
                    out["en"].setdefault(ko, en)
                if base_of["ja"].get(ko_base):
                    out["ja"].setdefault(ko, f"{base_of['ja'][ko_base]}「{code}」" if code else base_of["ja"][ko_base])
                elif en:
                    out["ja"].setdefault(ko, en)      # 일본어 이름을 모르면 영어 — 한국어보다는 읽힌다
                if base_of["zh"].get(ko_base):
                    out["zh"].setdefault(ko, f"{base_of['zh'][ko_base]} [{code}]" if code else base_of["zh"][ko_base])
                elif en:
                    out["zh"].setdefault(ko, en)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=list(LOCALES.values()))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    for locale, lang in LOCALES.items():
        if a.lang and lang != a.lang:
            continue
        build(locale, lang, a.offline)
    # 큐브·보스는 니케와 다른 표에서 온다 — 같은 파일에 절만 보탠다
    cubes, bosses = build_cubes(a.offline), build_bosses()
    for lang in LOCALES.values():
        if a.lang and lang != a.lang:
            continue
        p = OUT_DIR / f"game.{lang}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["cubes"], d["bosses"] = cubes[lang], bosses[lang]
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{lang:3s} 큐브 문구 {len(cubes[lang])} · 보스 {len(bosses[lang])}")


if __name__ == "__main__":
    main()
