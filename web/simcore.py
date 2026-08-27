"""컴파일된 계산 코어(`nikke_py` 확장 모듈) 어댑터 — `web/server.py`가 `NIKKE_SIM_ENGINE=native`일 때 쓴다.

    from simcore import available, load_error, run

- `run(squad, config, enemy)` → `calculator.sim_result.SimResult`. `calculator.timeline.simulate`와 같은
  입력에 같은 결과(히트 목록·합계·버스트 기록·최공 기록)를 낸다. 서버가 파생하는 것(detail·dps_timeline·
  burst_cycles·summarize_top_atk)은 이 SimResult에 기존 함수를 그대로 적용한다.
- 확장 모듈 파일(`nikke_py.so`)은 저장소에 없다 — 서버에서 빌드해 이 파일 옆에 둔다. 없으면
  `available()`이 False고, 서버는 그 요청을 실패로 끝낸다(다른 경로로 대신 답하지 않는다).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_lock = threading.Lock()
_mod = None
_load_error: str | None = None


def available(data_dir: Path, threads: int = 0) -> bool:
    """확장 모듈을 임포트하고 데이터 디렉터리를 지정한다. 실패해도 예외를 던지지 않는다.
    `threads`는 배치 계산 스레드 수(0 = CPU 수) — 첫 호출 때만 반영된다."""
    global _mod, _load_error
    with _lock:
        if _mod is not None:
            return True
        if _load_error is not None:
            return False
        try:
            import nikke_py  # noqa: E402 — 이 파일 옆의 nikke_py.so
            nikke_py.load_data(str(data_dir), threads=int(threads or 0))
            _mod = nikke_py
            return True
        except Exception as ex:  # noqa: BLE001
            _load_error = f"{type(ex).__name__}: {ex}"
            return False


def _request_of(job: dict, profile_cache: dict) -> str:
    """서버 job dict → 코어 요청 JSON. 프로필 문자열은 요청당 한 번만 파싱한다."""
    pj = job.get("profile_json")
    profile = None
    if pj:
        if pj not in profile_cache:
            profile_cache[pj] = json.loads(pj)
        profile = profile_cache[pj]
    req = {
        "names": [str(n) for n in job["names"]],
        "code": job.get("code"),
        "duration": float(job["duration"]),
        "profile": profile,
        "enemy": job.get("enemy"),
        "config_over": job.get("config_over"),
        "control": job.get("control"),
    }
    if job.get("verbose") == "trace":
        # 타임라인 뷰어 전용 — 코어가 응답 끝에 trace 절을 붙인다. 평소에는 키 자체가 없다.
        req["verbose"] = "trace"
    if job.get("lean") is True:
        # 비교(대량 반복)용 — 코어가 파생 요약을 아예 만들지 않고 {total, chars}만 준다.
        req["lean"] = True
    return json.dumps(req, ensure_ascii=False)


def run_request_batch(jobs: list) -> list:
    """덱 job 여러 개(서버 `_sim_one`이 받던 dict)를 코어가 **한 번에** 조립·계산·요약한다.
    결과는 `_sim_one`과 같은 키(`sec`만 빼고)의 dict 목록, 입력 순서. 스펙 오류는 ValueError로 올린다."""
    if _mod is None:
        raise RuntimeError("계산 코어가 준비되지 않았다 — available(data_dir)를 먼저 부른다")
    cache: dict = {}
    reqs = [_request_of(j, cache) for j in jobs]
    try:
        outs = _mod.simulate_request_batch_json(reqs)
    except ValueError as ex:
        msg = str(ex)
        # 코어는 «[i] 문장»으로 어느 덱인지 붙여 준다 — 사용자에게는 문장만
        if msg.startswith("[") and "] " in msg:
            msg = msg.split("] ", 1)[1]
        raise ValueError(msg) from None
    return [json.loads(o) for o in outs]


def run_request(job: dict) -> dict:
    """덱 하나 — `run_request_batch([job])[0]`"""
    return run_request_batch([job])[0]


def load_error() -> str | None:
    return _load_error


def _decode_hits(h: dict):
    from calculator.sim_result import HitEvent
    casters, tags, skills = h["casters"], h["tags"], h["skills"]
    return [HitEvent(t=row[0], caster=casters[row[1]], damage=row[2], is_crit=bool(row[3]),
                     crit_frac=row[4], hit_tag=tags[row[5]], skill_name=skills[row[6]])
            for row in h["rows"]]


def _to_simresult(out: dict):
    from calculator.sim_result import SimResult
    return SimResult(
        hits=_decode_hits(out["hits"]),
        char_total=dict(out["char_total"]),
        squad_total=out["squad_total"],
        duration=out["duration"],
        log=None,
        burst_casts=[(t, s, n) for (t, s, n) in out["burst_casts"]],
        full_bursts=[(s, e) for (s, e) in out["full_bursts"]],
        top_atk_picks=list(out.get("top_atk_picks") or []),
    )


def run(squad: list, config: dict, enemy: dict | None):
    """`simulate(squad, config, enemy)`와 같은 입력 → SimResult. `available()`이 먼저 참이어야 한다."""
    if _mod is None:
        raise RuntimeError("계산 코어가 준비되지 않았다 — available(data_dir)를 먼저 부른다")
    case = json.dumps({"squad": squad, "config": config, "enemy": enemy}, ensure_ascii=False)
    return _to_simresult(json.loads(_mod.simulate_json(case)))
