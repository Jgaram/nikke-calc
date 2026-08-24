"""`/api/stats` JSON을 사람이 읽는 줄로 바꾼다. `deploy/monitor.sh`가 부른다.

셸 안에 파이썬을 박아 넣었더니 f-string 안의 따옴표 이스케이프가 깨졌다
(`{d[\"uptime\"]}` → SyntaxError). 파일로 빼면 그 문제가 없다.
"""
import json
import sys


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("  (서버 응답 없음)")
        return
    load = d.get("load") or [0]
    print(f"  가동 {d['uptime']} · 부하 {load[0]:.2f} · 워커 {d['pool_jobs']}개"
          f" · 조회 {'켜짐' if d['fetch_on'] else '꺼짐'}")
    print(f"  페이지 {d['page']}건")
    print(f"  계산   요청 {d['sim_req']}건 · 덱 {d['sim_deck']}개 · 실패 {d['sim_err']}건"
          f" · 누적 {d['sim_sec']}초")
    print(f"  조회   요청 {d['fetch_req']}건 · 성공 {d['fetch_ok']}건 · 실패 {d['fetch_err']}건")
    # 실패는 종류를 갈라 봐야 뜻이 있다 — 비공개는 사용자 실수(정상), 세션 만료는
    # 운영자가 쿠키를 갱신해야 하는 일이다.
    kinds = [("비공개", d.get("fetch_err_private", 0)),
             ("세션만료", d.get("fetch_err_session", 0)),
             ("없는계정", d.get("fetch_err_notfound", 0)),
             ("기타", d.get("fetch_err_other", 0)),
             ("입력오류", d.get("fetch_bad_input", 0))]
    if any(v for _, v in kinds):
        print("         └ " + " · ".join(f"{k} {v}" for k, v in kinds if v))
    if d.get("fetch_err_session"):
        print("         ⚠ 세션 만료가 있다 — scraper/.session_cookie를 갱신해야 한다")
    print(f"  차단   봇 {d['bot_403']}건 · 대기열 만원 {d['busy_429']}건")
    q = d.get("queue") or {}
    print(f"  대기중 계산 {q.get('sim', 0)} · 조회 {q.get('fetch', 0)}")


if __name__ == "__main__":
    main()
