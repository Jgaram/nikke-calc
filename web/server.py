"""웹앱 서버 — 정적 서빙 + 조회 프록시 + 병렬 계산.

    python web/server.py                 # 8765, 127.0.0.1 (Tailscale 안에서만)
    python web/server.py --port 8080 --host 0.0.0.0
    python web/server.py --no-fetch      # 조회 프록시 없이 (계산만)

표준 라이브러리만 쓴다. `web/build.py`가 만든 `web/dist/`를 서빙한다.

## 왜 서버가 있나 (없어도 전부 동작한다)

브라우저만으로 다 된다 — 수집은 북마클릿, 변환·계산은 Pyodide 워커다. 서버는 두 가지만
더한다:

| 라우트 | 하는 일 | 왜 서버여야 하나 |
|---|---|---|
| `POST /api/fetch` | openid로 블라링크 조회 → `raws`(지역별 목록) 반환 | 브라우저는 CORS로 못 부른다 (OPTIONS 405, ACAO 없음). 그리고 로그인 세션이 필요하다 |
| `POST /api/sim` | 덱 목록을 계산 코어가 한 번에 조립·계산·요약, **결과를 바로 답한다**(동기) | 실측(2026-08-26): 5덱 0.1~0.3초. 큐는 입장 제한으로만 남는다 |

**둘 다 선택이다.** `/api/sim`이 없으면 브라우저가 계산하고, `/api/fetch`가 없으면
북마클릿으로 받는다. 서버가 죽어도 정적 배포판은 그대로 동작한다.

## 저장하지 않는다

프로필·raw를 디스크에 쓰지 않는다. 정본은 방문자의 localStorage다. 조회 응답도 **캐시하지 않는다** —
캐시가 있으면 방금 육성한 것을 다시 불러도 옛 값이 나온다. **방문자를 구분하지 않는다** —
IP로 세지도 기록하지도 않고, 대신 조회를 **한 번에 하나씩만** 돌린다(`fetch_turn`).

## `/api/fetch`는 운영자 세션을 쓴다

`scraper/.session_cookie`(gitignore)로 조회한다. 즉 **모든 조회가 운영자 계정으로 나간다.**
1인 조회는 실측 7요청·345KB·3초이므로 캐시를 끼면 부하는 평탄하지만, 공개 트래픽이 커지면
그만큼 운영자 계정의 요청이 늘어난다는 사실은 바뀌지 않는다. 그게 싫으면 `--no-fetch`로
끄고 북마클릿(방문자 자기 세션)만 안내한다.

조회 대상은 **공개 프로필만** 가능하다 — 비공개 계정은 블라링크가 막는다(실측).

## 배포

`deploy/nikke-decklab.service`가 상시 구동용 systemd 유닛이다 (읽기 전용 파일시스템·
권한 없음·PrivateTmp). 서버는 `127.0.0.1`에만 바인딩하고, 밖으로는 Tailscale Funnel이
443 → 8766으로 넘긴다.
"""
from __future__ import annotations

import argparse
import traceback
import base64
import json
import os
import queue
import re
import secrets
import hashlib
import hmac
import sqlite3
import sys
import uuid
import threading
import time
import zlib
import urllib.parse
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

for _s in (sys.stdout, sys.stderr):     # 한글·em대시가 콘솔 코드페이지(cp949)로 깨지지 않게
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "web"))
import cp_engine  # noqa: E402  전투력 계산기 — 산식은 이 서버 밖으로 내보내지 않는다
import squad_ocr  # noqa: E402  스쿼드 캡처 판독 — 대조군 서명표도 서버에만 둔다
# 선택 기능은 없어도 서버가 뜬다. 다만 **왜 꺼졌는지는 반드시 남긴다** —
# 조용히 삼키면 상용에서 총딜을 못 읽고 있어도 아무도 모른다(실제로 그랬다:
# 서버에 OpenCV가 없어 `power_ocr`이 꺼진 채로 돌고 있었다).
_OPTIONAL_OFF: "dict[str, str]" = {}
try:
    import power_ocr  # noqa: E402  전투력 숫자 판독 (OpenCV). 없으면 그 기능만 꺼진다
except Exception as _e:              # noqa: BLE001
    power_ocr = None
    _OPTIONAL_OFF["power_ocr"] = f"{type(_e).__name__}: {_e}"

LV_MAX = 1400                   # 니케 레벨 상한 — 표가 1400까지 있다(app.js LV_MAX와 같다)
MAX_BODY = 8 * 1024 * 1024      # 8MB — 프로필(199종)이 400KB대라 넉넉하다
MAX_DECKS = 12                  # 요청 하나에 담을 수 있는 덱 수
MAX_DURATION = 600.0
RATE_WINDOW = 60.0              # 레이트리밋 창(초)
RATE_MAX_SIM = 12               # 「새 계산 차단」을 켰을 때 창당 계산 요청 (서버 전체)
RATE_MAX_CP = 600               # 창당 전투력 계산기 요청 — 옵션 클릭마다 오는 가벼운 산수다
RATE_MAX_SHARE = 6              # 창당 공유 링크 생성
RATE_MAX_OCR = 60               # 창당 스쿼드 캡처 판독 (한 장에 2번 오간다)
RATE_MAX_BOARD = 6              # 창당 피드백 등록 — 사람 손이 낼 수 있는 속도만
OCR_MAX_TILES = 30              # 솔로레이드는 5스쿼드 x 5명 = 25칸이 최대다
OCR_MAX_POWERS = 5              # 스쿼드는 다섯 개를 넘지 않는다

# ── 편성 공유 ─────────────────────────────────────────────────────────────
# 저장하는 것은 **편성과 표시용 딜 수치뿐**이다. 육성 스펙·닉네임·스펙 지문·기본 스펙
# 이탈 목록은 담지 않는다 (`share_clean`이 화이트리스트로 다시 만든다).
SHARE_TTL = 86400.0            # 하루. 링크를 오래 살려 둘 이유가 없고, 짧으면 저장량도 유계다
SHARE_MAX_BODY = 32 * 1024     # 편성만 담으면 2KB 안쪽이다 — 넉넉하되 blob 저장소가 되지 않게
SHARE_MAX_CHARS = 8            # 덱 하나에 담을 수 있는 니케 수 (5인이지만 여유를 둔다)
# systemd `StateDirectory=`가 넣어 주는 경로. 없으면(로컬 개발) 저장소 안에 만든다.
_state_env = (os.environ.get("STATE_DIRECTORY") or "").split(os.pathsep)[0]
SHARE_DIR = Path(_state_env) if _state_env else (ROOT / "web" / ".state")
SHARE_DB = SHARE_DIR / "share.db"
OPS_PATH = SHARE_DIR / "ops.json"

# 운영 중 바꾸는 서버 설정. 개인정보와 무관한 불리언 하나뿐이며, 재시작해도 운영자가
# 고른 상태를 유지한다. 파일이 아직 없으면 동시 요청을 받는 쪽(차단 꺼짐)이 기본이다.
_ops_lock = threading.Lock()
_ops_loaded = False
_ops = {"sim_busy_guard": False}


def _ops_load_locked() -> None:
    """운영 설정을 한 번 읽는다. **호출자가 `_ops_lock`을 쥐고 있어야 한다.**"""
    global _ops_loaded
    if _ops_loaded:
        return
    _ops_loaded = True
    try:
        raw = json.loads(OPS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _ops["sim_busy_guard"] = raw.get("sim_busy_guard") is True
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as e:
        print(f"운영 설정을 읽지 못해 기본값을 씁니다 ({OPS_PATH}): {e}", file=sys.stderr)


def sim_busy_guard_enabled() -> bool:
    with _ops_lock:
        _ops_load_locked()
        return bool(_ops["sim_busy_guard"])


def set_sim_busy_guard(enabled: bool) -> bool:
    """새 계산 차단 상태를 원자적으로 저장하고 적용한다."""
    enabled = bool(enabled)
    with _ops_lock:
        _ops_load_locked()
        try:
            SHARE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = OPS_PATH.with_name(OPS_PATH.name + ".tmp")
            tmp.write_text(json.dumps({"sim_busy_guard": enabled}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, OPS_PATH)
        except OSError as e:
            raise RuntimeError(f"운영 설정을 저장하지 못했습니다: {e}") from e
        _ops["sim_busy_guard"] = enabled
    return enabled

# **방문자를 구분하지 않는다.** IP로 세지도, 기록하지도 않는다 — 그러려면 방문자를
# 식별해 두어야 하는데 그건 이 사이트가 하지 않기로 한 일이다. 대신 서버가 **자기가
# 밖으로 거는 호출**을 스스로 조인다.
#
# 조회 **한 건 안에서는** 조이지 않는다. 조회 하나는 블라링크 요청 6건이고 순차로
# 나가므로(실측 4.5초) 그 자체는 이미 «사람 한 명이 쓰는 속도»다. 여기에 간격을 더
# 넣으면 멀쩡한 조회만 느려진다.
#
# 막아야 하는 건 **여러 조회가 겹치는 것**이다. 세 명이 동시에 누르면 블라링크에는
# 세 갈래가 한꺼번에 들이치고, 그 모양이 긁는 것처럼 보인다. 그래서 조회도 계산과
# 똑같이 **줄을 세우고 워커 하나가 차례로** 처리한다. 브라우저는 긴 POST로 버티는
# 대신 이벤트 스트림으로 «앞에 몇 건»을 보며 기다린다.
FETCH_QUEUE_MAX = 6             # 진행·대기를 합친 조회 수. 넘으면 그때만 거절한다

# 계산은 **한 번에 하나씩**만 돈다. 여러 명이 동시에 눌러도 순서대로 처리하고, 그동안
# 대기 순번을 알려 준다. 동시에 여러 건을 돌리면 코어를 나눠 쓰느라 모두가 느려지고,
# 그 사이 서버는 응답도 못 한다 — 차라리 줄을 세우는 편이 전체 대기시간이 짧다.
SIM_SLOTS = 1                   # 동시에 도는 계산 작업 수
SIM_QUEUE_MAX = 12              # 대기까지 포함한 최대 작업 수
JOB_TTL = 900.0                 # 끝난 작업을 기억해 두는 시간(초)
SSE_MAX = 1800.0                # 이벤트 스트림 최대 유지(초)

_hits: dict[tuple[str, str], list[float]] = {}
_hits_lock = threading.Lock()



# ── 운영 지표 ─────────────────────────────────────────────────────────────
# **개수만 센다.** 누가 불렀는지는 여전히 보지 않는다 — 주소도, 세션도, openid도
# 남기지 않는다. 디스크에도 안 쓴다(재시작하면 0부터). 알고 싶은 건 «얼마나 쓰이나»지
# «누가 쓰나»가 아니다.
_stats = {
    "start": time.time(),
    "page": 0,            # 페이지(정적 문서) 요청
    "sim_req": 0, "sim_deck": 0, "sim_err": 0, "sim_sec": 0.0,
    "fetch_req": 0, "fetch_ok": 0, "fetch_err": 0,
    # 실패를 한 숫자로 뭉치면 «비공개 계정이 많다»와 «세션이 만료됐다»를 구분할 수
    # 없다. 앞은 정상적인 사용자 실수고 뒤는 운영자가 손을 써야 하는 일이다.
    "fetch_err_private": 0,     # 「니케 정보 공개」가 꺼진 계정
    "fetch_err_session": 0,     # 운영자 세션 만료 — 쿠키를 갱신해야 한다
    "fetch_err_notfound": 0,    # 없는 openid / 캐릭터를 못 받음
    "fetch_err_other": 0,
    "fetch_bad_input": 0,       # openid·URL을 해석할 수 없어 큐에도 못 들어간 것
    "busy_429": 0, "bot_403": 0, "cp_req": 0,
    "share_put": 0, "share_get": 0, "share_miss": 0, "share_del": 0,
}
_stats_lock = threading.Lock()


def bump(key: str, n=1) -> None:
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n


# 유입 주소 집계. 보낸 쪽 **페이지 주소를 그대로** 센다 — 어느 글에서 오는지 알려면
# 쿼리까지 필요하다(갤러리 글 주소가 `?no=…` 형태다). 방문자를 구분하지 않으므로
# «접속자를 기록하지 않는다»는 약속과 충돌하지 않는다: 남는 것은 «어느 날 어느
# 주소에서 몇 번» 뿐이고, 그 주소는 보내는 쪽 공개 페이지다.
# 참고: 요즘 브라우저는 외부로 나갈 때 도메인만 보내는 경우가 많아, 경로가 없는
# 항목도 정상이다.
#
# **일자별로 디스크에 쌓는다** — 메모리 카운터는 재시작하면 사라져서 «어제보다
# 늘었나»를 볼 수 없다. 쓰기가 매 페이지 요청마다 오므로 큐에 모았다가 묶어 넣는다.
REF_LEN = 300          # 한 주소 길이 상한
REF_KST_OFFSET = 9 * 60 * 60  # 운영 통계의 날짜 경계는 한국 표준시(UTC+9)
_ref_q: list[tuple[str, str, str]] = []          # (날짜, 도메인, 주소)
_ref_lock = threading.Lock()


def _ref_init(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS ref ("
              "day TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL, "
              "n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, url))")
    c.commit()


def _ref_flush() -> None:
    """큐에 모인 유입을 한 번에 반영한다. 실패해도 방문에는 영향이 없어야 한다."""
    with _ref_lock:
        batch, _ref_q[:] = list(_ref_q), []
    if not batch:
        return
    try:
        with _share_lock:
            c = _share_conn(); _ref_init(c)
            c.executemany(
                "INSERT INTO ref (day, host, url, n) VALUES (?,?,?,1) "
                "ON CONFLICT(day, url) DO UPDATE SET n = n + 1", batch)
            c.commit()
    except Exception as e:                                        # noqa: BLE001
        print(f"ref  유입 기록 실패(무시): {e}", file=sys.stderr)


def _ref_day(at: float | None = None) -> str:
    """서버 OS 시간대와 무관한 한국 날짜."""
    at = time.time() if at is None else at
    return time.strftime("%Y-%m-%d", time.gmtime(at + REF_KST_OFFSET))


def bump_ref(referer: str | None) -> None:
    day = _ref_day()
    if not referer:
        host = url = "(직접·북마크)"
    else:
        try:
            u = urllib.parse.urlsplit(referer)
        except ValueError:
            return
        if not u.hostname or u.hostname.endswith("tetra-pantone.ts.net"):
            return                               # 사이트 내부 이동은 유입이 아니다
        host, url = u.hostname, referer[:REF_LEN]
    with _ref_lock:
        _ref_q.append((day, host, url))
        due = len(_ref_q) >= 20
    if due:
        _ref_flush()


def ref_stats(days: int = 30) -> dict:
    """일자별·도메인별 유입. 관리자 화면이 쓴다."""
    _ref_flush()
    since = _ref_day(time.time() - days * 86400)
    with _share_lock:
        c = _share_conn(); _ref_init(c)
        by_day = c.execute("SELECT day, SUM(n) FROM ref WHERE day >= ? "
                           "GROUP BY day ORDER BY day DESC", (since,)).fetchall()
        by_host = c.execute("SELECT host, SUM(n) FROM ref WHERE day >= ? "
                            "GROUP BY host ORDER BY SUM(n) DESC LIMIT 30",
                            (since,)).fetchall()
        by_url = c.execute("SELECT url, SUM(n) FROM ref WHERE day >= ? "
                           "GROUP BY url ORDER BY SUM(n) DESC LIMIT 60",
                           (since,)).fetchall()
        grid = c.execute("SELECT day, host, SUM(n) FROM ref WHERE day >= ? "
                         "GROUP BY day, host ORDER BY day DESC, SUM(n) DESC",
                         (since,)).fetchall()
    return {
        "days": [{"day": d, "n": n} for d, n in by_day],
        "hosts": [{"host": h, "n": n} for h, n in by_host],
        "urls": [{"url": u, "n": n} for u, n in by_url],
        "grid": [{"day": d, "host": h, "n": n} for d, h, n in grid],
    }


# ── 계산 엔진 선택 ───────────────────────────────────────────────────────────
# NIKKE_SIM_ENGINE = py(기본, calculator/ 순수 파이썬) · native(web/simcore — 컴파일된 계산 코어,
# 같은 입력에 같은 결과). native에서 코어를 못 쓰거나 예외가 나면 **그 요청은 실패로 끝난다** —
# 파이썬으로 대신 답하지 않는다(운영 결정: 조용히 다른 경로로 답하느니 고장을 드러낸다).
SIM_ENGINE = os.environ.get("NIKKE_SIM_ENGINE", "py")

# ── 동기 계산 ────────────────────────────────────────────────────────────────
# 계산 요청은 **붙들고 바로 답한다** — 코어가 덱당 0.1초 안이라 줄·이벤트 스트림으로 진행을 보여 줄
# 이유가 없어졌다(2026-08-26). 큐는 **입장 제한**으로만 남는다: 동시에 도는 계산은 SIM_SLOTS개,
# 그 뒤에 기다리는 요청은 SIM_QUEUE_MAX까지, 그보다 오래 기다리게 되면 429다. 운영자 스위치
# (`sim_busy_guard`)가 켜져 있으면 기다리지 않고 바로 거절한다(예전 `job_submit(reject_if_busy)`와 같다).
_sim_gate = threading.BoundedSemaphore(SIM_SLOTS)
_sim_gate_lock = threading.Lock()
_sim_running = 0
_sim_waiting = 0
SIM_WAIT_MAX = 30.0             # 초 — 이보다 기다리게 되면 거절한다

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
_pool_jobs = 1
_allow_fetch = True


# ── 계산 (서브프로세스에서 돈다) ───────────────────────────────────────────
def _simulate(squad: list, config: dict, enemy: dict | None):
    """`NIKKE_SIM_ENGINE`에 따라 계산 코어를 고른다. 반환은 SimResult."""
    if SIM_ENGINE != "native":
        from calculator.timeline import simulate
        return simulate(squad, config=config, enemy=enemy, verbose=False)
    import simcore
    if not simcore.available(ROOT / "data"):
        print(f"[sim] 계산 코어를 쓸 수 없다: {simcore.load_error()}", flush=True)
        raise RuntimeError("계산 코어를 쓸 수 없습니다")
    return simcore.run(squad, config, enemy)


def _sim_one(job: tuple) -> dict:
    """덱 하나를 계산한다. **모듈 최상위 함수여야 한다** — Windows spawn이 피클한다.

    브라우저 워커(`web/src/worker.js` run_one)와 **같은 것을 계산해야 한다.**
    한쪽만 고치면 서버를 켜고 끄는 것만으로 총딜이 달라진다.
    """
    # **dict로 받는다.** 위치 튜플이면 필드를 하나 늘리는 순간, 아직 살아 있는 옛
    # 부모 프로세스가 만든 짧은 튜플을 (디스크에서 새로 임포트된) 이 워커가 받아
    # "not enough values to unpack"으로 죽는다 — 코드는 맞는데 서버만 고장 난 것처럼
    # 보이는 종류의 사고다. dict면 없는 키가 None으로 빠질 뿐이다.
    if not isinstance(job, dict):
        # 아직 살아 있는 **옛 부모 프로세스**가 만든 위치 튜플. 워커는 디스크에서 새로
        # 임포트되므로 여기만 새 코드가 되고, 그 조합이 "not enough values to unpack"으로
        # 터졌다. 길이에 맞춰 받아 주면 서버를 재시작하지 않아도 계산은 된다.
        keys = ("names", "code", "duration", "profile_json", "enemy", "config_over")
        job = dict(zip(keys, tuple(job)))
    names = job["names"]
    code = job.get("code")
    duration = job["duration"]
    profile_json = job.get("profile_json")
    enemy = job.get("enemy")
    config_over = job.get("config_over")
    control = job.get("control")
    import time as _t

    from calculator.timeline import simulate
    from context import spec as char_spec

    t0 = _t.perf_counter()
    prof = None
    if profile_json:
        prof = char_spec.profile_from_dict(json.loads(profile_json), 
                                        where="전달된 프로필")
    # 컨트롤은 캐릭터별 오버라이드로 들어간다 — 육성 프로필이 아니라 **운용**이다
    squad = char_spec.build_squad([str(n) for n in names], control or None, profile=prof)
    config = char_spec.build_config(squad, {**(config_over or {}),
                                            "duration": float(duration),
                                            "rng_mode": "expected"})
    if enemy is None:
        enemy = {"code": code} if code else None
    r = _simulate(squad, config, enemy)
    # 니케별 내역 — 총딜 하나로는 «왜 이 딜인지»를 못 읽는다.
    # 기본공격/스킬 비중·히트 수·크리 횟수는 히트 목록에 이미 다 들어 있다.
    from calculator.sim_result import _is_normal, summarize_top_atk, dps_timeline, burst_cycles
    detail = {}
    for _nm in r.char_total:
        _h = [e for e in r.hits if e.caster == _nm]
        _n = sum(e.damage for e in _h if _is_normal(e))
        detail[_nm] = {"total": r.char_total[_nm], "normal": _n,
                       "skill": r.char_total[_nm] - _n,
                       "hits": len(_h),
                       "crit": sum(getattr(e, "crit_frac", 0.0) for e in _h)}
    return {
        "sec": _t.perf_counter() - t0,
        "total": r.squad_total,
        "chars": r.char_total,
        "detail": detail,
        # 「최종 공격력이 가장 높은 아군」 대상 버프가 누구에게 갔나 (미란다 애장품 등).
        # 시뮬이 verbose 없이도 모으므로 **여기 얹는 데 추가 비용이 없다** —
        # 이것 때문에 계산을 한 번 더 돌리지 않아도 된다.
        "top_atk": summarize_top_atk(r),
        "notes": char_spec.format_deviations(squad, profile=prof,
                                             show_profile_header=False,
                                             growth_as_cards=True,
                                             hide_cube=True).strip(),
        # 스킬 레벨·애장품 단계·미육성 — 위 notes에서 문장으로 안 나가는 대신
        # 여기로 원자료가 온다. 결과 화면이 초상화 카드로 그린다(유저 피드백:
        # 글로 쭉 나열하지 말고 버프 대상처럼 칸으로 보여 달라).
        "growth_flags": prof.growth_flags([c["name"] for c in squad]) if prof else None,
        # 타임라인 — 결과 화면 하단의 확인용 그래프 하나가 쓴다. 저장은 안 한다
        # (기록에는 안 실린다, collectDecks() 참고). hits는 이미 다 갖고 있으므로
        # 구간별로 접는 데 비용이 거의 없다.
        "timeline": dps_timeline(r),
        "burst_cycles": burst_cycles(r),
    }


# 전투 조건 — UI가 보낸 값을 **범위로 자른 뒤** 넘긴다. 그대로 통과시키면
# 음수 방어력·거대한 파츠 주기 같은 값이 계산기 안에서 이상하게 돈다.
_ENEMY_NUM = {"def": (0, 9_999_999), "core_px": (0, 400)}
_CONFIG_NUM = {
    "first_burst_time": (0.0, 60.0), "burst_switch_delay": (0.0, 3.0),
    "burst_reenter_delay": (0.0, 5.0), "part_break_interval": (0.0, 180.0),
    "max_burst_count": (1, 60),
    # 게이지 재충전(초). UI는 아직 안 보내지만 통로는 열어 둔다 — 기믹 보스 근사용
    "burst_regen_time": (0.5, 30.0),
}
_WEAPONS = ("AR", "SMG", "SG", "SR", "RL", "MG")


def _num(v, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(v)))
    except (TypeError, ValueError):
        return None


def _clean_enemy(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    out: dict = {}
    if e.get("code"):
        out["code"] = str(e["code"])[:8]
    for k, (lo, hi) in _ENEMY_NUM.items():
        if k in e:
            v = _num(e[k], lo, hi, int)
            if v is not None:
                out[k] = v
    if "has_parts" in e:
        out["has_parts"] = bool(e["has_parts"])
    w = e.get("optimal_range_weapons")
    if isinstance(w, list):
        out["optimal_range_weapons"] = [x for x in _WEAPONS if x in w]
    wc = e.get("weapon_coeff")
    if isinstance(wc, dict):
        # 무기군별 평타 실전 계수. 1.0은 무보정이라 안 보내는 것과 같으므로 걸러
        # 페이로드·지문을 짧게 유지한다 (범위는 UI 입력 상한과 맞춘 안전 클램프)
        cleaned = {}
        for k in _WEAPONS:
            v = _num(wc.get(k), 0.1, 1.5, float)
            if v is not None and v != 1.0:
                cleaned[k] = v
        if cleaned:
            out["weapon_coeff"] = cleaned
    return out or None


_CTRL_KEYS = {"tap_fire", "reload", "cover", "hold"}


def _clean_control(c) -> dict | None:
    """UI가 보낸 컨트롤을 **아는 키만** 남겨 통과시킨다.

    임의 dict를 그대로 `build_squad`에 흘리면 오타 하나가 조용히 무시되거나
    엉뚱한 키로 들어간다. `sequence`는 아직 UI에 없으므로 받지 않는다.
    """
    if not isinstance(c, dict):
        return None
    out = {}
    for name, v in c.items():
        if not isinstance(v, dict):
            continue
        entry = {}
        # dict인 키만 통과, 단 명시적 False도 통과 — 레이어가 자동으로 건 컨트롤의
        # 해제 표식이다(엔진 양쪽이 값을 truthy로 거르므로 «꺼짐». 계약 §4 control).
        ctrl = {k: v[k] for k in _CTRL_KEYS
                if isinstance(v.get(k), dict) or v.get(k) is False}
        if ctrl:
            entry["control"] = ctrl
        # 버스트 주기 — 세 모양을 받는다: 카탈로그 이름 · "every:N" · 사이클 목록.
        # ("안 씀"은 옛 저장값 호환 — 지금 UI에는 없다.)
        bp = v.get("burst_pattern")
        if isinstance(bp, str) and bp and len(bp) <= 40:
            if bp.startswith("every:"):
                try:
                    n = int(bp.split(":", 1)[1])
                except ValueError:
                    n = 0
                if 1 <= n <= 99:
                    entry["burst_pattern"] = f"every:{n}"
            else:
                entry["burst_pattern"] = None if bp == "안 씀" else bp
        elif (isinstance(bp, list) and 0 < len(bp) <= 40
              and all(isinstance(x, int) and 1 <= x <= 999 for x in bp)):
            entry["burst_pattern"] = sorted(set(bp))
        # 선버 — 같은 단계에서 배치보다 먼저. 값은 불리언 하나뿐이다.
        if v.get("burst_first") is True:
            entry["burst_first"] = True
        if entry:
            out[str(name)] = entry
    return out or None


def _clean_cubes(c) -> dict | None:
    """칸 큐브 → {니케: {"cube": {name, level}}}. 캐릭터 오버라이드 모양으로 낸다.

    큐브는 «칸에 붙는 설정»이라 컨트롤과 별도로 오지만, 계산에 넣을 때는 캐릭터
    오버라이드(`build_squad`)의 `cube`가 된다 — 프로필 층(계정 보유 최고)보다 우선한다.
    브라우저 워커(`worker.js`)와 **같은 규약**이어야 한다. 이름 길이·레벨 범위는
    `_clean_control`과 같은 이유로 여기서 막는다.
    """
    if not isinstance(c, dict):
        return None
    out = {}
    for name, v in c.items():
        if not isinstance(v, dict):
            continue
        nm, lv = v.get("name"), v.get("level")
        if not isinstance(nm, str) or not nm or len(nm) > 40:
            continue
        lv = _num(lv, 0, 15, int)
        if lv is None:
            continue
        out[str(name)[:40]] = {"cube": {"name": nm, "level": lv}}
    return out or None


def _no_burst_names(c) -> list[str]:
    """컨트롤에서 «버스트 금지»로 표시된 캐릭터 이름만 뽑는다.

    이건 캐릭터 오버라이드(`build_squad`)가 아니라 전투 설정(`config`)으로 가야 한다 —
    `timeline._rebuild_burst_order`가 버스트 후보에서 빼는 방식이라서다. 이름 길이를
    막는 건 `_clean_control`과 같은 이유다(임의 문자열을 그대로 흘리지 않는다).
    """
    if not isinstance(c, dict):
        return []
    return [str(n)[:40] for n, v in c.items()
            if isinstance(v, dict) and v.get("no_burst") is True][:5]


def _clean_config(c) -> dict:
    if not isinstance(c, dict):
        return {}
    out: dict = {}
    for k, (lo, hi) in _CONFIG_NUM.items():
        if k in c:
            v = _num(c[k], lo, hi, int if k == "max_burst_count" else float)
            if v is not None:
                out[k] = v
    return out


def _lower_priority() -> None:
    """워커 프로세스를 **낮은 우선순위**로 내린다 (풀 초기화 때 자식에서 돈다).

    안 내리면 코어를 100%로 채운 워커들이 HTTP 스레드를 굶겨서, 계산이 도는 동안
    새로고침조차 안 먹는다 — "서버가 멈췄다"로 보이는 게 이것이다. 서버는
    `ThreadingHTTPServer`라 요청 자체는 병렬인데, CPU가 없으면 소용이 없다.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)
    except Exception:                                   # noqa: BLE001
        pass                                            # 못 내려도 계산은 된다


# ── 계산 큐 ────────────────────────────────────────────────────────────────
_sim_q: "queue.Queue[str]" = queue.Queue()
_fetch_q: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_job_seq = 0


class BusyError(ValueError):
    """서버가 요청 모양은 이해했지만 지금은 작업을 받아 둘 수 없다."""


def _gc_jobs() -> None:
    """끝난 지 오래된 작업을 지운다. **호출자가 `_jobs_lock`을 쥐고 있어야 한다.**"""
    now = time.time()
    for k in [k for k, j in _jobs.items()
              if j["state"] in ("done", "error") and now - j["at"] > JOB_TTL]:
        _jobs.pop(k, None)


def job_pos(jid: str) -> int:
    """제 줄에서의 대기 순번 (1 = 다음 차례). 이미 도는 중이거나 끝났으면 0.

    계산과 조회는 **서로 다른 줄**이다 — 계산이 밀려 있다고 조회 순번이 뒤로 가면
    사용자에게 거짓말이 된다.
    """
    with _jobs_lock:
        me = _jobs.get(jid)
        if not me or me["state"] != "queued":
            return 0
        return 1 + sum(1 for j in _jobs.values()
                       if j["state"] == "queued" and j["kind"] == me["kind"]
                       and j["seq"] < me["seq"])


def job_submit(payload, kind: str, *, reject_if_busy: bool = False) -> tuple[str, int]:
    """작업을 제 줄에 세운다 → (작업 id, 대기 순번).

    `reject_if_busy`는 운영자 스위치가 켜졌을 때만 계산에 쓴다. 확인과 등록을 같은
    `_jobs_lock` 안에서 하므로 동시에 들어온 두 요청이 모두 빈 줄을 봤다고 착각하지 않는다.
    """
    global _job_seq
    q, cap, what = ((_sim_q, SIM_QUEUE_MAX, "계산") if kind == "sim"
                    else (_fetch_q, FETCH_QUEUE_MAX, "조회"))
    with _jobs_lock:
        _gc_jobs()
        busy = sum(1 for j in _jobs.values()
                   if j["kind"] == kind and j["state"] in ("queued", "running"))
        if reject_if_busy and busy:
            bump("busy_429")
            raise BusyError(f"서버가 다른 {what}을 처리하고 있습니다 — "
                            "잠시 후 다시 시도하세요.")
        if busy >= cap:
            bump("busy_429")
            raise BusyError(f"{what} 대기열이 가득 찼습니다 (진행·대기 {busy}건). "
                            f"잠시 후 다시 눌러 주세요.")
        _job_seq += 1
        jid = uuid.uuid4().hex[:12]
        _jobs[jid] = {"kind": kind, "state": "queued", "seq": _job_seq, "payload": payload,
                      "result": None, "error": None, "at": time.time()}
    q.put(jid)
    return jid, job_pos(jid)


def job_snapshot(jid: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(jid)
        if not j:
            return None
        out = {"state": j["state"], "kind": j["kind"]}
        if j["state"] == "done":
            out["results"] = j["result"]
        elif j["state"] == "error":
            out["error"] = j["error"]
    out["pos"] = job_pos(jid)
    return out


def _fetch_worker() -> None:
    """조회 줄에서 하나씩 꺼내 블라링크를 부른다. **이 스레드가 하나뿐이라** 조회가
    서로 겹치지 않는다 — 그게 이 큐를 둔 이유다."""
    while True:
        jid = _fetch_q.get()
        try:
            with _jobs_lock:
                j = _jobs.get(jid)
                if not j:
                    continue
                j["state"] = "running"
                j["at"] = time.time()
            try:
                payload = j["payload"]
                raws, cached = fetch_cached(payload["openid"], payload.get("area"))
                state, val = "done", {"raws": raws, "cached": cached}
                bump("fetch_ok")
            except BaseException as e:                   # noqa: BLE001
                # **사유를 로그에 남긴다.** 예전엔 브라우저로만 보내고 서버에는
                # 아무것도 안 남겨서, 실패가 20건인데 왜인지 알 수 없었다.
                # openid는 적지 않는다 — 무엇을 조회했는지는 남기지 않는다.
                kind = getattr(e, "kind", "other")
                bump("fetch_err")
                bump(f"fetch_err_{kind}" if f"fetch_err_{kind}" in _stats
                     else "fetch_err_other")
                sys.stderr.write(f"[!] 조회 실패({kind}) {str(e)[:120]}" + chr(10))
                state, val = "error", (str(e) if isinstance(e, (SystemExit, ValueError,
                                                                RuntimeError))
                                       else f"{type(e).__name__}: {e}")
            with _jobs_lock:
                j["state"] = state
                j["at"] = time.time()
                if state == "done":
                    j["result"] = val
                else:
                    j["error"] = val
                j["payload"] = None
        finally:
            _fetch_q.task_done()


def _sim_worker() -> None:
    """큐에서 하나씩 꺼내 돌린다. `SIM_SLOTS`개가 돈다."""
    while True:
        jid = _sim_q.get()
        try:
            with _jobs_lock:
                j = _jobs.get(jid)
                if not j:
                    continue
                j["state"] = "running"
                j["at"] = time.time()
            try:
                res = run_jobs(j["payload"])
                bump("sim_sec", sum(r.get("sec", 0) for r in res))
                state, val = "done", res
            except BaseException as e:                   # noqa: BLE001
                # SystemExit도 여기서 잡는다 — 저장소가 사용자 오류를 그걸로 낸다.
                bump("sim_err")
                state, val = "error", f"{type(e).__name__}: {e}" if not isinstance(
                    e, (SystemExit, ValueError)) else str(e)
            with _jobs_lock:
                j["state"] = state
                j["at"] = time.time()
                if state == "done":
                    j["result"] = val
                else:
                    j["error"] = val
                j["payload"] = None          # 프로필을 계속 들고 있을 이유가 없다
        finally:
            _sim_q.task_done()


def _new_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=_pool_jobs, initializer=_lower_priority)


def run_jobs(jobs: list[tuple]) -> list[dict]:
    """덱들을 병렬 계산한다. 풀이 깨졌으면 **다시 만들어 한 번 재시도한다.**

    워커 하나가 죽으면(메모리 부족·강제 종료 등) `ProcessPoolExecutor`는 그 뒤로 영구히
    깨진 상태가 되어 **이후 모든 요청이 실패한다.** 장시간 도는 서버에서는 한 번의 사고가
    서비스 전체를 끝내는 셈이라, 깨진 걸 확인하면 새로 만든다.
    재시도는 한 번만 한다 — 계속 깨진다면 원인이 일시적이지 않다는 뜻이고, 그때는
    에러를 그대로 올려 이유가 보이게 하는 게 낫다.
    """
    global _pool
    for attempt in (1, 2):
        with _pool_lock:
            if _pool is None:
                _pool = _new_pool()
            pool = _pool
        try:
            return list(pool.map(_sim_one, jobs))
        except BrokenExecutor as e:
            with _pool_lock:
                if _pool is pool:          # 다른 스레드가 이미 갈았으면 건드리지 않는다
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:      # noqa: BLE001  정리 실패는 무시하고 새로 만든다
                        pass
                    _pool = None
            if attempt == 2:
                raise RuntimeError(f"계산 워커 풀이 반복해서 깨진다: {e}") from e
            sys.stderr.write("[!] 계산 워커 풀이 깨졌다 — 다시 만들어 재시도한다\n")
    raise AssertionError("도달 불가")


def run_jobs_native(jobs: list[dict]) -> list[dict]:
    """덱들을 **코어 한 번 호출**로 — 조립·계산·요약 전부 코어(스레드 풀 `_pool_jobs`개)가 하고,
    `_sim_one`과 같은 모양의 dict 목록을 돌려준다. 코어를 못 쓰면 그 요청은 실패다(대신 답하지 않는다)."""
    import simcore
    if not simcore.available(ROOT / "data", threads=_pool_jobs):
        print(f"[sim] 계산 코어를 쓸 수 없다: {simcore.load_error()}", flush=True)
        raise RuntimeError("계산 코어를 쓸 수 없습니다")
    t0 = time.perf_counter()
    out = simcore.run_request_batch(jobs)
    sec = (time.perf_counter() - t0) / max(1, len(jobs))
    for d in out:
        d["sec"] = sec
    return out


def _run_sim_now(jobs: list[dict], *, reject_if_busy: bool = False) -> list[dict]:
    """입장 제한을 지나 계산을 돌리고 결과를 돌려준다. 거절은 `BusyError`(→ 429), 스펙·입력 오류는
    `ValueError`(→ 400), 그 밖은 그대로 올린다(→ 500)."""
    global _sim_running, _sim_waiting
    with _sim_gate_lock:
        if reject_if_busy and _sim_running:
            bump("busy_429")
            raise BusyError("서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시 시도하세요.")
        busy = _sim_running + _sim_waiting
        if busy >= SIM_QUEUE_MAX:
            bump("busy_429")
            raise BusyError(f"계산 대기열이 가득 찼습니다 (진행·대기 {busy}건). 잠시 후 다시 눌러 주세요.")
        _sim_waiting += 1
    try:
        if not _sim_gate.acquire(timeout=SIM_WAIT_MAX):
            bump("busy_429")
            raise BusyError("계산 대기가 너무 길어졌습니다 — 잠시 후 다시 시도하세요.")
    finally:
        with _sim_gate_lock:
            _sim_waiting -= 1
    with _sim_gate_lock:
        _sim_running += 1
    try:
        try:
            return run_jobs_native(jobs) if SIM_ENGINE == "native" else run_jobs(jobs)
        except BusyError:
            raise
        except (SystemExit, ValueError) as e:      # 스펙 조립·입력 오류 — 사용자에게 문장 그대로
            bump("sim_err")
            raise ValueError(str(e)) from None
        except Exception:
            bump("sim_err")
            raise
    finally:
        _sim_gate.release()
        with _sim_gate_lock:
            _sim_running -= 1


# ── 조회 (운영자 세션) ────────────────────────────────────────────────────
def openid_from_input(s: str) -> str:
    """블라링크 프로필 URL 또는 openid 문자열 → API가 받는 **원시 숫자** openid.

    URL의 `openid=` 값은 `"29080-<숫자>"`의 base64다(실측). API는 프리픽스를 뗀 숫자만
    받으므로 여기서 정규화한다 — 세 가지 입력을 다 받는다:
      https://www.blablalink.com/user?openid=MjkwODAtMTAz…
      29080-10346314715007941757
      10346314715007941757
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("openid 또는 프로필 URL이 필요하다")
    if "blablalink.com" in s or s.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
        vals = q.get("openid") or q.get("uid") or []
        if not vals:
            raise ValueError("URL에 openid= 파라미터가 없다")
        s = vals[0]
    given = s
    if not re.fullmatch(r"\d+", s):
        try:
            s = base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
        except Exception:
            pass
    m = re.search(r"(\d{6,})\s*$", s)
    if not m:
        # 디코드 결과를 그대로 보여 주면 깨진 바이트가 찍힌다 — 받은 값을 그대로 인용한다.
        raise ValueError(f"openid를 해석할 수 없다: {given[:40]!r}")
    return m.group(1)


class FetchFailed(RuntimeError):
    """조회 실패. `kind`로 **왜**를 함께 나른다.

    사용자에게 보이는 문구와 별개로, 운영 쪽에서는 종류가 필요하다 — 비공개 계정이
    16%인 것은 그냥 그런 것이고, 세션 만료는 당장 손을 써야 하는 일이다.
    문구만 남기면 그 둘을 세지 못한다.
    """

    def __init__(self, msg: str, kind: str = "other"):
        super().__init__(msg)
        self.kind = kind


_AREA_LABEL = {83: "한섭", 81: "일섭", 84: "글로벌섭"}
# 2026-08-23 레벨 410짜리 계정으로 실측 확인. 79·80·86~90은 `1303001 param invalid`로
# 존재하지 않는 area였고, 85는 파라미터는 유효한데(code 0) 이 계정 기준 캐릭터 0개라
# 보류 — 안 찍힌 지역까지 후보에 넣고 「모르면 글로벌」로 뭉치면 엉뚱한 지역을 오인시킬
# 수 있어(신규 서버·오탐), 실제로 캐릭터가 나온 지역만 후보에 둔다.
_AREA_CANDIDATES = (83, 81, 84)


def _area_label(area: int) -> str:
    """지역 코드 → 사람이 아는 이름. `_AREA_CANDIDATES`에 없는 코드는 안 들어온다."""
    return _AREA_LABEL.get(area, str(area))


def fetch_raw(openid: str, area: int | None = None) -> list[dict]:
    """운영자 세션으로 한 계정의 원시 육성 데이터를 받는다. 북마클릿과 같은 모양을 만든다.

    계정 하나에 지역(서버)이 여럿 걸릴 수 있다 — 같은 블라블라링크 로그인에 한섭·일섭
    양쪽 게임 계정을 연동해 둔 경우가 실제로 있다(실측). **감지된 지역을 전부** 받아
    리스트로 돌려준다 — 첫 지역에서 멈추면 그 계정의 다른 지역은 영영 못 본다
    (실측 사례: 일섭이 메인인 계정이 매번 한섭으로만 잡힘).

    `area`를 주면 그 지역 하나만 다시 받는다 — 다시 싱크용. 매번 전체를 훑으면
    나중에 지역이 하나 더 늘었을 때 다시 싱크가 엉뚱한 지역으로 튈 수 있어,
    프로필이 **처음 고른 지역에 고정**되게 한다.
    """
    import profile_fetch as pf

    cookie = pf._load_cookie()
    candidates = (area,) if area is not None else _AREA_CANDIDATES
    hits: list[tuple[int, list]] = []
    # 서버는 area를 모르니 흔한 값부터 훑는다. **틀린 area는 `param invalid`(1303001)**로
    # 떨어지므로, 그 응답들에 섞여 오는 «진짜 이유»(비공개 등)를 붙잡아 뒀다가 쓴다.
    # 안 그러면 비공개 계정도 «없는 openid»와 똑같은 문장을 보게 된다.
    reason = None
    for a in candidates:
        r = pf._post("Game/GetUserCharacters",
                     {"intl_open_id": openid, "nikke_area_id": a}, cookie)
        if r.get("code") == 0 and (r.get("data") or {}).get("characters"):
            hits.append((a, r["data"]["characters"]))
            continue
        if r.get("code") == 300001:
            raise FetchFailed("운영자 세션이 만료됐다 (game not login). "
                              "scraper/.session_cookie를 갱신해야 한다.", "session")
        # 1301002 = 프로필에서 «니케 정보 공개»를 꺼 둔 계정
        if r.get("code") == 1301002:
            reason = ("이 계정은 블라블라링크에서 «니케 정보 공개»가 꺼져 있습니다 — "
                      "설정을 켜거나, 켜기 싫으면 아래 북마클릿을 쓰세요 "
                      "(북마클릿은 본인 브라우저에서 받아 오므로 공개 설정이 필요 없습니다).")
    if not hits:
        if reason:
            raise FetchFailed(reason, "private")
        raise FetchFailed("캐릭터를 받지 못했다 — 비공개 계정이거나 없는 openid다.",
                          "notfound")

    out = []
    for a, chars in hits:
        codes = [c["name_code"] for c in chars]
        details, effects = [], []
        for i in range(0, len(codes), 60):
            d = pf._check(pf._post("Game/GetUserCharacterDetails",
                                   {"intl_open_id": openid, "nikke_area_id": a,
                                    "name_codes": codes[i:i + 60]}, cookie),
                          "GetUserCharacterDetails")
            details.extend(d["character_details"])
            effects.extend(d.get("state_effects", []))

        o = pf._post("Game/GetUserProfileOutpostInfo",
                     {"intl_open_id": openid, "nikke_area_id": a}, cookie)
        out.append({
            "openid": openid, "area": a, "area_label": _area_label(a),
            "characters": chars, "details": details,
            "state_effects": effects,
            "outpost": (o.get("data") or {}).get("outpost_info") or None,
            "union": pf.fetch_union(openid, a, cookie),
            "_source": "블라링크",     # 사용자에게 보이는 문자열 — «server»는 무엇을 받았는지 안 알려 준다
            "_collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
    return out


def fetch_cached(openid: str, area: int | None = None) -> tuple[list[dict], bool]:
    """조회. 반환: (raw 목록, False).

    **캐시하지 않는다.** 방금 육성한 것을 다시 불러도 옛 값이 나오면 「다시 싱크」가
    아무 일도 안 한 것처럼 보인다 — 그 손해가 캐시로 아끼는 부하보다 크다.
    블라링크 쪽 부하는 조회를 겹치지 않게 하는 것(`fetch_turn`)으로만 누른다.

    반환 형태(튜플)는 호출부를 안 건드리려고 그대로 둔다.
    """
    return fetch_raw(openid, area), False


# ── 편성 공유 저장소 ──────────────────────────────────────────────────────
# **이 서버가 파일을 쓰는 유일한 자리다.** 유닛은 파일시스템을 읽기 전용으로 잠가
# 두었고(`ProtectSystem=strict`), `StateDirectory=nikke-decklab` 한 줄이 여기에만
# 쓰기 경로를 준다. 그 밖은 여전히 못 쓴다.
#
# 여기 담기는 것은 **캐시**이지 정본이 아니다. 정본은 방문자 localStorage다. 그래서
# 이 파일이 지워지거나 열리지 않아도 사이트는 그대로 동작해야 한다 — `share_ok()`가
# 거짓이면 웹이 공유 버튼을 감춘다(클라이언트가 서버 기능을 추측하지 않게 한다).
_share_db: sqlite3.Connection | None = None
_share_lock = threading.Lock()
_share_dead = False               # 한 번 열기에 실패하면 매 요청마다 다시 시도하지 않는다


def _share_conn() -> sqlite3.Connection:
    """열려 있는 연결. **하나만 쓰고 락으로 감싼다** — 쓰기가 아주 드문 테이블이고,
    스레드마다 연결을 여는 편이 오히려 잠금 충돌을 만든다."""
    global _share_db
    if _share_db is None:
        SHARE_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(SHARE_DB, check_same_thread=False, timeout=5.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS share ("
                  "code TEXT PRIMARY KEY, body BLOB NOT NULL, created REAL NOT NULL)")
        c.commit()
        _share_db = c
    return _share_db


# 관리자 페이지. 파일로 두면 dist에 실려 공개되므로 서버 안에 내장한다 —
# /admin 라우트가 테일넷 발신지에만 내준다.
ADMIN_HTML = """<!doctype html><html lang=ko><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>운영 관리</title>
<style>
 body{font:14px/1.5 sans-serif;background:#15161a;color:#ddd;max-width:760px;margin:24px auto;padding:0 12px}
 h1{font-size:18px} .it{border:1px solid #333;border-radius:8px;padding:10px 12px;margin:10px 0;background:#1c1d22}
 .it.hid{opacity:.45} .meta{font-size:12px;color:#999} .body{white-space:pre-wrap;margin:6px 0}
 .rep{border-left:3px solid #4a8;margin:8px 0 4px;padding:4px 10px;color:#bfe;white-space:pre-wrap}
 textarea{width:100%;min-height:56px;background:#111;color:#ddd;border:1px solid #444;border-radius:6px;padding:6px}
 button{background:#2a2c33;color:#ddd;border:1px solid #555;border-radius:6px;padding:4px 10px;margin:4px 6px 0 0;cursor:pointer}
 button:hover{background:#3a3d46}
 h1 span{cursor:pointer;margin-right:14px}
 .tab-on{color:#fff;border-bottom:2px solid #4a8}
 .tab-off{color:#777}
 h2{font-size:14px;color:#9cf;margin:18px 0 6px}
 .rt{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:8px}
 .rt th,.rt td{border:1px solid #333;padding:3px 6px;text-align:left;word-break:break-all}
 .rt th{background:#22242a;color:#aaa}
 .rt td:last-child{text-align:right;width:60px}
 .rt a{color:#8ecbff;text-decoration:none}.rt a:hover{text-decoration:underline}
 .ops-card{border:1px solid #333;border-radius:10px;padding:16px;margin-top:14px;background:#1c1d22}
 .ops-row{display:flex;align-items:center;justify-content:space-between;gap:18px}
 .ops-title{font-weight:700;color:#fff}.ops-note{margin:8px 0 0;color:#999;font-size:12px}
 .switch{min-width:112px;margin:0;padding:8px 12px;border-color:#555;background:#292b31}
 .switch[aria-checked="true"]{border-color:#ff6f9d;background:#6f2943;color:#fff}
 .switch:disabled{cursor:wait;opacity:.65}
 .ops-status{margin-top:10px;color:#9cf;font-size:12px;min-height:18px}
</style>
<h1><span id=tab-fb class=tab-on>피드백 <small id=n></small></span>
 <span id=tab-ref class=tab-off>유입 통계</span>
 <span id=tab-ops class=tab-off>서버 설정</span></h1>
<div id=list></div><div id=refs hidden></div><div id=ops hidden></div>
<script src="/admin.js"></script>"""

# 관리자 스크립트. **인라인으로 두면 안 된다** — 서버가 모든 응답에 붙이는 CSP
# (script-src 'self')가 인라인 실행을 막아 버튼이 통째로 죽는다(실측: 탭이 안 눌렸다).
ADMIN_JS = r"""
const api=(b)=>fetch("/api/board/admin",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
function tbl(head, rows){
  const t=document.createElement("table"); t.className="rt";
  const hr=document.createElement("tr");
  for(const h of head){const th=document.createElement("th");th.textContent=h;hr.append(th);}
  t.append(hr);
  for(const r of rows){
    const tr=document.createElement("tr");
    for(const c of r){
      const td=document.createElement("td");
      if(c&&typeof c==="object"&&c.href){
        try{
          const u=new URL(c.href);
          if(u.protocol!=="http:"&&u.protocol!=="https:") throw new Error("bad scheme");
          const a=document.createElement("a"); a.href=u.href; a.textContent=c.text;
          a.target="_blank"; a.rel="noopener noreferrer"; td.append(a);
        }catch(_){td.textContent=c.text||"";}
      }else{td.textContent=c;}
      tr.append(td);
    }
    t.append(tr);
  }
  return t;
}
const link=(href,text)=>({href,text});
async function loadRefs(){
  const box=document.getElementById("refs");
  box.textContent="불러오는 중…";
  let d;
  try{
    d=await api({op:"refs",days:30});
    if(!d||!d.days) throw new Error(JSON.stringify(d));
  }catch(e){ box.textContent="유입 통계를 불러오지 못했습니다 — "+e.message; return; }
  box.textContent="";
  const h2=(s)=>{const e=document.createElement("h2");e.textContent=s;return e;};
  box.append(h2("일자별 유입 (최근 30일)"));
  box.append(tbl(["날짜","방문"], d.days.map(x=>[x.day,x.n])));
  box.append(h2("도메인별"));
  box.append(tbl(["도메인","방문"], d.hosts.map(x=>[
    x.host.startsWith("(")?x.host:link(`https://${x.host}/`,x.host),x.n])));
  box.append(h2("주소별 (상위 60)"));
  box.append(tbl(["주소","방문"], d.urls.map(x=>[
    /^https?:\/\//i.test(x.url)?link(x.url,x.url):x.url,x.n])));
  box.append(h2("날짜 × 도메인"));
  box.append(tbl(["날짜","도메인","방문"], d.grid.map(x=>[
    x.day,x.host.startsWith("(")?x.host:link(`https://${x.host}/`,x.host),x.n])));
}
async function loadOps(){
  const box=document.getElementById("ops");
  box.textContent="불러오는 중…";
  let d;
  try{
    d=await api({op:"settings"});
    if(typeof d.sim_busy_guard!=="boolean") throw new Error(JSON.stringify(d));
  }catch(e){ box.textContent="서버 설정을 불러오지 못했습니다 — "+e.message; return; }
  box.textContent="";
  const card=document.createElement("section"); card.className="ops-card";
  const row=document.createElement("div"); row.className="ops-row";
  const copy=document.createElement("div");
  const title=document.createElement("div"); title.className="ops-title"; title.textContent="새 계산 차단";
  const note=document.createElement("p"); note.className="ops-note";
  note.textContent=`켜면 계산 중 새 요청을 거절합니다. 끄면 최대 ${d.queue_max}건을 받아 순서대로 처리합니다 (동시 실행 ${d.slots}건).`;
  copy.append(title,note);
  const toggle=document.createElement("button"); toggle.className="switch";
  toggle.type="button"; toggle.setAttribute("role","switch");
  const paint=()=>{toggle.setAttribute("aria-checked",String(d.sim_busy_guard));toggle.textContent=d.sim_busy_guard?"차단 켜짐":"차단 꺼짐";};
  paint(); row.append(copy,toggle);
  const status=document.createElement("div"); status.className="ops-status";
  toggle.onclick=async()=>{
    toggle.disabled=true; status.textContent="저장하는 중…";
    try{
      const next=await api({op:"sim-guard",enabled:!d.sim_busy_guard});
      if(typeof next.sim_busy_guard!=="boolean") throw new Error(JSON.stringify(next));
      d.sim_busy_guard=next.sim_busy_guard; paint();
      status.textContent=d.sim_busy_guard?"계산 중에는 새 요청을 받지 않습니다.":"동시 요청을 대기열로 받고 있습니다.";
    }catch(e){status.textContent="저장하지 못했습니다 — "+e.message;}
    finally{toggle.disabled=false;}
  };
  card.append(row,status); box.append(card);
}
function show(which){
  const fb=which==="fb";
  const ref=which==="ref";
  document.getElementById("list").hidden=!fb;
  document.getElementById("refs").hidden=!ref;
  document.getElementById("ops").hidden=which!=="ops";
  for(const [id,w] of [["tab-fb","fb"],["tab-ref","ref"],["tab-ops","ops"]]){
    document.getElementById(id).className=which===w?"tab-on":"tab-off";
  }
  if(ref) loadRefs();
  if(which==="ops") loadOps();
}
for(const [id,w] of [["tab-fb","fb"],["tab-ref","ref"],["tab-ops","ops"]]){
  const e=document.getElementById(id);
  if(e){ e.onclick=()=>show(w); e.style.cursor="pointer"; }
}
async function load(){
  const d=await api({op:"list"});
  document.getElementById("n").textContent=`(${d.items.length}건 · 미답변 ${d.items.filter(i=>!i.reply&&!i.hidden).length})`;
  const box=document.getElementById("list"); box.textContent="";
  for(const it of d.items){
    const div=document.createElement("div"); div.className="it"+(it.hidden?" hid":"");
    const meta=document.createElement("div"); meta.className="meta";
    meta.textContent=`${it.nick} · ${new Date(it.ts*1000).toLocaleString("ko-KR")} · ${it.id}`+(it.hidden?" · 숨김":"");
    const body=document.createElement("div"); body.className="body"; body.textContent=it.body;
    div.append(meta,body);
    if(it.reply){const r=document.createElement("div");r.className="rep";r.textContent=it.reply;div.append(r);}
    const ta=document.createElement("textarea"); ta.placeholder="답변 (비우고 저장하면 답변 삭제)"; ta.value=it.reply||"";
    const save=document.createElement("button"); save.textContent="답변 저장";
    save.onclick=async()=>{await api({op:"reply",id:it.id,body:ta.value});load();};
    const hide=document.createElement("button"); hide.textContent=it.hidden?"숨김 해제":"숨김";
    hide.onclick=async()=>{await api({op:it.hidden?"unhide":"hide",id:it.id});load();};
    const del=document.createElement("button"); del.textContent="삭제";
    del.onclick=async()=>{if(confirm("완전히 지웁니다?")){await api({op:"del",id:it.id});load();}};
    div.append(ta,save,hide,del); box.append(div);
  }
}
load();
"""


def _board_init(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS board ("
              "id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, "
              "nick TEXT NOT NULL, body TEXT NOT NULL, "
              "reply TEXT, reply_ts REAL, hidden INTEGER NOT NULL DEFAULT 0, "
              "private INTEGER NOT NULL DEFAULT 0, pw TEXT)")
    # 이미 옛 스키마로 만들어진 표에는 열을 덧붙인다 (있으면 조용히 넘어감)
    for col in ("private INTEGER NOT NULL DEFAULT 0", "pw TEXT"):
        try:
            c.execute(f"ALTER TABLE board ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    c.commit()


def board_list(admin: bool = False, before: float | None = None,
               limit: int = 200) -> list[dict]:
    """피드백 목록 (최신순). 방문자에게는 숨김 글을 빼고 주고,
    before(ts)보다 오래된 글부터 limit개 — «더 보기» 페이징용."""
    with _share_lock:
        c = _share_conn(); _board_init(c)
        cond, args = [], []
        if not admin:
            cond.append("hidden = 0")
        if before is not None:
            cond.append("ts < ?"); args.append(before)
        q = ("SELECT id, ts, kind, nick, body, reply, reply_ts, hidden, private "
             "FROM board ")
        if cond:
            q += "WHERE " + " AND ".join(cond) + " "
        q += "ORDER BY ts DESC LIMIT ?"
        rows = c.execute(q, (*args, max(1, min(int(limit), 200)))).fetchall()
    keys = ("id", "ts", "kind", "nick", "body", "reply", "reply_ts", "hidden", "private")
    out = [dict(zip(keys, r)) for r in rows]
    if not admin:
        # 비공개 글은 본문·답변을 빼고 껍데기만 준다 — 열람은 /api/board/view가
        # 비밀번호를 확인하고 내준다. 답변이 달렸다는 사실(has_reply)만 알린다.
        for it in out:
            if it["private"]:
                it["has_reply"] = bool(it["reply"])
                it["body"] = ""
                it["reply"] = None
    return out


def _board_hash(pw: str, salt: bytes | None = None) -> str:
    """비공개 글 비밀번호 해시 — 글마다 다른 솔트 + PBKDF2 20만 회.
    DB가 통째로 새어도 비밀번호를 되돌리기 어렵게 한다."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def board_view(bid: str, pw: str) -> dict | None:
    """비공개 글 열람 — 등록 때 정한 비밀번호가 맞아야 전문을 내준다.
    본문·답변은 이 경로로만 나간다 (목록은 서버가 껍데기만 내려보낸다)."""
    with _share_lock:
        c = _share_conn(); _board_init(c)
        r = c.execute("SELECT id, ts, kind, nick, body, reply, reply_ts, pw "
                      "FROM board WHERE id = ? AND private = 1 AND hidden = 0",
                      (bid,)).fetchone()
    if r is None or not r[7] or "$" not in r[7]:
        return None
    salt_hex, want = r[7].split("$", 1)
    got = _board_hash(pw, bytes.fromhex(salt_hex)).split("$", 1)[1]
    if not hmac.compare_digest(want, got):      # 상수시간 비교 — 타이밍 누출 방지
        return None
    keys = ("id", "ts", "kind", "nick", "body", "reply", "reply_ts")
    return {**dict(zip(keys, r[:7])), "private": 1}


def board_add(kind: str, nick: str, body: str,
              private: bool = False, pw: str = "") -> str:
    bid = uuid.uuid4().hex[:8]
    with _share_lock:
        c = _share_conn(); _board_init(c)
        # 같은 본문 재전송(더블클릭·스팸)은 조용히 기존 글로 갈음한다
        # 공개 글끼리만 대조한다 — 비공개 글까지 보면 「이 문장이 비공개로
        # 올라와 있나」를 확인하는 오라클이 된다
        dup = None if private else c.execute(
            "SELECT id FROM board WHERE body = ? AND private = 0 AND ts > ?",
            (body, time.time() - 3600)).fetchone()
        if dup:
            return dup[0]
        pw_hash = _board_hash(pw) if private else None
        c.execute("INSERT INTO board (id, ts, kind, nick, body, private, pw) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (bid, time.time(), kind, nick, body, 1 if private else 0, pw_hash))
        c.commit()
    return bid


def board_admin(op: str, bid: str, body: str = "") -> bool:
    with _share_lock:
        c = _share_conn(); _board_init(c)
        if op == "reply":
            r = c.execute("UPDATE board SET reply = ?, reply_ts = ? WHERE id = ?",
                          (body or None, time.time() if body else None, bid))
        elif op in ("hide", "unhide"):
            r = c.execute("UPDATE board SET hidden = ? WHERE id = ?",
                          (1 if op == "hide" else 0, bid))
        elif op == "del":
            r = c.execute("DELETE FROM board WHERE id = ?", (bid,))
        else:
            return False
        c.commit()
        return r.rowcount > 0


def share_ok() -> bool:
    """공유를 받을 수 있나. 실패하면 그 이유를 **로그에만** 남기고 기능만 끈다."""
    global _share_dead
    if _share_dead:
        return False
    try:
        with _share_lock:
            _share_conn()
        return True
    except Exception as e:                                        # noqa: BLE001
        _share_dead = True
        print(f"share  저장소를 열 수 없어 공유를 끕니다 ({SHARE_DB}): {e}", file=sys.stderr)
        return False


def _share_num(v, lo: float, hi: float) -> float:
    """공유본의 숫자 한 칸. **이름을 `_num`으로 두면 안 된다** — 위쪽에 같은 이름의
    헬퍼(`_num(v, lo, hi, cast)`)가 이미 있어서, 나중에 정의된 이쪽이 그것을 덮고
    `_clean_enemy`·`_clean_config`가 `TypeError`로 죽는다(실제로 그랬다: 서버 계산이
    전부 500이 됐다). 범위를 벗어나면 조용히 자르지 않고 거절하는 것도 위쪽과 다른
    점이라, 같은 이름을 쓸 이유가 없다."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("숫자가 아닌 값이 있습니다")
    if not (lo <= float(v) <= hi):
        raise ValueError("값이 범위를 벗어났습니다")
    return float(v)


def share_clean(obj) -> dict:
    """공유 payload를 **화이트리스트로 다시 만든다.**

    클라이언트가 무엇을 보내든 여기에 적힌 키만 저장된다. 웹에서 한 번 걸러 보내지만
    그것과 별개로 서버가 다시 짓는다 — 닉네임(`profileName`)·스펙 지문(`profileSig`)·
    기본 스펙 이탈 목록(`notes`)이 실수로 실려 오는 경로를 **코드로** 막는 자리다.
    (`notes`에는 `equip_skills.charge_speed_pct: 0 → 9.26`처럼 장비 실수치가 문장으로
     들어 있어서, 그대로 저장하면 «편성만 공유한다»가 사실이 아니게 된다.)
    """
    if not isinstance(obj, dict):
        raise ValueError("공유할 내용이 아닙니다")
    decks_in = obj.get("decks")
    if not isinstance(decks_in, list) or not decks_in:
        raise ValueError("공유할 덱이 없습니다")
    if len(decks_in) > MAX_DECKS:
        raise ValueError(f"덱이 너무 많습니다 (최대 {MAX_DECKS})")

    decks = []
    for d in decks_in:
        if not isinstance(d, dict):
            raise ValueError("덱 모양이 아닙니다")
        names_in = d.get("names")
        if not isinstance(names_in, list) or not names_in:
            raise ValueError("덱에 니케가 없습니다")
        names = []
        for n in names_in[:SHARE_MAX_CHARS]:
            if n is None:
                names.append(None)
            elif isinstance(n, str) and 0 < len(n) <= 40:
                names.append(n)
            else:
                raise ValueError("니케 이름이 아닙니다")
        chars_in = d.get("chars") or {}
        if not isinstance(chars_in, dict) or len(chars_in) > SHARE_MAX_CHARS:
            raise ValueError("니케별 딜 모양이 아닙니다")
        chars = {}
        for k, v in chars_in.items():
            if not isinstance(k, str) or not (0 < len(k) <= 40):
                raise ValueError("니케 이름이 아닙니다")
            chars[k] = _share_num(v, 0, 1e18)
        one = {"names": names,
               "total": _share_num(d.get("total"), 0, 1e18),
               "chars": chars}
        # 유니온 레이드는 줄마다 다른 보스를 친다 — 어느 보스였는지가 그 편성의 뜻이다.
        w = d.get("weak")
        if isinstance(w, str) and 0 < len(w) <= 8:
            one["weak"] = w
        decks.append(one)

    code = obj.get("code")
    if code is not None and not (isinstance(code, str) and len(code) <= 8):
        raise ValueError("속성 코드가 아닙니다")
    # 어느 콘텐츠의 편성인가. **없으면 솔로**다 — 예전에 만든 공유 링크가 그대로 살아야
    # 하므로 기본값이 곧 옛 동작이다.
    mode = obj.get("mode")
    mode = mode if mode in ("solo", "union") else None
    out = {
        "v": 1,
        "code": code or None,
        "duration": _share_num(obj.get("duration"), 1, MAX_DURATION),
        "total": _share_num(obj.get("total"), 0, 1e18),
        "decks": decks,
    }
    if mode:
        out["mode"] = mode
    return out


def share_put(clean: dict) -> tuple[str, float]:
    """공유본을 저장하고 (코드, 만료시각)을 준다.

    코드는 `secrets`로 뽑는다 — 순번이면 남의 공유를 차례로 훑을 수 있다.
    만료는 **쓰기 때마다** 치운다. 쓰기가 드문 표라 별도 타이머를 둘 이유가 없다.
    """
    now = time.time()
    body = zlib.compress(json.dumps(clean, ensure_ascii=False).encode("utf-8"), 9)
    with _share_lock:
        db = _share_conn()
        db.execute("DELETE FROM share WHERE created < ?", (now - SHARE_TTL,))
        for _ in range(8):
            code = secrets.token_urlsafe(6)
            try:
                db.execute("INSERT INTO share (code, body, created) VALUES (?, ?, ?)",
                           (code, body, now))
                db.commit()
                return code, now + SHARE_TTL
            except sqlite3.IntegrityError:
                continue                      # 같은 코드가 이미 있다 — 다시 뽑는다
        db.rollback()
    raise RuntimeError("공유 코드를 만들지 못했습니다 — 잠시 후 다시 시도하세요.")


def share_get(code: str) -> dict | None:
    with _share_lock:
        db = _share_conn()
        row = db.execute("SELECT body, created FROM share WHERE code = ?", (code,)).fetchone()
    if not row or time.time() - row[1] > SHARE_TTL:
        return None                           # 만료분은 다음 쓰기가 치운다
    return json.loads(zlib.decompress(row[0]).decode("utf-8"))


def share_del(code: str) -> bool:
    """공유를 지운다. **코드를 아는 것이 곧 권한이다** — 만든 사람만 코드를 갖고 있다."""
    with _share_lock:
        db = _share_conn()
        cur = db.execute("DELETE FROM share WHERE code = ?", (code,))
        db.commit()
    return cur.rowcount > 0


_SHARE_CODE = re.compile(r"^[A-Za-z0-9_-]{4,16}$")


def share_code(raw) -> str:
    code = str(raw or "")
    if not _SHARE_CODE.match(code):
        raise ValueError("공유 코드가 아닙니다")
    return code


def base_atk_of(names: list, profile: dict | None) -> dict:
    """니케들의 **소지 공격력**만. 시뮬 없이 표 조회뿐이라 즉시다.

    브라우저 워커(`web/src/worker.js base_atk_of`)와 **같은 것을 돌려줘야 한다** —
    한쪽만 고치면 서버를 켜고 끄는 것만으로 «누가 버프를 받나»가 달라진다.
    """
    # `_sim_one`은 서브프로세스라 자기 안에서 import한다 — 여기(부모)도 지연 import한다
    from calculator.base_stat import calc_base_stats
    from context import spec as char_spec
    prof = None
    if profile:
        prof = char_spec.profile_from_dict(profile, 
                                           where="전달된 프로필")
    squad = char_spec.build_squad([str(n) for n in names], None, profile=prof)
    # 공증도 함께 — 브라우저가 스펙에서 읽으면 «고정 스펙»에서 0이 되어 예측이 낮게 나온다
    return {c["name"]: {
        "atk": round(calc_base_stats(c).get("atk", 0.0)),
        "atk_pct": float((c.get("equip_skills") or {}).get("atk_pct") or 0.0),
    } for c in squad}


# ── 레이트리밋 ────────────────────────────────────────────────────────────
def from_our_page(handler) -> bool:
    """우리 페이지가 보낸 요청처럼 보이나.

    브라우저는 `Sec-Fetch-Site`를 **언제나** 붙인다(우회하려면 헤더를 위조해야 한다).
    같은 오리진이 아니면 다른 사이트가 우리 서버를 부르고 있거나, 브라우저가 아닌
    스크립트·크롤러다. `/api/fetch`는 운영자 계정을 쓰므로 그런 호출을 받을 이유가 없다.
    """
    site = handler.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site == "same-origin"
    # Sec-Fetch를 안 보내는 클라이언트 — Origin으로 한 번 더 본다
    origin = handler.headers.get("Origin")
    if not origin:
        return False
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host") or ""
    return bool(host) and origin.rstrip("/").endswith("//" + host)


def is_local_only(handler) -> bool:
    """**직접 로컬 접속인가.** 실험 기능을 켤지 판단하는 유일한 근거다.

    Funnel(공개)이든 tailnet이든 프록시를 거치면 헤더가 붙는다 —
    `X-Forwarded-For`(Funnel), `Tailscale-*`(tailnet). 아무것도 없으면 브라우저가
    이 기계의 서버에 곧바로 붙은 것이다.

    **운영에서는 항상 거짓이어야 한다.** 코드가 실수로 배포돼도 실험 기능이 켜지지
    않게 하는 것이 목적이다 — 「tailnet이면 허용」으로 하면 배포된 사이트를 내 기기에서
    열었을 때 켜져 버린다.
    """
    for h in ("X-Forwarded-For", "Tailscale-Funnel-Request", "Tailscale-User-Login"):
        if handler.headers.get(h) is not None:
            return False
    return True


def rate_ok(ip: str, kind: str, limit: int) -> bool:
    now = time.time()
    key = (ip, kind)
    with _hits_lock:
        seen = [t for t in _hits.get(key, []) if now - t < RATE_WINDOW]
        if len(seen) >= limit:
            _hits[key] = seen
            return False
        seen.append(now)
        _hits[key] = seen
    return True


# ── HTTP ──────────────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    # HTTP/1.0이면 응답마다 연결을 끊는다. 초상화 205장을 받는 첫 방문에서
    # 연결을 205번 새로 여는 셈이라 눈에 띄게 느렸다. 1.1로 올려 재사용한다
    # (SSE는 제 손으로 `Connection: close`를 보내므로 그대로 동작한다).
    protocol_version = "HTTP/1.1"
    # 파이썬 판번호를 광고하지 않는다 — 알려진 취약점을 가진 판을 찾는 스캐너에게
    # 공짜 정보다. 기능에는 영향이 없다.
    server_version = "DILDORO"
    sys_version = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(DIST), **kw)

    def log_message(self, fmt, *args):
        # **주소를 적지 않는다.** 남기면 방문자를 특정할 수 있게 되고, 이 사이트는
        # 그걸 하지 않는다. 무엇이 얼마나 불렸는지만 알면 운영에는 충분하다.
        if "/api/" in (self.path or ""):
            sys.stderr.write((fmt % args) + chr(10))

    # 초상화 205장이 5MB다. 여기에 no-store를 걸면 **새로 고칠 때마다 5MB를 다시 받는다** —
    # 이미지가 늦게 뜨는 원인이 이것이었다. 내용이 바뀌면 파일명이 바뀌는 성질의 자산
    # (초상화·아이콘)은 길게 캐시하고, 빌드마다 갱신되는 것만 no-store로 둔다.
    _LONG_CACHE = (".webp", ".png", ".jpg", ".jpeg", ".svg", ".woff2", ".ico")

    # 밖에서 받는 것은 셋뿐이다: Pyodide(jsdelivr), 그리고 글꼴 두 벌(구글 폰트).
    # `wasm-unsafe-eval`은 Pyodide가 WASM을 컴파일하는 데 필요하고, `blob:`은 Pyodide가
    # 만드는 워커와 캔버스 이미지 저장에 쓴다.
    #
    # 글꼴은 `tokens.css`가 `@import`로 부른다 — **CSS 안에서 부르는 것도 CSP에 걸린다.**
    # 처음 CSP를 짤 때 `.js`와 `.html`만 훑어 이걸 빠뜨렸고, 글자가 기본 글꼴로 떨어졌다.
    _CSP = ("default-src 'self'; "
            "script-src 'self' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; "
            "worker-src 'self' blob:; "
            "connect-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            # base-uri는 'self' — 경로형 주소(/deck/3)가 생기며 index.html이 <base href="/">를 쓴다(계약 §0)
            "object-src 'none'; base-uri 'self'; form-action 'none'; "
            "frame-ancestors 'none'")

    def end_headers(self):
        self.send_header("Content-Security-Policy", self._CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        path, _, query = (self.path or "").partition("?")
        path = path.lower()
        # 다국어 사전은 지문(`?v=`)이 붙은 채로만 불린다 — 내용이 바뀌면 index.html이
        # 새 지문을 가리키므로 길게 캐시해도 낡은 것을 쓸 길이 없다.
        tagged = path.startswith("/i18n/") and path.endswith(".js") and query.startswith("v=")
        if path.endswith(self._LONG_CACHE) or tagged:
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ── 응답 도우미 ──
    def _squad(self, mode):
        """스쿼드 캡처 판독. 화소는 base64로 온다 (JSON에 바이트를 넣을 수 없다)."""
        b = self._body()

        def dec(v, what):
            try:
                return base64.b64decode(v, validate=True)
            except Exception:                       # noqa: BLE001
                raise ValueError(f"{what}가 base64가 아니다")

        if mode == "align":
            samples = b.get("samples") or []
            if not isinstance(samples, list) or not (1 <= len(samples) <= 8):
                raise ValueError("samples는 1~8칸이어야 한다")
            got = [[dec(v, "samples") for v in row] for row in samples]
            for row in got:
                if len(row) != len(squad_ocr.ALIGN):
                    raise ValueError(f"칸마다 틀 {len(squad_ocr.ALIGN)}개가 와야 한다")
            i, align = squad_ocr.pick_align(got)
            return self._json({"align_index": i, "align": align})

        tiles = b.get("tiles") or []
        if not isinstance(tiles, list) or not (1 <= len(tiles) <= OCR_MAX_TILES):
            raise ValueError(f"tiles는 1~{OCR_MAX_TILES}칸이어야 한다")
        got = [{k: dec(t.get(k, ""), k) for k in ("c12", "c24", "c32", "badge")}
               for t in tiles]
        # 사람이 고친 칸은 고정한다 — 한 칸을 고치면 겹치던 다른 칸이 저절로 풀린다
        locked = {}
        for k, v in (b.get("locked") or {}).items():
            if isinstance(v, str) and v:
                locked[int(k)] = v
        return self._json({"cells": squad_ocr.read(got, locked)})

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, status=400):
        # 조용히 0을 만들지 않고 이유를 그대로 올린다 (웹이 그 문구를 그대로 보여 준다)
        self._drain()
        self._json({"error": str(msg)}, status)

    def _drain(self):
        """읽지 않은 요청 본문을 버린다.

        keep-alive(HTTP/1.1)에서는 한 연결에 요청이 이어 붙는다. 레이트리밋처럼
        **본문을 읽기 전에 응답하는** 경로가 있으면 남은 바이트가 다음 요청의
        요청줄로 파싱돼 엉뚱한 501이 난다 — 실제로 429 다음 요청이 그랬다.
        """
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        n = int(self.headers.get("Content-Length") or 0)
        while n > 0:                      # 통째로 읽지 않는다 — 상한을 넘으면 끊는다
            if n > MAX_BODY:
                self.close_connection = True
                return
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                return
            n -= len(chunk)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self._body_read = True
            raise ValueError("빈 요청")
        if n > MAX_BODY:
            # 8MB를 넘는 본문은 읽지 않고 연결을 끊는다 — 버리려고 읽는 것 자체가 부하다
            self.close_connection = True
            self._body_read = True
            raise ValueError(f"요청이 너무 큽니다 ({n:,}B > {MAX_BODY:,}B)")
        data = self.rfile.read(n)
        self._body_read = True
        return json.loads(data.decode("utf-8"))

    def handle_one_request(self):
        self._body_read = False           # 연결이 이어져도 요청마다 새로 센다
        super().handle_one_request()

    def do_GET(self):
        # `do_POST`와 같은 그물을 친다. 예전엔 GET에서 예외가 나면 응답도 없이 연결이
        # 끊겨 «서버가 죽은 것처럼» 보였다 — 이유가 어디에도 안 남는다.
        try:
            self._get()
        except ValueError as e:
            self._err(e, 400)
        except Exception:                            # noqa: BLE001
            traceback.print_exc()
            self._err("서버 오류입니다 — 잠시 후 다시 시도하세요.", 500)

    def _get(self):
        # 클라이언트가 서버 기능을 **추측하지 않게** 한다. 계산을 서버에 맡길지,
        # URL 동기화를 안내할지는 이 응답으로 정한다.
        p = urllib.parse.urlsplit(self.path)
        route = p.path.rstrip("/")
        # 다국어 사전은 400KB짜리 JS다 — 빌드가 옆에 둔 .gz(≈80KB)를 받는 쪽이 받아 준다면
        # 그걸 준다. 파일명은 build.py가 정한 셋뿐이라 경로를 조립하지 않는다.
        if route.startswith("/i18n/") and route.endswith(".js") and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            name = route[len("/i18n/"):]
            gz = DIST / "i18n" / (name + ".gz")
            if name in ("en.js", "ja.js", "zh.js") and gz.is_file():
                data = gz.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        if route in ("/api/sim/events", "/api/fetch/events"):
            jid = urllib.parse.parse_qs(p.query).get("id", [""])[0]
            return self._sim_events(jid)
        if route in ("/api/sim/result", "/api/fetch/result"):
            jid = urllib.parse.parse_qs(p.query).get("id", [""])[0]
            snap = job_snapshot(jid)
            return self._json(snap) if snap else self._err("없는 작업입니다", 404)
        if route == "/api/stats":
            return self._stats()
        if route == "/api/board":
            qs = urllib.parse.parse_qs(p.query)
            try:
                before = float(qs.get("before", [""])[0]) if qs.get("before", [""])[0] else None
                n = int(qs.get("n", ["30"])[0])
            except ValueError:
                before, n = None, 30
            return self._json({"items": board_list(admin=False, before=before, limit=n)})
        # 관리자 페이지 — **테일넷 안에서만.** Funnel을 지나온 공개 트래픽은 프록시가
        # 로컬에서 접속하므로 127.0.0.1로 보이고, 테일넷 직통(100.85.249.28:8766)만
        # 100.x 발신지가 된다. 밖에서는 존재 자체를 숨긴다(404).
        if route == "/admin.js":
            if not self.client_address[0].startswith("100."):
                return self._err("not found", 404)
            js = ADMIN_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(js)))
            self.end_headers()
            self.wfile.write(js)
            return
        if route == "/admin":
            if not self.client_address[0].startswith("100."):
                return self._err("not found", 404)
            data = ADMIN_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # 관리자 화면은 자주 바뀐다 — 캐시가 남으면 새 버튼이 안 보인다
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if route == "/api/share":
            code = share_code(urllib.parse.parse_qs(p.query).get("c", [""])[0])
            got = share_get(code) if share_ok() else None
            if got is None:
                bump("share_miss")
                return self._err("이 링크는 만료됐거나 지워졌습니다 (공유는 24시간 유지됩니다).",
                                 404)
            bump("share_get")
            return self._json(got)
        # 공유 페이지. **경로에 코드를 붙이지 않는다** — `index.html`의 자산 링크가 전부
        # 상대경로(`app.js`·`style.css`·`image/…`)라서 `/s/<코드>`로 서빙하면 브라우저가
        # `/s/app.js`를 찾아 전부 404가 된다. 질의문(`/s?c=…`)이면 기준 경로가 `/`로
        # 남아 아무것도 손대지 않아도 된다. 끝의 `/`는 같은 이유로 되돌려 보낸다.
        if p.path == "/s/":
            self.send_response(301)
            self.send_header("Location", "/s" + (("?" + p.query) if p.query else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.path == "/s":
            bump("page"); bump_ref(self.headers.get("Referer"))
            self.path = "/index.html"
            return super().do_GET()
        if self.path.rstrip("/") == "/api/health":
            return self._json({
                "sim": True,
                "cp": True,
                # 캡처 판독은 서명표(data/face_sig.json)가 있어야 한다 —
                # 웹이 «기록 불러오기» 버튼을 감출 근거다
                "ocr": squad_ocr.available(),
                # 전투력 숫자 판독은 OpenCV + 학습된 모델이 있어야 한다
                "power_ocr": bool(power_ocr and power_ocr.available()),
                # 공유는 저장소를 열 수 있을 때만 켠다 — 웹이 버튼을 감출 근거다
                "share": share_ok(),
                # 실험 기능(버프 대상 진단). **로컬 직접 접속에서만 참**이다 —
                # 배포에 딸려 가도 운영에서는 꺼진 채로 있는다.
                "lab": is_local_only(self),
                # 유니온 레이드 — 아직 만드는 중이라 **로컬 직접 접속에서만** 보인다.
                # 상용에 딸려 나가도 켜지지 않는다(is_local_only 주석 참고).
                # 다 만든 뒤 켤 때는 서비스 유닛에 `NIKKE_UNION=1`만 넣으면 된다.
                "union": is_local_only(self) or os.environ.get("NIKKE_UNION") == "1",
                "share_ttl": int(SHARE_TTL),
                "fetch": _allow_fetch and (ROOT / "scraper" / ".session_cookie").exists(),
                "max_decks": MAX_DECKS,
                "max_duration": MAX_DURATION,
                "jobs": _pool_jobs,
                "slots": SIM_SLOTS,
                "queue_max": SIM_QUEUE_MAX,
            })
        # 문서 요청만 센다 — 이미지·css·js까지 세면 «몇 명이 왔나»가 안 보인다.
        if not route.startswith("/api/") and "." not in (p.path or "/").rsplit("/", 1)[-1]:
            bump("page"); bump_ref(self.headers.get("Referer"))
            # SPA 경로 폴백(계약 §3): /result·/deck/3 같은 확장자 없는 화면 주소는 새로고침·
            # 직접 진입에서도 앱이 떠야 한다 — /s와 같은 처리. /api/*와 확장자 있는 경로는
            # 진짜 404로 남긴다(오타 난 자산 주소가 200 HTML로 위장하면 디버깅이 지옥이다).
            if not os.path.isfile(self.translate_path(p.path)):
                self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        try:
            if self.path.rstrip("/") == "/api/board":
                if not rate_ok("*", "board", RATE_MAX_BOARD):
                    return self._err("피드백이 너무 잦습니다 — 잠시 후 다시 남겨 주세요.", 429)
                b = self._body()
                if b.get("web"):                     # 허니팟 — 사람 눈에 안 보이는 칸
                    return self._json({"ok": True})  # 봇에게는 성공한 척
                kind = "피드백"          # 유형 구분은 뺐다 (유저 결정) — 열만 남긴다
                body_text = str(b.get("body") or "").strip()
                if not (2 <= len(body_text) <= 1000):
                    return self._err("내용은 2~1000자로 적어 주세요")
                nick = (str(b.get("nick") or "").strip() or "익명")[:12]
                private = bool(b.get("private"))
                pw = str(b.get("pw") or "")
                if private and not (4 <= len(pw) <= 32):
                    return self._err("비공개 글은 4~32자 비밀번호가 필요합니다")
                return self._json({"ok": True,
                                   "id": board_add(kind, nick, body_text, private, pw)})
            if self.path.rstrip("/") == "/api/board/view":
                # 비공개 글 열람. 무차별 대입을 창당 상한으로 누른다
                if not rate_ok("*", "boardpw", 10):
                    return self._err("시도가 너무 잦습니다 — 잠시 후 다시 해 주세요.", 429)
                b = self._body()
                got = board_view(str(b.get("id") or ""), str(b.get("pw") or ""))
                if got is None:
                    return self._err("비밀번호가 맞지 않습니다", 403)
                return self._json(got)
            if self.path.rstrip("/") == "/api/board/admin":
                if not self.client_address[0].startswith("100."):
                    return self._err("not found", 404)
                b = self._body()
                if str(b.get("op")) == "list":
                    return self._json({"items": board_list(admin=True)})
                if str(b.get("op")) == "refs":
                    try:
                        return self._json(ref_stats(int(b.get("days") or 30)))
                    except ValueError:
                        return self._json(ref_stats(30))
                if str(b.get("op")) == "settings":
                    return self._json({"sim_busy_guard": sim_busy_guard_enabled(),
                                       "slots": SIM_SLOTS, "queue_max": SIM_QUEUE_MAX})
                if str(b.get("op")) == "sim-guard":
                    enabled = b.get("enabled") is True
                    return self._json({"ok": True,
                                       "sim_busy_guard": set_sim_busy_guard(enabled)})
                ok = board_admin(str(b.get("op") or ""), str(b.get("id") or ""),
                                 str(b.get("body") or "").strip())
                return self._json({"ok": ok}) if ok else self._err("실패 — id·op 확인")
            if self.path.rstrip("/") == "/api/sim":
                # 차단을 끄면 동시 요청을 모두 유계 대기열로 받는다. 켠 경우에만 창당
                # 상한과 원자적인 busy 검사를 함께 써서 새 요청을 거절한다.
                guard = sim_busy_guard_enabled()
                if guard and not rate_ok("*", "sim", RATE_MAX_SIM):
                    return self._err("서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시 시도하세요.", 429)
                return self._sim(reject_if_busy=guard)
            if self.path.rstrip("/") == "/api/cp":
                # 전투력 계산기 — 계산은 마이크로초 단위 산수라 큐가 필요 없다.
                # 산식·계수는 서버에만 있다(web/cp_engine.py). 상한은 넉넉히:
                # 옵션 하나 바꿀 때마다 한 번씩 오는 라우트다.
                if not rate_ok("*", "cp", RATE_MAX_CP):
                    return self._err("요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                bump("cp_req")
                body = self._body()
                res = cp_engine.compute(body)
                # 협전(협동작전) 전투력 — 레벨을 40으로 고정하고 나머지는 그대로 다시
                # 계산한다. 823렙 캐릭터 실측으로 이미 검증된 조합이다
                # (996,044 − 924,435「레벨 시뮬 40렙 하향분」= 71,609, 서버와 ±0 —
                # context/scenarios/전투력 산식.md). 실제 레벨이 40 미만이어도 그대로
                # 40으로 다시 계산한다 — «지금 몇 렙이든 협전에서는 40으로 본다»가
                # 이 값이 답하는 질문이라, 실제 레벨과 무관하게 항상 보여 준다.
                try:
                    res["cp40"] = cp_engine.compute({**body, "level": 40})["cp"]
                except (KeyError, ValueError):
                    res["cp40"] = None
                return self._json(res)
            if self.path.rstrip("/") == "/api/atk":
                # 소지 공격력만 — 시뮬이 아니라 표 조회라 큐가 필요 없다
                if not rate_ok("*", "cp", RATE_MAX_CP):
                    return self._err("요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                b = self._body()
                names = b.get("names") or []
                if not isinstance(names, list) or not (1 <= len(names) <= 5):
                    raise ValueError("names는 1~5명이어야 한다")
                return self._json({"atk": base_atk_of(names, b.get("profile"))})
            if self.path.rstrip("/") == "/api/squad/power":
                # 전투력 숫자. 브라우저가 SQUAD 라벨 기준으로 잘라 배율까지 맞춘
                # «영역»만 보낸다 — 캡처 원본은 오지 않는다.
                if not rate_ok("*", "ocr", RATE_MAX_OCR):
                    return self._err("판독 요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                if not (power_ocr and power_ocr.available()):
                    return self._err("전투력 판독을 쓸 수 없습니다 "
                                     "(OpenCV 또는 학습 모델 없음).", 503)
                bump("ocr_req")
                b = self._body()
                regs = b.get("regions") or []
                if not isinstance(regs, list) or not (1 <= len(regs) <= OCR_MAX_POWERS):
                    raise ValueError(f"regions는 1~{OCR_MAX_POWERS}개여야 한다 "
                                     "— 솔로레이드는 스쿼드가 다섯이다")
                got = []
                for r in regs:
                    got.append({"w": int(r.get("w", 0)), "h": int(r.get("h", 0)),
                                "rgb": base64.b64decode(r.get("rgb", ""), validate=True)})
                return self._json({"powers": power_ocr.read_regions(got)})
            if self.path.rstrip("/") in ("/api/squad/align", "/api/squad/read"):
                # 스쿼드 캡처 판독. **캡처 원본은 오지 않는다** — 브라우저가 칸을
                # 잘라 줄인 화소만 보낸다(web/squad_ocr.py 첫머리 참조).
                if not rate_ok("*", "ocr", RATE_MAX_OCR):
                    return self._err("판독 요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                if not squad_ocr.available():
                    return self._err("판독 대조군이 없습니다 "
                                     "(python scraper/face_sig.py).", 503)
                bump("ocr_req")
                return self._squad(self.path.rstrip("/").rsplit("/", 1)[-1])
            if self.path.rstrip("/") == "/api/share":
                # 만드는 쪽만 우리 페이지로 제한한다. 읽는 쪽(GET)은 링크를 받은 사람이
                # 열어야 하므로 걸지 않는다.
                if not from_our_page(self):
                    bump("bot_403")
                    return self._err("이 사이트의 페이지에서만 공유할 수 있습니다.", 403)
                if not rate_ok("*", "share", RATE_MAX_SHARE):
                    return self._err("공유 요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                if not share_ok():
                    return self._err("이 서버는 공유 저장소를 열 수 없습니다.", 503)
                n = int(self.headers.get("Content-Length") or 0)
                if n > SHARE_MAX_BODY:
                    return self._err(f"공유 내용이 너무 큽니다 ({n:,}B > "
                                     f"{SHARE_MAX_BODY:,}B) — 편성과 딜 수치만 담깁니다.", 413)
                clean = share_clean(self._body())
                code, exp = share_put(clean)
                bump("share_put")
                # **주소를 서버가 짓지 않는다.** 프록시 헤더를 믿어야 하고, 웹은
                # `location.origin`으로 정확히 같은 것을 만들 수 있다.
                return self._json({"code": code, "expires": int(exp),
                                   "ttl": int(SHARE_TTL)})
            if self.path.rstrip("/") == "/api/unshare":
                if not rate_ok("*", "share", RATE_MAX_SHARE):
                    return self._err("요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429)
                if not share_ok():
                    return self._err("이 서버는 공유 저장소를 열 수 없습니다.", 503)
                gone = share_del(share_code(self._body().get("code")))
                if gone:
                    bump("share_del")
                return self._json({"deleted": gone})
            if self.path.rstrip("/") == "/api/fetch":
                # 이 라우트만 운영자 계정을 쓴다 — 우리 페이지 밖에서 부를 이유가 없다.
                # 크롤러·스크립트는 여기서 걸린다(헤더를 위조하면 통과하지만, 그때는
                # 아래 세 상한이 남는다).
                if not from_our_page(self):
                    bump("bot_403")
                    return self._err("이 사이트의 페이지에서만 조회할 수 있습니다.", 403)
                if not _allow_fetch:
                    return self._err("이 서버는 조회 프록시를 끄고 실행되었습니다 "
                                     "(--no-fetch). 북마클릿을 사용하세요.", 503)
                return self._fetch()
            self._err("없는 라우트", 404)
        except BusyError as e:
            self._err(e, 429)
        except ValueError as e:
            self._err(e, 400)
        except SystemExit as e:
            # 저장소는 사용자 오류를 `raise SystemExit`으로 낸다 (CLI 관용구 —
            # `spec.profile_from_dict`·`load_profile`·`GrowthProfile`). SystemExit은
            # Exception이 아니라 BaseException이라 아래 절에 걸리지 않고, 잡지 않으면
            # **핸들러 스레드가 죽어 응답 없이 연결이 끊긴다** — 클라이언트에는 이유가
            # 하나도 남지 않는다. 그래서 여기서 잡아 400으로 바꾼다.
            self._err(e, 400)
        except RuntimeError as e:
            # 조회 실패처럼 **사용자에게 설명할 수 있는** 실패다 (비공개 계정 등).
            self._err(e, 502)
        except Exception as e:                       # noqa: BLE001
            # 그 밖의 예외는 경로·내부 구조를 그대로 뱉을 수 있다 (FileNotFoundError가
            # 절대경로를 담는 식). 자세한 건 서버 로그에만 남기고 밖으로는 안 보낸다.
            traceback.print_exc()
            self._err("서버 오류입니다 — 잠시 후 다시 시도하세요.", 500)

    def _sim(self, *, reject_if_busy: bool = False):
        b = self._body()
        decks = b.get("decks") or []
        if not isinstance(decks, list) or not decks:
            raise ValueError("decks가 비었다")
        if len(decks) > MAX_DECKS:
            raise ValueError(f"덱이 너무 많다 ({len(decks)} > {MAX_DECKS})")
        for d in decks:
            if not isinstance(d, list) or not (1 <= len(d) <= 5) or not all(d):
                raise ValueError("각 덱은 1~5명의 캐릭터 이름 배열이어야 한다 "
                                 "(빈 슬롯이 있으면 계산하지 않는다)")
        duration = float(b.get("duration") or 180.0)
        if not (1.0 <= duration <= MAX_DURATION):
            raise ValueError(f"duration이 범위를 벗어났다 (1~{MAX_DURATION:.0f})")
        code = b.get("code") or None
        profile = b.get("profile")
        profile_json = json.dumps(profile, ensure_ascii=False) if profile else None
        if profile_json:
            # **워커에 넘기기 전에 여기서 검사한다.** 워커 안에서 터지면 SystemExit이
            # 워커를 죽여 풀이 깨지고(BrokenProcessPool) 덱 수만큼 프로세스가 낭비된다.
            # 여기서 걸러야 이유가 담긴 400 한 번으로 끝난다.
            from context import spec as char_spec
            char_spec.profile_from_dict(json.loads(profile_json), 
                                    where="전달된 프로필")

        enemy = _clean_enemy(b.get("enemy"))
        config_over = _clean_config(b.get("config"))
        controls = b.get("controls") or []
        # 버스트 금지는 캐릭터 오버라이드가 아니라 **전투 설정**이라 덱마다 config에
        # 얹는다(브라우저 워커와 같은 규약 — 한쪽만 고치면 서버 on/off로 총딜이 갈린다).
        cubes = b.get("cubes") or []
        # 덱별 니케 레벨(유니온 레이드). 솔로는 안 보내고, 계산기 기본 400이 남는다.
        raw_levels = b.get("levels")
        levels = [(_num(v, 1, LV_MAX, int) if v is not None else None)
                  for v in (raw_levels if isinstance(raw_levels, list) else [])]
        raw_codes = b.get("codes")
        codes = [str(c)[:8] if c else None
                 for c in (raw_codes if isinstance(raw_codes, list) else [])]
        # 덱별 «적»과 «전투 조건». 유니온 레이드는 줄마다 다른 보스를 치므로 방어력도
        # 코어도 적정거리도 제각각이다. 안 오면 위의 공용 값이 그대로 쓰인다(솔로).
        raw_enemies = b.get("enemies")
        enemies = [_clean_enemy(e) for e in raw_enemies] if isinstance(raw_enemies, list) else []
        raw_configs = b.get("configs")
        configs = [_clean_config(c) for c in raw_configs] if isinstance(raw_configs, list) else []
        # 타임라인 뷰어 전용 스위치 — "trace"만 통과, 그 외 값은 조용히 버린다(계약 §4).
        # 켰을 때만 job에 키가 생긴다(새 서버 sim.ts와 동일). native 엔진에서만 응답에 trace가 붙는다.
        verbose = "trace" if b.get("verbose") == "trace" else None
        jobs = []
        for i, d in enumerate(decks):
            raw_ctrl = controls[i] if i < len(controls) else None
            no_burst = _no_burst_names(raw_ctrl)
            # 컨트롤과 큐브는 둘 다 캐릭터 오버라이드로 합쳐 넘긴다 — 키가 겹치지
            # 않는다(`control` vs `cube`).
            over = _clean_control(raw_ctrl) or {}
            for nm, cb in (_clean_cubes(cubes[i] if i < len(cubes) else None) or {}).items():
                over.setdefault(nm, {}).update(cb)
            # 레벨은 덱 전원에게 같은 값 — 동기화 소대 레벨이라 니케별로 다르지 않다
            d_level = levels[i] if i < len(levels) else None
            if d_level:
                for nm in d:
                    if isinstance(nm, str) and nm:
                        over.setdefault(nm, {})["level"] = d_level
            # 덱별 약점 코드 — 유니온 레이드는 덱마다 다른 보스를 친다(같은 보스를
            # 여러 덱으로 쳐도 된다). 안 오면 전역 `code`가 그대로 쓰인다(솔로).
            d_code = codes[i] if i < len(codes) else None
            d_enemy = (enemies[i] if i < len(enemies) and enemies[i] else enemy)
            d_config = (configs[i] if i < len(configs) and configs[i] else config_over)
            jobs.append({
                "names": d, "code": (d_code or code), "duration": duration,
                "profile_json": profile_json, "enemy": d_enemy,
                "config_over": ({**d_config, "no_burst_chars": no_burst}
                                if no_burst else d_config),
                "control": over or None,
                **({"verbose": verbose} if verbose else {}),
            })
        # **붙들고 바로 답한다.** 코어가 덱당 0.1초 안이라 줄·이벤트 스트림이 필요 없다 — 입장 제한만
        # 지난다(`_run_sim_now`). 거절(429)·입력 오류(400)는 호출부의 예외 처리로 간다.
        bump("sim_req")
        bump("sim_deck", len(decks))
        res = _run_sim_now(jobs, reject_if_busy=reject_if_busy)
        bump("sim_sec", sum(r.get("sec", 0) for r in res))
        self._json({"results": res})

    def _sim_events(self, jid: str):
        """작업 진행을 Server-Sent Events로 흘려보낸다.

        `queued`(순번 포함) → `running` → `done`/`error` 순으로 상태가 **바뀔 때만**
        한 줄씩 보낸다. 끝나면 스트림을 닫는다.
        """
        if job_snapshot(jid) is None:
            return self._err("없는 작업입니다", 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        last = None
        deadline = time.time() + SSE_MAX
        try:
            while time.time() < deadline:
                snap = job_snapshot(jid)
                if snap is None:
                    break
                # 조회 결과(raw)는 340KB쯤 된다 — 이벤트 스트림에 실어 보내지 않고
                # 「끝났다」만 알린 뒤 `/api/fetch/result`로 받아 가게 한다. 스트림은
                # 진행 상황을 알리는 통로지 데이터를 나르는 통로가 아니다.
                if snap.get("kind") == "fetch":
                    snap.pop("results", None)
                key = (snap["state"], snap["pos"])
                if key != last:
                    self.wfile.write(
                        f"data: {json.dumps(snap, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last = key
                if snap["state"] in ("done", "error"):
                    break
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                      # 브라우저가 탭을 닫은 것 — 조용히 끝낸다

    def _stats(self):
        """운영 지표. **tailnet 안에서만** 보인다.

        공개(Funnel) 요청에는 `Tailscale-Funnel-Request` 헤더가 붙고, tailnet 안에서
        온 요청에는 `Tailscale-User-Login`이 붙는다(실측). 밖에서는 없는 라우트처럼
        404를 준다 — «있는데 막혔다»를 알려 줄 이유가 없다.

        세는 것은 **개수뿐**이다. 주소도 openid도 세션도 담기지 않는다.
        """
        if self.headers.get("Tailscale-Funnel-Request") is not None:
            return self._err("없는 라우트", 404)
        with _stats_lock:
            st = dict(_stats)
        try:
            r = ref_stats(30)
            st["유입_일자"] = {x["day"]: x["n"] for x in r["days"]}
            st["유입_도메인"] = {x["host"]: x["n"] for x in r["hosts"]}
        except Exception:                                         # noqa: BLE001
            pass
        up = time.time() - st.pop("start")
        with _jobs_lock:
            q = {k: sum(1 for j in _jobs.values()
                        if j["kind"] == k and j["state"] in ("queued", "running"))
                 for k in ("sim", "fetch")}
        # `os.getloadavg`는 **Windows에 없다** — `AttributeError`라서 `except OSError`로는
        # 안 잡히고, 응답도 못 보낸 채 연결이 끊겼다(개발은 Windows에서 한다).
        try:
            load = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        except OSError:
            load = None
        st["sim_sec"] = round(st.get("sim_sec", 0.0), 1)
        self._json({
            "uptime": f"{int(up // 86400)}일 {int(up % 86400 // 3600)}시간 "
                      f"{int(up % 3600 // 60)}분",
            "uptime_sec": round(up),
            **st,
            "queue": q, "load": load,
            "pool_jobs": _pool_jobs, "fetch_on": _allow_fetch,
            "sim_busy_guard": sim_busy_guard_enabled(),
        })

    def _fetch(self):
        """조회를 **줄에 세우고 id만 준다.** 결과는 이벤트 스트림으로 받아 간다.

        긴 POST로 붙들고 있으면 대기 순번을 보여 줄 수 없고, 앞에 몇 건이 밀린 동안
        프록시·브라우저 타임아웃에 걸린다. 계산(`/api/sim`)과 같은 모양이다.
        """
        b = self._body()
        try:
            openid = openid_from_input(b.get("openid") or b.get("url") or "")
        except ValueError:
            bump("fetch_bad_input")
            raise
        # area: 다시 싱크할 때만 온다 — 그 지역 하나로 고정해서 조회한다.
        # 새로 받을 때는 없으니 fetch_raw가 후보를 전부 훑는다.
        area = b.get("area")
        area = int(area) if isinstance(area, (int, str)) and str(area).strip() else None
        bump("fetch_req")
        jid, pos = job_submit({"openid": openid, "area": area}, "fetch")
        self._json({"job": jid, "queued": pos}, 202)


class Server(ThreadingHTTPServer):
    """`ConnectionResetError`에 스택 트레이스를 찍지 않는다.

    브라우저가 이벤트 스트림을 닫거나 페이지를 떠나면 소켓이 그냥 끊긴다 — **정상**이다.
    그런데 기본 동작은 그때마다 20줄짜리 트레이스를 찍어서, 6시간 로그 3,766줄 가운데
    900줄이 이 소음이었다. 진짜 예외는 그대로 찍는다.
    """

    allow_reuse_address = True

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, BrokenPipeError,
                          ConnectionAbortedError, TimeoutError)):
            return
        traceback.print_exc()


def main() -> None:
    global _pool, _pool_jobs, _allow_fetch
    ap = argparse.ArgumentParser(description="DILDORO 서버")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본은 로컬만. Tailscale 안에서 쓰려면 그대로 두고 "
                         "테일스케일 IP로 접속하거나 0.0.0.0을 준다")
    ap.add_argument("--jobs", type=int, default=0, help="계산 워커 수 (0=자동)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="조회 프록시를 끈다 (운영자 세션을 쓰지 않는다)")
    args = ap.parse_args()

    if not DIST.exists():
        raise SystemExit(f"{DIST} 없음 — 먼저 `python web/build.py`를 돌려라.")

    _allow_fetch = not args.no_fetch
    # 코어 하나는 비워 둔다 — 전부 계산에 쓰면 서버 스레드가 굶어 응답이 끊긴다.
    _pool_jobs = args.jobs or max(1, min(8, (os.cpu_count() or 2) - 1))
    _pool = _new_pool()
    jobs = _pool_jobs

    cookie_ok = (ROOT / "scraper" / ".session_cookie").exists()
    # 계산은 요청 스레드가 동기로 돌린다(`_run_sim_now`) — 계산 워커 스레드는 띄우지 않는다.
    # (`_sim_worker`·`/api/sim/events`는 되살릴 수 있게 코드로 남긴다.)
    # 조회 워커는 **하나뿐이다** — 조회가 서로 겹치지 않게 하는 장치가 이것이다.
    threading.Thread(target=_fetch_worker, daemon=True).start()

    with Server((args.host, args.port), Handler) as httpd:
        print(f"http://{args.host}:{args.port}  (dist={DIST})")
        print(f"계산 워커 {jobs}개 · 조회 프록시 "
              + ("켜짐" if _allow_fetch else "꺼짐(--no-fetch)")
              + (" · .session_cookie 있음" if cookie_ok else " · .session_cookie 없음"))
        # 재기동 뒤 «뭐가 안 켜졌나»를 journalctl 한 줄로 알 수 있어야 한다.
        feats = {
            "전투력계산기": True,
            "얼굴판독": bool(squad_ocr and squad_ocr.available()),
            "총딜판독": bool(power_ocr and power_ocr.available()),
            "공유링크": bool(SHARE_DIR and SHARE_DIR.exists()),
        }
        print("기능: " + " · ".join(f"{k} {'켜짐' if v else '꺼짐'}"
                                    for k, v in feats.items()))
        for name, why in _OPTIONAL_OFF.items():
            print(f"[!] {name} 꺼짐 — {why}")
            print(f"    고치려면: pip3 install --user --break-system-packages "
                  f"-r deploy/requirements-server.txt")
        for name, on in feats.items():
            if not on and name not in ("공유링크",):
                print(f"[!] {name}이 꺼진 채로 뜬다 — /api/health로 확인해라.")
        if _allow_fetch and not cookie_ok:
            print("[!] 조회 프록시가 켜져 있는데 scraper/.session_cookie가 없다 — "
                  "/api/fetch는 실패한다. 북마클릿만 쓸 거면 --no-fetch로 끄는 게 낫다.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료")
        finally:
            with _pool_lock:
                if _pool is not None:
                    _pool.shutdown(cancel_futures=True)


if __name__ == "__main__":
    main()
