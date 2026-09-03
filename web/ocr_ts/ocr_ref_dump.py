# -*- coding: utf-8 -*-
"""전투력 판독 파이썬 정본의 **단계별 기준값**을 덤프한다 — TS 포팅을 단계마다 대조하기 위해.

캡처(.shots/*.png) → detect_squads로 영역을 잘라(브라우저가 하는 일) read_regions 입력 모양으로 만들고,
각 영역에 대해: 최종 결과 · 숫자 조각 상자 · 정규화 캔버스(16×24) · 증강 7종 캔버스 · HOG(540) · SVM 라벨.
"""
import io, sys, os, json, base64, glob
import numpy as np, cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'C:/claude/nikke-calc-dil/web')
import power_ocr as po                                             # noqa: E402

OUT = 'C:/Users/kingdom8/AppData/Local/Temp/claude/C--claude-nikke-calc-rust/3774a69a-e34b-4d44-9d17-fad19aa4dbcf/scratchpad/ocr_ref'
os.makedirs(OUT, exist_ok=True)
svm = po.load_model()
shots = sorted(glob.glob('C:/claude/nikke-calc/.shots/*.png'))
truth = {}
for line in open('C:/claude/nikke-calc-dil/data/power_truth.txt', encoding='utf-8'):
    if line.startswith('#') or not line.strip():
        continue
    parts = line.split()
    truth[os.path.basename(parts[0])] = parts[1:]

summary = []
for shot in shots:
    img = cv2.imread(shot)
    if img is None:
        continue
    name = os.path.basename(shot)
    regions, cases = [], []
    for box in po.detect_squads(img):
        reg, _abs, _sc = po.normalize_squad_row(img, box)
        if reg is None:
            continue
        h, w = reg.shape[:2]
        rgb = np.ascontiguousarray(reg[:, :, ::-1]).tobytes()
        regions.append({'w': int(w), 'h': int(h), 'rgb': rgb})
    results = po.read_regions(regions)
    for r, res in zip(regions, results):
        arr = np.frombuffer(r['rgb'], np.uint8).reshape(r['h'], r['w'], 3)[:, :, ::-1]
        digs, _bw = po.segment_digits(np.ascontiguousarray(arr))
        digits = []
        for d in digs:
            canvas = po.normalize_digit(d)
            augs = po.augment(canvas)
            digits.append({
                'box': [int(d['x']), int(d['y']), int(d['w']), int(d['h'])],
                'canvas': canvas.tolist(),
                'augs': [a.tolist() for a in augs],
                'hog': [round(float(v), 6) for v in po.hog_of(canvas)],
                'labels': [po.classify_digit(svm, a) for a in augs],
            })
        cases.append({'w': r['w'], 'h': r['h'], 'rgb_b64': base64.b64encode(r['rgb']).decode(),
                      'expected': res, 'digits': digits})
    json.dump({'shot': name, 'truth': truth.get(name), 'cases': cases},
              open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    got = [c['expected']['value'] for c in cases]
    summary.append((name, got, truth.get(name)))
    print(f'{name}: 영역 {len(cases)}개 · 판독 {got} · 정답 {truth.get(name)}')
print(f'\n덤프 {len(summary)}장 → {OUT}')
