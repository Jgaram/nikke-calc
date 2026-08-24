"""전신 일러 수집 — 전투력 계산기의 인게임풍 화면용.

    python scraper/cdn_full.py            # 없는 것만 받는다
    python scraper/cdn_full.py --force    # 전부 다시

blablalink CDN `/character/full/c{리소스id:03d}_{코스튬:02d}.webp` (기본 코스튬 00).
`image/full/c###.webp`로 저장하고 `web/build.py`가 dist로 복사한다. 1장 ~140KB.

초상화(`cdn_fetch.py`)와 같은 CDN·같은 난독화 규칙을 쓴다. 다른 점은 크기뿐이라
파일을 따로 두고, 없으면 UI가 초상화로 물러난다(빌드를 세우지 않는다).

받은 뒤 **알파 경계**를 재서 `data/full_bbox.json`에 남긴다. 원본은 2048² 정사각형인데
캐릭터가 앉은 자리가 제각각이라(아래 여백 0~645px 실측) 한 값으로 잘라 내면 누구는
발이 잘리고 누구는 붕 뜬다. 화면이 이 경계로 정확히 맞춘다. Pillow가 없으면 이 단계만
건너뛴다 — 그림 수집 자체는 표준 라이브러리로 돌아간다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "image", "full")
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PATH = "/character/full/c{rid:03d}_00.webp"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36"}


def main() -> None:
    ap = argparse.ArgumentParser(description="니케 전신 일러 수집")
    ap.add_argument("--force", action="store_true", help="이미 있는 것도 다시 받는다")
    args = ap.parse_args()

    maps_path = os.path.join(ROOT, "web", "dist", "profile_maps.json")
    if not os.path.exists(maps_path):
        sys.exit("[!] web/dist/profile_maps.json 없음 — 먼저 python web/build.py")
    res_name = json.load(open(maps_path, encoding="utf-8"))["res_name"]

    os.makedirs(OUT_DIR, exist_ok=True)
    got = skip = miss = 0
    for rid_s in sorted(res_name, key=lambda x: int(x)):
        rid = int(rid_s)
        dest = os.path.join(OUT_DIR, f"c{rid:03d}.webp")
        if os.path.exists(dest) and not args.force:
            skip += 1
            continue
        url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(PATH.format(rid=rid))
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
        except urllib.error.HTTPError:
            miss += 1              # 일러가 없는 리소스도 있다 — UI가 초상화로 물러난다
            continue
        except Exception as e:     # noqa: BLE001
            print(f"  [!] {rid_s} {res_name[rid_s]}: {e}")
            miss += 1
            continue
        with open(dest, "wb") as f:
            f.write(data)
        got += 1
        if got % 25 == 0:
            print(f"  … {got}장")
    size = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR))
    print(f"[+] 새로 {got}장 · 건너뜀 {skip}장 · 없음 {miss}장 "
          f"→ {OUT_DIR} ({size / 1048576:.1f} MB)")
    write_bbox()


def write_bbox() -> None:
    """그림이 실제로 있는 범위(알파 경계)를 재서 조회표로 남긴다."""
    try:
        from PIL import Image
    except ImportError:
        print("[i] Pillow 없음 — 알파 경계는 건너뛴다 (화면이 정사각형 기준으로 맞춘다)")
        return
    out = {}
    for name in sorted(os.listdir(OUT_DIR)):
        if not name.endswith(".webp"):
            continue
        im = Image.open(os.path.join(OUT_DIR, name))
        bb = im.getchannel("A").getbbox() if im.mode == "RGBA" else None
        out[os.path.splitext(name)[0]] = list(bb) if bb else [0, 0, im.width, im.height]
    dest = os.path.join(ROOT, "data", "full_bbox.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"_comment": "전신 일러의 알파 경계 [x0,y0,x1,y1]. "
                               "python scraper/cdn_full.py 로 갱신.", "bbox": out},
                  f, ensure_ascii=False, indent=1)
    print(f"[+] 알파 경계 {len(out)}장 → {dest}")


if __name__ == "__main__":
    main()
