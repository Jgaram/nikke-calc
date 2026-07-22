"""문서-데이터 정합 린터 (Claude 전용 유지보수 도구).

`data/parsed_skills.json`(실데이터)과 문서(IMPL-STATUS.md 마스터 테이블 · PARSING.md §12
완료 목록)의 정합을 검사한다. calculator 로직이 아니라 **문서 관리 도구**다.

검사 항목:
  A. parsed_skills.json에 쓰인 모든 키(stat/timing/condition/target)가 IMPL-STATUS
     마스터 테이블에 존재하는가 (미등록 키 = 문서 누락)
  B. PARSING-CHARS.md '현황 목록 § 완료' ↔ parsed_skills.json 캐릭터 키 일치 (유령/누락 항목)

키 매칭은 첫 콜론 이전 prefix 기준 (예: `hit_count:다탄두:3` ↔ 문서 `hit_count:N`).

사용:
  python -m context.doclint            # 정합 검사. 불일치 시 exit 1
  python -m context.doclint --usage    # + 키별 사용 캐릭터 수 (one-off 식별용)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "parsed_skills.json"
NIKKE = ROOT / "data" / "parsed_nikke.json"
IMPL = ROOT / "context" / "IMPL-STATUS.md"
CHARS = ROOT / "context" / "PARSING-CHARS.md"  # 현황 목록(완료/예정) 정본

_BACKTICK = re.compile(r"`([^`]+)`")


def prefix(key: str) -> str:
    """키를 첫 콜론 이전 prefix로 정규화. 따옴표·공백 제거."""
    key = key.strip().strip('"').strip("'").strip()
    return key.split(":", 1)[0]


# ── 실데이터: parsed_skills.json에서 실제 사용된 키 수집 ──────────────────

def load_used() -> tuple[dict[str, dict[str, set[str]]], list[str]]:
    """반환: used[category][prefix] = {그 키를 쓴 캐릭터 집합}, 캐릭터 목록."""
    data = json.loads(SKILLS.read_text(encoding="utf-8"))
    used: dict[str, dict[str, set[str]]] = {
        c: defaultdict(set) for c in ("stat", "timing", "condition", "target")
    }
    chars = [c for c in data if not c.startswith("test_")]
    for char in chars:
        for eff in data[char]:
            if isinstance(eff.get("stat"), str):
                used["stat"][prefix(eff["stat"])].add(char)
            tgt = eff.get("target")
            if isinstance(tgt, str):
                used["target"][prefix(tgt)].add(char)
            trig = eff.get("trigger", {})
            for t in trig.get("timing", []) or []:
                if isinstance(t, str):
                    used["timing"][prefix(t)].add(char)
            for c in trig.get("condition", []) or []:
                if isinstance(c, str):
                    used["condition"][prefix(c)].add(char)
    return used, chars


# ── 문서: IMPL-STATUS 마스터 테이블에서 등록된 키 수집 ─────────────────────

def load_documented() -> dict[str, set[str]]:
    """카테고리별 문서 등록 키 prefix 집합."""
    doc: dict[str, set[str]] = {c: set() for c in ("stat", "timing", "condition", "target")}
    cat: str | None = None
    for line in IMPL.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## stat 마스터 테이블"):
            cat = "stat"
        elif s.startswith("## trigger/condition 마스터 테이블"):
            cat = None  # ### timing / ### condition을 기다림
        elif s == "### timing":
            cat = "timing"
        elif s == "### condition":
            cat = "condition"
        elif s.startswith("## target 마스터 테이블"):
            cat = "target"
        elif s.startswith("## "):  # 그 외 상위 섹션(빠른 참조 등)은 수집 중단
            cat = None
        elif cat and s.startswith("|") and not s.startswith("|--") and not set(s) <= {"|", "-", " ", ":"}:
            first_cell = s.split("|")[1]  # 첫 컬럼 = 키
            for tok in _BACKTICK.findall(first_cell):
                doc[cat].add(prefix(tok))
    return doc


# ── 문서: PARSING-CHARS.md 현황 목록 § 완료 ──────────────────────────────

def load_roster_done() -> list[str]:
    names: list[str] = []
    in_done = False
    for line in CHARS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("### 완료"):
            in_done = True
            continue
        if in_done:
            if s.startswith("#"):  # 다음 소절(### 진행 중 등)에서 종료
                break
            if s:
                names.append(s)
    return names


def main() -> int:
    used, chars = load_used()
    doc = load_documented()
    done = load_roster_done()

    fail = False

    # 하드코딩 캐릭터명 target은 마스터의 `[캐릭터명]` 패턴에 해당 → 미등록으로 보지 않음
    nikke_names = set(json.loads(NIKKE.read_text(encoding="utf-8")))

    # 검사 A: 미등록 키
    print("=== A. 미등록 키 (parsed_skills.json에 있으나 IMPL-STATUS 마스터에 없음) ===")
    any_unknown = False
    for cat in ("stat", "timing", "condition", "target"):
        unknown = sorted(
            k for k in used[cat]
            if k not in doc[cat] and not (cat == "target" and k in nikke_names)
        )
        if unknown:
            fail = any_unknown = True
            for k in unknown:
                owners = sorted(used[cat][k])
                print(f"  [{cat}] {k}  ← {', '.join(owners)}")
    if not any_unknown:
        print("  (없음)")

    # 검사 B: 로스터 정합
    print("\n=== B. 로스터 정합 (PARSING-CHARS 현황§완료 ↔ parsed_skills.json) ===")
    done_set, char_set = set(done), set(chars)
    phantom = sorted(done_set - char_set)   # 완료 목록엔 있으나 JSON엔 없음
    missing = sorted(char_set - done_set)   # JSON엔 있으나 완료 목록엔 없음
    if phantom:
        fail = True
        print("  유령(완료 목록엔 있으나 JSON 없음):", ", ".join(phantom))
    if missing:
        fail = True
        print("  누락(JSON엔 있으나 완료 목록 없음):", ", ".join(missing))
    if not phantom and not missing:
        print("  (일치)")

    if "--usage" in sys.argv:
        print("\n=== 키별 사용 캐릭터 수 (one-off = 1명 전용) ===")
        for cat in ("stat", "timing", "condition", "target"):
            items = sorted(used[cat].items(), key=lambda kv: (-len(kv[1]), kv[0]))
            one_off = [k for k, v in items if len(v) == 1]
            print(f"\n[{cat}] 총 {len(items)}종 / one-off {len(one_off)}종")
            for k, v in items:
                mark = "  ← one-off: " + next(iter(v)) if len(v) == 1 else ""
                print(f"  {len(v):2d}  {k}{mark}")

    print(f"\n캐릭터 {len(chars)}명 · 완료목록 {len(done)}명 · "
          f"결과: {'FAIL' if fail else 'OK'}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
