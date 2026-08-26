"""서버 회귀 대조 — 파이썬 서버와 새 서버(web/srv)를 같은 요청으로 때려 계약(SERVER-CONTRACT.md)대로
같은지 본다.

    python deploy/compare_servers.py --py http://127.0.0.1:8931 --new http://127.0.0.1:8932

두 서버는 **서로 다른(깨끗한) STATE_DIRECTORY**로 띄운다 — 같은 요청 순서를 양쪽에 똑같이 보내므로
저장 상태도 대칭으로 자란다. 무작위·시각 값(id·ts·code·expires·sec·uptime)은 눕혀 비교한다.

비교 규칙:
- 상태코드 · 계약 헤더 4종(CSP·nosniff·Referrer-Policy·Cache-Control) · API는 Content-Type까지.
- JSON 본문은 파싱 뒤 깊은 비교(직렬화 포맷 차이는 계약이 아니다).
- 오류 본문은 `error` 문장의 문자열 일치. 예외(계약 §0·§4): JSON 파싱 오류·비수치 duration 문장은
  파서 산물이라 상태코드만 본다.
- 정적은 본문 바이트 일치(404 페이지는 상태만).
- /api/health는 아직 안 옮긴 기능 플래그(cp·ocr·power_ocr·fetch)를 빼고, /api/stats는 시각·부하·
  fetch_on을 눕히고 본다.

라우트를 옮길 때마다 케이스를 얹는다. 관리자 양성 경로(테일넷 발신)는 로컬에서 만들 수 없어
서버 소킹 때 본다 — 여기서는 게이트(404)만 확인한다.

**신선한 서버 한 쌍당 한 번만 돌린다** — 창당 상한(공유 6/분·보드 6/분)에 케이스 수를 맞춰 놨고
운영 카운터(stats)를 대조하므로, 같은 서버에 두 번 돌리면 429와 카운터 불일치가 그 재실행 탓으로 난다.
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

CONTRACT_HEADERS = ("content-security-policy", "x-content-type-options", "referrer-policy", "cache-control")
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def call(base: str, method: str, path: str, body=None, headers: dict | None = None,
         raw: bytes | None = None) -> tuple[int, dict, bytes]:
    data = raw if raw is not None else (
        json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
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


def norm_json(b: bytes, norm_keys=frozenset(), drop_keys=frozenset()):
    v = json.loads(b.decode("utf-8"))

    def walk(x):
        if isinstance(x, dict):
            return {k: (0 if k in norm_keys or k == "sec" else walk(y))
                    for k, y in x.items() if k not in drop_keys}
        if isinstance(x, list):
            return [walk(y) for y in x]
        return x
    return walk(v)


def diff_json(va, vb, out: list[str], path=""):
    if len(out) >= 6:
        return
    if isinstance(va, dict) and isinstance(vb, dict):
        if list(va.keys()) != list(vb.keys()):
            out.append(f"{path}: 키 {list(va)[:9]} vs {list(vb)[:9]}")
        for k in va:
            if k in vb:
                diff_json(va[k], vb[k], out, f"{path}.{k}")
        return
    if isinstance(va, list) and isinstance(vb, list):
        if len(va) != len(vb):
            out.append(f"{path}: 길이 {len(va)} vs {len(vb)}")
        for i, (p, q) in enumerate(zip(va, vb)):
            diff_json(p, q, out, f"{path}[{i}]")
        return
    if va != vb:
        out.append(f"{path}: {va!r} vs {vb!r}")


class Case:
    def __init__(self, name: str, method: str, path: str, body=None, *, kind: str = "json",
                 status_only: bool = False, headers: dict | None = None,
                 norm_keys=frozenset(), drop_keys=frozenset(), raw: bytes | None = None):
        self.name, self.method, self.path, self.body = name, method, path, body
        self.kind, self.status_only, self.headers = kind, status_only, headers
        self.norm_keys, self.drop_keys, self.raw = norm_keys, drop_keys, raw


def names_from_big() -> list[list[str]]:
    """실존 캐릭터로 덱 3개 — 로컬·서버 어디서든 되게 저장소의 파싱 명단에서 결정적으로 뽑는다."""
    names = sorted(json.loads((ROOT / "data" / "parsed_nikke.json").read_text(encoding="utf-8")).keys())
    return [names[0:3], names[3:6], names[6:9]]


NORM_TS = {"ts", "reply_ts", "expires", "id", "code"}
STATS_NORM = {"uptime", "uptime_sec", "load", "fetch_on", "sim_sec"}
# 프록시 라우트의 운영 카운터는 새 서버에서는 사이드카 안에 산다(계약 §9) — 대조에서 뺀다
STATS_DROP = {"cp_req", "ocr_req", "fetch_req", "fetch_ok", "fetch_err", "fetch_err_private",
              "fetch_err_session", "fetch_err_notfound", "fetch_err_other", "fetch_bad_input", "bot_403"}
CP_BODY = {"cls": "화력형", "weapon": "AR", "level": 200, "grade": 0, "core": 0, "affinity": 1,
           "s1": 1, "s2": 1, "ub": 1, "cube_lv": 0, "coll_stage": "없음", "equipment": {},
           "ol": [[None] * 3] * 4, "console": {}}


def build_cases() -> list[Case]:
    decks = names_from_big()
    d0 = decks[0]
    share_gate_body = {"duration": 180, "total": 1234567.0, "code": "철갑",
                       "decks": [{"names": d0[:5] + [None], "total": 1234567.0,
                                  "chars": {d0[0]: 999999.5}, "weak": "철갑"}], "mode": "union"}
    big_pad = {"decks": [{"names": ["a"], "total": 1, "chars": {}}], "pad": "x" * (33 * 1024)}
    return [
        Case("health", "GET", "/api/health"),
        # 계산 — 정상
        Case("sim 1덱", "POST", "/api/sim", {"decks": [d0], "duration": 60}),
        Case("sim 3덱+옵션", "POST", "/api/sim", {
            "decks": decks[:3], "duration": 90, "code": "철갑",
            "enemy": {"def": 12000, "core_px": 120, "has_parts": True,
                      "optimal_range_weapons": ["AR", "XX"], "weapon_coeff": {"AR": 0.9, "SMG": 1.0}},
            "config": {"first_burst_time": 4.2, "max_burst_count": 3, "part_break_interval": 999},
            "controls": [{d0[0]: {"control": {"reload": {"policy": "before_fb_end"}},
                                  "burst_pattern": "every:2", "burst_first": True, "no_burst": False}},
                         {(decks[1][0] if len(decks) > 1 and decks[1] else d0[0]): {"burst_pattern": [3, 1, 3, 999],
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
        Case("sim 본문 없음", "POST", "/api/sim", None),
        Case("sim JSON 아님", "POST", "/api/sim", raw=b"{not json", status_only=True),
        # 공유 — 게이트·오류
        Case("share 게이트", "POST", "/api/share", share_gate_body),  # Sec-Fetch 없음 → 403
        Case("share 덱 없음", "POST", "/api/share", {"duration": 180, "total": 1}, headers=SAME_ORIGIN),
        # 주의: 공유 POST는 분당 6건(서버 전역) — 표 3건 + 시나리오 2건(put·unshare)으로 상한 안에 맞춘다
        Case("share 숫자 아님", "POST", "/api/share",
             {"duration": 180, "total": "x", "decks": [{"names": ["a"], "total": 1, "chars": {}}]},
             headers=SAME_ORIGIN),
        Case("share 413", "POST", "/api/share", big_pad, headers=SAME_ORIGIN),
        Case("share GET 형식", "GET", "/api/share?c=!!"),
        Case("share GET 없음", "GET", "/api/share?c=abcdefgh"),
        # 보드 — 오류·허니팟 (성공 흐름은 시나리오에서)
        Case("board 허니팟", "POST", "/api/board", {"web": 1, "body": "spam"}),
        Case("board 짧음", "POST", "/api/board", {"body": "a"}),
        Case("board 비번 없음", "POST", "/api/board", {"body": "비공개 글입니다", "private": True}),
        Case("board 목록 기본", "GET", "/api/board", norm_keys=NORM_TS),
        Case("board 목록 잘못된 파라미터", "GET", "/api/board?before=abc&n=xyz", norm_keys=NORM_TS),
        # 관리자·지표 게이트 (로컬 발신 → 404 / 지표는 로컬에서 보인다)
        Case("admin 게이트", "GET", "/admin", status_only=True),
        Case("admin.js 게이트", "GET", "/admin.js", status_only=True),
        Case("board admin 게이트", "POST", "/api/board/admin", {"op": "list"}, status_only=True),
        Case("stats", "GET", "/api/stats", norm_keys=STATS_NORM, drop_keys=STATS_DROP),
        # ── 사이드카 프록시 라우트 (투명성 검증 — 응답은 파이썬 코드가 만든다) ──
        Case("cp 정상", "POST", "/api/cp", CP_BODY),
        Case("cp 빈 몸통", "POST", "/api/cp", {}),
        Case("atk 정상", "POST", "/api/atk", {"names": [d0[0]]}),
        Case("atk 오류", "POST", "/api/atk", {"names": []}),
        Case("squad read 빈 tiles", "POST", "/api/squad/read", {"tiles": []}),
        Case("squad read b64 아님", "POST", "/api/squad/read",
             {"tiles": [{"c12": "!!!", "c24": "", "c32": "", "badge": ""}]}),
        Case("squad align 빈 samples", "POST", "/api/squad/align", {"samples": []}),
        Case("power 빈 regions", "POST", "/api/squad/power", {"regions": []}),
        Case("fetch 게이트", "POST", "/api/fetch", {"openid": "123456789"}),
        Case("fetch openid 없음", "POST", "/api/fetch", {"openid": ""}, headers=SAME_ORIGIN),
        Case("fetch openid 해석불가", "POST", "/api/fetch", {"openid": "zzz"}, headers=SAME_ORIGIN),
        Case("fetch result 없음", "GET", "/api/fetch/result?id=zzzz"),
        Case("fetch events 없음", "GET", "/api/fetch/events?id=zzzz"),
        Case("sim result 없음", "GET", "/api/sim/result?id=zzzz"),
        Case("sim events 없음", "GET", "/api/sim/events?id=zzzz"),
        # 프로필 오류는 stats 뒤에 — 응답(문장·상태 400)은 동일하지만, 새 서버는 사전 검증이 없어
        # 운영 카운터(sim_req/sim_err)에 +1로 잡힌다(SERVER-CONTRACT §9의 문서화된 차이)
        Case("sim 이상한 프로필", "POST", "/api/sim", {"decks": [d0], "duration": 60, "profile": {"x": 1}}),
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


def compare(c: Case, a, b) -> list[str]:
    diffs: list[str] = []
    sa, ha, ba = a
    sb, hb, bb = b
    if sa != sb:
        diffs.append(f"상태 {sa} vs {sb}")
        return diffs
    if c.status_only:
        return diffs
    for h in CONTRACT_HEADERS:
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
    if (ha.get("content-type") or "") != (hb.get("content-type") or ""):
        diffs.append(f"Content-Type {ha.get('content-type')!r} vs {hb.get('content-type')!r}")
    try:
        va = norm_json(ba, c.norm_keys, c.drop_keys)
        vb = norm_json(bb, c.norm_keys, c.drop_keys)
    except ValueError as e:
        diffs.append(f"JSON 파싱 실패: {e}")
        return diffs
    if va != vb:
        out: list[str] = []
        diff_json(va, vb, out)
        diffs.extend(out or ["JSON 다름"])
    return diffs


def run_case(c: Case, py: str, new: str) -> list[str]:
    headers = dict(c.headers or {})
    if c.kind == "static_gz":
        headers["Accept-Encoding"] = "gzip"
    a = call(py, c.method, c.path, c.body, headers=headers or None, raw=c.raw)
    b = call(new, c.method, c.path, c.body, headers=headers or None, raw=c.raw)
    return compare(c, a, b)


# ── 시나리오 — 서버별 상태를 만들며 단계마다 대조한다 ───────────────────────

def scenario_share(py: str, new: str) -> list[str]:
    d0 = names_from_big()[0]
    nm = d0[0] if d0 else "a"
    body = {"duration": 180, "total": 42.5,
            "decks": [{"names": [nm], "total": 42.5, "chars": {nm: 42.5}}]}
    diffs: list[str] = []
    codes = {}
    for side, base in (("py", py), ("new", new)):
        s, _, b = call(base, "POST", "/api/share", body, headers=SAME_ORIGIN)
        if s != 200:
            return [f"share put({side}) 상태 {s}: {b[:160]!r}"]
        codes[side] = json.loads(b)["code"]
    got_a = call(py, "GET", f"/api/share?c={codes['py']}")
    got_b = call(new, "GET", f"/api/share?c={codes['new']}")
    diffs += [f"share get: {d}" for d in compare(Case("x", "GET", "/api/share"), got_a, got_b)]
    del_a = call(py, "POST", "/api/unshare", {"code": codes["py"]})
    del_b = call(new, "POST", "/api/unshare", {"code": codes["new"]})
    diffs += [f"unshare: {d}" for d in compare(Case("x", "POST", "/api/unshare"), del_a, del_b)]
    gone_a = call(py, "GET", f"/api/share?c={codes['py']}")
    gone_b = call(new, "GET", f"/api/share?c={codes['new']}")
    diffs += [f"share gone: {d}" for d in compare(Case("x", "GET", "/api/share"), gone_a, gone_b)]
    return diffs


def scenario_board(py: str, new: str) -> list[str]:
    diffs: list[str] = []
    ids: dict[str, dict[str, str]] = {}
    for side, base in (("py", py), ("new", new)):
        s1, _, b1 = call(base, "POST", "/api/board", {"body": "공개 피드백입니다", "nick": "테스터"})
        s2, _, b2 = call(base, "POST", "/api/board", {"body": "공개 피드백입니다", "nick": "다른닉"})
        s3, _, b3 = call(base, "POST", "/api/board",
                         {"body": "비밀 글입니다", "private": True, "pw": "pw1234"})
        if not (s1 == s2 == s3 == 200):
            return [f"board add({side}) 상태 {s1}/{s2}/{s3}"]
        i1, i2, i3 = json.loads(b1)["id"], json.loads(b2)["id"], json.loads(b3)["id"]
        if i1 != i2:
            diffs.append(f"board dup({side}): 같은 본문인데 id가 다르다 {i1} vs {i2}")
        ids[side] = {"pub": i1, "priv": i3}
    la = call(py, "GET", "/api/board?n=5")
    lb = call(new, "GET", "/api/board?n=5")
    diffs += [f"board list: {d}"
              for d in compare(Case("x", "GET", "/api/board", norm_keys=NORM_TS), la, lb)]
    wa = call(py, "POST", "/api/board/view", {"id": ids["py"]["priv"], "pw": "wrong!"})
    wb = call(new, "POST", "/api/board/view", {"id": ids["new"]["priv"], "pw": "wrong!"})
    diffs += [f"board view 오답: {d}" for d in compare(Case("x", "POST", "/x"), wa, wb)]
    va = call(py, "POST", "/api/board/view", {"id": ids["py"]["priv"], "pw": "pw1234"})
    vb = call(new, "POST", "/api/board/view", {"id": ids["new"]["priv"], "pw": "pw1234"})
    diffs += [f"board view 정답: {d}"
              for d in compare(Case("x", "POST", "/x", norm_keys=NORM_TS), va, vb)]
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
    for name, fn in (("share 수명주기", scenario_share), ("board 흐름", scenario_board)):
        diffs = fn(args.py, args.new)
        if diffs:
            n_bad += 1
            print(f"FAIL 시나리오 {name}")
            for d in diffs[:8]:
                print(f"   {d}")
        else:
            print(f"ok   시나리오 {name}")
    print(("PASS 전부 일치" if n_bad == 0 else f"FAIL {n_bad}건"))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
