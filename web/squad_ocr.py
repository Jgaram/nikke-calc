"""스쿼드 캡처 판독 — 브라우저가 잘라 보낸 칸에서 니케를 알아낸다.

브라우저가 캡처에서 격자를 찾아 칸마다 **미리 줄인 원시 화소**를 보낸다 —
12x12 · 24x24 · 32x32 세 장과 속성 뱃지 16x16. 서버는 그것으로 서명을 만들어
대조군(`data/face_sig.json`)과 맞춘다.

세 크기를 브라우저가 만드는 이유: 서버에서 한 장을 다시 줄이면 비율이 안 맞아
화질이 깎인다(56->32는 1.5배라 실측에서 25칸 중 2칸을 잃었다). 캔버스는 고품질
축소를 공짜로 해 주고, 덤으로 전송량도 5분의 1이 된다(칸당 5KB).

이렇게 나눈 이유가 셋이다:
  1. 서버가 이미지를 다루지 않으니 **Pillow가 필요 없다** (SITE.md의 표준
     라이브러리 약속). 화소 배열은 캔버스가 이미 풀어서 준다.
  2. **캡처 원본이 서버에 올라오지 않는다.** 남의 계정 화면이니 그게 맞다.
  3. 무거운 자르기·디코딩이 사람 기기로 가서 서버는 내적만 한다.

판독은 세 갈래를 섞는다 — 하나만 쓰면 화질이 나쁠 때 한쪽으로 쏠린다:
  · NCC 24x24 RGB   — 구도가 같을 때 가장 강하다
  · pHash 32x32 DCT — 축소·압축에 가장 둔감하다
  · 색조x채도 분포   — 애니 얼굴은 머리색이 제일 잘 갈린다
그 위에 **속성 뱃지**로 후보를 좁히고(199명 -> 40명대), 마지막에 «한 니케는 한 번만»
으로 전역 배정한다. 같은 니케가 두 덱에 들어갈 수 없다는 사실이 오답을 걷어 낸다.

실측 정확도(정답표 3장 · 75칸): top1 74/75 · top3 75/75.
"""
from __future__ import annotations

import json
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SIG_PATH = os.path.join(_ROOT, "data", "face_sig.json")

BADGE = 16                                  # 속성 뱃지 조각 한 변(화소)
W_NCC, W_PH, W_COL = 0.50, 0.26, 0.24
ELEM_GATE = 0.25                            # 이 아래면 «모르겠다» — 거르지 않는다
ELEM_BONUS = 0.20
SHORTLIST = 32
TOPN = 5                                    # 칸마다 돌려줄 후보 수

# 격자가 준 상자는 카드 테두리를 조금 물고 있어서, 그대로 쓰면 대조군과 틀이
# 어긋난다(실측: 새 캡처에서 25칸 중 4칸을 잃었다). 브라우저가 아래 틀들로 잘라
# **12x12만** 먼저 보내면 서버가 어느 틀이 맞는지 골라 준다 — 그 다음에 본 판독을
# 한다. 두 번 오가지만 첫 왕복이 15KB뿐이라 체감이 없다.
ALIGN = [(dx, dy, sc) for dx in (-0.03, 0.0, 0.03) for dy in (-0.03, 0.0, 0.03)
         for sc in (0.94, 1.0, 1.06)]

# 속성 원의 색조 — **짐작이 아니라 실측**이다(정답표 75칸에서 잼). 작열은 빨강(0)이
# 아니라 진홍 253이고, 철갑은 노랑(40)이 아니라 주황 22다. 짐작값으로 두면 주황
# 화소가 두 속성 모두에 투표해 뒤집힌다.
ELEM_HUE = {"작열": 254, "수냉": 150, "풍압": 97, "전격": 214, "철갑": 22}
HUE_WIN = 14
ELEM_BOX = (0.78, 0.755, 0.99, 0.965)       # 칸 안 속성 원 자리 (비율)

_DB = None


class SquadOcrUnavailable(RuntimeError):
    """서명표가 없다 — `python scraper/face_sig.py`를 돌려야 한다."""


def _load():
    global _DB
    if _DB is None:
        if not os.path.exists(SIG_PATH):
            raise SquadOcrUnavailable(SIG_PATH)
        doc = json.load(open(SIG_PATH, encoding="utf-8"))
        g = doc["grid"]
        m = doc["mask"]
        _DB = {
            "cards": doc["cards"],
            "C": g["coarse"], "F": g["fine"], "PH": g["phash"],
            "HB": g["hb"], "SB": g["sb"],
            "mc": _pool(m["mask"], g["coarse"], m["g"]),
            "mf": _pool(m["mask"], g["fine"], m["g"]),
            "mp": _pool(m["mask"], g["phash"], m["g"]),
        }
    return _DB


def available() -> bool:
    try:
        _load()
        return True
    except Exception:                        # noqa: BLE001
        return False


def _pool(mask, g, src):
    step = src / g
    out = []
    for y in range(g):
        for x in range(g):
            y0, y1 = int(y * step), max(int(y * step) + 1, int((y + 1) * step))
            x0, x1 = int(x * step), max(int(x * step) + 1, int((x + 1) * step))
            v = [mask[yy * src + xx] for yy in range(y0, y1) for xx in range(x0, x1)]
            out.append(1 if sum(v) * 2 >= len(v) else 0)
    return out


def _norm(v):
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0
    return [(x - m) / sd for x in v]


_COS: dict = {}


def _dct1(v):
    n = len(v)
    if n not in _COS:
        _COS[n] = [[math.cos(math.pi * (2 * x + 1) * k / (2 * n)) for x in range(n)]
                   for k in range(n)]
    cs = _COS[n]
    return [sum(v[x] * cs[k][x] for x in range(n)) for k in range(n)]


def _lum(p):
    return (p[0] * 299 + p[1] * 587 + p[2] * 114) // 1000


def _rgb2hsv(p):
    r, g, b = p
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0
    elif mx == r:
        h = (43 * (g - b) // d) % 256
    elif mx == g:
        h = (85 + 43 * (b - r) // d) % 256
    else:
        h = (171 + 43 * (r - g) // d) % 256
    return h, (0 if mx == 0 else 255 * d // mx), mx


def _planes(tile):
    """{"c12","c24","c32","badge"} 각각 RGB 바이트열 -> (R,G,B) 리스트로 편다."""
    def rd(key, g):
        b = tile[key]
        if len(b) != g * g * 3:
            raise ValueError(f"{key}는 {g}x{g} RGB여야 한다 (받은 길이 {len(b)})")
        return [(b[i * 3], b[i * 3 + 1], b[i * 3 + 2]) for i in range(g * g)]
    db = _load()
    return (rd("c12", db["C"]), rd("c24", db["F"]), rd("c32", db["PH"]),
            rd("badge", BADGE))


def signatures(bc, bf, bp32):
    """줄여 온 화소 -> (coarse, fine, phash, color). scraper/face_sig.py와 규칙이 같아야 한다."""
    db = _load()
    PH = db["PH"]

    coarse = _norm([_lum(p) for p, m in zip(bc, db["mc"]) if m])

    fine = []
    for ch in range(3):
        fine += _norm([p[ch] for p, m in zip(bf, db["mf"]) if m])

    bp = [_lum(p) for p in bp32]
    mp = db["mp"]
    live = [v for v, m in zip(bp, mp) if m]
    fill = sum(live) // len(live) if live else 128
    bp = [v if m else fill for v, m in zip(bp, mp)]
    rows = [_dct1(bp[y * PH:(y + 1) * PH]) for y in range(PH)]
    cols = [_dct1([rows[y][k] for y in range(PH)]) for k in range(8)]
    vals = [cols[k][y] for k in range(8) for y in range(8)][1:]
    med = sorted(vals)[len(vals) // 2]
    phash = int("".join("1" if v > med else "0" for v in vals), 2)

    HB, SB = db["HB"], db["SB"]
    hist = [0.0] * (HB * SB)
    for p, m in zip(bf, db["mf"]):
        if not m:
            continue
        h, s, v = _rgb2hsv(p)
        if v < 30:
            continue
        hist[(h * HB // 256) * SB + min(SB - 1, s * SB // 256)] += 1
    tot = sum(hist) or 1.0
    return coarse, fine, phash, [x / tot for x in hist]


def read_element(badge):
    """속성 원 조각의 색을 읽는다 -> (속성, 확신도).

    평균 색조는 못 쓴다 — 뱃지와 아래 노란 띠가 섞이면 «중간값»이 나와 엉뚱한
    속성으로 떨어진다. 가장 진한 화소만 골라 속성별로 투표시킨다.
    """
    px = [t for t in (_rgb2hsv(p) for p in badge) if t[2] > 45]
    if len(px) < 10:
        return None, 0.0
    px.sort(key=lambda t: -t[1])
    keep = [t for t in px[:max(8, len(px) // 3)] if t[1] > 70]
    if len(keep) < 6:
        return None, 0.0
    votes = {nm: 0.0 for nm in ELEM_HUE}
    for h, s, _v in keep:
        for nm, hv in ELEM_HUE.items():
            d = min(abs(h - hv), 256 - abs(h - hv))
            if d < HUE_WIN:
                votes[nm] += (1 - d / HUE_WIN) * (s / 255)
    rank = sorted(votes.items(), key=lambda kv: -kv[1])
    if rank[0][1] <= 0:
        return None, 0.0
    tot = sum(votes.values()) or 1.0
    return rank[0][0], (rank[0][1] - rank[1][1]) / tot


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b)) / len(a)


def _ham(a, b):
    return (63 - bin(a ^ b).count("1")) / 63 * 2 - 1


def _inter(a, b):
    return 2 * sum(min(x, y) for x, y in zip(a, b)) - 1


def score_cell(tile):
    """칸 하나 -> ({이름: (점수, 코스튬, 파일)}, (속성, 확신도))."""
    db = _load()
    bc, bf, bp, badge = _planes(tile)
    co, fi, ph, cl = signatures(bc, bf, bp)
    el, ec = read_element(badge)
    cards = db["cards"]
    sub = cards
    bonus = None
    if el and ec >= ELEM_GATE:
        f = [c for c in cards if c["e"] == el]
        if len(f) >= 3:
            sub = f
    elif el:
        bonus = el

    best = {}
    for c in sub:
        s = _dot(co, c["co"])
        if s > best.get(c["n"], (-9, None))[0]:
            best[c["n"]] = (s, c)
    short = sorted(best.values(), key=lambda t: -t[0])[:SHORTLIST]

    raw = {}
    for _s, c in short:
        v = (W_NCC * _dot(fi, c["fi"]) + W_PH * _ham(ph, c["ph"])
             + W_COL * _inter(cl, c["cl"]))
        if bonus and c["e"] == bonus:
            v += ELEM_BONUS * ec
        raw[c["n"]] = (v, c["c"], c["f"])
    vs = [v[0] for v in raw.values()]
    m = sum(vs) / len(vs)
    sd = math.sqrt(sum((x - m) ** 2 for x in vs) / len(vs)) or 1.0
    return {nm: ((v[0] - m) / sd, v[1], v[2]) for nm, v in raw.items()}, (el, ec)


def assign(cells, locked=None):
    """한 니케는 한 번만. locked(사람이 확정한 칸)은 건드리지 않는다."""
    locked = locked or {}
    taken = {nm: i for i, nm in locked.items()}
    pick = [None] * len(cells)
    for i, nm in locked.items():
        pick[i] = nm
    free = [i for i in range(len(cells)) if i not in locked]
    for i in sorted(free, key=lambda k: -max((v[0] for v in cells[k].values()),
                                             default=0)):
        for nm, v in sorted(cells[i].items(), key=lambda kv: -kv[1][0]):
            if nm not in taken:
                taken[nm] = i
                pick[i] = nm
                break
    for _ in range(6):
        moved = False
        for a_i in range(len(free)):
            for b_i in range(a_i + 1, len(free)):
                i, j = free[a_i], free[b_i]
                a, b = pick[i], pick[j]
                if not a or not b or a == b or b not in cells[i] or a not in cells[j]:
                    continue
                if cells[i][b][0] + cells[j][a][0] > cells[i][a][0] + cells[j][b][0] + 1e-9:
                    pick[i], pick[j] = b, a
                    moved = True
        if not moved:
            break
    return pick


def pick_align(samples):
    """samples = [[12x12 RGB 바이트열, ...ALIGN 순서], ...] -> 가장 잘 맞는 틀 번호.

    표본 몇 칸이면 된다 — 격자가 규칙적이라 **정렬은 캡처 하나에 하나**다.
    """
    db = _load()
    C = db["C"]
    cards = db["cards"]
    score = [0.0] * len(ALIGN)
    for tile_views in samples:
        for k, raw in enumerate(tile_views):
            if len(raw) != C * C * 3:
                raise ValueError(f"표본은 {C}x{C} RGB여야 한다 (받은 길이 {len(raw)})")
            px = [(raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]) for i in range(C * C)]
            co = _norm([_lum(p) for p, m in zip(px, db["mc"]) if m])
            score[k] += max(_dot(co, c["co"]) for c in cards)
    best = max(range(len(ALIGN)), key=lambda k: score[k])
    return best, ALIGN[best]


def read(tiles, locked=None):
    """tiles = [{"c12","c24","c32","badge"}...] (각 값은 RGB 바이트열) -> 칸마다 후보.

    `locked`는 {칸번호: 니케이름} — 사람이 고친 칸이다. 고정해 두고 나머지를
    다시 배정하므로, 한 칸을 고치면 겹치던 다른 칸이 저절로 풀린다.
    """
    cells, elems = [], []
    for t in tiles:
        c, e = score_cell(t)
        cells.append(c)
        elems.append(e)
    pick = assign(cells, locked)
    out = []
    for i, cell in enumerate(cells):
        rank = sorted(cell.items(), key=lambda kv: -kv[1][0])[:TOPN]
        chosen = pick[i]
        top = rank[0][1][0] if rank else 0.0
        second = rank[1][1][0] if len(rank) > 1 else 0.0
        out.append({
            "pick": chosen,
            "element": elems[i][0],
            "element_conf": round(elems[i][1], 3),
            "margin": round(top - second, 2),
            "sure": bool(chosen == (rank[0][0] if rank else None) and top - second >= 0.6),
            "candidates": [{"name": nm, "cos": v[1], "file": v[2],
                            "score": round(v[0], 2)} for nm, v in rank],
        })
    return out
