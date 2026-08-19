"""웹앱 번들 빌더.

`web/src/`의 정적 파일과 계산기 일체를 `web/dist/`로 모은다.
브라우저는 `dist/`만 보면 되고, 계산기 코드·데이터는 **복사본을 만들지 않는다** —
매 빌드마다 정본에서 다시 압축한다 (webapp-roadmap.md §4).

    python web/build.py
    python web/build.py --serve 8765     # 빌드 후 로컬 서버까지

산출물:
    dist/repo.zip     calculator/ + context/spec.py + data/  (Pyodide가 푼다)
    dist/roster.json  캐릭터 메타 (context/roster.py의 collect() 재사용)
    dist/image/       초상화·아이콘 (변경분만 복사)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
DIST = ROOT / "web" / "dist"

sys.path.insert(0, str(ROOT))
from context.roster import collect  # noqa: E402  (경로 주입 후에만 import 가능)

# Pyodide 가상 FS에 풀릴 파일들. context/spec.py는 `_ROOT`를 부모의 부모로 잡으므로
# 압축 안에서도 저장소와 같은 배치를 유지해야 data/를 찾는다.
BUNDLE_GLOBS = ("calculator/*.py", "data/*.json", "data/base_stat_tables/*.json")
BUNDLE_FILES = ("context/spec.py",)


def build_zip() -> tuple[int, int]:
    """계산기 번들을 만든다. 반환: (파일 수, 압축 바이트)."""
    out = DIST / "repo.zip"
    paths: list[Path] = [ROOT / f for f in BUNDLE_FILES]
    for g in BUNDLE_GLOBS:
        paths.extend(sorted(ROOT.glob(g)))

    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit("번들 대상 없음: " + ", ".join(p.name for p in missing))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            z.write(p, p.relative_to(ROOT).as_posix())
    return len(paths), out.stat().st_size


def build_roster() -> int:
    """캐릭터 메타를 JSON으로. 파싱 여부까지 담아 UI가 선택 가능 여부를 판단한다."""
    done, todo = collect()
    chars = [_row(r, True) for r in done] + [_row(r, False) for r in todo]

    (DIST / "roster.json").write_text(
        json.dumps({"generated": date.today().isoformat(), "chars": chars},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(chars)


def _row(rec: dict, parsed: bool) -> dict:
    img = rec["img"]
    return {
        "name": rec["name"],
        "burst": rec["burst"],
        "element": rec["element"],
        "cls": rec["cls"],
        "corp": rec["corp"],
        "weapon": rec["weapon"],
        # portrait()는 "image/<파일>"을 준다. dist에서는 image/가 루트라 접두사를 뗀다.
        "img": img.split("/", 1)[1] if img else None,
        "parsed": parsed,
    }


def copy_tree(src: Path, dst: Path, pattern: str = "*") -> int:
    """변경분만 복사한다 (초상화가 5MB대라 매 빌드 전량 복사는 낭비)."""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob(pattern)):
        if not p.is_file():
            continue
        target = dst / p.name
        if target.exists() and target.stat().st_mtime >= p.stat().st_mtime:
            continue
        shutil.copy2(p, target)
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="웹앱 번들 빌드")
    ap.add_argument("--serve", type=int, metavar="PORT", help="빌드 후 로컬 서버 실행")
    args = ap.parse_args()

    DIST.mkdir(parents=True, exist_ok=True)

    n_src = copy_tree(SRC, DIST)
    n_zip, size = build_zip()
    n_char = build_roster()
    n_img = copy_tree(ROOT / "image", DIST / "image", "*.webp")
    n_icon = copy_tree(ROOT / "image" / "icon", DIST / "image" / "icon", "*.webp")

    print(f"src        {n_src}개 갱신")
    print(f"repo.zip   {n_zip}개 파일 · {size / 1048576:.2f} MB")
    print(f"roster     {n_char}명")
    print(f"image      초상화 {n_img}개 · 아이콘 {n_icon}개 갱신")
    print(f"→ {DIST}")

    if args.serve:
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(DIST), **kw)

            def end_headers(self):  # 개발 중 캐시가 남으면 고친 게 안 보인다
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.serve), Handler) as httpd:
            print(f"\nhttp://localhost:{args.serve}  (같은 Wi-Fi의 폰에서도 접속 가능)")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
