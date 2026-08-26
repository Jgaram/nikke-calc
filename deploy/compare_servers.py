"""서버 회귀 대조 — 파이썬 서버와 새 서버(web/srv)를 같은 요청으로 때려 계약(SERVER-CONTRACT.md)대로
같은지 본다.

    python deploy/compare_servers.py --py http://127.0.0.1:8931 --new http://127.0.0.1:8932

비교 규칙:
- 상태코드 · 계약 헤더 4종(CSP·nosniff·Referrer-Policy·Cache-Control) · API는 Content-Type까지.
- JSON 본문은 **파싱 뒤 깊은 비교**(직렬화 포맷 차이는 계약이 아니다) — 단 `sec`(시간)은 0으로 눕히고,
  /api/health는 아직 안 옮긴 기능 플래그(cp·ocr·power_ocr·share·fetch)를 빼고 본다(슬라이스 단계).
- 오류 본문은 `error` 문장의 **문자열 일치**. 예외 둘(계약 §0·§4 예외): JSON 파싱 오류·비수치 duration의
  문장은 파서 산물이라 상태코드만 본다.
- 정적은 본문 바이트 일치(404 페이지는 상태만 — 파이썬 기본 오류 페이지는 계약이 아니다).

슬라이스 단계라 /api/sim·/api/health·정적·/s·404만 때린다. 라우트를 옮길 때마다 여기에 케이스를 얹는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

CONTRACT_HEADERS = ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Cache-Control")


def call(base: str, method: str, path: str, body: dict | None = None,
         headers: dict | None = None) -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept-Encoding", "identity")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def norm_json(b: bytes, drop_health_flags: bool = False):
    v = json.loads(b.decode("utf-8"))
    def walk(x):
        if isinstance(x, dict):
            return {k: (0 if k == "sec" else walk(y)) for k, y in x.items()}
        if isinstance(x, list):
            return [walk(y) for y in x]
        return x
    v = walk(v)
    if drop_health_flags and isinstance(v, dict):
        for k in ("cp", "ocr", "power_ocr", "share", "fetch"):
            v.pop(k, None)
    return v


class Case:
    def __init__(self, name: str, method: str, path: str, body: dict | None = None, *,
                 kind: str = "json", status_only: bool = False, health: bool = False,
                 skip_cache_header: bool = False):
        self.name, self.method, self.path, self.body = name, method, path, body
        self.kind, self.status_only, self.health = kind, status_only, health
        self.skip_cache_header = skip_cache_header


def names_from_big() -> list[list[str]]:
    """무작위 세트 케이스에서 실제 존재하는 덱 이름 몇 개를 빌린다."""
    decks = []
    for cid in ("g0001", "g0002", "g0003"):
        p = ROOT / "harness_missing"  # placeholder — 아래에서 nikke-core 쪽을 본다
    core = Path(r"C:\claude\nikke-core\harness\cases\big")
    for cid in ("g0001", "g0002", "g0003"):
        f = core / f"{cid}.in.json"
        if f.exists():
            case = json.loads(f.read_text(encoding="utf-8"))
            decks.append([str(n) for n in case["meta"]["request"]["names"]])
    return decks or [[]]


def build_cases() -> list[Case]:
    decks = names_from_big()
    d0 = decks[0]
    cases: list[Case] = [
        Case("health", "GET", "/api/health", health=True),
        # 계산 — 정상
        Case("sim 1덱", "POST", "/api/sim", {"decks": [d0], "duration": 60}),
        Case("sim 3덱+옵션", "POST", "/api/sim", {
            "decks": decks[:3], "duration": 90, "code": "철갑",
            "enemy": {"def": 12000, "core_px": 120, "has_parts": True,
                      "optimal_range_weapons": ["AR", "XX"], "weapon_coeff": {"AR": 0.9, "SMG": 1.0}},
            "config": {"first_burst_time": 4.2, "max_burst_count": 3, "part_break_interval": 999},
            "controls": [{d0[0]: {"control": {"reload": {"policy": "before_fb_end"}},
                                  "burst_pattern": "every:2", "burst_first": True, "no_burst": False}},
                         {decks[1][0] if len(decks) > 1 and decks[1] else d0[0]: {"burst_pattern": [3, 1, 3, 999],
                                                                                  "no_burst": True}},
                         {}],
            "cubes": [{d0[0]: {"name": "습격 큐브", "level": 7}}, None, None],
            "levels": [200, None, 0],
            "codes": ["작열", None, None],
            "enemies": [None, {"def": 500}, None],
            "configs": [None, {"burst_regen_time": 2.0}, {}],
        }),
        # 계산 — 입력 오류 (문장까지)
        Case("sim decks 없음", "POST", "/api/sim", {"decks": []}),
        Case("sim 덱 초과", "POST", "/api/sim", {"decks": [["a"]] * 13}),
        Case("sim 덱 모양", "POST", "/api/sim", {"decks": [["a", "", "b"]]}),
        Case("sim 빈 슬롯", "POST", "/api/sim", {"decks": [[None, "a"]]}),
        Case("sim duration 밖", "POST", "/api/sim", {"decks": [d0], "duration": 0.5}),
        Case("sim duration 위", "POST", "/api/sim", {"decks": [d0], "duration": 601}),
        Case("sim 없는 캐릭터", "POST", "/api/sim", {"decks": [["없는캐릭터"]], "duration": 60}),
        Case("sim 이상한 프로필", "POST", "/api/sim", {"decks": [d0], "duration": 60, "profile": {"x": 1}}),
        Case("sim 본문 없음", "POST", "/api/sim", None),
        Case("sim JSON 아님", "POST", "/api/sim", None, kind="rawbad", status_only=True),
        # 정적·라우팅
        Case("index", "GET", "/", kind="static"),
        Case("s 페이지", "GET", "/s?c=abcd1234", kind="static"),
        Case("s 슬래시", "GET", "/s/?c=abcd1234", kind="redirect"),
        Case("style css", "GET", "/style.css", kind="static"),
        Case("i18n gz", "GET", "/i18n/en.js", kind="static_gz"),
        Case("없는 정적", "GET", "/no-such-file.xyz", kind="static", status_only=True),
        Case("없는 api GET", "GET", "/api/no-such", kind="static", status_only=True),
        Case("없는 api POST", "POST", "/api/no-such", {"x": 1}),
    ]
    return cases


def run_case(c: Case, py: str, new: str) -> list[str]:
    diffs: list[str] = []
    if c.kind == "rawbad":
        def raw(base):
            req = urllib.request.Request(base + c.path, data=b"{not json", method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
            except urllib.error.HTTPError as e:
                return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        a, b = raw(py), raw(new)
    else:
        gz = {"Accept-Encoding": "gzip"} if c.kind == "static_gz" else None
        a = call(py, c.method, c.path, c.body, headers=gz)
        b = call(new, c.method, c.path, c.body, headers=gz)
    sa, ha, ba = a
    sb, hb, bb = b
    if sa != sb:
        diffs.append(f"상태 {sa} vs {sb}")
        return diffs
    if c.status_only:
        return diffs
    for h in [x.lower() for x in CONTRACT_HEADERS]:
        if c.skip_cache_header and h == "cache-control":
            continue
        if (ha.get(h) or "") != (hb.get(h) or ""):
            diffs.append(f"헤더 {h}: {ha.get(h)!r} vs {hb.get(h)!r}")
    if c.kind == "redirect":
        if (ha.get("location") or "") != (hb.get("location") or ""):
            diffs.append(f"Location {ha.get('location')!r} vs {hb.get('location')!r}")
        return diffs
    if c.kind in ("static", "static_gz"):
        if c.kind == "static_gz" and (ha.get("content-encoding") or "") != (hb.get("content-encoding") or ""):
            diffs.append(f"Content-Encoding {ha.get('content-encoding')!r} vs {hb.get('content-encoding')!r}")
        if ba != bb:
            diffs.append(f"본문 바이트 다름 ({len(ba)} vs {len(bb)})")
        return diffs
    # JSON API
    if (ha.get("content-type") or "") != (hb.get("content-type") or ""):
        diffs.append(f"Content-Type {ha.get('content-type')!r} vs {hb.get('content-type')!r}")
    try:
        va = norm_json(ba, drop_health_flags=c.health)
        vb = norm_json(bb, drop_health_flags=c.health)
    except ValueError as e:
        diffs.append(f"JSON 파싱 실패: {e}")
        return diffs
    if va != vb:
        out = []
        def _diff(x, y, path=""):
            if len(out) >= 6:
                return
            if isinstance(x, dict) and isinstance(y, dict):
                if list(x.keys()) != list(y.keys()):
                    out.append(f"{path}: 키 {list(x)[:8]} vs {list(y)[:8]}")
                for k in x:
                    if k in y:
                        _diff(x[k], y[k], f"{path}.{k}")
                return
            if isinstance(x, list) and isinstance(y, list):
                if len(x) != len(y):
                    out.append(f"{path}: 길이 {len(x)} vs {len(y)}")
                for i, (p, q) in enumerate(zip(x, y)):
                    _diff(p, q, f"{path}[{i}]")
                return
            if x != y:
                out.append(f"{path}: {x!r} vs {y!r}")
        _diff(va, vb)
        diffs.extend(out or ["JSON 다름"])
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", default="http://127.0.0.1:8931")
    ap.add_argument("--new", default="http://127.0.0.1:8932")
    args = ap.parse_args()
    n_bad = 0
    for c in build_cases():
        diffs = run_case(c, args.py, args.new)
        if diffs:
            n_bad += 1
            print(f"FAIL {c.name}")
            for d in diffs[:6]:
                print(f"   {d}")
        else:
            print(f"ok   {c.name}")
    print(("PASS 전부 일치" if n_bad == 0 else f"FAIL {n_bad}건"))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
