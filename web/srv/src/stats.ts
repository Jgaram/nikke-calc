// 운영 지표 — 개수만 센다(파이썬 서버와 같은 키). 디스크에 안 쓴다(재시작하면 0부터).
export const stats: Record<string, number> = {
  page: 0,
  sim_req: 0, sim_deck: 0, sim_err: 0, sim_sec: 0,
  fetch_req: 0, fetch_ok: 0, fetch_err: 0,
  fetch_err_private: 0, fetch_err_session: 0, fetch_err_notfound: 0, fetch_err_other: 0,
  fetch_bad_input: 0,
  busy_429: 0, bot_403: 0, cp_req: 0,
  share_put: 0, share_get: 0, share_miss: 0, share_del: 0,
};
export const startedAt = Date.now() / 1000;

export function bump(key: string, n = 1): void {
  stats[key] = (stats[key] ?? 0) + n;
}
