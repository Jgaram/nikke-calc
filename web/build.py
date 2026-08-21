"""웹앱 번들 빌더.

`web/src/`의 정적 파일과 계산기 일체를 `web/dist/`로 모은다.
브라우저는 `dist/`만 보면 되고, 계산기 코드·데이터는 **복사본을 만들지 않는다** —
매 빌드마다 정본에서 다시 압축한다 (webapp-roadmap.md §4).

    python web/build.py
    python web/build.py --serve 8765     # 빌드 후 로컬 서버까지

산출물:
    dist/repo.zip     calculator/ + context/spec.py + data/  (Pyodide가 푼다)
    dist/roster.json  캐릭터 메타 (context/roster.py의 collect() 재사용)
    dist/cost.json    재화 소모량 (data/cost_expected.json 그대로 — 정본은 cost/)
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
# 경로 주입 후에만 import 가능. 로스터 분류축과 코드 상성은 여기서 가져다 쓴다 —
# 웹앱이 같은 표를 다시 적으면 캐릭터가 늘 때마다 두 곳을 고쳐야 한다.
from calculator.base_stat import NO_ITEM  # noqa: E402
from calculator.damage import is_element_match  # noqa: E402
from context.roster import (  # noqa: E402
    BURST_LABEL, BURST_ORDER, CLASS_ICON, CORP_ORDER, ELEMENT_COLOR, ELEMENT_ORDER,
    WEAPON_ORDER, collect,
)
from context.spec import CHAR_DEFAULTS, DEFAULT_CHAR, UNGROWN  # noqa: E402

# 재화 소모량은 `cost.build`가 구운 것을 그대로 실어 보낸다. 육성 탭이 Pyodide를
# 기다리지 않고 비용을 띄워야 해서 JS 쪽에도 있어야 하는데, 웹앱이 표를 다시 적으면
# 정본이 둘로 늘어난다. 그래서 **복사가 아니라 빌드 산출물**로 둔다 (repo.zip과 같은 방식).
COST_SRC = ROOT / "data" / "cost_expected.json"

_TABLES = ROOT / "data" / "base_stat_tables"
_EQUIP_SKILLS: dict = json.loads((_TABLES / "equipment_skills.json").read_text(encoding="utf-8"))
_CUBES: dict = json.loads((_TABLES / "cube.json").read_text(encoding="utf-8"))
_COLLECTION: dict = json.loads((_TABLES / "collection.json").read_text(encoding="utf-8"))
_AFFINITY: dict = json.loads((_TABLES / "affinity.json").read_text(encoding="utf-8"))

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
        json.dumps({
            "generated": date.today().isoformat(),
            "chars": chars,
            "facets": _facets(),
            "elementColor": ELEMENT_COLOR,
            "weak": _weakness(),
            # 육성 탭이 "프로필에 없는 캐릭터"를 화면에도 계산과 같게 보여주려면 이 값이
            # 필요하다. 정본은 context/spec.py라 웹앱이 다시 적지 않고 여기서 실어 보낸다.
            "ungrown": UNGROWN,
            "noItem": NO_ITEM,
            "parts": list(DEFAULT_CHAR["equipment"]),
            "optionLabel": _option_labels(),
            # ── 세부 조정(덱 안에서 한 명씩 손보는 화면)이 쓰는 것들 ───────────
            # 전부 정본에서 그대로 실어 보낸다. 웹앱이 같은 값을 다시 적으면 표가 둘로
            # 갈려, 큐브가 늘거나 기본 스펙이 바뀔 때 한쪽만 따라간다.
            "defaultChar": _default_char(),
            "cubes": _cubes(),
            "collectionStages": _collection_stages(),
            "affinityMax": _affinity_max(),
        }, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(chars)


def build_cost() -> int:
    """`data/cost_expected.json`을 dist로 옮긴다. 없으면 끊는다 — 조용히 빠지면
    육성 탭이 비용 없이 Δ만 내면서 그 사실을 설명하지 못한다."""
    if not COST_SRC.exists():
        raise SystemExit(f"{COST_SRC.name}이 없다 — `python -m cost.build`를 먼저 돌린다.")
    out = DIST / "cost.json"
    shutil.copy2(COST_SRC, out)
    return out.stat().st_size


def _option_labels() -> dict:
    """오버로드 옵션 키 → 짧은 한글 이름. 인게임 문구에서 잘라 낸다.

    `equipment_skills.json`의 `template`("공격력 {0}% 증가")이 정본이라 웹앱이 이름을
    따로 적지 않는다 — 옵션이 늘거나 문구가 바뀌면 여기가 같이 따라간다.
    """
    return {k: str(v["template"]).split(" {0}")[0]
            for k, v in _EQUIP_SKILLS.items() if isinstance(v, dict) and "template" in v}


def _default_char() -> dict:
    """1층 기본 스펙 중 **세부 조정이 건드리는 칸**만. 화면의 출발값이자 `기본값` 표시다.

    통째로 싣지 않는 이유는 조정 대상이 아닌 칸(레벨·버스트 재생 시간 등)이 섞이면
    화면이 "이것도 고칠 수 있나"로 읽히기 때문이다. 정본은 `context/spec.DEFAULT_CHAR`.
    """
    keys = ("breakthrough", "core_enhancement", "affinity", "skill_levels",
            "equip_skills", "cube", "collection_stage", "favorite_stage")
    out = {k: DEFAULT_CHAR[k] for k in keys}
    # 장비는 부위별 dict라 강화 단계만 뽑는다 (`skills`는 오버로드 옵션 쪽 이야기다).
    out["equipment"] = {p: v.get("level") for p, v in DEFAULT_CHAR["equipment"].items()}
    return out


def _cubes() -> dict:
    """큐브 이름 → 고를 수 있는 레벨과 미지원 사유. 스탯은 모든 큐브가 같다(`_stats`).

    `unsupported`가 붙은 큐브는 고유 효과가 계산에 안 들어간다 — 숨기지 않고 그 사실을
    화면에 적는다. 스탯과 우월 코드는 붙으므로 고르는 것 자체는 의미가 있다.
    """
    return {
        name: {
            "levels": sorted((int(k) for k in (v.get("values") or {})), reverse=True),
            "unsupported": v.get("unsupported"),
        }
        for name, v in _CUBES.items()
        if not name.startswith("_") and isinstance(v, dict) and name != "공통"
    }


def _affinity_max() -> int:
    """호감도 상한. 클래스마다 표가 따로지만 길이는 같다 — 아무 하나에서 센다."""
    tables = [v for k, v in _AFFINITY.items() if not k.startswith("_")]
    return max(int(k) for k in tables[0])


def _collection_stages() -> list[str]:
    """고를 수 있는 소장품 단계. 미장착이 맨 앞이다."""
    return [NO_ITEM, *(_COLLECTION.get("_stat_table") or {})]


def _facets() -> dict:
    """풀 필터의 분류축. roster.html과 같은 축·같은 순서를 쓴다."""
    return {
        "burst": [[b, BURST_LABEL[b]] for b in BURST_ORDER],
        "element": [[e, e] for e in ELEMENT_ORDER],
        "corp": [[c, c] for c in CORP_ORDER],
        "weapon": [[w, w] for w in WEAPON_ORDER],
        "cls": [[c, c] for c in CLASS_ICON],
    }


def _weakness() -> dict:
    """랩쳐 코드 → 그 랩쳐에 우월한(약점을 찌르는) 니케 속성."""
    return {
        enemy: nikke
        for enemy in ELEMENT_ORDER
        for nikke in ELEMENT_ORDER
        if is_element_match(nikke, enemy)
    }


def _row(rec: dict, parsed: bool) -> dict:
    img = rec["img"]
    row = {
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
    if (layer := _layer(rec["name"])):
        row["layer"] = layer
    return row


def _layer(name: str) -> dict:
    """캐릭터별 기본 레이어(2층) 중 세부 조정 화면이 보여줄 것. 없으면 빈 dict.

    화면이 "지금 무엇이 걸려 있는가"를 말하려면 레이어를 알아야 한다 — 컨트롤은 여기
    없는 캐릭터가 곧 자동 사격이고(`CONTROL.md §캐릭터별 기본 컨트롤`), 버스트 패턴은
    등록된 후보 자체가 캐릭터마다 다르다. 정본은 `data/char_defaults.json`이다.

    조건부 컨트롤(`_control_rules`)은 스쿼드 전원을 봐야 판정되므로(`spec._when_ok`)
    값을 옮기지 않고 **있다는 사실만** 싣는다. 브라우저가 판정을 다시 구현하면 규칙이
    둘로 갈리고, 계산에 실제로 걸리는 쪽은 언제나 파이썬이다.
    """
    src = CHAR_DEFAULTS.get(name) or {}
    out: dict = {}
    if src.get("control"):
        out["control"] = src["control"]
    if src.get("_control_rules"):
        out["controlRules"] = True
    if src.get("equip_skills"):
        out["equip_skills"] = src["equip_skills"]
    # 버스트 패턴은 이름이 아니라 **값까지** 싣는다. 화면이 고른 패턴을 그대로 계산에
    # 넘기기 때문이다 (`spec.burst_pattern_of`는 러너 쪽 통로다 — §worker.js).
    if src.get("_burst_patterns"):
        out["patterns"] = src["_burst_patterns"]
    if src.get("burst_pattern"):
        out["pattern"] = src["burst_pattern"]
    return out


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
    n_cost = build_cost()
    n_img = copy_tree(ROOT / "image", DIST / "image", "*.webp")
    n_icon = copy_tree(ROOT / "image" / "icon", DIST / "image" / "icon", "*.webp")
    n_icon += copy_tree(ROOT / "image" / "icon", DIST / "image" / "icon", "*.png")

    print(f"src        {n_src}개 갱신")
    print(f"repo.zip   {n_zip}개 파일 · {size / 1048576:.2f} MB")
    print(f"roster     {n_char}명")
    print(f"cost.json  {n_cost / 1024:.1f} KB")
    print(f"image      초상화 {n_img}개 · 아이콘 {n_icon}개 갱신")
    print(f"→ {DIST}")

    if args.serve:
        import http.server

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(DIST), **kw)

            def end_headers(self):  # 개발 중 캐시가 남으면 고친 게 안 보인다
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

        # 스레드 서버여야 한다 — keep-alive 연결 하나가 나머지 요청을 전부 막는다
        # (초상화 200장과 repo.zip을 동시에 받는 화면이다)
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        with http.server.ThreadingHTTPServer(("0.0.0.0", args.serve), Handler) as httpd:
            print(f"\nhttp://localhost:{args.serve}  (같은 Wi-Fi의 폰에서도 접속 가능)")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
