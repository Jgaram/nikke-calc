"""blablalink CDN에서 **인게임 아이콘**을 뽑아 `image/icon/`에 넣는다.

경로 규칙은 프론트엔드 `ICONS_URL`에서 그대로 옮겼다:

    ICONS_URL = ({path, name}) => getIngameResourceUrl(`/icon/${path}/${name}.webp`)

즉 평문 경로가 `/icon/<네임스페이스>/<리소스명>.webp`이고, 실제 URL은 `cdn_path`가
난독화한다. 네임스페이스는 프론트 청크(`icon-*.js`, `equip-icon-*.js`)에서 확인한 것:

| 네임스페이스           | 담긴 것                                              |
|------------------------|------------------------------------------------------|
| `atlas_common_class`   | `icn_class_*` 역할군 · `icn_element_*` 속성 · `icn_burst_0*` |
| `atlas_common_corp`    | `icn_corp_0*` 기업 · `img_logo_*` 기업 로고           |
| `atlas_common_grade`   | `ele_grade_icon_001/002/003` = R·SR·SSR 등급 마크     |
| `favoriteitem`         | `si_favoriteitem_*` 소장품(R·SR)·애장품(SSR)          |

**CDN에 없는 것 둘.** 장비 타일의 오버로드 뱃지와 바탕틀은 게임 CDN이 아니라
블라링크 **프론트 자산**에 있다. 네임스페이스·이름을 570여 조합 찔러도 안 나와서,
니케 상세 화면의 DOM에서 실제 `src`를 읽어 확인했다 (`SITE_ICONS` 참조).

소장품은 무기군별 공용(R·SR)과 캐릭터 전용(SSR)으로 나뉜다. 전용 아이콘의 이름에
캐릭터 리소스 id가 박혀 있어(`si_favoriteitem_c072_00` → 72) 캐릭터로 되짚을 수 있다.

사용:
  python scraper/cdn_icons.py            # 아이콘 + data/favorite_icons.json 갱신
  python scraper/cdn_icons.py --check    # 받지 않고 빠진 것만 보고
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cdn_path  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "image" / "icon"
OUT_JSON = ROOT / "data" / "favorite_icons.json"
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"

FAVORITE_RARE_MAP_PATH = "/equip/favorite_rare_map.json"
FAVORITE_PATH = "/equip/ko/favorite_{fid}.json"
ICON_PATH = "/icon/{ns}/{name}.webp"

# 캐릭터 전용 애장품 아이콘에 박힌 리소스 id. `cdn_fetch.py`의 ICON_RID_RE와 같은 규칙.
CHAR_RID_RE = re.compile(r"_c(\d+)_")
# 공용 소장품은 무기군이 이름에 들어간다 (si_favoriteitem_ar_00 → AR).
WEAPON_RE = re.compile(r"si_favoriteitem_([a-z]+)_\d+$")

# 등급 마크 — 인게임 카드가 쓰는 바로 그 R/SR/SSR 글자다.
# 004는 등급이 아니라 **FAV**(애장품) 마크다. 같은 자리에 있어 함께 받아 둔다.
GRADE_ICONS = {"R": "ele_grade_icon_001", "SR": "ele_grade_icon_002",
               "SSR": "ele_grade_icon_003", "FAV": "ele_grade_icon_004"}


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(cdn_path.url(path), timeout=30) as r:
        return r.read()


def fetch_json(path: str) -> dict:
    return json.loads(fetch(path))


def collect_favorites() -> list[dict]:
    """소장품 전량 → [{tid, grade, icon, name, weapon?, char_rid?}]."""
    rare_map = fetch_json(FAVORITE_RARE_MAP_PATH)
    pairs = [(g, fid) for g in ("R", "SR", "SSR") for fid in rare_map.get(g, [])]

    def one(pair: tuple[str, str]) -> dict:
        grade, fid = pair
        d = fetch_json(FAVORITE_PATH.format(fid=fid))
        icon = d.get("icon_resource_id", "")
        rec = {"tid": str(fid), "grade": grade, "icon": icon,
               "name": d.get("name_localkey", "")}
        if m := CHAR_RID_RE.search(icon):
            rec["char_rid"] = int(m.group(1))
        elif m := WEAPON_RE.search(icon):
            rec["weapon"] = m.group(1).upper()
        return rec

    with cf.ThreadPoolExecutor(10) as ex:
        out = list(ex.map(one, pairs))
    print(f"소장품 {len(out)}종 "
          f"(R {sum(r['grade'] == 'R' for r in out)}"
          f" · SR {sum(r['grade'] == 'SR' for r in out)}"
          f" · SSR {sum(r['grade'] == 'SSR' for r in out)})")
    return out


def rid_to_name() -> dict[int, str]:
    """캐릭터 리소스 id → 캐릭명. 수집본에 이미 있으므로 CDN을 다시 때리지 않는다."""
    if not SCRAPED.exists():
        print("  [WARN] nikke_scraped.json 없음 — 애장품을 캐릭터에 못 붙인다")
        return {}
    d = json.loads(SCRAPED.read_text(encoding="utf-8"))
    out = {}
    for name, v in d.items():
        # 수집본의 `id`가 곧 CDN 리소스 id다 (`cdn_fetch.PORTRAIT_PATH`가 같은 값을 쓴다).
        if isinstance(v, dict) and (r := v.get("id")) is not None:
            out[int(r)] = name
    return out


# 블라링크 프론트 자산 (게임 CDN이 아니다 — 위 문서 참조).
SITE_ASSETS = "https://www.blablalink.com/assets/nikke/version/default/shiftysassets/images/"
SITE_ICONS = {
    "icon-overload.png": "icon-overload.png",           # 오버로드 장비 뱃지(자홍 육각)
    "nikkes/nikke-equip-bg.png": "nikke-equip-bg.png",  # 장비 타일 바탕틀
}


def download_site(*, check: bool) -> int:
    """블라링크 사이트 자산 → image/icon/. CDN 규칙(webp)이 아니라 그냥 PNG다."""
    todo = {k: v for k, v in SITE_ICONS.items() if not (ICON_DIR / v).exists()}
    if check:
        for v in todo.values():
            print(f"  없음 {v}")
        return len(todo)
    got = 0
    for path, fname in todo.items():
        req = urllib.request.Request(SITE_ASSETS + path, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0",
            "Referer": "https://www.blablalink.com/"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                b = r.read()
        except Exception as e:                          # noqa: BLE001
            print(f"  실패 {fname} — {type(e).__name__}")
            continue
        if not b.startswith(bytes([0x89]) + b"PNG"):
            print(f"  실패 {fname} — PNG 아님")
            continue
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        (ICON_DIR / fname).write_bytes(b)
        got += 1
    return got


def download(targets: list[tuple[str, str]], *, check: bool) -> int:
    """[(평문경로, 파일명)] → image/icon/에 저장. 이미 있으면 건너뛴다."""
    todo = [(p, f) for p, f in targets if not (ICON_DIR / f).exists()]
    if check:
        for _, f in todo:
            print(f"  없음 {f}")
        return len(todo)
    if not todo:
        return 0
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    def one(t: tuple[str, str]) -> tuple[str, int | str]:
        path, fname = t
        try:
            b = fetch(path)
        except urllib.error.HTTPError as e:
            return fname, f"HTTP {e.code}"
        except Exception as e:                      # noqa: BLE001
            return fname, type(e).__name__
        # 200이어도 이미지가 아닐 수 있다 — SPA 폴백 HTML을 파일로 저장하면
        # 나중에 깨진 아이콘으로만 드러나 원인을 찾기 어렵다.
        if not b.startswith(b"RIFF") or b[8:12] != b"WEBP":
            return fname, "webp 아님"
        (ICON_DIR / fname).write_bytes(b)
        return fname, len(b)

    with cf.ThreadPoolExecutor(10) as ex:
        res = list(ex.map(one, todo))
    ok = [r for r in res if isinstance(r[1], int)]
    for f, why in res:
        if not isinstance(why, int):
            print(f"  [WARN] {f}: {why}")
    return len(ok)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="받지 않고 빠진 것만 보고")
    args = ap.parse_args()

    favs = collect_favorites()
    names = rid_to_name()

    targets = [(ICON_PATH.format(ns="favoriteitem", name=r["icon"]), f"{r['icon']}.webp")
               for r in favs if r["icon"]]
    targets += [(ICON_PATH.format(ns="atlas_common_grade", name=n), f"{n}.webp")
                for n in GRADE_ICONS.values()]
    n = download(targets, check=args.check)
    print(f"아이콘 {'빠진 것 ' + str(n) if args.check else str(n) + '개 받음'}"
          f" (총 {len(targets)}개 대상)")
    m = download_site(check=args.check)
    print(f"사이트 자산 {'빠진 것 ' + str(m) if args.check else str(m) + '개 받음'}"
          f" (총 {len(SITE_ICONS)}개 대상)")
    if args.check:
        return

    # 브라우저가 쓸 조회표. 두 갈래로 찾을 수 있게 둘 다 담는다:
    #   - 애장품(SSR): 캐릭명으로            (CSV에는 tid가 없다)
    #   - 소장품(R·SR): "등급_무기군"으로    (블라 API에도 CSV에도 이 둘은 늘 있다)
    by_char, by_kind, by_tid = {}, {}, {}
    unmatched = []
    for r in favs:
        f = f"{r['icon']}.webp"
        by_tid[r["tid"]] = f
        if rid := r.get("char_rid"):
            if nm := names.get(rid):
                by_char[nm] = f
            else:
                unmatched.append(r["icon"])
        elif w := r.get("weapon"):
            by_kind[f"{r['grade']}_{w}"] = f
    if unmatched:
        print(f"  [WARN] 캐릭터를 못 찾은 애장품 {len(unmatched)}종: {unmatched[:5]}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({
        "_comment": "인게임 소장품·애장품 아이콘 조회표. python scraper/cdn_icons.py 로 갱신.",
        "_source": "blablalink CDN /equip/favorite_rare_map.json + /icon/favoriteitem/*",
        "by_char": dict(sorted(by_char.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_tid": dict(sorted(by_tid.items())),
        "grade": {g: f"{n}.webp" for g, n in GRADE_ICONS.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"data/favorite_icons.json — 애장품 {len(by_char)}명 · 공용 {len(by_kind)}종")


if __name__ == "__main__":
    main()
