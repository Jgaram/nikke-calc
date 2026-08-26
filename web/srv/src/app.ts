// Hono 앱 조립 — 계약(context/SERVER-CONTRACT.md). 파이썬 web/server.py와 같은 응답을 내는 것이
// 존재 이유다: 문장·상태·헤더가 다르면 회귀(deploy/compare_servers.py)가 잡는다.
//
// 남은 이식(§4 실행 계획): cp/atk · 판독(OpenCV) 사이드카 · fetch 프록시+SSE(+job result 라우트).
import { readFileSync, writeFileSync, mkdirSync, renameSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { Hono, type Context } from "hono";

import { ADMIN_HTML, ADMIN_JS } from "./admin_assets.js";
import { boardAdd, boardAdmin, boardList, boardView } from "./board.js";
import { pyInt } from "./clean.js";
import { stateDir } from "./db.js";
import { OpsError } from "./errors.js";
import { bumpRef, refStats } from "./refs.js";
import { shareClean, shareCode, shareDel, shareGet, shareOk, sharePut, SHARE_MAX_BODY, SHARE_TTL } from "./share.js";
import { buildJobs, runSimJobs, BusyError, MAX_DECKS, MAX_DURATION, SIM_QUEUE_MAX, SIM_SLOTS } from "./sim.js";
import { available, getLoadError, InputError } from "./simcore.js";
import { bump, stats, startedAt } from "./stats.js";
import { cacheControlFor, lookup } from "./static.js";
import { proxyTo, sidecarHealth } from "./proxy.js";

export const MAX_BODY = 8 * 1024 * 1024;
const RATE_WINDOW = 60_000;
const RATE = { sim: 12, cp: 600, share: 6, ocr: 60, board: 6, boardpw: 10 } as const;

export type Conf = {
  root: string; // 저장소 루트 (nikke-calc)
  dist: string;
  threads: number;
  /** 파이썬 사이드카(내부 포트) — 아직 TS로 안 옮긴 라우트를 그대로 통과시킨다. null이면 그 라우트는 502. */
  sidecar: string | null;
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

// ── 게이트 ──────────────────────────────────────────────────────────────────
/** `from_our_page` — 우리 페이지가 보낸 요청처럼 보이나 (계약 §2). */
function fromOurPage(c: Context): boolean {
  const site = c.req.header("sec-fetch-site");
  if (site !== undefined) return site === "same-origin";
  const origin = c.req.header("origin");
  if (!origin) return false;
  const host = c.req.header("x-forwarded-host") ?? c.req.header("host") ?? "";
  return Boolean(host) && origin.replace(/\/+$/, "").endsWith("//" + host);
}

function isLocalOnly(c: Context): boolean {
  for (const h of ["x-forwarded-for", "tailscale-funnel-request", "tailscale-user-login"]) {
    if (c.req.header(h) !== undefined) return false;
  }
  return true;
}

declare const Bun: unknown;

type ConnInfoFn = (c: Context) => { remote: { address?: string } };
let connInfo: ConnInfoFn | null = null;

async function clientIp(c: Context): Promise<string> {
  if (!connInfo) {
    if (typeof Bun !== "undefined") {
      const m = await import("hono/bun");
      connInfo = m.getConnInfo as ConnInfoFn;
    } else {
      const m = await import("@hono/node-server/conninfo");
      connInfo = m.getConnInfo as ConnInfoFn;
    }
  }
  let ip = "";
  try {
    ip = connInfo(c).remote.address ?? "";
  } catch {
    ip = "";
  }
  return ip.startsWith("::ffff:") ? ip.slice("::ffff:".length) : ip;
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

/** POST 핸들러 공통 예외 매핑 (계약 §0) — 파이썬 do_POST의 그물과 같다. */
async function guard(c: Context, fn: () => Promise<Response>): Promise<Response> {
  try {
    return await fn();
  } catch (e) {
    await drain(c);
    if (e instanceof BusyError) return jsonErr(c, e.message, 429);
    if (e instanceof InputError) return jsonErr(c, e.message, 400);
    if (e instanceof OpsError) return jsonErr(c, e.message, 502);
    console.error(e);
    return jsonErr(c, "서버 오류입니다 — 잠시 후 다시 시도하세요.", 500);
  }
}

// ── 운영 스위치 (ops.json — 파이썬 서버와 같은 파일을 본다) ────────────────
function opsPath(root: string): string {
  return path.join(stateDir(root), "ops.json");
}

function simBusyGuardEnabled(root: string): boolean {
  try {
    const raw = JSON.parse(readFileSync(opsPath(root), "utf-8")) as Record<string, unknown>;
    return raw.sim_busy_guard === true;
  } catch {
    return false;
  }
}

function setSimBusyGuard(root: string, enabled: boolean): boolean {
  try {
    const p = opsPath(root);
    mkdirSync(path.dirname(p), { recursive: true });
    const tmp = p + ".tmp";
    writeFileSync(tmp, JSON.stringify({ sim_busy_guard: enabled }), "utf-8");
    renameSync(tmp, p);
  } catch (e) {
    throw new OpsError(`운영 설정을 저장하지 못했습니다: ${e instanceof Error ? e.message : e}`);
  }
  return enabled;
}

export function makeApp(conf: Conf): Hono {
  const app = new Hono();
  const poolJobs = conf.threads || Math.max(1, Math.min(8, os.cpus().length - 1));
  const coreReady = () => available(path.join(conf.root, "data"), poolJobs);

  // ── 사이드카 프록시 (계약 §9 — cp·atk·판독·조회와 job events/result) ──
  const viaSidecar = (c: Context) =>
    conf.sidecar
      ? proxyTo(c, conf.sidecar)
      : jsonErr(c, "서버 오류입니다 — 잠시 후 다시 시도하세요.", 502);
  for (const p of ["/api/cp", "/api/atk", "/api/squad/power", "/api/squad/align", "/api/squad/read", "/api/fetch"]) {
    app.post(p, (c) => viaSidecar(c));
  }
  for (const p of ["/api/fetch/events", "/api/fetch/result", "/api/sim/events", "/api/sim/result"]) {
    app.get(p, (c) => viaSidecar(c));
  }

  // ── 계산 ──
  app.post("/api/sim", (c) => guard(c, async () => {
    const guardOn = simBusyGuardEnabled(conf.root);
    if (guardOn && !rateOk("sim", RATE.sim)) {
      await drain(c);
      return jsonErr(c, "서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시 시도하세요.", 429);
    }
    const b = await readBody(c);
    // 파이썬 순서 그대로: 검증·정제(buildJobs)를 통과한 뒤에야 sim_req/sim_deck을 세고,
    // sim_err는 코어 실행 실패만 센다(검증 400은 집계에 없다)
    const jobs = buildJobs(b);
    bump("sim_req");
    bump("sim_deck", jobs.length);
    try {
      if (!coreReady()) {
        console.log(`[sim] 계산 코어를 쓸 수 없다: ${getLoadError()}`);
        throw new OpsError("계산 코어를 쓸 수 없습니다");
      }
      const out = await runSimJobs(jobs, guardOn, () => bump("busy_429"));
      bump("sim_sec", (out.results as Array<{ sec?: number }>).reduce((s, r) => s + (r.sec ?? 0), 0));
      return jsonOk(c, out);
    } catch (e) {
      if (!(e instanceof BusyError)) bump("sim_err");
      throw e;
    }
  }));

  // ── 공유 ──
  app.get("/api/share", (c) => guard(c, async () => {
    const code = shareCode(new URL(c.req.url).searchParams.get("c") ?? "");
    const got = shareOk(conf.root) ? shareGet(code) : null;
    if (got === null) {
      bump("share_miss");
      return jsonErr(c, "이 링크는 만료됐거나 지워졌습니다 (공유는 24시간 유지됩니다).", 404);
    }
    bump("share_get");
    return jsonOk(c, got);
  }));

  app.post("/api/share", (c) => guard(c, async () => {
    if (!fromOurPage(c)) {
      bump("bot_403");
      await drain(c);
      return jsonErr(c, "이 사이트의 페이지에서만 공유할 수 있습니다.", 403);
    }
    if (!rateOk("share", RATE.share)) {
      await drain(c);
      return jsonErr(c, "공유 요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429);
    }
    if (!shareOk(conf.root)) {
      await drain(c);
      return jsonErr(c, "이 서버는 공유 저장소를 열 수 없습니다.", 503);
    }
    const n = Number(c.req.header("content-length") ?? 0);
    if (n > SHARE_MAX_BODY) {
      await drain(c);
      return jsonErr(c, `공유 내용이 너무 큽니다 (${n.toLocaleString("en-US")}B > ` +
        `${SHARE_MAX_BODY.toLocaleString("en-US")}B) — 편성과 딜 수치만 담깁니다.`, 413);
    }
    const clean = shareClean(await readBody(c));
    const { code, expires } = sharePut(clean);
    bump("share_put");
    return jsonOk(c, { code, expires: Math.trunc(expires), ttl: Math.trunc(SHARE_TTL) });
  }));

  app.post("/api/unshare", (c) => guard(c, async () => {
    if (!rateOk("share", RATE.share)) {
      await drain(c);
      return jsonErr(c, "요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.", 429);
    }
    if (!shareOk(conf.root)) {
      await drain(c);
      return jsonErr(c, "이 서버는 공유 저장소를 열 수 없습니다.", 503);
    }
    const gone = shareDel(shareCode((await readBody(c)).code));
    if (gone) bump("share_del");
    return jsonOk(c, { deleted: gone });
  }));

  // ── 피드백 보드 ──
  app.get("/api/board", (c) => guard(c, async () => {
    const q = new URL(c.req.url).searchParams;
    let before: number | null = null;
    let n = 30;
    try {
      const bRaw = q.get("before") ?? "";
      before = bRaw ? pyFloatStrict(bRaw) : null;
      n = pyIntStrict(q.get("n") ?? "30");
    } catch {
      before = null;
      n = 30;
    }
    if (!shareOk(conf.root)) throw new OpsError("이 서버는 공유 저장소를 열 수 없습니다.");
    return jsonOk(c, { items: boardList(false, before, n) });
  }));

  app.post("/api/board", (c) => guard(c, async () => {
    if (!rateOk("board", RATE.board)) {
      await drain(c);
      return jsonErr(c, "피드백이 너무 잦습니다 — 잠시 후 다시 남겨 주세요.", 429);
    }
    const b = await readBody(c);
    if (b.web) return jsonOk(c, { ok: true }); // 허니팟 — 봇에게는 성공한 척
    const kind = "피드백";
    const bodyText = String(b.body ?? "").trim();
    if (!(2 <= bodyText.length && bodyText.length <= 1000)) {
      return jsonErr(c, "내용은 2~1000자로 적어 주세요", 400);
    }
    const nick = (String(b.nick ?? "").trim() || "익명").slice(0, 12);
    const priv = Boolean(b.private);
    const pw = String(b.pw ?? "");
    if (priv && !(4 <= pw.length && pw.length <= 32)) {
      return jsonErr(c, "비공개 글은 4~32자 비밀번호가 필요합니다", 400);
    }
    if (!shareOk(conf.root)) throw new OpsError("이 서버는 공유 저장소를 열 수 없습니다.");
    return jsonOk(c, { ok: true, id: boardAdd(kind, nick, bodyText, priv, pw) });
  }));

  app.post("/api/board/view", (c) => guard(c, async () => {
    if (!rateOk("boardpw", RATE.boardpw)) {
      await drain(c);
      return jsonErr(c, "시도가 너무 잦습니다 — 잠시 후 다시 해 주세요.", 429);
    }
    const b = await readBody(c);
    if (!shareOk(conf.root)) throw new OpsError("이 서버는 공유 저장소를 열 수 없습니다.");
    const got = boardView(String(b.id ?? ""), String(b.pw ?? ""));
    if (got === null) return jsonErr(c, "비밀번호가 맞지 않습니다", 403);
    return jsonOk(c, got);
  }));

  app.post("/api/board/admin", (c) => guard(c, async () => {
    if (!(await clientIp(c)).startsWith("100.")) {
      await drain(c);
      return jsonErr(c, "not found", 404);
    }
    const b = await readBody(c);
    const op = String(b.op ?? "");
    if (!shareOk(conf.root)) throw new OpsError("이 서버는 공유 저장소를 열 수 없습니다.");
    if (op === "list") return jsonOk(c, { items: boardList(true, null, 200) });
    if (op === "refs") {
      const days = pyInt(b.days ?? 30);
      return jsonOk(c, refStats(conf.root, days === null ? 30 : days));
    }
    if (op === "settings") {
      return jsonOk(c, { sim_busy_guard: simBusyGuardEnabled(conf.root), slots: SIM_SLOTS, queue_max: SIM_QUEUE_MAX });
    }
    if (op === "sim-guard") {
      const enabled = b.enabled === true;
      return jsonOk(c, { ok: true, sim_busy_guard: setSimBusyGuard(conf.root, enabled) });
    }
    const ok = boardAdmin(op, String(b.id ?? ""), String(b.body ?? "").trim());
    return ok ? jsonOk(c, { ok }) : jsonErr(c, "실패 — id·op 확인", 400);
  }));

  // ── 운영 지표 ──
  app.get("/api/stats", (c) => guard(c, async () => {
    if (c.req.header("tailscale-funnel-request") !== undefined) {
      return jsonErr(c, "없는 라우트", 404);
    }
    const st: Record<string, unknown> = { ...stats };
    st.sim_sec = Math.round((stats.sim_sec ?? 0) * 10) / 10;
    try {
      const r = refStats(conf.root, 30) as { days: { day: string; n: number }[]; hosts: { host: string; n: number }[] };
      st["유입_일자"] = Object.fromEntries(r.days.map((x) => [x.day, x.n]));
      st["유입_도메인"] = Object.fromEntries(r.hosts.map((x) => [x.host, x.n]));
    } catch {
      /* 유입 표가 없으면 생략 */
    }
    const up = Date.now() / 1000 - startedAt;
    return jsonOk(c, {
      uptime: `${Math.trunc(up / 86400)}일 ${Math.trunc((up % 86400) / 3600)}시간 ${Math.trunc((up % 3600) / 60)}분`,
      uptime_sec: Math.round(up),
      ...st,
      queue: { sim: 0, fetch: 0 }, // 파이썬도 작업 표 기준이라 sim은 늘 0이다(동기 전환 뒤) — fetch는 이식 때
      load: process.platform === "win32" ? null : os.loadavg(),
      pool_jobs: poolJobs,
      fetch_on: false, // TODO: 조회 프록시 이식 때
      sim_busy_guard: simBusyGuardEnabled(conf.root),
    });
  }));

  // ── 관리자 (테일넷 발신에만 — 밖에서는 존재를 숨긴다) ──
  app.get("/admin.js", (c) => guard(c, async () => {
    if (!(await clientIp(c)).startsWith("100.")) return jsonErr(c, "not found", 404);
    commonHeaders(c);
    const js = Buffer.from(ADMIN_JS, "utf-8");
    return c.body(js as unknown as ArrayBuffer, 200, {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store, must-revalidate",
      "Content-Length": String(js.byteLength),
    });
  }));

  app.get("/admin", (c) => guard(c, async () => {
    if (!(await clientIp(c)).startsWith("100.")) return jsonErr(c, "not found", 404);
    commonHeaders(c);
    const html = Buffer.from(ADMIN_HTML, "utf-8");
    return c.body(html as unknown as ArrayBuffer, 200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store, must-revalidate",
      "Content-Length": String(html.byteLength),
    });
  }));

  // ── 기능 플래그 (cp·판독·조회는 사이드카의 것을 그대로 보고한다) ──
  app.get("/api/health", async (c) => {
    const sc = conf.sidecar ? await sidecarHealth(conf.sidecar) : null;
    return jsonOk(c, {
      sim: true,
      cp: sc?.cp === true,
      ocr: sc?.ocr === true,
      power_ocr: sc?.power_ocr === true,
      share: shareOk(conf.root),
      lab: isLocalOnly(c),
      union: isLocalOnly(c) || process.env.NIKKE_UNION === "1",
      share_ttl: Math.trunc(SHARE_TTL),
      fetch: sc?.fetch === true,
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
      bumpRef(conf.root, c.req.header("referer"));
      pathname = "/index.html";
    } else if (!pathname.startsWith("/api/")) {
      const last = (pathname || "/").split("/").pop() ?? "";
      if (!last.includes(".")) {
        bump("page");
        bumpRef(conf.root, c.req.header("referer"));
      }
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

// 파이썬 float()/int()가 던지는 자리(보드 페이징) — 실패를 예외로 알려야 «둘 다 기본값» 규칙이 된다
function pyFloatStrict(s: string): number {
  const t = s.trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) throw new Error("float 아님");
  return Number(t);
}

function pyIntStrict(s: string): number {
  const t = s.trim();
  if (!/^[+-]?\d+$/.test(t)) throw new Error("int 아님");
  return Number(t);
}

export { stats };
