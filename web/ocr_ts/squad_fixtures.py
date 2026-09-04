# -*- coding: utf-8 -*-
"""스쿼드 캡처 → 브라우저가 보내는 모양의 `/api/squad/read`·`/api/squad/align` 요청 본문(픽스처).

카드 격자는 파이썬 `power_ocr.detect_card_grid`로 찾고, 타일은 `squadshot.js`의 shotTile/shotBadge 규칙
(틀 dx·dy·배율로 흔든 칸을 g×g로 축소, 뱃지는 ELEM_BOX 비율 자리)을 따라 자른다. 축소는 INTER_AREA —
브라우저의 축소와 화소가 같지는 않지만, TS 포팅 대조에는 «같은 바이트를 넣었을 때 같은 답이 나오는가»만
필요하므로 충분하다. 실제 인식률은 알고리즘·서명표가 그대로라 바뀌지 않는다.

    python squad_fixtures.py   → squad_ref/read_<shot>.json · align_<shot>.json
"""
import io, sys, os, json, base64, glob
import numpy as np, cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'C:/claude/nikke-calc-dil/web')
import power_ocr as po                                             # noqa: E402
import squad_ocr as so                                             # noqa: E402

OUT = 'C:/Users/kingdom8/AppData/Local/Temp/claude/C--claude-nikke-calc-rust/3774a69a-e34b-4d44-9d17-fad19aa4dbcf/scratchpad/squad_ref'
os.makedirs(OUT, exist_ok=True)
OCR_C, BADGE, ELEM_BOX = (12, 24, 32), 16, (0.78, 0.755, 0.99, 0.965)


def crop_resize(img, sx, sy, sw, sh, g):
    H, W = img.shape[:2]
    x0, y0 = int(round(sx)), int(round(sy))
    x1, y1 = int(round(sx + sw)), int(round(sy + sh))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, max(x0 + 1, x1)), min(H, max(y0 + 1, y1))
    reg = img[y0:y1, x0:x1]
    r = cv2.resize(reg, (g, g), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(r[:, :, ::-1]).tobytes()          # BGR → RGB


def tile(img, box, align, g):
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0, by1 - by0
    dx, dy, sc = align
    pad = (sc - 1) / 2
    sx, sy = bx0 + bw * (-pad + dx), by0 + bh * (-pad + dy)
    sw, sh = bw * (1 + 2 * pad), bh * (1 + 2 * pad)
    return crop_resize(img, sx, sy, sw, sh, g)


def badge(img, box, align):
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0, by1 - by0
    dx, dy, sc = align
    pad = (sc - 1) / 2
    tx, ty = bx0 + bw * (-pad + dx), by0 + bh * (-pad + dy)
    tw, th = bw * (1 + 2 * pad), bh * (1 + 2 * pad)
    ex0, ey0, ex1, ey1 = ELEM_BOX
    return crop_resize(img, tx + tw * ex0, ty + th * ey0, tw * (ex1 - ex0), th * (ey1 - ey0), BADGE)


b64 = lambda b: base64.b64encode(b).decode()                       # noqa: E731
made = 0
for shot in sorted(glob.glob('C:/claude/nikke-calc/.shots/*.png')):
    img = cv2.imread(shot)
    if img is None:
        continue
    rows, cols = po.detect_card_grid(img)
    boxes = [(cx0, ry0, cx1, ry1) for (ry0, ry1) in rows for (cx0, cx1) in cols]
    if len(boxes) < 4:
        print(f'{os.path.basename(shot)}: 카드 격자 못 찾음 — 건너뜀')
        continue
    name = os.path.splitext(os.path.basename(shot))[0]
    samples = [[b64(tile(img, boxes[i], a, OCR_C[0])) for a in so.ALIGN] for i in range(0, len(boxes), max(1, len(boxes) // 4))][:4]
    json.dump({'samples': samples}, open(f'{OUT}/align_{name}.json', 'w', encoding='utf-8'))
    i, align = so.pick_align([[base64.b64decode(v) for v in row] for row in samples])
    tiles = [{'c12': b64(tile(img, b, align, 12)), 'c24': b64(tile(img, b, align, 24)),
              'c32': b64(tile(img, b, align, 32)), 'badge': b64(badge(img, b, align))} for b in boxes[:25]]
    locked = {}
    json.dump({'tiles': tiles, 'locked': locked}, open(f'{OUT}/read_{name}.json', 'w', encoding='utf-8'))
    # 잠금이 있는 판도 하나: 첫 칸을 파이썬이 고른 이름으로 고정
    res = so.read([{k: base64.b64decode(t[k]) for k in t} for t in tiles], {})
    if res and res[0]['pick']:
        json.dump({'tiles': tiles, 'locked': {'0': res[0]['pick'], '2': res[2]['pick'] if len(res) > 2 and res[2]['pick'] else res[0]['pick']}},
                  open(f'{OUT}/readlock_{name}.json', 'w', encoding='utf-8'))
    made += 1
    print(f'{name}: 칸 {len(boxes)} · 틀 {i} · 첫 칸 {res[0]["pick"] if res else None}')
print(f'\n픽스처 {made}장 → {OUT}')
