// Hono 앱 조립 — 계약(context/SERVER-CONTRACT.md)의 공통 계층과 슬라이스 라우트(/api/sim ·
// /api/health · 정적 · /s). 나머지 라우트는 단계적으로 옮긴다(§4 실행 계획).
import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { Hono, type Context } from "hono";

import { cacheControlFor, lookup } from "./static.js";
import { handleSim, BusyError, MAX_DECKS, MAX_DURATION, SIM_QUEUE_MAX, SIM_SLOTS } from "./sim.js";
import { available, getLoadError, InputError } from "./simcore.js";
import { bump, stats } from "./stats.js";

export const MAX_BODY = 8 * 1024 * 1024;
const RATE_WINDOW = 60_000;
const RATE_MAX_SIM = 12;

export type Conf = {
  root: string; // 저장소 루트 (nikke-calc)
  dist: string;
  threads: number;
};

// ── 공통 헤더 (모든 응답 — 정적 포함) ──────────────────────────────────────
const CSP =
  "default-src 'self'; " +
  "script-src 'self' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; " +
  "worker-src 'self' blob:; " +
  "connect-src 'self' https://cdn.jsdelivr.net; " +
  "img-src 'self' data: blob:; " +
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
  "font-src 'self' https://fonts.gstatic.com; " +
  "object-src 'none'; base-uri 'none'; form-action 'none'; " +
  "frame-ancestors 'none'";

function commonHeaders(c: Context): void {
  c.header("Content-Security-Policy", CSP);
  c.header("X-Content-Type-Options", "nosniff");
  c.header("Referrer-Policy", "no-referrer");
  const url = new URL(c.req.url);
  c.header("Cache-Control", cacheControlFor(url.pathname + (url.search ? url.search : "")));
}

// ── 창당 상한 (서버 전역 — 파이썬 rate_ok(ip="*")와 같다) ───────────────────
const hits = new Map<string, number[]>();

function rateOk(kind: string, limit: number): boolean {
  const now = Date.now();
  const seen = (hits.get(kind) ?? []).filter((t) => now - t < RATE_WINDOW);
  if (seen.length >= limit) {
    hits.set(kind, seen);
    return false;
  }
  seen.push(now);
  hits.set(kind, seen);
  return true;
}

// ── 본문 (8MB 상한 · 소진 규칙) ─────────────────────────────────────────────
async function drain(c: Context): Promise<void> {
  try {
    await c.req.raw.arrayBuffer();
  } catch {
    /* 이미 소비됐거나 연결이 끊김 */
  }
}

async function readBody(c: Context): Promise<Record<string, unknown>> {
  const n = Number(c.req.header("content-length") ?? 0);
  if (!n || n <= 0) throw new InputError("빈 요청");
  if (n > MAX_BODY) {
    throw new InputError(`요청이 너무 큽니다 (${n.toLocaleString("en-US")}B > ${MAX_BODY.toLocaleString("en-US")}B)`);
  }
  const buf = await c.req.raw.arrayBuffer();
  const text = new TextDecoder("utf-8").decode(buf);
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch (e) {
    // 파이썬은 json.loads의 영어 문장을 그대로 냈다 — 문장은 계약 예외(상태 400만 계약)
    throw new InputError(e instanceof Error ? e.message : String(e));
  }
}

function jsonErr(c: Context, msg: unknown, status: number): Response {
  commonHeaders(c);
  return c.body(JSON.stringify({ error: String(msg) }), status as 400, {
    "Content-Type": "application/json; charset=utf-8",
  });
}

function jsonOk(c: Context, obj: unknown, status = 200): Response {
  commonHeaders(c);
  return c.body(JSON.stringify(obj), status as 200, {
    "Content-Type": "application/json; charset=utf-8",
  });
}

// ── 운영 스위치 (ops.json — 파이썬 서버와 같은 파일을 본다) ────────────────
function simBusyGuardEnabled(root: string): boolean {
  const stateDir = (process.env.STATE_DIRECTORY ?? "").split(path.delimiter)[0];
  const opsPath = stateDir
    ? path.join(stateDir, "ops.json")
    : path.join(root, "web", ".state", "ops.json");
  try {
    const raw = JSON.parse(readFileSync(opsPath, "utf-8")) as Record<string, unknown>;
    return raw.sim_busy_guard === true;
  } catch {
    return false;
  }
}

function isLocalOnly(c: Context): boolean {
  for (const h of ["x-forwarded-for", "tailscale-funnel-request", "tailscale-user-login"]) {
    if (c.req.header(h) !== undefined) return false;
  }
  return true;
}

export function makeApp(conf: Conf): Hono {
  const app = new Hono();
  const poolJobs = conf.threads || Math.max(1, Math.min(8, os.cpus().length - 1));
  const coreReady = () => available(path.join(conf.root, "data"), poolJobs);

  app.post("/api/sim", async (c) => {
    try {
      const guard = simBusyGuardEnabled(conf.root);
      if (guard && !rateOk("sim", RATE_MAX_SIM)) {
        await drain(c);
        return jsonErr(c, "서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시 시도하세요.", 429);
      }
      const b = await readBody(c);
      if (!coreReady()) {
        console.log(`[sim] 계산 코어를 쓸 수 없다: ${getLoadError()}`);
        bump("sim_err");
        return jsonErr(c, "서버 오류입니다 — 잠시 후 다시 시도하세요.", 500);
      }
      bump("sim_req");
      bump("sim_deck", Array.isArray(b.decks) ? (b.decks as unknown[]).length : 0);
      const out = await handleSim(b, guard, () => bump("busy_429"));
      bump("sim_sec", (out.results as Array<{ sec?: number }>).reduce((s, r) => s + (r.sec ?? 0), 0));
      return jsonOk(c, out);
    } catch (e) {
      if (e instanceof BusyError) return jsonErr(c, e.message, 429);
      if (e instanceof InputError) {
        bump("sim_err");
        return jsonErr(c, e.message, 400);
      }
      bump("sim_err");
      console.error(e);
      return jsonErr(c, "서버 오류입니다 — 잠시 후 다시 시도하세요.", 500);
    }
  });

  app.get("/api/health", (c) => {
    return jsonOk(c, {
      sim: true,
      cp: false, // TODO: 라우트 이식 때 켠다 (§4 실행 계획 3)
      ocr: false, // TODO: 사이드카 연결 때
      power_ocr: false, // TODO: 사이드카 연결 때
      share: false, // TODO: sqlite 이식 때
      lab: isLocalOnly(c),
      union: isLocalOnly(c) || process.env.NIKKE_UNION === "1",
      share_ttl: 86400,
      fetch: false, // TODO: 조회 프록시 이식 때
      max_decks: MAX_DECKS,
      max_duration: MAX_DURATION,
      jobs: poolJobs,
      slots: SIM_SLOTS,
      queue_max: SIM_QUEUE_MAX,
    });
  });

  // 공유 페이지 — 질의문 형태(/s?c=…)만. 끝의 /는 되돌려 보낸다 (계약 §3)
  app.get("/s/", (c) => {
    commonHeaders(c);
    const url = new URL(c.req.url);
    return c.body(null, 301, { Location: "/s" + (url.search ?? ""), "Content-Length": "0" });
  });

  // 정적 + /s (+ 문서 방문 집계)
  app.on(["GET", "HEAD"], "*", async (c) => {
    const url = new URL(c.req.url);
    let pathname = url.pathname;
    // 파이썬은 GET의 없는 /api/* 를 정적으로 흘린다(404 HTML) — JSON «없는 라우트»는 POST 전용이다
    if (pathname === "/s") {
      bump("page");
      pathname = "/index.html";
    } else {
      const last = (pathname || "/").split("/").pop() ?? "";
      if (!last.includes(".")) bump("page");
    }
    const hit = await lookup(conf.dist, pathname, (c.req.header("accept-encoding") ?? "").includes("gzip"));
    commonHeaders(c);
    const head = c.req.method === "HEAD";
    if (hit.kind === "gzip_i18n") {
      const h = {
        "Content-Type": "text/javascript; charset=utf-8",
        "Content-Encoding": "gzip",
        Vary: "Accept-Encoding",
        "Content-Length": String(hit.body.byteLength),
      };
      return head ? c.body(null, 200, h) : c.body(hit.body as unknown as ArrayBuffer, 200, h);
    }
    if (hit.kind === "file") {
      const h = { "Content-Type": hit.type, "Content-Length": String(hit.body.byteLength) };
      return head ? c.body(null, 200, h) : c.body(hit.body as unknown as ArrayBuffer, 200, h);
    }
    return c.body("File not found", 404, { "Content-Type": "text/plain; charset=utf-8" });
  });

  // 그 밖의 메서드 — POST로 없는 라우트
  app.all("*", async (c) => {
    await drain(c);
    return jsonErr(c, "없는 라우트", 404);
  });

  return app;
}

export { stats };
