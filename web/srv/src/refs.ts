// 유입 집계 — 문서 방문의 Referer를 일자(KST)·도메인·주소로 sqlite `ref` 표에 20건 묶음으로 쌓는다.
// 방문자를 구분하지 않는다: 남는 것은 «어느 날 어느 주소에서 몇 번»뿐이다 (계약 §3).
import { refInit, db, shareOk } from "./db.js";

const REF_LEN = 300;
const KST_OFFSET = 9 * 60 * 60 * 1000;

let queue: Array<[string, string, string]> = [];

function refDay(at?: number): string {
  const d = new Date((at ?? Date.now()) + KST_OFFSET);
  return d.toISOString().slice(0, 10);
}

function refFlush(root: string): void {
  const batch = queue;
  queue = [];
  if (!batch.length || !shareOk(root)) return;
  try {
    refInit();
    for (const [day, host, url] of batch) {
      db().run(
        "INSERT INTO ref (day, host, url, n) VALUES (?,?,?,1) " +
        "ON CONFLICT(day, url) DO UPDATE SET n = n + 1",
        [day, host, url]
      );
    }
  } catch (e) {
    console.error(`ref  유입 기록 실패(무시): ${e instanceof Error ? e.message : e}`);
  }
}

export function bumpRef(root: string, referer: string | undefined): void {
  const day = refDay();
  let host: string;
  let url: string;
  if (!referer) {
    host = url = "(직접·북마크)";
  } else {
    let u: URL;
    try {
      u = new URL(referer);
    } catch {
      return;
    }
    if (!u.hostname || u.hostname.endsWith("tetra-pantone.ts.net")) return; // 사이트 내부 이동은 유입이 아니다
    host = u.hostname;
    url = referer.slice(0, REF_LEN);
  }
  queue.push([day, host, url]);
  if (queue.length >= 20) refFlush(root);
}

export function refStats(root: string, days = 30): Record<string, unknown> {
  refFlush(root);
  if (!shareOk(root)) throw new Error("share 저장소가 준비되지 않았다");
  refInit();
  const since = refDay(Date.now() - days * 86400 * 1000);
  const byDay = db().all("SELECT day, SUM(n) AS n FROM ref WHERE day >= ? GROUP BY day ORDER BY day DESC", [since]);
  const byHost = db().all(
    "SELECT host, SUM(n) AS n FROM ref WHERE day >= ? GROUP BY host ORDER BY SUM(n) DESC LIMIT 30", [since]);
  const byUrl = db().all(
    "SELECT url, SUM(n) AS n FROM ref WHERE day >= ? GROUP BY url ORDER BY SUM(n) DESC LIMIT 60", [since]);
  const grid = db().all(
    "SELECT day, host, SUM(n) AS n FROM ref WHERE day >= ? GROUP BY day, host ORDER BY day DESC, SUM(n) DESC", [since]);
  return {
    days: byDay.map((x) => ({ day: x.day, n: x.n })),
    hosts: byHost.map((x) => ({ host: x.host, n: x.n })),
    urls: byUrl.map((x) => ({ url: x.url, n: x.n })),
    grid: grid.map((x) => ({ day: x.day, host: x.host, n: x.n })),
  };
}
