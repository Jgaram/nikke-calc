// 부트스트랩 — Node(@hono/node-server)와 Bun(Bun.serve) 겸용. 기본은 로컬 바인딩(파이썬 서버와 같다).
import path from "node:path";
import { fileURLToPath } from "node:url";

import { makeApp } from "./app.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..", ".."); // web/srv/src → 저장소 루트
const DIST = path.join(ROOT, "web", "dist");

const args = process.argv.slice(2);
const argOf = (name: string, dflt: string): string => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : dflt;
};
const port = Number(argOf("--port", "8765"));
const host = argOf("--host", "127.0.0.1");
const threads = Number(argOf("--jobs", "0"));
// 파이썬 사이드카(내부 포트) — 없으면 "" 로 꺼서 해당 라우트를 502로 둔다
const sidecar = argOf("--sidecar", "http://127.0.0.1:8768");

const app = makeApp({ root: ROOT, dist: DIST, threads, sidecar: sidecar || null });

declare const Bun: { serve(o: { port: number; hostname: string; fetch: unknown }): unknown } | undefined;

if (typeof Bun !== "undefined" && Bun) {
  Bun.serve({ port, hostname: host, fetch: app.fetch });
  console.log(`http://${host}:${port}  (dist=${DIST}) [bun]`);
} else {
  const { serve } = await import("@hono/node-server");
  serve({ fetch: app.fetch, port, hostname: host });
  console.log(`http://${host}:${port}  (dist=${DIST}) [node]`);
}
