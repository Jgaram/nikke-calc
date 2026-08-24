"""전투력 계산기용 인게임 아이콘 수집 — 장비·큐브·스킬.

    python scraper/cdn_ui_icons.py            # 없는 것만
    python scraper/cdn_ui_icons.py --force

CDN 평문 경로(`cdn_icons.py`와 같은 규칙, 프론트 번들 실측):
  장비  /icon/equip/<ItemEquipTable의 resource_id>.webp
  큐브  /icon/equip/ie_<큐브 resource_id>.webp
  스킬  /icon/skill/char_skill/<스킬 icon>.webp

산출물:
  image/ui/…                 아이콘 파일
  data/ui_icons.json         조회표 — 빌드가 dist로 굽는다
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "image", "ui")
MAP_PATH = os.path.join(ROOT, "data", "ui_icons.json")
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36"}
SUB_PART = {"Module_A": "머리", "Module_B": "몸통", "Module_C": "팔", "Module_D": "다리"}
CLS_KO = {"Attacker": "화력형", "Defender": "방어형", "Supporter": "지원형"}


def get(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return r.read()
    except Exception:                      # noqa: BLE001  없는 아이콘은 흔하다
        return None


def get_json(path: str):
    b = get(path)
    return json.loads(b) if b else None


def save(name: str, data: bytes, force: bool) -> bool:
    dest = os.path.join(OUT_DIR, name)
    if os.path.exists(dest) and not force:
        return False
    with open(dest, "wb") as f:
        f.write(data)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="전투력 계산기 아이콘 수집")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {"equip": {}, "cube": {}, "skill": {}}
    got = 0

    # ── 장비: 단계·클래스·부위별 아이콘 ────────────────────────────────
    tbl = get_json("/equip/ItemEquipTable-ko.json")
    recs = (tbl or {}).get("records") or tbl or []
    # **클래스 전용을 먼저** 넣는다 — `class: All`이 먼저 들어가면 setdefault가 그걸
    # 세 클래스 모두에 박아 방어형 자리에 화력형 그림이 뜬다(실측 버그).
    recs = sorted(recs, key=lambda r: r.get("class") in (None, "", "All"))
    for r in recs:
        rid, sub, cls = r.get("resource_id"), r.get("item_sub_type"), r.get("class")
        rare = r.get("item_rare")
        if not rid or sub not in SUB_PART or rare in (None, ""):
            continue
        part = SUB_PART[sub]
        # 기업 장비(T9 제조사)는 클래스가 All인 것도 있다 — 클래스별로 다 담는다
        classes = [CLS_KO[cls]] if cls in CLS_KO else list(CLS_KO.values())
        fname = f"{rid}.webp"
        if not os.path.exists(os.path.join(OUT_DIR, fname)) or args.force:
            data = get(f"/icon/equip/{rid}.webp")
            if data and save(fname, data, args.force):
                got += 1
        if os.path.exists(os.path.join(OUT_DIR, fname)):
            for ko in classes:
                out["equip"].setdefault(f"{rare}|{ko}|{part}", fname)

    # ── 큐브 ────────────────────────────────────────────────────────────
    for e in (get_json("/equip/cube_rare_map.json") or []):
        cid, res = e.get("id"), e.get("resource_id")
        d = get_json(f"/equip/ko/cube_{cid}.json") or {}
        name = d.get("name_localkey")
        if not res or not name:
            continue
        fname = f"ie_{res}.webp"
        if not os.path.exists(os.path.join(OUT_DIR, fname)) or args.force:
            data = get(f"/icon/equip/ie_{res}.webp")
            if data and save(fname, data, args.force):
                got += 1
        if os.path.exists(os.path.join(OUT_DIR, fname)):
            out["cube"][name] = fname
        # 큐브 스킬 아이콘 + 레벨별 스킬 레벨 (인게임 큐브 상자의 동그란 칸)
        sk = []
        for g in (d.get("harmonycube_skill_group") or []):
            if not isinstance(g, dict):      # 표에 빈 칸(null)이 섞여 있다
                continue
            ic = g.get("icon")
            if not ic or ic in sk:
                continue
            f2 = f"{ic}.webp"
            if not os.path.exists(os.path.join(OUT_DIR, f2)) or args.force:
                data2 = get(f"/icon/skill/char_skill/{ic}.webp")
                if data2 and save(f2, data2, args.force):
                    got += 1
            if os.path.exists(os.path.join(OUT_DIR, f2)):
                sk.append(ic)
        if sk:
            out.setdefault("cube_skill", {})[name] = [f"{x}.webp" for x in sk]
        # 스킬 이름·설명 — 큐브 상자의 동그란 칸 툴팁에 쓴다. 게임 텍스트의 꾸밈
        # 태그(<color>·<word_group>)는 벗기고 사람이 읽는 문장만 남긴다.
        info = []
        for g in (d.get("harmonycube_skill_group") or []):
            if not isinstance(g, dict):
                continue
            desc = str(g.get("description_localkey") or "")
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            # 값은 «자리마다 레벨별 배열»이다: [{description_value: [lv1, lv2, …]}, …]
            # 자리 표시를 {0}·{1}로 바꿔 두고 레벨은 화면에서 채운다.
            vals = []
            for i, v in enumerate(g.get("description_value_list") or []):
                arr = v.get("description_value") if isinstance(v, dict) else None
                vals.append([str(x) for x in (arr or [])])
                desc = desc.replace("{description_value_%02d}" % (i + 1), "{%d}" % i)
            info.append({"name": g.get("name_localkey") or "", "desc": desc, "vals": vals})
        if info:
            out.setdefault("cube_skill_info", {})[name] = info
        out.setdefault("cube_levels", {})[name] = {
            k: d.get(k) for k in ("level1", "level2", "level3") if d.get(k)}

    # ── 스킬: 캐릭터별 3개 ──────────────────────────────────────────────
    # 실측 모양: [{resource_id, skill1_icon, skill2_icon, ulti_skill_icon}, …]
    smap = get_json("/character/character_skill_map.json") or []
    seen: set[str] = set()
    for row in smap:
        icons = []
        key = row.get("resource_id")
        for fld in ("skill1_icon", "skill2_icon", "ulti_skill_icon"):
            ic = row.get(fld)
            if not ic:
                continue
            fname = f"{ic}.webp"
            if ic not in seen:
                seen.add(ic)
                if not os.path.exists(os.path.join(OUT_DIR, fname)) or args.force:
                    data = get(f"/icon/skill/char_skill/{ic}.webp")
                    if data and save(fname, data, args.force):
                        got += 1
            if os.path.exists(os.path.join(OUT_DIR, fname)):
                icons.append(fname)
        if icons:
            out["skill"][str(key)] = icons[:3]

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    total = sum(os.path.getsize(os.path.join(OUT_DIR, x)) for x in os.listdir(OUT_DIR))
    print(f"[+] 새로 {got}장 · 장비 {len(out['equip'])}조합 · 큐브 {len(out['cube'])}종 "
          f"· 스킬 {len(out['skill'])}캐릭 → {OUT_DIR} ({total / 1048576:.1f} MB)")
    print(f"[+] 조회표: {MAP_PATH}")


if __name__ == "__main__":
    main()
