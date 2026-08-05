"""딜량 보고서 HTML 렌더러 (`.claude/skills/report/report.py` 전용).

자체완결 HTML을 만든다 — 이미지는 base64 인라인, CSS·JS도 인라인이라
파일 하나만 있으면 어디서든 열린다.

색은 dataviz 스킬 레퍼런스 팔레트(검증본)를 그대로 쓴다.
캐릭터 색은 **스쿼드 자리 순서**로 고정 배정한다 (딜 순위로 칠하지 않는다).
"""

from __future__ import annotations

import base64
import datetime
import functools
import html
import io
import json
import os

# 이 파일은 `.claude/skills/report/` 안에 있다. 저장소 루트는 3단계 위.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMG_DIR = os.path.join(_ROOT, "image")
_IMG_EXT = {".webp", ".png", ".jpg", ".jpeg"}

# 카드 썸네일 한 변 (px). 원본은 256×512 세로형이라 위쪽 정사각형만 잘라 쓴다.
_THUMB = 150


# ── 이미지 ────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.replace(" ", "").replace(":", "").replace("_", "").lower()


@functools.lru_cache(maxsize=1)
def _img_index() -> dict[str, str]:
    if not os.path.isdir(_IMG_DIR):
        return {}
    idx = {}
    for fn in os.listdir(_IMG_DIR):
        stem, ext = os.path.splitext(fn)
        if ext.lower() in _IMG_EXT:
            idx[_norm(stem)] = os.path.join(_IMG_DIR, fn)
    return idx


@functools.lru_cache(maxsize=256)
def char_image(name: str) -> str | None:
    """캐릭터명 → 정사각형으로 자른 썸네일 data URI. 없으면 None."""
    path = _img_index().get(_norm(name))
    if not path:
        return None
    from PIL import Image
    img = Image.open(path).convert("RGB")
    side = min(img.width, img.height)
    # 세로형 원본(256×512)에서 얼굴이 가운데 오는 정사각 구간. 위에서 18% 내려간 지점부터
    # 자르면 이마가 잘리지 않으면서 얼굴 전체가 들어온다 (여러 캐릭터로 확인).
    top = min(int(img.height * 0.18), img.height - side)
    img = img.crop((0, top, side, top + side))
    if side > _THUMB:
        img = img.resize((_THUMB, _THUMB), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="webp", quality=82)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


# ── 숫자 표기 ─────────────────────────────────────────────────────────────

def _kor(n: float) -> str:
    """딜량 표기 — 억 단위 소수점 둘째 자리로 통일.

    표준편차·스킬별 딜까지 같은 단위여야 카드 안에서 크기 비교가 바로 된다.
    100만 미만(0.01억 미만)은 억으로 쓰면 전부 0.00억이 되므로 원 수치로 적는다.
    """
    if abs(n) >= 1e6:
        return f"{n/1e8:,.2f}억"
    return f"{n:,.0f}"


def _n(n: float) -> str:
    return f"{n:,.0f}"


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def _esc(s) -> str:
    return html.escape(str(s))


# ── CSS ───────────────────────────────────────────────────────────────────

_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
_SERIES_DARK  = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]

_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb; --plane: #f9f9f7;
  --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --good: #006300; --bad: #d03b3b;
  --seq: #2a78d6; --seq-soft: #cde2fb;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface: #1a1a19; --plane: #0d0d0d;
    --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --good: #0ca30c; --bad: #d03b3b;
    --seq: #3987e5; --seq-soft: #184f95;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 24px 80px;
  background: var(--plane); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
  font-size: 14px; line-height: 1.55;
}
.wrap { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 16px; margin: 40px 0 14px; letter-spacing: -0.01em; }
h3 { font-size: 14px; margin: 0 0 10px; }
.sub { color: var(--ink2); margin: 0 0 18px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.chip {
  border: 1px solid var(--border); border-radius: 999px;
  padding: 3px 10px; font-size: 12px; color: var(--ink2); background: var(--surface);
}
.boss {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 9px 13px; font-size: 12.5px; color: var(--ink2);
  font-variant-numeric: tabular-nums;
}
.boss b { color: var(--ink); margin-right: 8px; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px;
}
.tabs { display: flex; gap: 4px; margin: 18px 0 0; border-bottom: 1px solid var(--border); }
.tab {
  appearance: none; background: none; border: none; cursor: pointer;
  font: inherit; font-size: 13.5px; color: var(--ink2);
  padding: 9px 16px; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab:hover { color: var(--ink); }
.tab.on { color: var(--ink); font-weight: 640; border-bottom-color: var(--seq); }
.panel > h2:first-of-type { margin-top: 26px; }
/* 케이스 요약: 한 줄에 한 케이스 — 왼쪽 스쿼드, 오른쪽 수치 */
.cases { display: flex; flex-direction: column; gap: 12px; }
.caserow { display: flex; flex-wrap: wrap; align-items: center; gap: 20px; }
.casehead { flex: 0 0 100%; }              /* 이름·설명은 카드 폭 전체 */
.caseleft { flex: 0 0 290px; min-width: 0; }
.caseright { flex: 1; min-width: 0; }
.case-name { font-weight: 650; font-size: 15px; }
.case-note { color: var(--ink2); font-size: 12.5px; margin-top: 2px; }
.squad { display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; }
@media (max-width: 820px) {
  .caserow { flex-direction: column; align-items: stretch; gap: 14px; }
  .caseleft { flex: none; }
}
.port { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.port .pic, .port .noimg {
  width: 100%; aspect-ratio: 1 / 1; background-size: cover; background-position: center;
  border-radius: 8px; border: 1px solid var(--border); display: block;
}
.port .noimg { background: var(--grid); }
.port span {
  font-size: 9.5px; color: var(--ink2); text-align: center; line-height: 1.2;
  overflow-wrap: anywhere;
}
.hero { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.hero b { font-size: 30px; font-weight: 660; letter-spacing: -0.02em; }
.hero .pm { color: var(--ink2); font-size: 13px; }
.delta { font-size: 13px; font-weight: 600; }
.up { color: var(--good); } .down { color: var(--bad); } .flat { color: var(--muted); }
.kv { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px;
      font-size: 12px; color: var(--ink2); font-variant-numeric: tabular-nums; }
.rowlab { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; }
.rowlab .r { color: var(--ink2); font-variant-numeric: tabular-nums; }
.track { position: relative; height: 14px; background: var(--grid);
         border-radius: 4px; margin: 5px 0 12px; }
.fill { position: absolute; inset: 0 auto 0 0; background: var(--seq); border-radius: 4px; }
.whisk { position: absolute; top: 50%; height: 8px; transform: translateY(-50%);
         border-left: 2px solid var(--surface); border-right: 2px solid var(--surface); }
.stackwrap { margin: 6px 0 8px; }
.stack { display: flex; height: 26px; border-radius: 5px; overflow: hidden;
         background: var(--grid); gap: 2px; }
.seg { display: flex; align-items: center; justify-content: center; min-width: 0;
       font-size: 11px; color: #fff; font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: var(--ink2); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
            margin-right: 5px; vertical-align: -1px; }
details { border-top: 1px solid var(--border); }
details > summary {
  cursor: pointer; padding: 10px 2px; font-size: 13px; color: var(--ink2);
  list-style: none; user-select: none;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "▸ "; color: var(--muted); }
details[open] > summary::before { content: "▾ "; }
details > summary:hover { color: var(--ink); }
.detail-body { padding: 4px 2px 18px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px;
        font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 5px 8px; border-bottom: 1px solid var(--grid); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 500; white-space: nowrap; }
.charhead { display: flex; align-items: center; gap: 10px; }
.charhead .mini { width: 30px; height: 30px; border-radius: 6px; flex: none;
                  background-size: cover; background-position: center;
                  border: 1px solid var(--border); }
.dot { width: 9px; height: 9px; border-radius: 3px; flex: none; }
pre { background: var(--plane); border: 1px solid var(--border); border-radius: 8px;
      padding: 10px 12px; overflow-x: auto; font-size: 11.5px; color: var(--ink2); margin: 0; }
.foot { color: var(--muted); font-size: 12px; margin-top: 36px; }
#tip {
  position: fixed; z-index: 99; pointer-events: none; opacity: 0;
  background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 8px; padding: 7px 10px; font-size: 12px; white-space: pre;
  box-shadow: 0 6px 20px rgba(0,0,0,0.16); transition: opacity .08s;
  font-variant-numeric: tabular-nums;
}
"""

_JS = """
(function () {
  var tabs = document.querySelectorAll('.tab');
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      tabs.forEach(function (o) { o.classList.remove('on'); });
      t.classList.add('on');
      document.querySelectorAll('.panel').forEach(function (p) {
        p.hidden = (p.id !== t.dataset.panel);
      });
    });
  });

  var tip = document.getElementById('tip');
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest('[data-tip]');
    if (!el) return;
    tip.textContent = el.getAttribute('data-tip');
    tip.style.opacity = 1;
  });
  document.addEventListener('mousemove', function (e) {
    if (tip.style.opacity != 1) return;
    var x = e.clientX + 14, y = e.clientY + 16;
    var r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  document.addEventListener('mouseout', function (e) {
    if (e.target.closest('[data-tip]')) tip.style.opacity = 0;
  });
})();
"""


# ── 조각 렌더러 ───────────────────────────────────────────────────────────

# 랩쳐 속성 → 그 속성이 약점으로 맞는 공격 속성 (ui/team_panel._CODE_LABELS와 같은 표)
_CODE_WEAK = {"전격": "철갑", "수냉": "전격", "작열": "수냉", "풍압": "작열", "철갑": "풍압"}


def _enemy_desc(enemy: dict | None) -> str:
    """랩쳐 설정 한 줄 요약. 미지정 항목은 timeline.DEFAULT_ENEMY 기본값."""
    e = enemy or {}
    code = e.get("code")
    parts = [f"코드 {code}(약점 {_CODE_WEAK.get(code, '?')})" if code else "코드 없음"]
    parts.append(f"방어력 {e.get('def', 31784):,}")
    core = e.get("core_px", 0)
    parts.append(f"코어 {core:g}px" if core else "코어 없음")
    parts.append("파츠 있음" if e.get("has_parts") else "파츠 없음")
    if e.get("optimal_range_weapons"):
        parts.append("적정거리 " + ", ".join(e["optimal_range_weapons"]))
    return " · ".join(parts)


# 캐릭터명 → CSS 클래스. 같은 캐릭터가 여러 케이스·탭에 나와도 이미지 데이터는
# 한 번만 인라인된다 (variant를 늘려도 파일이 배로 커지지 않는다).
_IMG_CLASS: dict[str, str] = {}


def _img_css(names: list[str]) -> str:
    """등장하는 캐릭터 이미지를 CSS 클래스로 한 번씩만 정의한다."""
    _IMG_CLASS.clear()
    rules = []
    for i, nm in enumerate(dict.fromkeys(names)):
        src = char_image(nm)
        if not src:
            continue
        cls = f"im{i}"
        _IMG_CLASS[nm] = cls
        rules.append(f".{cls}{{background-image:url({src})}}")
    return "\n".join(rules)


def _squad_strip(names: list[str]) -> str:
    cells = []
    for nm in names:
        cls = _IMG_CLASS.get(nm)
        img = (f'<div class="pic {cls}" role="img" aria-label="{_esc(nm)}"></div>' if cls
               else '<div class="noimg" title="이미지 없음"></div>')
        cells.append(f'<div class="port">{img}<span>{_esc(nm)}</span></div>')
    # 5명 미만이면 빈 칸으로 채워 정사각형 격자를 유지한다
    cells += ['<div class="port"></div>'] * (5 - len(names))
    return f'<div class="squad">{"".join(cells)}</div>'


def _case_card(c: dict, show_name: bool) -> str:
    """케이스 요약 카드 1장.

    show_name: 같은 탭에 스쿼드가 같은 케이스가 둘 이상일 때만 이름을 적는다.
               조합 비교에서는 초상화가 곧 이름이라 중복이고, 육성·운용 비교처럼
               스쿼드가 같은 케이스끼리는 이름만이 구분 수단이라 반드시 필요하다.
    """
    t = c["total"]
    tip = (f"{c['name']}\n평균 {_kor(t['mean'])}\n표준편차 {_kor(t['std'])} ({t['cv']:.2f}%)\n"
           f"최소 {_kor(t['min'])}\n최대 {_kor(t['max'])}\nn={t['n']}회")
    # 이름·설명은 카드 폭 전체를 쓴다 (왼쪽 칸에 갇히면 줄바꿈이 심하다).
    head = ""
    if show_name:
        head += f'<div class="case-name">{_esc(c["name"])}</div>'
    if c["note"]:
        head += f'<div class="case-note">{_esc(c["note"])}</div>'
    head = f'<div class="casehead">{head}</div>' if head else ""

    # 한 줄에 한 케이스 — 왼쪽 스쿼드, 오른쪽 수치.
    return f"""
<div class="card caserow">
  {head}
  <div class="caseleft">
    {_squad_strip(c['squad'])}
  </div>
  <div class="caseright">
  <div class="hero" data-tip="{_esc(tip)}">
    <b>{_kor(t['mean'])}</b>
    <span class="pm">± {_kor(t['std'])} ({t['cv']:.2f}%)</span>
  </div>
  <div class="kv">
    <span>범위 {_kor(t['min'])} ~ {_kor(t['max'])}</span>
    <span>{c['duration']:.0f}초 · 풀버스트 {c['burst_count']:.0f}회</span>
  </div>
  </div>
</div>"""


def _total_chart(cases: list[dict], hi: float) -> str:
    """케이스별 총딜 막대. hi는 전 탭 공통 최대값 — 탭 간에도 길이가 비교된다."""
    rows = []
    for c in cases:
        t = c["total"]
        w = t["mean"] / hi * 100
        lo_w, hi_w = t["min"] / hi * 100, t["max"] / hi * 100
        tip = (f"{c['name']}\n평균 {_kor(t['mean'])}\n±1σ {_kor(t['std'])}\n"
               f"범위 {_kor(t['min'])} ~ {_kor(t['max'])}")
        rows.append(f"""
    <div class="rowlab"><span>{_esc(c['name'])}</span>
      <span class="r">{_kor(t['mean'])} &nbsp;±{_kor(t['std'])}</span></div>
    <div class="track" data-tip="{_esc(tip)}">
      <div class="fill" style="width:{w:.3f}%"></div>
      <div class="whisk" style="left:{lo_w:.3f}%; width:{max(hi_w-lo_w, 0.3):.3f}%"></div>
    </div>""")
    return f"""
<div class="card">
  <h3>케이스별 스쿼드 총딜 (평균, 0 기준)</h3>
  {''.join(rows)}
  <div class="legend"><span>막대 = {cases[0]['total']['n']}회 평균 · 흰 구간 = 최소~최대</span></div>
</div>"""


def _by_damage(c: dict) -> list[tuple[dict, str]]:
    """(캐릭터, 색) 목록을 딜 내림차순으로 반환한다.

    색은 **스쿼드 자리 순서**로 배정한 뒤 함께 들고 다닌다 — 표시 순서가 딜 순위로
    바뀌어도 캐릭터의 색은 그대로다 (색이 순위를 따라가면 안 된다).
    """
    pairs = [(ch, f"var(--s{i+1})") for i, ch in enumerate(c["chars"])]
    return sorted(pairs, key=lambda p: -p[0]["mean"])


def _contrib_block(c: dict, hi: float) -> str:
    """캐릭터 기여 스택. 막대 전체 길이는 케이스 총딜에 비례한다 (최고 케이스 = 100%)."""
    total = sum(ch["mean"] for ch in c["chars"]) or 1
    segs, legend = [], []
    for ch, color in _by_damage(c):
        share = ch["mean"] / total * 100
        tip = (f"{ch['name']}\n평균 {_kor(ch['mean'])} ({share:.1f}%)\n"
               f"±{_kor(ch['std'])} (CV {ch['cv']:.2f}%)")
        # 세그먼트 안에는 글자를 넣지 않는다 — 밝은 슬롯(노랑·아쿠아·마젠타) 위 텍스트는
        # 대비가 부족하다. 이름·딜량은 아래 범례가 직접 라벨로 담당한다.
        segs.append(f'<div class="seg" style="flex:{share:.4f} 0 0; background:{color}" '
                    f'data-tip="{_esc(tip)}"></div>')
        legend.append(f'<span><i style="background:{color}"></i>{_esc(ch["name"])} '
                      f'{_kor(ch["mean"])}</span>')
    width = total / hi * 100 if hi else 100
    return f"""
  <div style="margin-bottom:18px">
    <div class="rowlab"><span><b>{_esc(c['name'])}</b></span>
      <span class="r">{_kor(c['total']['mean'])}</span></div>
    <div class="stackwrap"><div class="stack" style="width:{width:.3f}%">{''.join(segs)}</div></div>
    <div class="legend">{''.join(legend)}</div>
  </div>"""


def _char_detail(c: dict) -> str:
    total = sum(ch["mean"] for ch in c["chars"]) or 1
    blocks = []
    for ch, color in _by_damage(c):
        share = ch["mean"] / total * 100
        cls = _IMG_CLASS.get(ch["name"])
        img = f'<span class="mini {cls}"></span>' if cls else ""
        ct = ch["mean"] or 1

        rows = "".join(
            f"<tr><td>{_esc(s['name'])}</td>"
            f"<td>{_kor(s['damage'])}</td>"
            f"<td>{s['damage']/ct*100:.1f}%</td>"
            f"<td>{s['hits']:.1f}</td></tr>"
            for s in ch["skills"]
        )
        fb_sum = ch["fb_self"] + ch["fb_other"] + ch["non_fb"] or 1
        blocks.append(f"""
    <details>
      <summary>
        <span class="charhead">
          <span class="dot" style="background:{color}"></span>{img}
          <b>{_esc(ch['name'])}</b>
          <span>{_kor(ch['mean'])} · {share:.1f}% · ±{_kor(ch['std'])} (CV {ch['cv']:.2f}%)</span>
        </span>
      </summary>
      <div class="detail-body">
        <div class="kv">
          <span>기본공격 {_pct(ch['normal']/ct*100)} ({_kor(ch['normal'])})</span>
          <span>스킬 {_pct(ch['skill']/ct*100)} ({_kor(ch['skill'])})</span>
          <span>풀버스트(본인) {_pct(ch['fb_self']/fb_sum*100)}</span>
          <span>풀버스트(타인) {_pct(ch['fb_other']/fb_sum*100)}</span>
          <span>비풀버스트 {_pct(ch['non_fb']/fb_sum*100)}</span>
        </div>
        <table>
          <thead><tr><th>대미지 출처</th><th>평균 딜</th><th>캐릭 내 비중</th>
            <th>히트수</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </details>""")

    return f"""
<details>
  <summary><b>{_esc(c['name'])}</b> — 캐릭터별 딜 상세 ({len(c['chars'])}명)</summary>
  <div class="detail-body">{''.join(blocks)}</div>
</details>"""


def _raw_table(cases: list[dict], seeds: list) -> str:
    head = "".join(f"<th>{('랜덤' if s is None else f'seed {s}')}</th>" for s in seeds)
    rows = []
    for c in cases:
        cells = "".join(f"<td>{_kor(r['squad_total'])}</td>" for r in c["runs"])
        rows.append(f"<tr><td>{_esc(_full_name(c))}</td>{cells}"
                    f"<td><b>{_kor(c['total']['mean'])}</b></td>"
                    f"<td>{_kor(c['total']['std'])}</td></tr>")
    return f"""
<details>
  <summary>회차별 원자료 표</summary>
  <div class="detail-body">
    <table><thead><tr><th>케이스</th>{head}<th>평균</th><th>표준편차</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table>
  </div>
</details>"""


def _config_block(spec: dict, cases: list[dict]) -> str:
    parts = [f"<h3 style='margin-top:8px'>공통 육성 기본값</h3><pre>"
             f"{_esc(json.dumps(spec['defaults'], ensure_ascii=False, indent=2))}</pre>"]
    for c in cases:
        chars_diff = {
            ch["name"]: {k: v for k, v in _char_of(spec, c, ch["name"]).items()
                         if k != "name" and v != spec["defaults"].get(k)}
            for ch in c["chars"]
        }
        chars_diff = {k: v for k, v in chars_diff.items() if v}
        body = {"config": c["config"], "enemy": c["enemy"]}
        if chars_diff:
            body["육성 오버라이드"] = chars_diff
        parts.append(f"<h3 style='margin-top:14px'>{_esc(_full_name(c))}</h3>"
                     f"<pre>{_esc(json.dumps(body, ensure_ascii=False, indent=2))}</pre>")
    return f"""
<details>
  <summary>실행 설정 (육성·버스트·랩쳐)</summary>
  <div class="detail-body">{''.join(parts)}</div>
</details>"""


def _char_of(spec: dict, case: dict, char_name: str) -> dict:
    """스펙 원본에서 해당 케이스·캐릭터의 전개된 육성 dict를 찾는다.

    variant를 쓰면 케이스 이름이 중복되므로 (name, variant) 쌍으로 찾는다.
    """
    for sc in spec["cases"]:
        if sc["name"] == case["name"] and sc.get("variant", "") == case.get("variant", ""):
            for ch in sc["squad"]:
                if ch["name"] == char_name:
                    return ch
    return {}


def _full_name(c: dict) -> str:
    return f"{c['name']} — {c['variant']}" if c.get("variant") else c["name"]


# ── 진입점 ────────────────────────────────────────────────────────────────

def render_html(spec: dict, cases: list[dict], seeds: list, random_seed: bool) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n = len(seeds)
    seed_txt = "매 회차 랜덤 시드" if random_seed else f"고정 시드 {seeds[0]}~{seeds[-1]}"

    chips = [
        f"케이스 {len(cases)}개",
        f"케이스당 {n}회",
        seed_txt,
        f"전투 {cases[0]['duration']:.0f}초" if cases else "",
        f"생성 {now}",
    ]
    chip_html = "".join(f'<span class="chip">{_esc(t)}</span>' for t in chips if t)

    # 이미지 CSS는 패널 생성보다 먼저 만들어야 한다 (_IMG_CLASS를 채운다).
    img_css = _img_css([nm for c in cases for nm in c["squad"]])

    # variant(조건 축)별로 탭을 만든다. variant가 없으면 탭 하나짜리와 같다.
    groups: dict[str, list[dict]] = {}
    for c in cases:
        groups.setdefault(c.get("variant", ""), []).append(c)

    # 막대 길이는 **전 탭 통틀어** 최고 총딜 기준 — 탭을 바꿔도 길이가 서로 비교된다.
    global_hi = max((c["total"]["max"] for c in cases), default=1) or 1
    global_hi_char = max(
        (sum(x["mean"] for x in c["chars"]) for c in cases), default=1) or 1

    tabs, panels = [], []
    for i, (vname, gcases) in enumerate(groups.items()):
        act = " on" if i == 0 else ""
        label = vname or "전체"
        tabs.append(f'<button class="tab{act}" data-panel="p{i}">{_esc(label)}</button>')

        # 스쿼드가 겹치는 케이스가 있으면 초상화만으로 구분이 안 되니 이름을 적는다.
        squads = [tuple(c["squad"]) for c in gcases]
        show_name = len(set(squads)) < len(squads)

        enemies = {json.dumps(c["enemy"] or {}, sort_keys=True, ensure_ascii=False)
                   for c in gcases}
        if len(enemies) == 1:
            boss = (f'<div class="boss"><b>랩쳐</b> '
                    f'{_esc(_enemy_desc(gcases[0]["enemy"]))}</div>')
        else:
            rows = "".join(f'<div><b>{_esc(c["name"])}</b> {_esc(_enemy_desc(c["enemy"]))}</div>'
                           for c in gcases)
            boss = f'<div class="boss"><b>랩쳐 (케이스별 상이)</b>{rows}</div>'

        panels.append(f"""
<div class="panel" id="p{i}"{'' if i == 0 else ' hidden'}>
  {boss}

  <h2>케이스 요약</h2>
  <div class="cases">{''.join(_case_card(c, show_name) for c in gcases)}</div>

  <h2>총딜 비교</h2>
  {_total_chart(gcases, global_hi)}

  <h2>캐릭터 기여도</h2>
  <div class="card">
    <h3>케이스별 캐릭터 딜 (막대 길이 = 총딜, 전 탭 최고 케이스 기준)</h3>
    {''.join(_contrib_block(c, global_hi_char) for c in gcases)}
  </div>

  <h2>캐릭터별 딜 상세</h2>
  <div class="card" style="padding-top:0">{''.join(_char_detail(c) for c in gcases)}</div>
</div>""")

    tab_html = f'<div class="tabs">{"".join(tabs)}</div>' if len(groups) > 1 else ""
    note = f'<p class="sub">{_esc(spec["note"])}</p>' if spec.get("note") else ""

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(spec['title'])} — 딜량 보고서</title>
<style>{_CSS}
{img_css}</style></head>
<body><div class="wrap">
  <h1>{_esc(spec['title'])}</h1>
  {note}
  <div class="chips">{chip_html}</div>
  {tab_html}
  {''.join(panels)}

  <h2>원자료 · 설정</h2>
  <div class="card" style="padding-top:0">
    {_raw_table(cases, seeds)}
    {_config_block(spec, cases)}
  </div>

  <p class="foot">
    시드를 고정하면 같은 스펙에서 같은 수치가 재현된다. 표준편차는 시드 간 편차이며,
    스킬 상세의 히트수는 회차 평균이라 소수점이 나온다.
  </p>
</div>
<div id="tip"></div>
<script>{_JS}</script>
</body></html>"""
