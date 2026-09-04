# -*- coding: utf-8 -*-
"""정답 있는 캡처(.shots)를 여러 폭으로 줄여 저화질 시험 세트를 만든다 + 정답 파일.

    python make_scaled.py <출력 폴더> <폭1,폭2,...>
"""
import io, sys, os, glob
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
OUT, widths = sys.argv[1], [int(v) for v in sys.argv[2].split(',')]
os.makedirs(OUT, exist_ok=True)
truth = {}
for line in open('C:/claude/nikke-calc-dil/data/power_truth.txt', encoding='utf-8'):
    if line.startswith('#') or not line.strip():
        continue
    parts = line.split()
    truth[os.path.basename(parts[0])] = parts[1:]
lines = []
n = 0
for shot in sorted(glob.glob('C:/claude/nikke-calc/.shots/*.png')):
    name = os.path.basename(shot)
    if name not in truth:
        continue
    img = cv2.imread(shot)
    H, W = img.shape[:2]
    for tw in widths:
        if tw >= W:
            continue
        th = int(round(H * tw / W))
        small = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        # 실제 저화질 캡처처럼 JPEG로 한 번 눌러 준다(품질 80)
        out = os.path.join(OUT, f'{os.path.splitext(name)[0]}_w{tw}.jpg')
        cv2.imwrite(out, small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        lines.append(f'{os.path.basename(out)} ' + ' '.join(truth[name]))
        n += 1
open(os.path.join(OUT, 'truth.txt'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print(f'{n}장 → {OUT} (원본 폭 {W})')
