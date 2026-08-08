"""외부 기준값 대조 렌더러 (add-on).

이미 돌린 보고서 캐시(`reports/out/<이름>.data.json`)에 **외부 출처의 딜량**을 얹어
케이스마다 `기준값`과 `비율(우리/기준)`을 작게 덧붙인 HTML을 따로 낸다.
enikk.app 실사용 파스처럼 육성 수준이 다른 기록과 우리 시뮬을 견줄 때 쓴다.

`report_html.py`는 **고치지 않는다** — 여기서 `_case_card`만 감싸 갈아끼운다.
공용 보고서 형식은 그대로 두고 이 스크립트로 뽑은 것만 대조 열이 붙는다.

    python .claude/skills/report/report_ref.py <data.json> <ref.json> [-o out.html]

기준값 파일 형식:

```jsonc
{
  "label": "enikk 평균",        // 화면에 찍히는 이름
  "unit": "B",                  // 값 뒤에 붙는 단위 표기 (없으면 생략)
  "scale": 1e9,                 // 기준값 → 원 단위 환산 계수. 비율 계산에만 쓴다
  "by_squad": {                 // 키 = 스쿼드 정식 명칭을 " · "로 이은 것
    "토브 · 아르카나 : 포츈 메이트 · 도로시 : 세렌디피티 · 드레이크 · 솔린 : 프로스트 티켓": 5.84
  }
}
```

`by_squad`에 없는 케이스는 대조 줄 없이 원래대로 나온다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "report"))

import report_html as R  # noqa: E402

_ORIG_CASE_CARD = R._case_card
_REF: dict = {}


def _ref_line(c: dict) -> str:
    """케이스 카드에 덧붙일 대조 줄. 기준값이 없으면 빈 문자열."""
    val = _REF.get("by_squad", {}).get(" · ".join(c["squad"]))
    if val is None:
        return ""
    label = _REF.get("label", "기준")
    unit = _REF.get("unit", "")
    scale = float(_REF.get("scale", 1.0))
    ratio = c["total"]["mean"] / (val * scale) if val else None
    # 비율이 1에서 멀수록 눈에 띄게 — 0.9~1.1은 중립색으로 둔다.
    cls = "ref"
    if ratio is not None:
        cls += " ref-lo" if ratio < 0.9 else (" ref-hi" if ratio > 1.1 else "")
    r_txt = f' · 비율 <b>{ratio:.2f}</b>' if ratio is not None else ""
    return (f'<span class="{cls}">{R._esc(label)} {val:g}{R._esc(unit)}{r_txt}</span>')


def _case_card(c: dict, show_name: bool, ops: str = "") -> str:
    html = _ORIG_CASE_CARD(c, show_name, ops)
    line = _ref_line(c)
    if not line:
        return html
    # `.kv` 블록 끝에 한 칸 더 붙인다 (범위·풀버스트 옆).
    marker = "  </div>\n  </div>\n</div>"
    if marker not in html:          # 원본 구조가 바뀌면 조용히 넘어가지 않는다
        raise RuntimeError("report_html._case_card 구조가 바뀌었다 — report_ref.py의 앵커를 고쳐라")
    return html.replace(marker, f"    {line}\n{marker}", 1)


_CSS = """
.kv .ref { border:1px solid var(--border); border-radius:999px;
           padding:1px 8px; opacity:.85; font-size:11px; }
.kv .ref b { font-weight:700; }
.kv .ref-lo { color:#e0823d; border-color:#e0823d66; }
.kv .ref-hi { color:#4aa3df; border-color:#4aa3df66; }
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="보고서 캐시에 외부 기준값을 얹어 다시 렌더한다")
    ap.add_argument("data", help="reports/out/<이름>.data.json")
    ap.add_argument("ref", help="기준값 JSON")
    ap.add_argument("-o", "--out", help="출력 HTML (기본: <data>와 같은 자리의 .ref.html)")
    a = ap.parse_args()

    global _REF
    d = json.loads(pathlib.Path(a.data).read_text(encoding="utf-8"))
    _REF = json.loads(pathlib.Path(a.ref).read_text(encoding="utf-8"))

    R._case_card = _case_card
    html = R.render_html(d["spec"], d["cases"], d["seeds"], d.get("random", False))
    html = html.replace("</style>", _CSS + "</style>", 1)

    out = pathlib.Path(a.out) if a.out else \
        pathlib.Path(a.data).with_suffix("").with_suffix(".ref.html")
    out.write_text(html, encoding="utf-8")

    hit = sum(1 for c in d["cases"] if " · ".join(c["squad"]) in _REF.get("by_squad", {}))
    print(f"{out}  (케이스 {len(d['cases'])}개 중 기준값 매칭 {hit}개)")


if __name__ == "__main__":
    main()
