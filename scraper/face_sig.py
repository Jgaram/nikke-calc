"""얼굴 카드 서명표 생성 — 스쿼드 캡처 판독의 대조군.

    python scraper/face_sig.py

`image/face/`의 카드마다 서명 넷을 뽑아 `data/face_sig.json`에 넣는다. 서버는 이
표만 읽으면 되고 **이미지를 다루지 않는다** — 그래서 서버에 Pillow가 필요 없다
(SITE.md의 «표준 라이브러리만» 약속을 지킨다).

서명 넷은 서로 다른 것을 본다. 하나만 쓰면 화질이 나쁠 때 한쪽으로 쏠린다:
  coarse  12x12 흑백  — 후보 좁히기용
  fine    24x24 RGB   — 순위 매기기용
  phash   32x32 DCT   — 축소·압축에 가장 둔감
  color   색조x채도    — 애니 얼굴은 머리색이 제일 잘 갈린다

전부 **가림막**을 통과시킨다. 카드 위에는 돌파★·버스트 육각·LV 세로줄·레벨 띠·
속성 원이 얹히는데, 그 자리를 빼야 얼굴만 비교된다. 가림막은 짐작이 아니라
실측이다 — 판독된 (칸, 카드) 쌍의 화소별 차이에서 뽑았다.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FACE_DIR = os.path.join(ROOT, "image", "face")
IDX_PATH = os.path.join(ROOT, "data", "face_index.json")
MASK_PATH = os.path.join(ROOT, "data", "face_mask.json")
OUT_PATH = os.path.join(ROOT, "data", "face_sig.json")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

C, F, PH = 12, 24, 32                      # 굵은/고운/pHash 격자
HB, SB = 12, 4                             # 색조 x 채도 칸 수


def pool(mask, g, src):
    """가림막을 g x g로 줄인다 — 반 이상 가려지면 가린 것으로 본다."""
    step = src / g
    out = []
    for y in range(g):
        for x in range(g):
            y0, y1 = int(y * step), max(int(y * step) + 1, int((y + 1) * step))
            x0, x1 = int(x * step), max(int(x * step) + 1, int((x + 1) * step))
            v = [mask[yy * src + xx] for yy in range(y0, y1) for xx in range(x0, x1)]
            out.append(1 if sum(v) * 2 >= len(v) else 0)
    return out


def norm(v):
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0
    return [round((x - m) / sd, 4) for x in v]


_COS = {}


def dct1(v):
    n = len(v)
    if n not in _COS:
        _COS[n] = [[math.cos(math.pi * (2 * x + 1) * k / (2 * n)) for x in range(n)]
                   for k in range(n)]
    cs = _COS[n]
    return [sum(v[x] * cs[k][x] for x in range(n)) for k in range(n)]


def signatures(im, masks):
    """PIL 이미지 -> 서명 넷. web/squad_ocr.py의 같은 이름 함수와 **규칙이 같아야** 한다."""
    from PIL import Image
    mc, mf, mp = masks
    g = im.convert("L")
    d = list(g.resize((C, C), Image.LANCZOS).getdata())
    coarse = norm([d[i] for i, m in enumerate(mc) if m])

    s = im.convert("RGB").resize((F, F), Image.LANCZOS)
    fine = []
    for ch in s.split():
        d = list(ch.getdata())
        fine += norm([d[i] for i, m in enumerate(mf) if m])

    px = list(g.resize((PH, PH), Image.LANCZOS).getdata())
    live = [px[i] for i, m in enumerate(mp) if m]
    fill = sum(live) / len(live) if live else 128
    px = [px[i] if mp[i] else fill for i in range(PH * PH)]
    rows = [dct1(px[y * PH:(y + 1) * PH]) for y in range(PH)]
    cols = [dct1([rows[y][k] for y in range(PH)]) for k in range(8)]
    vals = [cols[k][y] for k in range(8) for y in range(8)][1:]
    med = sorted(vals)[len(vals) // 2]
    phash = int("".join("1" if v > med else "0" for v in vals), 2)

    hsv = list(im.convert("HSV").resize((F, F), Image.LANCZOS).getdata())
    hist = [0.0] * (HB * SB)
    for i, m in enumerate(mf):
        if not m:
            continue
        h, sa, v = hsv[i]
        if v < 30:
            continue
        hist[(h * HB // 256) * SB + min(SB - 1, sa * SB // 256)] += 1
    tot = sum(hist) or 1.0
    color = [round(x / tot, 5) for x in hist]
    return coarse, fine, phash, color


def main() -> None:
    from PIL import Image                                   # 이 도구에서만 쓴다
    mask_doc = json.load(open(MASK_PATH, encoding="utf-8"))
    g0, mask = mask_doc["g"], mask_doc["mask"]
    masks = (pool(mask, C, g0), pool(mask, F, g0), pool(mask, PH, g0))

    idx = json.load(open(IDX_PATH, encoding="utf-8"))["faces"]
    roster = json.load(open(os.path.join(ROOT, "web", "dist", "roster.json"),
                            encoding="utf-8"))
    chars = roster["chars"] if isinstance(roster, dict) else roster
    elem = {c["name"]: c["element"] for c in chars}

    out = []
    for fn, meta in sorted(idx.items()):
        # 이름 없는 카드(미출시·NPC)는 가질 수 없으니 후보에서 뺀다 — 넣으면
        # 오답으로만 튀어나온다.
        if not meta.get("name"):
            continue
        im = Image.open(os.path.join(FACE_DIR, fn))
        co, fi, ph, cl = signatures(im, masks)
        out.append({"n": meta["name"], "c": meta["cos"],
                    "e": elem.get(meta["name"]), "f": fn,
                    "co": co, "fi": fi, "ph": ph, "cl": cl})

    doc = {"_comment": "얼굴 카드 서명표. python scraper/face_sig.py 로 갱신. "
                       "web/squad_ocr.py가 읽는다.",
           "grid": {"coarse": C, "fine": F, "phash": PH, "hb": HB, "sb": SB},
           "mask": {"g": g0, "mask": mask},
           "cards": out}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(OUT_PATH) / 1048576
    print(f"[+] 서명 {len(out)}장 / {len({c['n'] for c in out})}명 → {OUT_PATH} ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
