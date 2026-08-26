#!/usr/bin/env python3
"""니케 대사 수집 — roledata의 `character_dialog_group_list`에서 **글자만** 뽑는다.

    python scraper/cdn_dialog.py                # ko 원본 받기(없는 것만) + 표 굽기
    python scraper/cdn_dialog.py --offline      # 받아 둔 원본만으로 다시 굽기

블라블라링크 «니케 보이스»에서 재생 버튼 옆에 적혀 있는 그 대사다 — 음성 파일이 아니라
텍스트. 상황 라벨(voice_description: Burst Skill·Reload·Full Burst …)과 함께 전부 남긴다 —
타임라인 뷰어의 버스트 말풍선이 첫 사용처고, 다른 데서도 쓸 수 있게 통째로 보관한다.

원본 캐시: research/blablalink/json/roledata/<locale>/<rid>.json (cdn_locale.py와 같은 자리 —
ko는 이 스크립트가 처음 받는다). 요청은 1초에 하나(남의 CDN), 있으면 다시 안 받는다.

산출물: scraper/nikke_dialog.json
    {한국어 이름: {"ko"|"en"|"ja"|"zh": [{"desc": 상황, "id": speech_id, "text": 대사}]}}
버스트 대사는 speech_id의 `_Ult_Skill_`(단, `_Ready`는 «사용 가능» 알림이라 제외)로 고른다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from cdn_locale import RAW_DIR, fetch_raw  # noqa: E402  (캐시·속도 규칙을 그대로 물려받는다)

OUT = Path(__file__).parent / "nikke_dialog.json"
# roledata 경로의 locale 표기 → 산출물 키 (cdn_locale.LOCALES와 같은 결, ko만 추가)
LOCALES = {"ko": "ko", "en": "en", "ja": "ja", "zh-tw": "zh"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def dialog_rows(data) -> list[dict]:
    row = data[0] if isinstance(data, list) and data else data
    out = []
    for e in (row or {}).get("character_dialog_group_list") or []:
        text = e.get("speech_localkey")
        if not text:
            continue
        out.append({"desc": e.get("voice_description") or "", "id": e.get("speech_id") or "",
                    "text": text})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="받아 둔 원본만으로 다시 굽기")
    args = ap.parse_args()

    # rid 목록은 이미 받아 둔 en 원본의 파일명이 정본이다 — 별도 목록 요청이 필요 없다
    rids = sorted(int(p.stem) for p in (RAW_DIR / "en").glob("*.json") if p.stem.isdigit())
    if not rids:
        raise SystemExit("[!] roledata/en 원본이 없다 — 먼저 python scraper/cdn_locale.py")

    client = None if args.offline else httpx.Client(timeout=30)
    out: dict = {}
    burst_n = 0
    try:
        for rid in rids:
            per: dict = {}
            name = None
            for locale, key in LOCALES.items():
                cache = RAW_DIR / locale / f"{rid}.json"
                if cache.exists():
                    data = json.loads(cache.read_text(encoding="utf-8"))
                elif args.offline:
                    continue
                else:
                    data = fetch_raw(client, rid, locale)
                if not data:
                    continue
                if locale == "ko":
                    row = data[0] if isinstance(data, list) and data else data
                    name = (row or {}).get("name_localkey")
                rows = dialog_rows(data)
                if rows:
                    per[key] = rows
            if not name or not per:
                continue
            out[name] = per
            if any("_Ult_Skill_" in r["id"] and "_Ready" not in r["id"] for r in per.get("ko", [])):
                burst_n += 1
    finally:
        if client:
            client.close()

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = sum(len(rows) for per in out.values() for rows in per.values())
    print(f"대사: 캐릭터 {len(out)}명 · {lines:,}줄 → {OUT.name} (ko 버스트 대사 {burst_n}명)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
