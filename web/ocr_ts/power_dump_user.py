# -*- coding: utf-8 -*-
"""유저 캡처 폴더 → 전투력 판독 기준값(파이썬 정본) 덤프. ocr_ref_dump.py의 폴더 인자판.

    python power_dump_user.py <캡처 폴더> <출력 폴더> [정답 파일]
정답 파일: 줄마다 「파일이름 숫자 숫자 …」(쉼표 없이).
"""
import io, sys, os, json, base64, glob
import numpy as np, cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'C:/claude/nikke-calc-dil/web')
import power_ocr as po                                             # noqa: E402

SRC, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
truth = {}
if len(sys.argv) > 3:
    for line in open(sys.argv[3], encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        truth[parts[0]] = [p.replace(',', '') for p in parts[1:]]

svm = po.load_model()
shots = sorted(p for p in glob.glob(os.path.join(SRC, '*')) if p.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')))
for shot in shots:
    img = cv2.imread(shot)
    if img is None:
        print(f'{os.path.basename(shot)}: 못 읽음'); continue
    name = os.path.basename(shot)
    regions, cases = [], []
    boxes = po.detect_squads(img)
    for box in boxes:
        reg, _abs, _sc = po.normalize_squad_row(img, box)
        if reg is None:
            continue
        h, w = reg.shape[:2]
        regions.append({'w': int(w), 'h': int(h), 'rgb': np.ascontiguousarray(reg[:, :, ::-1]).tobytes()})
    results = po.read_regions(regions)
    for r, res in zip(regions, results):
        cases.append({'w': r['w'], 'h': r['h'], 'rgb_b64': base64.b64encode(r['rgb']).decode(), 'expected': res})
    json.dump({'shot': name, 'truth': truth.get(name), 'cases': cases},
              open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    got = [c['expected']['value'] for c in cases]
    H, W = img.shape[:2]
    print(f'{name} ({W}x{H}): 스쿼드 상자 {len(boxes)} · 영역 {len(cases)} · 판독 {got} · 정답 {truth.get(name)}')
