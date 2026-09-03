# -*- coding: utf-8 -*-
"""얼굴 서명 대조(squad_ocr) 파이썬 정본의 **단계별 기준값** 덤프 — 사이트 TS 포팅 대조용.

입력은 브라우저가 실제로 보내는 요청 본문(JSON 파일)이다:
  read  : {"tiles": [{"c12","c24","c32","badge"}(base64)...], "locked": {"칸": "이름"}}
  align : {"samples": [[base64 × ALIGN 25]...]}
칸마다 signatures(coarse·fine·phash·color) · read_element · score_cell(전 후보 점수) · assign · 최종 read를,
align은 표본·틀별 점수와 best를 적는다.

    python squad_ref_dump.py <요청.json> [...]   → 같은 이름 .ref.json
"""
import io, sys, os, json, base64

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'C:/claude/nikke-calc-dil/web')
import squad_ocr as so                                                # noqa: E402

for path in sys.argv[1:]:
    req = json.load(open(path, encoding='utf-8'))
    out = {'source': os.path.basename(path)}
    dec = lambda v: base64.b64decode(v, validate=True)                 # noqa: E731
    if 'samples' in req:
        got = [[dec(v) for v in row] for row in req['samples']]
        db = so._load()
        C = db['C']
        per = []
        for tile_views in got:
            row = []
            for raw in tile_views:
                px = [(raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]) for i in range(C * C)]
                co = so._norm([so._lum(p) for p, m in zip(px, db['mc']) if m])
                row.append(max(so._dot(co, c['co']) for c in db['cards']))
            per.append(row)
        i, align = so.pick_align(got)
        out.update({'mode': 'align', 'per_sample_scores': per, 'align_index': i, 'align': list(align)})
    else:
        tiles = [{k: dec(t.get(k, '')) for k in ('c12', 'c24', 'c32', 'badge')} for t in req['tiles']]
        locked = {int(k): v for k, v in (req.get('locked') or {}).items() if isinstance(v, str) and v}
        cells, stages = [], []
        for t in tiles:
            bc, bf, bp, badge = so._planes(t)
            co, fi, ph, cl = so.signatures(bc, bf, bp)
            el, ec = so.read_element(badge)
            scored, _e = so.score_cell(t)
            cells.append(scored)
            stages.append({'coarse': co, 'fine': fi, 'phash': ph, 'color': cl,
                           'element': el, 'element_conf': ec,
                           'scores': {nm: [v[0], v[1], v[2]] for nm, v in scored.items()}})
        pick = so.assign(cells, locked)
        out.update({'mode': 'read', 'locked': locked, 'stages': stages, 'assign': pick,
                    'result': so.read(tiles, locked)})
    dst = os.path.splitext(path)[0] + '.ref.json'
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'{os.path.basename(path)} → {os.path.basename(dst)} ({out["mode"]}, '
          f'{len(out.get("stages", out.get("per_sample_scores", [])))}칸)')
