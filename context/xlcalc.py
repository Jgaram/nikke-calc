"""참조 엑셀 계산기 구동 CLI (Claude 전용).

`context/xlcalc.xlsx`(손계산 기반 참조 계산기)를 Excel COM으로 열어 딜러·서포터·
랩쳐 조건을 바꾸고 재계산한다. 우리 시뮬(`context/sim.py`) 결과를 대조할 때 쓴다.

    python -m context.xlcalc --list
    python -m context.xlcalc "신데렐라,아니스,마스트,앵커"
    python -m context.xlcalc "신데렐라,아니스,마스트,앵커" --view cols
    python -m context.xlcalc "신데렐라,아니스,마스트,앵커" --view buff
    python -m context.xlcalc "라피" --core 1 --enemy-def 40000

첫 항목이 딜러, 나머지가 서포터1~4다. 이름은 **엑셀 시트의 이름**이며 우리
`parsed_nikke.json`과 다르다 (엑셀 "마스트" = 우리 "마스트 : 로망틱 메이드").
`--list`로 엑셀이 아는 이름을 확인한다. 상세는 `context/XLCALC.md`.

원본 `xlcalc.xlsx`는 건드리지 않는다 — 매 실행마다 임시 사본을 열고 버린다.
(`--save`를 주면 변경을 원본에 반영한다.)

전제: Windows + Excel 설치 + pywin32. 없으면 실행 즉시 안내하고 종료한다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xlcalc.xlsx")

# 메인 시트 입력 셀
INPUT_CELLS = {
    "dealer": "F4", "sup1": "G4", "sup2": "H4", "sup3": "I4", "sup4": "J4",
    "enemy_def": "C3", "core": "C4", "part_core": "C5", "core_px": "C6",
    "core_hit": "C7", "optimal_range": "C8", "element": "C9", "charge_calc": "C13",
}

# 딜량계산 열 → 구간 이름
DEAL_COLS = [
    ("C", "버스트 시전"),   ("D", "버스트 추가"),
    ("E", "자버전반 딜1"),  ("F", "자버전반 딜2"), ("G", "자버전반 딜3"), ("H", "자버전반 딜4"),
    ("I", "자버후반 딜1"),  ("J", "자버후반 딜2"), ("K", "자버후반 딜3"), ("L", "자버후반 딜4"),
    ("M", "자버이후"),
    ("N", "타버전반 딜1"),  ("O", "타버전반 딜2"), ("P", "타버전반 딜3"), ("Q", "타버전반 딜4"),
    ("R", "타버후반 딜1"),  ("S", "타버후반 딜2"), ("T", "타버후반 딜3"), ("U", "타버후반 딜4"),
]

# 버프정리 위상 합산 열
BUFF_PHASES = [("D", "자버직전"), ("J", "자버전반"), ("P", "자버후반"),
               ("V", "자버이후"), ("AB", "타버전반"), ("AH", "타버후반")]
BUFF_ROWS = list(range(4, 42))          # B열에 항목명, 4~41행


def _excel():
    try:
        import win32com.client as win32
    except ImportError:
        sys.exit("pywin32가 없다.  pip install pywin32")
    try:
        app = win32.gencache.EnsureDispatch("Excel.Application")
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"Excel을 띄울 수 없다 (Windows + Excel 필요): {e}")
    app.Visible = False
    app.DisplayAlerts = False
    return app


def _roster(ws):
    """시트 1행에서 캐릭터 블록 이름을 뽑는다 (2행이 '자버이전'인 열)."""
    out, col = [], 1
    used = ws.UsedRange.Columns.Count
    for c in range(1, used + 1):
        if ws.Cells(2, c).Value == "자버이전":
            v = ws.Cells(1, c).Value
            if v:
                out.append(str(v))
    return out


def list_names() -> None:
    app = _excel()
    wb = None
    try:
        wb = app.Workbooks.Open(BOOK, ReadOnly=True)
        for sheet, label in (("딜러데이터", "딜러"), ("서포터데이터", "서포터")):
            names = _roster(wb.Worksheets(sheet))
            print(f"\n[{label}] {len(names)}명")
            for i in range(0, len(names), 6):
                print("  " + "  ".join(f"{n:<16s}" for n in names[i:i + 6]))
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        app.Quit()


def run(sets: dict, view: str, cycles: int, save: bool) -> None:
    if not os.path.exists(BOOK):
        sys.exit(f"참조 엑셀이 없다: {BOOK}")

    # Excel COM은 경로에 작은따옴표가 있으면 저장에 실패한다 → 임시 폴더에서 연다.
    tmpdir = tempfile.mkdtemp(prefix="xlcalc_")
    work = os.path.join(tmpdir, "xlcalc.xlsx")
    shutil.copyfile(BOOK, work)

    app = _excel()
    wb = None
    try:
        wb = app.Workbooks.Open(work)
        main = wb.Worksheets("메인")

        for key, val in sets.items():
            main.Range(INPUT_CELLS[key]).Value = val
        app.CalculateFullRebuild()

        dealer = main.Range("F4").Value
        sups = [main.Range(c).Value for c in ("G4", "H4", "I4", "J4")]
        sups = [s for s in sups if s]
        own, other = main.Range("N14").Value, main.Range("N15").Value
        one = main.Range("N16").Value

        print(f"딜러 : {dealer}  ({main.Range('F5').Value} / {main.Range('F6').Value})")
        print(f"서포터: {', '.join(sups) if sups else '(없음)'}")
        print(f"랩쳐 : 방어력 {main.Range('C3').Value:,.0f}  "
              f"코어 {main.Range('C4').Value}  적정거리 {main.Range('C8').Value}  "
              f"우월코드 {main.Range('C9').Value}")
        print()
        print(f"  본인 버스트 : {own:8.4f} 억   ({own / one:6.1%})")
        print(f"  타인 버스트 : {other:8.4f} 억   ({other / one:6.1%})")
        print(f"  1 사이클    : {one:8.4f} 억")
        print(f"  {cycles} 사이클   : {one * cycles:8.4f} 억   = {one * cycles * 1e8:,.0f}")
        print(f"  일반공격 {main.Range('M5').Value:.1%} / 스킬 {main.Range('N5').Value:.1%}")

        if view == "cols":
            deal = wb.Worksheets("딜량계산")
            print("\n  열  구간            유형        한발대미지      발수          소계")
            print("  " + "-" * 68)
            for col, label in DEAL_COLS:
                total = deal.Range(f"{col}5").Value
                if not total:
                    continue
                print(f"  {col:2s}  {label:14s} {str(deal.Range(f'{col}15').Value):10s} "
                      f"{deal.Range(f'{col}19').Value:12,.0f} "
                      f"{deal.Range(f'{col}9').Value:7,.1f} {total:14,.0f}")

        elif view == "buff":
            bf = wb.Worksheets("버프정리")
            def _cell(v) -> str:
                if not isinstance(v, (int, float)):
                    return f"{'':>12s}"
                # 시전자 기준 공격력만 절대값(수만 단위) — 나머지는 배율
                return f"{v:12,.0f}" if abs(v) >= 1000 else f"{v:12.4f}"

            width = 24
            hdr = "".join(f"{lbl:>12s}" for _, lbl in BUFF_PHASES)
            print(f"\n  {'항목':<{width - 2}s}{hdr}")
            print("  " + "-" * (width + 12 * len(BUFF_PHASES)))
            for r in BUFF_ROWS:
                label = bf.Range(f"B{r}").Value
                if not label:
                    continue
                vals = [bf.Range(f"{c}{r}").Value for c, _ in BUFF_PHASES]
                if not any(v for v in vals):
                    continue
                pad = width - sum(2 if ord(ch) > 0x7F else 1 for ch in str(label))
                print(f"  {label}{' ' * max(pad, 1)}{''.join(_cell(v) for v in vals)}")

        if save:
            wb.Save()
            shutil.copyfile(work, BOOK)
            print(f"\n원본에 반영: {BOOK}")
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        app.Quit()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="참조 엑셀 계산기 구동 (context/xlcalc.xlsx)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--view 종류\n"
            "  summary  사이클 딜량 요약 (기본)\n"
            "  cols     딜량계산 열별 한발대미지·발수·소계\n"
            "  buff     버프정리 6개 위상별 버프 합산표\n"
        ),
    )
    ap.add_argument("squad", nargs="?", help="딜러,서포터1,...  (엑셀 시트 기준 이름)")
    ap.add_argument("--list", action="store_true", help="엑셀이 아는 딜러·서포터 이름 출력")
    ap.add_argument("--view", default="summary", choices=("summary", "cols", "buff"))
    ap.add_argument("--cycles", type=int, default=7, help="총딜 환산 사이클 수 (기본 7)")
    ap.add_argument("--enemy-def", type=float, dest="enemy_def", help="적 방어력")
    ap.add_argument("--core", type=float, help="코어여부 0(노코)~1(상코)")
    ap.add_argument("--part-core", type=float, dest="part_core", help="파츠코어 0/1")
    ap.add_argument("--core-px", type=float, dest="core_px", help="코어 직경(px)")
    ap.add_argument("--optimal-range", type=float, dest="optimal_range", help="적정거리 0/1")
    ap.add_argument("--element", type=float, help="우월코드 0/1")
    ap.add_argument("--charge-calc", type=float, dest="charge_calc", help="차속으로 발수 계산 0/1")
    ap.add_argument("--save", action="store_true", help="변경을 xlcalc.xlsx에 반영")
    a = ap.parse_args()

    if a.list:
        list_names()
        return

    sets: dict = {}
    if a.squad:
        members = [n.strip() for n in a.squad.split(",") if n.strip()]
        if not 1 <= len(members) <= 5:
            sys.exit(f"스쿼드는 1~5명 (입력 {len(members)}명)")
        sets["dealer"] = members[0]
        for i in range(1, 5):
            sets[f"sup{i}"] = members[i] if i < len(members) else ""
    for key in ("enemy_def", "core", "part_core", "core_px",
                "optimal_range", "element", "charge_calc"):
        v = getattr(a, key)
        if v is not None:
            sets[key] = v

    run(sets, a.view, a.cycles, a.save)


if __name__ == "__main__":
    main()
