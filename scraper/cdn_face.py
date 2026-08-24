"""얼굴 카드 수집 — 스쿼드 캡처 판독용 대조군.

    python scraper/cdn_face.py            # 없는 것만
    python scraper/cdn_face.py --force

경로: `/character/si/si_c{리소스id:03d}_{코스튬:02d}_s.webp` (68×68 정사각).
블라링크 «니케 도감»의 작은 카드가 쓰는 바로 그 그림이고, 인게임 스쿼드 목록
캡처에 뜨는 얼굴과 같다. 초상화(`/character/mi/mi_c###_00_s.webp`)는 그림이
달라서 대조군으로 못 쓴다 — 자름틀을 20가지로 훑어도 1등 점수가 0.5대에
2등과의 차이가 0.02밖에 안 나온다(실측).

**코스튬(스킨)마다 얼굴이 다르다.** 같은 니케라도 00·01·02가 전부 다른 그림이라
번호를 끝까지 훑어 모아 두고, 판독할 때 «어느 코스튬이든 맞으면 그 니케»로 친다.

산출물:
  image/face/si_c###_##.webp   얼굴 카드
  data/face_index.json         파일 → 리소스id·코스튬·이름
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "image", "face")
MAP_PATH = os.path.join(ROOT, "data", "face_index.json")
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PATH = "/character/si/si_c{rid:03d}_{cos:02d}_s.webp"
ROLE = "/roledata/{rid}-v2-ko.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36"}
# 코스튬 번호는 촘촘하지 않다 — 중간이 비어도 뒤에 더 있을 수 있어 끝까지 훑는다.
MAX_COSTUME = 24
# 리소스 번호는 «우리가 아는 199명»에 갇히지 않는다. CDN을 직접 훑어야 빠진 니케가
# 안 생긴다(실측: 카드가 있는 리소스가 232개 — 우리가 모르던 게 33개였다).
MAX_RID = 900


def get(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            b = r.read()
    except Exception:                          # noqa: BLE001  없는 코스튬이 훨씬 많다
        return None
    # 200이어도 이미지가 아닐 수 있다 — SPA 폴백을 파일로 저장하면 나중에 깨진
    # 아이콘으로만 드러나 원인을 찾기 어렵다.
    return b if b.startswith(b"RIFF") and b[8:12] == b"WEBP" else None


def get_json(path: str):
    b = get_raw(path)
    try:
        return json.loads(b) if b else None
    except Exception:                          # noqa: BLE001
        return None


def get_raw(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return r.read()
    except Exception:                          # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="니케 얼굴 카드 수집")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) 카드가 있는 리소스 번호를 CDN에서 직접 찾는다 (코스튬 00으로 훑는다)
    with cf.ThreadPoolExecutor(24) as ex:
        rids = [r for r in ex.map(
            lambda r: r if get(PATH.format(rid=r, cos=0)) else None, range(1, MAX_RID))
            if r]
    print(f"[+] 얼굴 카드가 있는 리소스 {len(rids)}개")

    # 2) 이름 — 우리 조회표에 없으면 roledata에서 캐 온다. 그래도 없으면 미출시·
    #    NPC 등이라 이름이 없다. 이름 없는 카드는 판독 후보에서 뺀다(가질 수 없는
    #    니케가 오답으로 튀어나오면 손해다).
    maps_path = os.path.join(ROOT, "web", "dist", "profile_maps.json")
    res_name = {}
    if os.path.exists(maps_path):
        res_name = json.load(open(maps_path, encoding="utf-8")).get("res_name") or {}
    unknown = [r for r in rids if str(r) not in res_name]
    with cf.ThreadPoolExecutor(12) as ex:
        for r, d in zip(unknown, ex.map(lambda r: get_json(ROLE.format(rid=r)), unknown)):
            if isinstance(d, dict) and d.get("name_localkey"):
                res_name[str(r)] = d["name_localkey"]
    named = sum(1 for r in rids if str(r) in res_name)
    print(f"[+] 이름 있음 {named}개 · 이름 없음 {len(rids) - named}개")

    # 3) 코스튬 전부
    jobs = [(r, c) for r in rids for c in range(MAX_COSTUME)]
    have = {f for f in os.listdir(OUT_DIR) if f.endswith(".webp")}

    def one(job):
        rid, cos = job
        fname = f"si_c{rid:03d}_{cos:02d}.webp"
        if fname in have and not args.force:
            return fname, rid, cos, 0
        data = get(PATH.format(rid=rid, cos=cos))
        if not data:
            return None
        with open(os.path.join(OUT_DIR, fname), "wb") as f:
            f.write(data)
        return fname, rid, cos, len(data)

    index, got, skip = {}, 0, 0
    with cf.ThreadPoolExecutor(24) as ex:
        for r in ex.map(one, jobs):
            if not r:
                continue
            fname, rid, cos, n = r
            index[fname] = {"rid": rid, "cos": cos, "name": res_name.get(str(rid))}
            if n:
                got += 1
            else:
                skip += 1

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump({"_comment": "얼굴 카드 → 리소스id·코스튬·이름. name이 null이면 "
                               "이름을 못 찾은 것(미출시·NPC)이라 판독 후보에서 뺀다. "
                               "python scraper/cdn_face.py 로 갱신.", "faces": index},
                  f, ensure_ascii=False, indent=1)
    chars = len({v["rid"] for v in index.values()})
    size = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR))
    print(f"[+] 새로 {got}장 · 건너뜀 {skip}장 · 총 {len(index)}장 / {chars}명 "
          f"({size / 1048576:.1f} MB) → {OUT_DIR}")
    print(f"[+] 조회표: {MAP_PATH}")


if __name__ == "__main__":
    main()
