"""Human-facing report output and private per-report work bundles."""

from __future__ import annotations

import datetime as dt
import html
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
REPORTS_DIR = ROOT / "reports"
WORK_DIR = ROOT / ".report-work"


def bundle_dir(slug: str) -> Path:
    if not slug or Path(slug).name != slug or slug in {".", ".."}:
        raise ValueError(f"잘못된 보고서 슬러그: {slug!r}")
    return WORK_DIR / slug


def output_path(slug: str) -> Path:
    return REPORTS_DIR / f"{slug}.html"


def slug_from_spec(source: str | Path) -> str:
    path = Path(source).resolve()
    if path.name == "spec.json" and path.parent.parent.resolve() == WORK_DIR.resolve():
        return path.parent.name
    return path.stem


def spec_path(slug: str) -> Path:
    return bundle_dir(slug) / "spec.json"


def data_path(slug: str) -> Path:
    return bundle_dir(slug) / "result.data.json"


def ref_path(slug: str) -> Path:
    return bundle_dir(slug) / "ref.json"


def prepare(slug: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    work = bundle_dir(slug)
    work.mkdir(parents=True, exist_ok=True)
    return work


def preserve_spec(source: str | Path, slug: str) -> Path:
    """Copy the editable input spec into the report's work bundle."""
    source_path = Path(source).resolve()
    target = spec_path(slug).resolve()
    prepare(slug)
    if source_path != target:
        shutil.copy2(source_path, target)
    return target


def preserve_ref(source: str | Path, slug: str) -> Path:
    source_path = Path(source).resolve()
    target = ref_path(slug).resolve()
    prepare(slug)
    if source_path != target:
        shutil.copy2(source_path, target)
    return target


def write_manifest(
    slug: str,
    *,
    kind: str,
    title: str,
    dependencies: list[str] | None = None,
) -> Path:
    work = prepare(slug)
    path = work / "manifest.json"
    old: dict = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
    manifest = {
        **old,
        "slug": slug,
        "kind": kind,
        "title": title or slug,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "output": f"reports/{slug}.html",
    }
    for key, artifact in (("spec", spec_path(slug)), ("data", data_path(slug)),
                          ("ref", ref_path(slug))):
        if artifact.exists():
            manifest[key] = artifact.relative_to(ROOT).as_posix()
        else:
            manifest.pop(key, None)
    if dependencies is not None:
        manifest["dependencies"] = sorted(set(dependencies))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _entry(path: Path) -> dict:
    slug = path.stem
    manifest_path = bundle_dir(slug) / "manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    stamp = manifest.get("generated_at") or dt.datetime.fromtimestamp(
        path.stat().st_mtime, tz=dt.timezone.utc
    ).astimezone().isoformat(timespec="seconds")
    return {
        "slug": slug,
        "title": manifest.get("title") or slug,
        "kind": manifest.get("kind") or "report-squad",
        "generated_at": stamp,
        "size": path.stat().st_size,
    }


def write_index() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    entries = [_entry(p) for p in REPORTS_DIR.glob("*.html") if p.name != "index.html"]
    entries.sort(key=lambda item: item["generated_at"], reverse=True)
    labels = {
        "report-squad": "스쿼드 비교",
        "report-growth": "육성 효율",
        "enikk": "enikk 대조",
        "optimize": "N덱 최적화",
    }
    cards = []
    for item in entries:
        size = item["size"] / 1024
        size_text = f"{size / 1024:.1f} MB" if size >= 1024 else f"{size:.0f} KB"
        date = item["generated_at"].replace("T", " ")[:16]
        cards.append(
            '<li><a href="{href}"><strong>{title}</strong>'
            '<span>{kind} · {date} · {size}</span>'
            '<code>{slug}</code></a></li>'.format(
                href=html.escape(f"{item['slug']}.html", quote=True),
                title=html.escape(item["title"]),
                kind=html.escape(labels.get(item["kind"], item["kind"])),
                date=html.escape(date),
                size=html.escape(size_text),
                slug=html.escape(item["slug"]),
            )
        )
    empty = "<p class=empty>아직 생성된 보고서가 없습니다.</p>" if not cards else ""
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NIKKE 보고서</title><style>
:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{margin:0;background:#f3f4f6;color:#171717}}
main{{max-width:920px;margin:auto;padding:48px 24px}}h1{{margin:0 0 8px}}p{{color:#666}}ul{{list-style:none;padding:0;display:grid;gap:12px}}
li a{{display:grid;grid-template-columns:1fr auto;gap:5px 20px;padding:18px 20px;border:1px solid #ddd;border-radius:12px;background:#fff;color:inherit;text-decoration:none}}
li a:hover{{border-color:#777}}strong{{font-size:17px}}span,code{{font-size:13px;color:#666}}code{{grid-column:1/-1}}
@media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}p,span,code{{color:#aaa}}li a{{background:#191919;border-color:#333}}}}
</style></head><body><main><h1>NIKKE 보고서</h1><p>최신순 · {len(entries)}개</p>{empty}<ul>{''.join(cards)}</ul></main></body></html>"""
    path = REPORTS_DIR / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
