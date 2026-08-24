"""코스튬(스킨) 표·그림 수집.

    python scraper/cdn_costume.py              # 표 + 초상화 (없는 것만)
    python scraper/cdn_costume.py --full       # 전신 일러까지
    python scraper/cdn_costume.py --force      # 이미 있는 것도 다시

블라 프로필 API가 캐릭터마다 **장착 중인 코스튬 id**를 준다
(`characters[].costume_id` = `details[].costume_tid`, 0이면 기본 코스튬).
그 id를 그림으로 바꾸려면 코스튬 표가 있어야 하는데, 표는 CDN roledata 안에 있다:

    /roledata/{리소스id}-v2-ko.json → character_costume_list
      { "id": 10005, "resource_id": 10, "costume_index": 2,
        "costume_grade_id": "Event", "costume_name_locale": "클래식 바캉스", ... }

`id`가 프로필이 주는 그 값이고, `costume_index`가 그림 파일의 번호다. 기본 코스튬은
목록에 없다(index 0이 곧 기본). 그림 경로는 기본 코스튬과 같은 틀에 번호만 바뀐다:

    /character/mi/mi_c###_##_s.webp   초상화 256×512  → image/costume/mi/
    /character/full/c###_##.webp      전신 일러       → image/costume/full/
    /character/si/si_c###_##_s.webp   얼굴 카드 68×68 → image/face/ (cdn_face.py가 이미 모은다)

**전신은 기본이 꺼져 있다.** 한 장이 160KB대라 코스튬 전체면 30MB 가까이 붙는데,
배포 묶음이 그만큼 무거워진다. 초상화(장당 25KB대)만으로 편성·목록 화면은 전부
스킨으로 바뀌므로, 전신은 필요할 때 `--full`로 따로 받는다.

리소스 번호는 `data/face_index.json`(cdn_face.py가 CDN을 직접 훑어 만든다)에서 온다 —
우리가 아는 199명에 갇히지 않기 위해서다.

산출물:
  data/costume_index.json          리소스id → {코스튬id: 번호·이름·등급}
  image/costume/mi/mi_c###_##.webp 스킨 초상화
  image/costume/full/c###_##.webp  스킨 전신 (--full)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROLE = "/roledata/{rid}-v2-ko.json"
MI = "/character/mi/mi_c{rid:03d}_{cos:02d}_s.webp"
FULL = "/character/full/c{rid:03d}_{cos:02d}.webp"

FACE_INDEX = os.path.join(ROOT, "data", "face_index.json")
OUT_INDEX = os.path.join(ROOT, "data", "costume_index.json")
MI_DIR = os.path.join(ROOT, "image", "costume", "mi")
FULL_DIR = os.path.join(ROOT, "image", "costume", "full")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36"}


def get(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return r.read()
    except Exception:                          # noqa: BLE001  없는 코스튬이 훨씬 많다
        return None


def get_json(path: str):
    b = get(path)
    try:
        return json.loads(b) if b else None
    except Exception:                          # noqa: BLE001
        return None


def get_image(path: str) -> bytes | None:
    """200이어도 이미지가 아닐 수 있다 — SPA 폴백을 파일로 저장하면 나중에 깨진
    아이콘으로만 드러나 원인을 찾기 어렵다(cdn_face.py와 같은 이유)."""
    b = get(path)
    return b if b and b.startswith(b"RIFF") and b[8:12] == b"WEBP" else None


def resource_ids() -> list[int]:
    if not os.path.exists(FACE_INDEX):
        raise SystemExit(f"{FACE_INDEX}가 없다. 먼저 `python scraper/cdn_face.py`를 돌려라 "
                         f"— 리소스 번호 목록이 거기서 온다.")
    faces = (json.load(open(FACE_INDEX, encoding="utf-8")) or {}).get("faces") or {}
    return sorted({v["rid"] for v in faces.values()})


def _add_bbox(index: dict) -> None:
    """전신 일러가 실제로 그려진 범위를 재서 표에 `fbb`로 넣는다.

    Pillow가 없으면 조용히 건너뛴다 — 그림 수집 자체는 표준 라이브러리로 돌아가고,
    경계가 없으면 화면이 정사각형 기준으로 맞춘다(`cdn_full.py`와 같은 규칙).
    """
    if not os.path.isdir(FULL_DIR):
        return
    try:
        from PIL import Image
    except ImportError:
        print("[i] Pillow 없음 — 전신 알파 경계는 건너뛴다")
        return
    n = 0
    for rid, cs in index.items():
        for c in cs.values():
            f = os.path.join(FULL_DIR, f"c{int(rid):03d}_{c['cos']:02d}.webp")
            if not os.path.exists(f):
                continue
            im = Image.open(f)
            bb = im.getchannel("A").getbbox() if im.mode == "RGBA" else None
            c["fbb"] = list(bb) if bb else [0, 0, im.width, im.height]
            n += 1
    print(f"[+] 전신 알파 경계 {n}장")


def main() -> None:
    ap = argparse.ArgumentParser(description="코스튬 표·그림 수집")
    ap.add_argument("--full", action="store_true", help="전신 일러까지 받는다 (30MB대)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    os.makedirs(MI_DIR, exist_ok=True)
    if args.full:
        os.makedirs(FULL_DIR, exist_ok=True)

    rids = resource_ids()
    print(f"[+] 리소스 {len(rids)}개의 코스튬 표를 읽는 중…")
    with cf.ThreadPoolExecutor(12) as ex:
        role = dict(zip(rids, ex.map(lambda r: get_json(ROLE.format(rid=r)), rids)))

    # 표. `is_hidden`은 도감에서 감춘 것뿐이라 걸러내지 않는다 — 장착 중이면 그려야 한다.
    index: dict[str, dict] = {}
    for rid in rids:
        lst = (role.get(rid) or {}).get("character_costume_list") or []
        for c in lst:
            cid, cos = c.get("id"), c.get("costume_index")
            if not cid or cos in (None, 0):
                continue                       # 0은 기본 코스튬이라 표에 담을 게 없다
            index.setdefault(str(rid), {})[str(cid)] = {
                "cos": int(cos),
                "name": c.get("costume_name_locale") or "",
                "grade": c.get("costume_grade_id") or "",
            }
    n_cos = sum(len(v) for v in index.values())
    print(f"[+] 코스튬 {n_cos}종 / {len(index)}명")

    # 그림
    def fetch(spec):
        d, tmpl, rid, cos = spec
        fname = os.path.basename(tmpl.format(rid=rid, cos=cos)).replace("_s.webp", ".webp")
        dest = os.path.join(d, fname)
        if os.path.exists(dest) and not args.force:
            return fname, 0
        data = get_image(tmpl.format(rid=rid, cos=cos))
        if not data:
            return None
        with open(dest, "wb") as f:
            f.write(data)
        return fname, len(data)

    jobs = [(MI_DIR, MI, int(rid), v["cos"])
            for rid, cs in index.items() for v in cs.values()]
    if args.full:
        jobs += [(FULL_DIR, FULL, int(rid), v["cos"])
                 for rid, cs in index.items() for v in cs.values()]
    got = skip = miss = 0
    with cf.ThreadPoolExecutor(16) as ex:
        for r in ex.map(fetch, jobs):
            if not r:
                miss += 1
            elif r[1]:
                got += 1
            else:
                skip += 1

    # 전신 일러의 알파 경계 — 기본 코스튬과 같은 이유다(`cdn_full.py write_bbox`).
    # 2048² 정사각형 안에서 캐릭터가 앉은 자리가 코스튬마다 또 달라서, 기본 코스튬의
    # 경계를 그대로 쓰면 스킨만 발이 잘리거나 붕 뜬다.
    _add_bbox(index)

    with open(OUT_INDEX, "w", encoding="utf-8") as f:
        json.dump({"_comment": "리소스id → {코스튬id: {cos(그림 번호), name, grade}}. "
                               "코스튬id는 블라 프로필의 costume_tid와 같은 값이다. "
                               "python scraper/cdn_costume.py 로 갱신.",
                   "costumes": index}, f, ensure_ascii=False, indent=1)

    def size(d):
        return (sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
                if os.path.isdir(d) else 0)
    print(f"[+] 그림 새로 {got}장 · 건너뜀 {skip}장 · 없음 {miss}장 "
          f"(초상화 {size(MI_DIR) / 1048576:.1f} MB"
          + (f" · 전신 {size(FULL_DIR) / 1048576:.1f} MB" if args.full else "") + ")")
    print(f"[+] 조회표: {OUT_INDEX}")


if __name__ == "__main__":
    main()
