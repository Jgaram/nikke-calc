"""List, index, and safely remove local report artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from report_workspace import (
    REPORTS_DIR, WORK_DIR, bundle_dir, output_path, write_index, write_manifest,
)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


def _adopt_missing() -> None:
    """Rebuild minimal manifests for legacy or manually recovered bundles."""
    if not WORK_DIR.exists():
        return
    for work in WORK_DIR.iterdir():
        if not work.is_dir() or (work / "manifest.json").exists():
            continue
        data_file = work / "result.data.json"
        if not data_file.exists():
            continue
        try:
            payload = json.loads(data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (work / "ref.json").exists():
            kind = "enikk"
        elif "meta" in payload:
            kind = "report-growth"
        elif "solutions" in payload and "candidate_counts" in payload:
            kind = "optimize"
        else:
            kind = "report-squad"
        title = (payload.get("spec") or {}).get("title") or payload.get("title") or work.name
        write_manifest(work.name, kind=kind, title=title)


def _manifests() -> list[dict]:
    _adopt_missing()
    rows = []
    if not WORK_DIR.exists():
        return rows
    for path in sorted(WORK_DIR.glob("*/manifest.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            rows.append({"slug": path.parent.name, "kind": "?", "title": "손상된 manifest"})
    known = {row.get("slug") for row in rows}
    for work in sorted(p for p in WORK_DIR.iterdir() if p.is_dir()):
        if work.name not in known:
            rows.append({"slug": work.name, "kind": "draft", "title": work.name,
                         "draft": True})
    return rows


def _remove(slug: str) -> None:
    out = output_path(slug)
    work = bundle_dir(slug)
    if out.exists():
        out.unlink()
    if work.exists():
        shutil.rmtree(work)


def main() -> None:
    ap = argparse.ArgumentParser(description="로컬 보고서 목록·색인·정리")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="보고서와 작업 묶음 상태를 표시")
    sub.add_parser("reindex", help="reports/index.html을 다시 생성")
    remove = sub.add_parser("remove", help="보고서와 같은 슬러그의 작업 묶음을 함께 삭제")
    remove.add_argument("slug")
    remove.add_argument("--yes", action="store_true", help="실제로 삭제")
    remove.add_argument("--force", action="store_true", help="다른 보고서가 참조해도 삭제")
    prune = sub.add_parser("prune", help="HTML이 없는 고아 작업 묶음을 정리")
    prune.add_argument("--yes", action="store_true", help="실제로 삭제")
    args = ap.parse_args()

    if args.command == "reindex":
        _adopt_missing()
        print(write_index())
        return

    if args.command == "list":
        seen = set()
        for row in _manifests():
            slug = row.get("slug", "?")
            seen.add(slug)
            state = ("초안" if row.get("draft") else
                     ("HTML 있음" if output_path(slug).exists() else "작업 묶음만 있음"))
            print(f"{slug:<36} {row.get('kind','?'):<14} {state}  {row.get('title', slug)}")
        if REPORTS_DIR.exists():
            for path in sorted(REPORTS_DIR.glob("*.html")):
                if path.name != "index.html" and path.stem not in seen:
                    print(f"{path.stem:<36} {'?':<14} HTML만 있음")
        return

    if args.command == "remove":
        targets = [p for p in (output_path(args.slug), bundle_dir(args.slug)) if p.exists()]
        if not targets:
            raise SystemExit(f"없는 보고서: {args.slug}")
        dependents = [row.get("slug") for row in _manifests()
                      if args.slug in (row.get("dependencies") or [])]
        print("삭제 대상:")
        for path in targets:
            print(f"  {path}")
        if dependents:
            print("참조 중인 보고서: " + ", ".join(dependents))
        if not args.yes:
            print("미리보기만 했다. 실제 삭제는 --yes를 붙인다.")
            return
        if dependents and not args.force:
            raise SystemExit("참조 중이라 삭제하지 않았다. 관계를 확인한 뒤 --force를 붙인다.")
        _remove(args.slug)
        write_index()
        return

    manifests = _manifests()
    referenced = {slug for row in manifests for slug in (row.get("dependencies") or [])}
    protected = [row.get("slug", "") for row in manifests
                 if row.get("slug") in referenced and not output_path(row["slug"]).exists()]
    orphans = [row.get("slug", "") for row in manifests
               if row.get("slug") and not row.get("draft")
               and not output_path(row["slug"]).exists()]
    orphans = [slug for slug in orphans if slug not in referenced]
    if protected:
        print("다른 보고서가 참조해 보존한 작업 묶음:")
        for slug in protected:
            print(f"  {bundle_dir(slug)}")
    if not orphans:
        print("고아 작업 묶음 없음")
        return
    print("고아 작업 묶음:")
    for slug in orphans:
        print(f"  {bundle_dir(slug)}")
    if not args.yes:
        print("미리보기만 했다. 실제 삭제는 --yes를 붙인다.")
        return
    for slug in orphans:
        _remove(slug)
    write_index()


if __name__ == "__main__":
    main()
