"""전투력 판독기 학습·평가 — 표본이 늘어나면 이것만 다시 돌리면 된다.

    python power_train.py            # 캡처 하나씩 빼고 시험(정직한 성적) + 최종 학습
    python power_train.py --quick    # 시험 없이 전부로 학습만

정답표는 `truth.txt` 한 줄에 «캡처 숫자5개». 캡처를 추가하려면 그 줄만 늘리면 된다.
"""
import sys, os, time, json
sys.stdout.reconfigure(encoding="utf-8")
import cv2, numpy as np
import power_read as P

TRUTH_TXT = "truth.txt"
MODEL = "power_svm.xml"


def load_truth(path=TRUTH_TXT):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        out[parts[0]] = [int(v) for v in parts[1:]]
    return out


def collect(src, truths):
    """캡처 한 장 -> [(정규화된 숫자그림, 정답숫자)]. 끊기가 어긋난 줄은 버린다."""
    img = cv2.imread(src)
    if img is None:
        print(f"    [!] 못 읽음 {src}")
        return []
    out, rows_ok, rows_all = [], 0, 0
    for b, truth in zip(P.detect_squads(img), truths):
        rows_all += 1
        reg, _a, _s = P.normalize_squad_row(img, b)
        if reg is None:
            continue
        digs, _bw = P.segment_digits(reg)
        s = str(truth)
        if len(digs) != len(s):
            continue
        rows_ok += 1
        for d, ch in zip(digs, s):
            out.append((P.normalize_digit(d), int(ch)))
    print(f"    {os.path.basename(src)}: 줄 {rows_ok}/{rows_all} 사용 · 숫자 {len(out)}개")
    return out


def build(pool, keys):
    X, y = [], []
    for k in keys:
        for canvas, lab in pool[k]:
            for a in P.augment(canvas):
                X.append(a)
                y.append(lab)
    return X, y


def main():
    truth = load_truth()
    print(f"정답표 {len(truth)}장 · 숫자 줄 {sum(len(v) for v in truth.values())}개")
    pool = {}
    for src, t in truth.items():
        pool[src] = collect(src, t)
    n_dig = sum(len(v) for v in pool.values())
    print(f"  학습 표본 {n_dig}개 (증강 전)")
    per = {}
    for canvas, lab in [x for v in pool.values() for x in v]:
        per[lab] = per.get(lab, 0) + 1
    print("  숫자별:", " ".join(f"{d}:{per.get(d, 0)}" for d in range(10)))

    if "--quick" not in sys.argv and len(truth) > 1:
        print("\n캡처 하나씩 빼고 시험 (정직한 성적):")
        tot = ok_all = 0
        for held in truth:
            X, y = build(pool, [k for k in truth if k != held])
            if len(set(y)) < 10:
                print(f"  [{os.path.basename(held)} 빼면 숫자 종류가 모자람 — 건너뜀]")
                continue
            svm = P.train_svm(X, y)
            img = cv2.imread(held)
            t0 = time.time()
            got = P.read_all_squad_powers(img, svm)
            ms = (time.time() - t0) * 1000
            want = truth[held]
            ok = sum(1 for g, t in zip(got, want) if g == t)
            tot += len(want)
            ok_all += ok
            print(f"  {os.path.basename(held):<18} {ok}/{len(want)} · {ms:.0f}ms")
            for g, t in zip(got, want):
                if g != t:
                    print(f"      {t} -> {g}")
        if tot:
            print(f"  합계 {ok_all}/{tot} ({round(100 * ok_all / tot)}%)")

    X, y = build(pool, list(truth))
    svm = P.train_svm(X, y, auto="--auto" in sys.argv)
    svm.save(MODEL)
    print(f"\n최종 학습 완료 (증강 후 {len(X)}개) → {MODEL}")


if __name__ == "__main__":
    main()
