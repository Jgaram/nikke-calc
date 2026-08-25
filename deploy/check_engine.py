"""서버에서 계산 코어 점검 — 같은 요청을 파이썬 경로(`_sim_one`, 순수 파이썬 엔진)와 코어 경로
(`simcore.run_request_batch`)로 돌려 **결과 dict가 같은지**(파생값 포함) 보고, HTTP `/api/sim` 체감 시간을 잰다.

    cd ~/nikke-calc && python3 deploy/check_engine.py            # 기본 덱 1개
    python3 deploy/check_engine.py --names "그레이브,나가,센티" --code 전격 --no-http

배포 뒤·코어를 다시 빌드한 뒤에 한 번 돌린다. 파이썬 경로는 느리다(덱당 2~3초).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))

VOLATILE = ("sec",)


def strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in VOLATILE}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="그레이브,나가,센티,아스카 : WILLE,리타")
    ap.add_argument("--code", default="전격")
    ap.add_argument("--duration", type=float, default=180.0)
    ap.add_argument("--no-http", action="store_true")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    job = {"names": names, "code": args.code, "duration": args.duration, "profile_json": None,
           "enemy": None, "config_over": None, "control": None}

    os.environ["NIKKE_SIM_ENGINE"] = "py"
    import server as srv  # noqa: E402
    import simcore  # noqa: E402

    t0 = time.perf_counter()
    r_py = srv._sim_one(dict(job))
    t_py = time.perf_counter() - t0
    if not simcore.available(srv.ROOT / "data", threads=srv._pool_jobs or 0):
        print("FAIL: 계산 코어를 쓸 수 없다:", simcore.load_error())
        return 1
    simcore.run_request(dict(job))                     # 워밍업(스레드별 데이터 로딩)
    t0 = time.perf_counter()
    r_rs = simcore.run_request(dict(job))
    t_rs = time.perf_counter() - t0
    same = json.dumps(strip(r_py), sort_keys=True, ensure_ascii=False) == json.dumps(strip(r_rs), sort_keys=True, ensure_ascii=False)
    print(f"deck {names} · py {t_py:.2f}s · core {t_rs:.3f}s · {'SAME' if same else 'DIFF'}")
    if not same:
        a, b = strip(r_py), strip(r_rs)
        for k in sorted(set(a) | set(b)):
            if json.dumps(a.get(k), sort_keys=True) != json.dumps(b.get(k), sort_keys=True):
                print("  DIFF", k, str(a.get(k))[:140], "|", str(b.get(k))[:140])
        return 1
    if args.no_http:
        return 0
    base = f"http://127.0.0.1:{args.port}"
    for n in (1, 5):
        body = json.dumps({"decks": [names] * n, "duration": args.duration, "code": args.code}).encode()
        req = urllib.request.Request(base + "/api/sim", data=body, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                j = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {n}덱: {e.code} {e.read()[:200]!r}")
            continue
        el = time.perf_counter() - t0
        res = j.get("results")
        ok = isinstance(res, list) and len(res) == n and all("total" in r for r in res)
        print(f"HTTP {n}덱: {el:.2f}s · {'ok' if ok else 'BAD ' + str(j)[:200]}")
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
