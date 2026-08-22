#!/usr/bin/env python3
"""프리뷰 원문 ↔ 스크랩 원문 대조 (char-add 단계 R).

출시 전 카드에서 옮겨 적은 `preview_skills.json` 항목과, 출시 후 수집된
`nikke_scraped.json` 항목의 **스킬 원문·수치**를 비교한다.

판정은 사람이 한다 — 이 도구는 차이를 빠짐없이 보여줄 뿐, "영향 없음"을 판정하지 않는다.
사소해 보이는 문구가 실제로는 발동 조건·타이밍 변경인 경우가 있다
(`명중 시` → `공격 시`, `사용 후` → `사용 시`).

사용:
  python -m scraper.preview_diff              # 프리뷰 항목 전부 (출시된 것만 대조)
  python -m scraper.preview_diff <캐릭터명>   # 한 명만
  python -m scraper.preview_diff <프리뷰명> --as <출시명>   # 이름이 바뀐 경우
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = ROOT / "scraper" / "preview_skills.json"
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"

_TAG = re.compile(r"</?(?:color|word_group)[^>]*>", re.I)
_WS = re.compile(r"[ \t]+")

# 대조하는 필드. 무기상세는 계산기 수치의 원천이라 함께 본다.
WEAPON_FIELDS = ("무기유형", "최대 장탄 수", "재장전 시간", "무기스킬")


def normalize(text: str) -> list[str]:
    """색상·word_group 태그와 공백 차이를 지운 비교용 줄 목록.

    `■` clause 경계에서도 줄을 나눠, 한 clause만 바뀌었을 때 diff가 그 clause만 가리키게 한다.
    """
    text = _TAG.sub("", str(text))
    text = text.replace("■", "\n■")
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _diff(label: str, old: str, new: str) -> bool:
    """차이가 있으면 출력하고 True."""
    a, b = normalize(old), normalize(new)
    if a == b:
        return False
    print(f"\n  --- {label}  (프리뷰 → 스크랩)")
    for line in difflib.unified_diff(a, b, lineterm="", n=1,
                                     fromfile="preview", tofile="scraped"):
        if line.startswith(("---", "+++")):
            continue
        print(f"    {line}")
    return True


def compare(name: str, prev: dict, scr: dict) -> bool:
    """반환: 차이 있으면 True."""
    print(f"\n=== {name} ===")
    changed = False

    pw, sw = prev.get("무기상세", {}), scr.get("무기상세", {})
    for f in WEAPON_FIELDS:
        changed |= _diff(f"무기상세.{f}", pw.get(f, ""), sw.get(f, ""))

    for f in ("레어도", "클래스", "기업", "속성", "버스트 단계"):
        p, s = str(prev.get(f, "")), str(scr.get(f, ""))
        if p != s:
            changed = True
            note = "  ← 카드에 없어 미상으로 뒀던 값" if p in ("", "미상") else ""
            print(f"\n  --- {f}: {p!r} → {s!r}{note}")

    pk, sk = list(prev.get("스킬", {})), list(scr.get("스킬", {}))
    if pk != sk:
        changed = True
        print(f"\n  --- 스킬 이름·순서: {pk} → {sk}")

    for i, key in enumerate(pk):
        pskill = prev["스킬"][key]
        # 이름이 바뀌었을 수 있으니 같은 이름이 없으면 같은 순서의 스킬과 댄다
        skill = scr.get("스킬", {}).get(key) or (
            scr["스킬"][sk[i]] if i < len(sk) else None)
        if skill is None:
            changed = True
            print(f"\n  --- 스킬 {key}: 스크랩에 대응 항목 없음")
            continue
        changed |= _diff(f"{key}.template", pskill.get("template", ""),
                         skill.get("template", ""))
        changed |= _diff(f"{key}.쿨타임", pskill.get("쿨타임", ""),
                         skill.get("쿨타임", ""))
        pv = pskill.get("values", {}).get("10")
        sv = skill.get("values", {}).get("10")
        if pv != sv:
            changed = True
            print(f"\n  --- {key}.values[10] (레벨 10 수치)")
            print(f"    프리뷰: {pv}")
            print(f"    스크랩: {sv}")

    if not changed:
        print("  차이 없음 — 프리뷰 원문이 출시본과 일치한다.")
        print("  → preview_skills.json에서 항목을 제거하고, parsed_skills.json의 values에")
        print("    레벨 1~9를 채운 뒤 `python -m context.snapshot`으로 회귀한다.")
    else:
        print("\n  → 차이를 유저에게 그대로 제시하고 char-add 단계 2(파싱)부터 재검토한다.")
        print("    에이전트가 자체 판단으로 '영향 없음'이라 결론짓지 않는다.")
    return changed


def main(argv: list[str]) -> int:
    if not PREVIEW.exists():
        print("preview_skills.json 없음")
        return 0
    preview = {k: v for k, v in json.loads(PREVIEW.read_text(encoding="utf-8")).items()
               if not k.startswith("_")}
    scraped = json.loads(SCRAPED.read_text(encoding="utf-8"))

    alias = None
    if "--as" in argv:
        i = argv.index("--as")
        alias = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]

    names = argv or list(preview)
    if not names:
        print("등록된 프리뷰 항목 없음")
        return 0

    fail = False
    for name in names:
        if name not in preview:
            print(f"[{name}] preview_skills.json에 없음")
            fail = True
            continue
        target = alias or name
        if target not in scraped:
            print(f"\n=== {name} ===\n  아직 출시 전 (nikke_scraped.json에 없음). 대조할 원문이 없다.")
            if alias is None and len(names) == 1:
                print("  이름이 바뀐 채 출시됐다면 `--as <출시명>`으로 대조한다.")
            continue
        if target != name:
            print(f"\n(이름 변경 대조: 프리뷰 {name!r} ↔ 스크랩 {target!r})")
        fail |= compare(name, preview[name], scraped[target])
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
