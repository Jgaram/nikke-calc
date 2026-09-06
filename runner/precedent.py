"""스킬 문구 선례 조회 — 같은 문구를 앞서 어떻게 파싱했는가.

`scraper/nikke_scraped.json`의 원문을 `■` clause 단위로 색인해 두고, 질의 문구와
가장 가까운 clause를 찾아 **그 clause에서 나온** `data/parsed_skills.json` 효과를
같이 보여준다. clause↔효과 연결은 template의 `{N}` 자리와 레벨 10 값으로 잡는다 —
이름·순서 추측이 아니라 값이 일치하는 항목만 연결하므로 틀린 짝을 만들지 않는다.

**파싱이 끝난 캐릭터만 선례다.** 아직 파싱 전인 캐릭터의 원문은 대조 대상이 아니라
색인하지 않는다. 같은 문구를 여러 명이 서로 다르게 파싱했으면 `선례 불일치`로 표시한다 —
전수조사가 반복해 잡아낸 결함 계열이라 조회 시점에 드러나는 편이 낫다.

**한계**: 이 도구는 *문구 → 키 매핑*에만 쓴다. 동일 timing 내 **효과 실행 순서**는
그 캐릭터 고유의 조합이라 선례가 답을 주지 않는다 — 그쪽은 시나리오 `## 해석 선언`의
몫이고, 여기서 답이 나왔다고 넘겨짚으면 조용히 틀린다.

사용:
  python -m runner.precedent "20회 피격 시 자신에게 [공격력 {0}% ▲]"
  python -m runner.precedent --file clause.txt        # 여러 줄 clause는 파일이나 stdin으로
  python -m runner.precedent --key max_ammo_pct       # 역방향: 이 키를 낳은 문구들
  python -m runner.precedent "..." --limit 10 --exclude "라피 : 레드 후드"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"
PREVIEW = ROOT / "scraper" / "preview_skills.json"
SKILLS = ROOT / "data" / "parsed_skills.json"

# 파싱 정본에 들어 있는 회귀 테스트용 더미
TEST_PREFIX = "test_"

_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_WS = re.compile(r"\s+")
# 키 끝의 수치 꼬리 (`enemies_lowest_hp:3` · `every:15.0s` · `sequential_damage:10`)
_TAIL = re.compile(r":\d+(?:\.\d+)?s?$")
# clause 자체가 갖는 트리거 문구 (「… 시 대상에게」)
_TRIGGER_HEAD = re.compile(r"시\s")


def norm(text: str) -> str:
    """수치 차이를 지우고 문구 골격만 남긴다.

    `{0}`도 `20`도 전부 `#`이 된다 — 「20회 피격 시」와 「10회 피격 시」는 같은
    timing 키에 횟수만 다른 것이므로 선례로 붙는 편이 맞다.
    """
    t = _PLACEHOLDER.sub("#", text)
    t = _NUM.sub("#", t)
    t = t.replace("■", " ")
    return _WS.sub(" ", t).strip()


def split_clauses(template: str) -> list[str]:
    """`■` 블록 단위로 자른다. 선행 `■`는 원문 그대로 보이도록 유지."""
    return [f"■ {p.strip()}" for p in template.split("■") if p.strip()]


@dataclass
class Clause:
    char: str
    source: str                # 스킬1 / 스킬2 / 스킬3
    favorite: int | None       # 애장품 단계 (기본 판본이면 None)
    skill_name: str
    text: str
    key: str                   # 정규화 문구 (그룹·비교의 단위)
    lv10: dict[int, str]       # 이 clause가 쓴 `{N}` 자리 → 레벨 10 값
    preview: bool

    @property
    def label(self) -> str:
        slot = self.source if self.favorite is None else f"{self.source}·애장품{self.favorite}"
        mark = " (프리뷰)" if self.preview else ""
        return f"{self.char}  {slot} 「{self.skill_name}」{mark}"


def _add_template(out: list[Clause], char: str, source: str, favorite: int | None,
                  skill_name: str, blk: dict, preview: bool) -> None:
    lv10 = (blk.get("values") or {}).get("10") or []
    for text in split_clauses(blk.get("template") or ""):
        slots = sorted({int(i) for i in _PLACEHOLDER.findall(text)})
        vals = {i: lv10[i] for i in slots if i < len(lv10)}
        out.append(Clause(char, source, favorite, skill_name, text, norm(text), vals, preview))


def _add_char(out: list[Clause], char: str, entry: dict, preview: bool) -> None:
    for n, (name, blk) in enumerate((entry.get("스킬") or {}).items(), 1):
        _add_template(out, char, f"스킬{n}", None, name, blk, preview)
    for step in (entry.get("애장품") or {}).get("단계별") or []:
        slot = step.get("교체슬롯")
        if slot:
            _add_template(out, char, f"스킬{slot}", step.get("단계"),
                          step.get("스킬명", ""), step, preview)


def load() -> tuple[list[Clause], dict[str, list[dict]]]:
    """색인과 파싱 정본을 함께 반환. 파싱된 캐릭터의 원문만 색인한다."""
    parsed = json.loads(SKILLS.read_text(encoding="utf-8"))
    parsed = {c: e for c, e in parsed.items() if not c.startswith(TEST_PREFIX)}
    clauses: list[Clause] = []

    scraped = json.loads(SCRAPED.read_text(encoding="utf-8"))
    for char, entry in scraped.items():
        if char in parsed and isinstance(entry, dict):
            _add_char(clauses, char, entry, preview=False)
    seen = {c.char for c in clauses}

    if PREVIEW.exists():
        for char, entry in json.loads(PREVIEW.read_text(encoding="utf-8")).items():
            if char in parsed and char not in seen and isinstance(entry, dict):
                _add_char(clauses, char, entry, preview=True)

    return clauses, parsed


# ── clause ↔ 효과 연결 ─────────────────────────────────────────────────────

LINKED, AMBIGUOUS, SLOT = "linked", "ambiguous", "slot"


def _num(raw) -> float | None:
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return None


def _eff_values(eff: dict) -> set[float]:
    lv10 = (eff.get("values") or {}).get("10") if isinstance(eff.get("values"), dict) else None
    return {v for v in (_num(lv10), _num(eff.get("fixed_value"))) if v is not None}


def _want(cl: Clause) -> set[float]:
    return {v for v in (_num(x) for x in cl.lv10.values()) if v is not None}


def slot_map(clauses: list[Clause]) -> dict[tuple, list[Clause]]:
    """같은 판본·같은 슬롯의 clause끼리 묶는다 — 값 중복 판정에 쓴다."""
    out: dict[tuple, list[Clause]] = {}
    for cl in clauses:
        out.setdefault((cl.char, cl.source, cl.favorite), []).append(cl)
    return out


def slot_effects(parsed: dict[str, list[dict]], cl: Clause) -> list[dict]:
    """같은 판본·같은 슬롯의 효과 전체."""
    return [e for e in parsed.get(cl.char, [])
            if e.get("source") == cl.source and e.get("favorite") == cl.favorite]


def link(parsed: dict[str, list[dict]], cl: Clause,
         siblings: list[Clause] = ()) -> tuple[list[dict], str]:
    """이 clause에서 나온 효과. 반환: (효과 목록, 연결 상태).

    `{N}` 자리의 레벨 10 값과 일치하는 효과만 연결한다.
    - `LINKED` — 이 clause에서 나왔다고 단정할 수 있다.
    - `AMBIGUOUS` — 같은 슬롯의 다른 clause가 같은 값을 쓴다(예: 율리아 `클라이맥스`는
      `{0}`·`{1}`이 둘 다 544.5). 값만으로는 못 가르므로 단정하지 않는다.
    - `SLOT` — 자리가 없는 clause(`[도발]` 같은 고정 블록)나 값이 변환된 효과
      (`배율 N%▼` 등)라 연결 자체가 안 됐다. 슬롯 전체를 돌려준다.
    """
    want = _want(cl)
    slot = slot_effects(parsed, cl)
    if want:
        hit = [e for e in slot if _eff_values(e) & want]
        if hit:
            other: set[float] = set()
            for sib in siblings:
                if sib is not cl:
                    other |= _want(sib)
            shared = want & other
            return hit, (AMBIGUOUS if any(_eff_values(e) & shared for e in hit) else LINKED)
    return slot, SLOT


def _skeleton(key) -> str:
    """`enemies_lowest_hp:3` → `enemies_lowest_hp`.

    문구 정규화가 숫자를 `#`으로 지우므로 키도 같은 수준으로 맞춘다 — 그러지 않으면
    「10회 공격 시 적 3기」와 「5회 공격 시 적 1기」가 같은 문구로 묶인 채 골격만
    다르다고 나온다.
    """
    return _TAIL.sub("", str(key))


def carries_trigger(text: str) -> bool:
    """clause 자체가 트리거 문구를 갖는가.

    `■ 아군 전체에게`처럼 대상만 있는 clause는 timing이 슬롯(쿨타임·버스트 사용)에서
    온다. 그런 clause끼리 timing이 다른 것은 정상이므로 골격 비교에서 뺀다.
    """
    return bool(_TRIGGER_HEAD.search(text.split("[", 1)[0]))


def signature(effs: list[dict], *, with_trigger: bool = True) -> tuple:
    """선례끼리 비교할 파싱 골격. 값·이름·수치 꼬리는 빼고 키만 본다."""
    out = []
    for e in effs:
        trg = e.get("trigger") or {}
        item = [_skeleton(e.get("stat")), _skeleton(e.get("target"))]
        if with_trigger:
            item.append(tuple(_skeleton(t) for t in trg.get("timing") or []))
            item.append(tuple(_skeleton(c) for c in trg.get("condition") or []))
        out.append(tuple(item))
    return tuple(sorted(out))


# ── 출력 ───────────────────────────────────────────────────────────────────

def fmt_effect(eff: dict) -> str:
    trg = eff.get("trigger") or {}
    bits = [f"{eff.get('type', '?')}",
            f"stat={eff.get('stat', '-')}",
            f"target={eff.get('target', '-')}",
            f"timing={'+'.join(trg.get('timing') or []) or '-'}"]
    if trg.get("condition"):
        bits.append(f"cond={'+'.join(trg['condition'])}")
    if eff.get("duration") is not None:
        bits.append(f"dur={eff['duration']}")
    if eff.get("max_stack"):
        bits.append(f"stack={eff['max_stack']}")
    if eff.get("polarity"):
        bits.append(str(eff["polarity"]))
    return "  ".join(bits)


def show_clause(text: str, indent: str = "    ") -> None:
    for line in text.splitlines():
        print(f"{indent}{line}")


NOTE = {
    AMBIGUOUS: "        값 중복 — 같은 슬롯의 다른 clause와 레벨 10 값이 같다. 아래는 후보:",
    SLOT: "        슬롯 전체 — 값으로 clause를 특정하지 못했다:",
}


def show_member(cl: Clause, effs: list[dict], status: str, slot_total: int = 0) -> None:
    print(f"    · {cl.label}")
    if not effs:
        print("        (이 슬롯에 파싱된 효과가 없다 — 누락 후보)")
        return
    if status in NOTE:
        print(NOTE[status])
    for eff in effs:
        print(f"        {eff.get('name', '?')}  ·  {fmt_effect(eff)}")
    # 고정값 블록(`[최대 장탄 수 50.66% ▼]`)이나 변환된 값은 `{N}`으로 못 잡는다.
    # 그 슬롯에 더 있다는 사실만 알려 준다 — 누락으로 오해하지 않도록.
    if status == LINKED and slot_total > len(effs):
        print(f"        (같은 슬롯에 값 미연결 효과 {slot_total - len(effs)}개 더 있다)")


# ── 조회 ───────────────────────────────────────────────────────────────────

def score(q: str, c: str) -> tuple[float, str]:
    if not q or not c:
        return 0.0, ""
    if q in c:
        return 1.0, "포함"
    if c in q:
        return 0.99, "피포함"
    return SequenceMatcher(None, q, c).ratio(), ""


def search(query: str, clauses: list[Clause], parsed: dict, *,
           limit: int, per: int, minimum: float, exclude: set[str]) -> None:
    q = norm(query)
    slots = slot_map(clauses)
    scored = []
    for cl in clauses:
        if cl.char in exclude:
            continue
        s, why = score(q, cl.key)
        if s >= minimum:
            scored.append((s, why, cl))
    scored.sort(key=lambda t: (-t[0], len(t[2].key), t[2].char))

    groups: dict[str, list[tuple[float, str, Clause]]] = {}
    for item in scored:
        groups.setdefault(item[2].key, []).append(item)

    print(f"질의: {' / '.join(query.strip().splitlines())}")
    print(f"색인: 파싱 완료 {len(parsed)}명 · clause {len(clauses)}개")
    if not groups:
        print(f"\n유사도 {minimum} 이상인 선례 없음 — 이 문구는 아직 파싱된 적이 없다.")
        print("PARSING.md Step 4 ①②③(기존 키 재활용 → 새 키 등록 → 질문) 순서로 처리한다.")
        return
    print(f"\n유사도 {minimum} 이상 {len(groups)}종 · 상위 {min(limit, len(groups))}종 표시\n")

    for rank, (_, items) in enumerate(list(groups.items())[:limit], 1):
        s, why, head = items[0]
        tag = f" ({why})" if why else ""
        print(f"[{rank}] 일치 {s:.2f}{tag} · {len(items)}건")
        show_clause(head.text)

        # 골격 비교는 **캐릭터 간**에만 한다 — 같은 캐릭터의 기본 판본과 애장품 판본이
        # 다른 것은 애장품 강화지 불일치가 아니다.
        sigs: dict[str, tuple] = {}
        shown: list[str] = []
        rest: list[str] = []
        for _, _, cl in items:
            effs, status = link(parsed, cl, slots[(cl.char, cl.source, cl.favorite)])
            if status == LINKED and cl.char not in sigs:
                sigs[cl.char] = signature(effs, with_trigger=carries_trigger(cl.text))
            if len(shown) < per and cl.char not in shown:
                shown.append(cl.char)
                show_member(cl, effs, status, len(slot_effects(parsed, cl)))
            else:
                rest.append(cl.label)
        if rest:
            print(f"    · 나머지 {len(rest)}건: {' · '.join(rest)}")
        if len(set(sigs.values())) > 1:
            print("    ⚠ 선례 불일치 — 같은 문구인데 파싱 골격이 다르다. 어느 쪽이 옳은지 확인할 것.")
        print()


def by_key(key: str, clauses: list[Clause], parsed: dict, *, limit: int) -> None:
    """역방향 — 이 파싱 키를 낳은 문구들."""
    slots = slot_map(clauses)
    groups: dict[str, tuple[str, list[str]]] = {}
    for cl in clauses:
        effs, status = link(parsed, cl, slots[(cl.char, cl.source, cl.favorite)])
        if status != LINKED:
            continue
        for eff in effs:
            trg = eff.get("trigger") or {}
            fields = [str(eff.get("stat")), str(eff.get("target")),
                      *(trg.get("timing") or []), *(trg.get("condition") or [])]
            if any(key in f for f in fields):
                _, members = groups.setdefault(cl.key, (cl.text, []))
                if cl.char not in members:
                    members.append(cl.char)
                break

    print(f"키 `{key}`를 낳은 문구: {len(groups)}종")
    if not groups:
        print("선례 없음 — 새 키이거나, 값 연결이 안 되는 자리에서만 쓰였다.")
        return
    print()
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1][1]))
    for rank, (_, (text, members)) in enumerate(ordered[:limit], 1):
        print(f"[{rank}] {len(members)}명 — {' · '.join(members[:6])}"
              + (" …" if len(members) > 6 else ""))
        show_clause(text)
        print()


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m runner.precedent",
        description="스킬 문구 선례 조회 — 같은 문구를 앞서 어떻게 파싱했는가.")
    ap.add_argument("query", nargs="?", help="조회할 원문 clause. 없으면 stdin에서 읽는다")
    ap.add_argument("--file", help="질의 문구를 담은 파일 (여러 줄 clause용)")
    ap.add_argument("--key", help="역방향 조회: 이 파싱 키(stat·timing·condition·target)를 낳은 문구들")
    ap.add_argument("--limit", type=int, default=5, help="표시할 문구 종수 (기본 5)")
    ap.add_argument("--per", type=int, default=2, help="문구당 펼쳐 볼 선례 수 (기본 2)")
    ap.add_argument("--min", type=float, default=0.5, dest="minimum",
                    help="유사도 하한 (기본 0.5)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="제외할 캐릭터 (재구현 대상 본인 등). 여러 번 지정 가능")
    args = ap.parse_args()

    clauses, parsed = load()

    if args.key:
        by_key(args.key, clauses, parsed, limit=args.limit)
        return

    if args.file:
        query = Path(args.file).read_text(encoding="utf-8")
    elif args.query:
        query = args.query
    elif not sys.stdin.isatty():
        query = sys.stdin.read()
    else:
        ap.error("질의 문구가 없다. 인자·--file·stdin 중 하나로 준다 (--key는 역방향 조회)")

    if not query.strip():
        ap.error("질의 문구가 비어 있다")

    search(query, clauses, parsed, limit=args.limit, per=args.per,
           minimum=args.minimum, exclude=set(args.exclude))


if __name__ == "__main__":
    main()
