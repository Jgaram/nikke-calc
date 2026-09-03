# -*- coding: utf-8 -*-
"""OpenCV SVM(XML) → TS가 읽을 JSON. 지원 벡터·판별함수(45쌍)·rho·alpha·index·gamma.

OpenCV C_SVC 예측: 클래스 쌍 (i<j) 순서의 판별함수마다 sum = Σ alpha·K(x, sv[index]) − rho,
sum > 0이면 i 표, 아니면 j 표 → 표가 가장 많은 클래스(동률은 앞 클래스).
"""
import io, sys, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
SRC = 'C:/claude/nikke-calc-dil/data/power_svm.xml'
OUT = 'C:/Users/kingdom8/AppData/Local/Temp/claude/C--claude-nikke-calc-rust/3774a69a-e34b-4d44-9d17-fad19aa4dbcf/scratchpad/ocr/power_svm.json'
s = open(SRC, encoding='utf-8').read()


def nums(block):
    return [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?(?:e[-+]?\d+)?', block)]


gamma = float(re.search(r'<gamma>([^<]+)</gamma>', s).group(1))
var_count = int(re.search(r'<var_count>(\d+)</var_count>', s).group(1))
labels = [int(v) for v in nums(re.search(r'<class_labels[^>]*>.*?<data>([^<]*)</data>', s, re.S).group(1))]
sv_block = re.search(r'<support_vectors>(.*?)</support_vectors>', s, re.S).group(1)
svs = [nums(m) for m in re.findall(r'<_>(.*?)</_>', sv_block, re.S)]
assert all(len(v) == var_count for v in svs), {len(v) for v in svs}
df_block = re.search(r'<decision_functions>(.*?)</decision_functions>', s, re.S).group(1)
dfs = []
for m in re.findall(r'<_>\s*<sv_count>(\d+)</sv_count>\s*<rho>([^<]+)</rho>\s*<alpha>([^<]+)</alpha>\s*<index>([^<]+)</index>\s*</_>', df_block, re.S):
    cnt, rho, alpha, index = int(m[0]), float(m[1]), nums(m[2]), [int(v) for v in nums(m[3])]
    assert len(alpha) == cnt == len(index)
    dfs.append({'rho': rho, 'alpha': alpha, 'index': index})
assert len(dfs) == 45, len(dfs)
json.dump({'gamma': gamma, 'var_count': var_count, 'labels': labels, 'sv': svs, 'dfs': dfs},
          open(OUT, 'w', encoding='utf-8'))
print(f'gamma {gamma} · var {var_count} · sv {len(svs)} · df {len(dfs)} → power_svm.json')
