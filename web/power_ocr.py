"""니케 솔로레이드 전투력 숫자 판독 — 이 UI에 특화된 가벼운 판독기.

범용 OCR을 쓰지 않는다. 파란 «SQUAD» 라벨을 기준점으로 삼아 배율을 재고, 그
배율로 전투력 영역을 상대 위치로 잡은 뒤, 숫자 조각을 갈라 0~9만 분류한다.

    powers = read_all_squad_powers(image, load_model())

절대 좌표를 박지 않는다. 입력이 0.5배든 2배든 SQUAD 라벨 크기로 정규화한다.

문서: web/docs/캡처판독-전투력숫자.md
학습: python scraper/power_train.py   (정답표는 data/power_truth.txt)
"""
from __future__ import annotations

import cv2
import numpy as np

# ── 표준 크기. 검출된 SQUAD 라벨을 이 폭이 되게 맞춰 이후 처리를 한 크기에서 한다.
STD_SQUAD_W = 96
# 전투력 영역 — SQUAD 라벨 상자를 기준으로 한 상대 배수 (첨부 캡처에서 실측)
POWER_X0, POWER_X1 = 1.15, 6.2
# 세로 창은 «넓히는» 게 아니라 «내리는» 것이다. 라벨 기준으로 잡으면 위에 흰 공간이
# 남고 아래에서 글자가 잘린다 — 그러면 쉼표와 숫자가 구분되지 않는다.
# 실측(자릿수 맞는 줄 / 40): -0.15~1.15 37 · -0.15~1.35 39 · **0.00~1.40 40** ·
# -0.35~1.35 40(창이 더 넓다) · -0.55~1.55 21(위아래 다른 요소를 문다).
POWER_Y0, POWER_Y1 = 0.00, 1.40
DIGIT_W, DIGIT_H = 16, 24


# ────────────────────────────────────────────────────────────── 1. SQUAD 검출
def detect_squads(img):
    """파란 SQUAD 라벨을 찾는다 → [(x, y, w, h), ...] 위에서 아래로."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 라벨은 진한 하늘색이다. 색조 범위를 넓게 잡고 채도·명도로 조인다.
    mask = cv2.inRange(hsv, np.array([95, 120, 90]), np.array([115, 255, 255]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cand = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 12 or h < 6:
            continue
        if not (1.6 < w / h < 6.0):                 # 가로로 긴 사각형
            continue
        if area < w * h * 0.45:                     # 속이 찬 덩어리
            continue
        cand.append((x, y, w, h))
    if not cand:
        return []
    # 여러 개가 «비슷한 크기»로 «비슷한 x»에 세로로 반복된다 — 그 무리만 남긴다
    ws = np.array([c[2] for c in cand], float)
    med_w = float(np.median(ws))
    cand = [c for c in cand if abs(c[2] - med_w) <= med_w * 0.35]
    if not cand:
        return []
    xs = np.array([c[0] for c in cand], float)
    med_x = float(np.median(xs))
    cand = [c for c in cand if abs(c[0] - med_x) <= med_w * 0.8]
    return sorted(cand, key=lambda c: c[1])


# ─────────────────────────────────────────────────── 2·3. 배율 정규화 + 영역
def normalize_squad_row(img, box):
    """SQUAD 라벨 상자 기준으로 전투력 영역을 잘라 표준 배율로 맞춘다.

    **줄이지는 않는다.** 큰 캡처를 줄이면 정보를 버리는 것이라서다. 반대로 키우는
    것은 효과가 없다 — 보간은 없던 정보를 만들지 못한다. 붙은 두 글자는 키워도
    붙은 채로 커지고, 1px 쉼표는 뭉갠 채로 커진다(실측: 96px 92% · 200px 92% ·
    280px 82%). 그래서 «작으면 키우고 크면 그대로»가 답이다.
    """
    x, y, w, h = box
    scale = max(1.0, STD_SQUAD_W / float(w))
    H, W = img.shape[:2]
    x0 = int(round(x + w * POWER_X0))
    x1 = int(round(x + w * POWER_X1))
    y0 = int(round(y + h * POWER_Y0))
    y1 = int(round(y + h * POWER_Y1))
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    if x1 - x0 < 8 or y1 - y0 < 6:
        return None, None, scale
    region = img[y0:y1, x0:x1]
    out = cv2.resize(region, (max(1, int(round(region.shape[1] * scale))),
                              max(1, int(round(region.shape[0] * scale)))),
                     interpolation=cv2.INTER_CUBIC)
    return out, (x0, y0, x1, y1), scale


def find_power_region(img, box):
    return normalize_squad_row(img, box)


# ────────────────────────────────────────────────────────── 4·5. 숫자 분리
def _components(gray, offset):
    """배경 밝기에서 offset만큼 어두운 것을 글자로 본다 → 조각 목록."""
    bg = float(np.percentile(gray, 90))
    thr = max(10.0, bg - offset)
    bw = (gray < thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 2:
            continue
        out.append({"x": x, "y": y, "w": w, "h": h, "area": int(area),
                    "mask": (lab[y:y + h, x:x + w] == i)})
    return sorted(out, key=lambda c: c["x"]), bw


def _pick_digits(comps, want_commas=False):
    """왕관·쉼표·잡티를 걷어 내고 숫자 줄만 남긴다.

    `want_commas`면 «숫자 줄 안의 쉼표»도 함께 돌려준다 — 쉼표 뒤는 반드시 세
    자리이므로 끊기를 검산하는 데 쓴다.
    """
    if len(comps) < 3:
        return []
    hs = np.array([c["h"] for c in comps], float)
    med_h = float(np.median(hs))
    # 숫자는 높이가 고르다. 왕관은 크고 쉼표는 작다.
    keep = [c for c in comps if 0.65 * med_h <= c["h"] <= 1.35 * med_h]
    if len(keep) < 3:
        return []
    # 밑선이 거의 같다
    base = float(np.median([c["y"] + c["h"] for c in keep]))
    keep = [c for c in keep if abs((c["y"] + c["h"]) - base) <= med_h * 0.28]
    if len(keep) < 3:
        return []
    # 왕관을 떼어 낸다. «간격 중앙값 x 배수» 같은 고정 배수로는 안 된다 — 캡처마다
    # 크기가 달라 어떤 것은 문턱 바로 아래로 들어온다. 대신 **바깥값**으로 본다:
    # 양 끝 조각이 나머지 어떤 간격보다도 유난히 멀면 그건 숫자가 아니다.
    # (이걸 안 해서 왕관이 «54»·«543»으로 쪼개져 앞에 붙었다 — 실측 오답 7건 중 6건)
    keep.sort(key=lambda c: c["x"])
    for _ in range(4):
        if len(keep) < 5:
            break
        gaps = [keep[i + 1]["x"] - (keep[i]["x"] + keep[i]["w"])
                for i in range(len(keep) - 1)]
        if gaps[0] >= 1.5 * max(gaps[1:]) and gaps[0] > 1:
            keep = keep[1:]
            continue
        if gaps[-1] >= 1.5 * max(gaps[:-1]) and gaps[-1] > 1:
            keep = keep[:-1]
            continue
        break
    if not want_commas:
        return keep
    # 쉼표 — 숫자보다 훨씬 낮고 밑선 아래에 붙는다. 숫자 구간 안의 것만 센다.
    lo, hi = keep[0]["x"], keep[-1]["x"] + keep[-1]["w"]
    base2 = float(np.median([c["y"] + c["h"] for c in keep]))
    top2 = float(np.median([c["y"] for c in keep]))
    # 진짜 쉼표는 **숫자와 숫자 사이 빈틈**에 있다. 숫자 밑에 겹치는 작은 조각은
    # 밑동이 떨어져 나온 파편이다(실측: 쉼표 3개짜리 줄에서 후보가 8개 잡혔다).
    spans = [(c["x"], c["x"] + c["w"]) for c in keep]

    def in_gap(cx):
        return not any(a - 1 <= cx <= b + 1 for a, b in spans)

    commas = [c for c in comps
              if lo < c["x"] + c["w"] / 2 < hi
              and c["h"] <= med_h * 0.5
              and c["y"] > top2 + med_h * 0.45
              and c["y"] + c["h"] <= base2 + med_h * 0.45
              and in_gap(c["x"] + c["w"] / 2)]
    return keep, sorted(commas, key=lambda c: c["x"])


def _split_wide(comps, unit):
    """붙어 버린 조각을 «세로획이 가장 옅은 골짜기»에서 가른다."""
    out = []
    for c in comps:
        n = max(1, int(round(c["w"] / unit)))
        if n == 1:
            out.append(c)
            continue
        col = c["mask"].sum(axis=0)
        cuts = [0]
        for k in range(1, n):
            mid = int(k * len(col) / n)
            lo, hi = max(1, mid - 2), min(len(col) - 1, mid + 3)
            # 조각이 아주 좁으면 고를 자리가 없다 — 그때는 균등분할 자리를 쓴다
            cuts.append(int(min(range(lo, hi), key=lambda t: (col[t], abs(t - mid))))
                        if hi > lo else mid)
        cuts.append(len(col))
        for k in range(n):
            a, b = cuts[k], cuts[k + 1]
            if b - a < 1:
                continue
            sub = c["mask"][:, a:b]
            ys = np.where(sub.any(axis=1))[0]
            if not len(ys):
                continue
            out.append({"x": c["x"] + a, "y": c["y"] + int(ys[0]),
                        "w": b - a, "h": int(ys[-1] - ys[0] + 1),
                        "area": int(sub.sum()),
                        "mask": sub[ys[0]:ys[-1] + 1]})
    return out


def _score_segmentation(digs, med_h):
    """고름 정도로만 점수를 낸다. 쉼표는 점수가 아니라 **관문**으로 쓴다
    (가산점으로 두면 잘못 쪼갠 13자리가 보너스를 받아 이겨 버린다 — 실측)."""
    hs = np.array([d["h"] for d in digs], float)
    ws = np.array([d["w"] for d in digs], float)
    gaps = np.array([digs[i + 1]["x"] - (digs[i]["x"] + digs[i]["w"])
                     for i in range(len(digs) - 1)], float)
    s = 0.0
    s -= float(np.std(hs) / (np.mean(hs) + 1e-6)) * 2.0
    s -= float(np.std(ws) / (np.mean(ws) + 1e-6)) * 1.2
    if len(gaps):
        s -= float(np.std(gaps)) / (med_h + 1e-6) * 0.8
    return s


def _comma_ok(digs, commas):
    """쉼표로 나눈 묶음이 «1~3자리 + 3자리 x n»인가.

    전투력 표기가 늘 그 꼴이라 이게 가장 강한 검산이다. 쉼표를 못 찾았으면
    (nc<2) 판단을 보류한다 — 없는 근거로 후보를 떨구지 않는다.
    """
    nc = len(commas)
    if nc < 2:
        return None
    cen = [d["x"] + d["w"] / 2 for d in digs]
    cuts = [sum(1 for t in cen if t < c["x"] + c["w"] / 2) for c in commas]
    if sorted(set(cuts)) != cuts:
        return False
    groups = [cuts[0]] + [cuts[k] - cuts[k - 1] for k in range(1, nc)]              + [len(digs) - cuts[-1]]
    return 1 <= groups[0] <= 3 and all(g == 3 for g in groups[1:])


# 문턱 하나로 고정하지 않는다 — 캡처마다 밝기·압축이 달라 숫자가 붙거나 갈라진다.
# 칸 폭도 조금씩 달리해 끊어 보고, 쉼표 검산으로 가장 그럴듯한 것을 고른다.
OFFSETS = (60, 75, 90, 105, 120, 135)
UNIT_SCALES = (0.86, 0.93, 1.0, 1.08, 1.16)


def segment_digits(region, offsets=OFFSETS):
    """여러 문턱·여러 칸폭으로 끊어 보고 가장 그럴듯한 것을 고른다 → 숫자 조각.

    **쉼표는 한 번만 세어 모든 후보에 똑같이 적용한다.** 문턱마다 다시 세면
    3개 -> 1개 -> 0개로 사라져서, 정답 끊기가 «쉼표 0개»로 잡혀 관문을 못 받고
    엉뚱한 후보에 점수로 밀린다(실측: 정답이 후보 안에 있는데도 떨어졌다).
    """
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    # 1단계 — 문턱마다 조각과 쉼표를 모아 둔다
    passes = []
    for off in offsets:
        comps, bw = _components(gray, off)
        got = _pick_digits(comps, want_commas=True)
        if isinstance(got, list) or len(got[0]) < 4:
            continue
        passes.append((got[0], got[1], bw))
    if not passes:
        return [], None

    # 2단계 — 쉼표 개수는 «0이 아닌 것 중 가장 흔한 값». 그 개수를 낸 문턱의
    #          쉼표 위치를 대표로 쓴다. 전투력은 늘 3자리씩 끊기므로 3개가 보통이다.
    counts = [len(c) for _b, c, _w in passes if c]
    ref = []
    if counts:
        mode = max(set(counts), key=lambda v: (counts.count(v), -abs(v - 3)))
        for _b, c, _w in passes:
            if len(c) == mode:
                ref = c
                break

    # 3단계 — 모든 후보를 같은 쉼표로 검산한다
    best, best_key, best_bw = None, (-1, -9e9), None
    for base, _c, bw in passes:
        ws = sorted(d["w"] for d in base)
        solo = [w for w in ws if w <= ws[len(ws) // 2] * 1.4] or ws
        unit0 = solo[len(solo) // 2]
        for us in UNIT_SCALES:
            digs = _split_wide(base, max(1.0, unit0 * us))
            digs.sort(key=lambda d: d["x"])
            if len(digs) < 4:
                continue
            ok = _comma_ok(digs, ref)
            if ok is False:
                continue
            med_h = float(np.median([d["h"] for d in digs]))
            key = (1 if ok else 0, _score_segmentation(digs, med_h))
            if key > best_key:
                best, best_key, best_bw = digs, key, bw
    return best or [], best_bw


# ──────────────────────────────────────────────────────────── 6. 정규화
def normalize_digit(comp):
    """조각 → DIGIT_W x DIGIT_H. 비율을 지키고 가운데 정렬한다."""
    m = (comp["mask"].astype(np.uint8)) * 255
    h, w = m.shape
    sc = min((DIGIT_H - 4) / h, (DIGIT_W - 4) / w)
    nw, nh = max(1, int(round(w * sc))), max(1, int(round(h * sc)))
    r = cv2.resize(m, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((DIGIT_H, DIGIT_W), np.uint8)
    y0 = (DIGIT_H - nh) // 2
    x0 = (DIGIT_W - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    return canvas


# ────────────────────────────────────────────────────── 7. HOG + SVM 분류
_HOG = cv2.HOGDescriptor((DIGIT_W, DIGIT_H), (8, 8), (4, 4), (4, 4), 9)


def hog_of(canvas):
    return _HOG.compute(canvas).ravel()


def augment(canvas):
    """저화질·압축·번짐을 흉내 내 표본을 늘린다 — 실제 캡처마다 화질이 다르다."""
    out = [canvas]
    small = cv2.resize(canvas, (DIGIT_W // 2, DIGIT_H // 2), interpolation=cv2.INTER_AREA)
    out.append(cv2.resize(small, (DIGIT_W, DIGIT_H), interpolation=cv2.INTER_CUBIC))
    out.append(cv2.GaussianBlur(canvas, (3, 3), 0.8))
    M = np.float32([[1, 0, 0.6], [0, 1, 0]])
    out.append(cv2.warpAffine(canvas, M, (DIGIT_W, DIGIT_H)))
    M = np.float32([[1, 0, -0.6], [0, 1, 0]])
    out.append(cv2.warpAffine(canvas, M, (DIGIT_W, DIGIT_H)))
    out.append(cv2.dilate(canvas, np.ones((2, 2), np.uint8)))
    out.append(cv2.erode(canvas, np.ones((2, 2), np.uint8)))
    return out


def train_svm(samples, labels, auto=False):
    """HOG -> SVM(RBF). `auto`면 격자 탐색을 하지만 표본이 늘면 아주 느리다
    (2천개에서 수십 분). 평소에는 실측으로 정한 고정 파라미터로 충분하다."""
    X = np.array([hog_of(s) for s in samples], np.float32)
    y = np.array(labels, np.int32)
    svm = cv2.ml.SVM_create()
    svm.setType(cv2.ml.SVM_C_SVC)
    svm.setKernel(cv2.ml.SVM_RBF)
    svm.setTermCriteria((cv2.TERM_CRITERIA_MAX_ITER + cv2.TERM_CRITERIA_EPS, 3000, 1e-6))
    if auto:
        svm.trainAuto(X, cv2.ml.ROW_SAMPLE, y)
    else:
        svm.setC(12.5)
        svm.setGamma(0.5 / max(1, X.shape[1]))
        svm.train(X, cv2.ml.ROW_SAMPLE, y)
    return svm


def classify_digit(svm, canvas):
    f = np.array([hog_of(canvas)], np.float32)
    return int(svm.predict(f)[1][0][0])


def read_power(img, box, svm):
    reg, _abs, _sc = normalize_squad_row(img, box)
    if reg is None:
        return None
    digs, _bw = segment_digits(reg)
    if not digs:
        return None
    txt = "".join(str(classify_digit(svm, normalize_digit(d))) for d in digs)
    return int(txt) if txt.isdigit() else None


def read_all_squad_powers(img, svm):
    return [read_power(img, b, svm) for b in detect_squads(img)]


# ─────────────────────────────────────────────── 니케 카드 격자 (SQUAD 앵커 기반)
# 채도 투영만으로 카드 줄을 찾던 방식은 캡처 모양이 조금만 달라도 무너진다
# (실측: 8장 중 2장에서 0행 1열 — 니케 판독이 통째로 실패했다).
# SQUAD 라벨은 8장 전부에서 잡히므로 그걸 기준으로 삼는다.
# 아래 배수는 **실측**이다 — 기존 방식이 성공한 캡처 6장에서 «SQUAD 라벨 대비
# 카드 줄 위치»를 재서 얻었다(위 1.17~2.04 · 아래 3.26~4.31 · 좌 1.41 · 우 5.44).
# 창은 그보다 넉넉히 잡고, 안쪽에서 채도로 실제 경계를 다시 찾는다.
CARD_Y0, CARD_Y1 = 1.00, 4.60      # SQUAD 라벨 높이 기준, 카드 줄을 넉넉히 감싸는 창
CARD_X0, CARD_X1 = 1.20, 5.80      # SQUAD 라벨 폭 기준


def detect_card_grid(img, squads=None):
    """SQUAD 앵커에서 니케 카드 5x5 격자를 찾는다 → (rows, cols) 절대 좌표."""
    squads = squads if squads is not None else detect_squads(img)
    if not squads:
        return [], []
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    rows, colsets = [], []
    for (x, y, w, h) in squads:
        y0 = max(0, int(round(y + h * CARD_Y0)))
        y1 = min(H, int(round(y + h * CARD_Y1)))
        x0 = max(0, int(round(x + w * CARD_X0)))
        x1 = min(W, int(round(x + w * CARD_X1)))
        if y1 - y0 < 8 or x1 - x0 < 20:
            continue
        band = sat[y0:y1, x0:x1] > 28
        # 세로: 카드가 실제로 차지하는 높이
        rp = band.sum(axis=1)
        thr_r = max(3, band.shape[1] * 0.12)
        ys = np.where(rp >= thr_r)[0]
        if not len(ys):
            continue
        rows.append((y0 + int(ys[0]), y0 + int(ys[-1]) + 1))
        # 가로: 카드 다섯 칸
        cp = band.sum(axis=0)
        thr_c = max(3, band.shape[0] * 0.12)
        runs, st = [], None
        for i, v in enumerate(list(cp) + [0]):
            if v >= thr_c and st is None:
                st = i
            elif v < thr_c and st is not None:
                if i - st >= max(6, (x1 - x0) // 30):
                    runs.append((x0 + st, x0 + i))
                st = None
        if len(runs) >= 3:
            colsets.append(runs)
    if not rows or not colsets:
        return [], []
    # 열은 줄마다 조금씩 흔들린다 — 가장 흔한 개수를 낸 줄의 것을 대표로 쓴다
    want = max(set(len(c) for c in colsets),
               key=lambda n: (sum(1 for c in colsets if len(c) == n), n))
    cols = next(c for c in colsets if len(c) == want)
    return rows, cols


# ────────────────────────────────────────────────────────── 서버에서 쓰는 입구
import os as _os

STABLE_GATE = 0.72          # 이 아래면 «애매» — 실측으로 정한 값(위 주석 참조)
_MODEL_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            "data", "power_svm.xml")
_SVM = None


def available() -> bool:
    return _os.path.exists(_MODEL_PATH)


def load_model():
    """학습된 SVM을 한 번만 읽어 둔다."""
    global _SVM
    if _SVM is None:
        if not available():
            raise RuntimeError("data/power_svm.xml이 없다 — python scraper/power_train.py")
        _SVM = cv2.ml.SVM_load(_MODEL_PATH)
    return _SVM


def read_regions(regions):
    """브라우저가 잘라 보낸 «전투력 영역»들을 읽는다.

    regions = [{"w":..., "h":..., "rgb": bytes(w*h*3)}, ...]
    영역은 이미 SQUAD 라벨 기준으로 잘라 배율까지 맞춰 온 것이다 —
    캡처 원본은 서버에 올라오지 않는다(니케 얼굴 판독과 같은 원칙).
    """
    svm = load_model()
    out = []
    for r in regions:
        w, h = int(r["w"]), int(r["h"])
        buf = r["rgb"]
        if w < 8 or h < 6 or len(buf) != w * h * 3:
            out.append({"value": None, "text": "", "digits": 0,
                            "stable": 0.0, "sure": False, "box": None})
            continue
        arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)[:, :, ::-1]   # RGB->BGR
        digs, _bw = segment_digits(np.ascontiguousarray(arr))
        if not digs:
            out.append({"value": None, "text": "", "digits": 0,
                            "stable": 0.0, "sure": False, "box": None})
            continue
        # 확신도 — 조각을 조금 흔들었을 때 답이 바뀌는가. 바뀌면 애매한 것이다.
        # 실측(캡처를 빼고 학습한 예측 40줄): 정답 줄은 최저 0.86, 오답 줄은 0.57·0.71.
        # 겹치지 않아서 0.72로 그으면 **오답 2/2를 헛표시 0으로** 잡는다.
        txt, stable = "", 1.0
        for d in digs:
            c = normalize_digit(d)
            votes = {}
            for a in augment(c):
                v = classify_digit(svm, a)
                votes[v] = votes.get(v, 0) + 1
            lab = max(votes, key=votes.get)
            txt += str(lab)
            stable = min(stable, max(votes.values()) / sum(votes.values()))
        # 실제로 잘라 읽은 구간을 함께 돌려준다 — 사람이 «읽은 값»과 «그 자리»를
        # 눈으로 대조할 수 있어야 고칠 수 있다.
        x0 = min(d["x"] for d in digs)
        x1 = max(d["x"] + d["w"] for d in digs)
        y0 = min(d["y"] for d in digs)
        y1 = max(d["y"] + d["h"] for d in digs)
        out.append({"value": int(txt) if txt.isdigit() else None,
                    "text": txt, "digits": len(digs),
                    "stable": round(stable, 3), "sure": bool(stable >= STABLE_GATE),
                    "box": [int(x0), int(y0), int(x1), int(y1)],
                    "rw": w, "rh": h})
    return out
