// 공유·피드백·유입 저장소 — 파이썬 서버와 **같은 파일**(share.db)·같은 스키마·같은 의미.
// 런타임 어댑터: Bun이면 bun:sqlite, Node면 node:sqlite(22는 실험 플래그라 없을 수 있다).
// 어느 쪽도 못 열면 파이썬과 같은 강등 — share_ok()가 false고 공유 라우트만 503이다.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require_ = createRequire(import.meta.url);

export type Row = Record<string, unknown>;

type Driver = {
  run(sql: string, params?: unknown[]): void;
  all(sql: string, params?: unknown[]): Row[];
  get(sql: string, params?: unknown[]): Row | null;
};

type Stmt = { run(...p: unknown[]): unknown; all(...p: unknown[]): Row[]; get(...p: unknown[]): Row | undefined };
type SqliteDb = { prepare(sql: string): Stmt };

declare const Bun: unknown;

function openDriver(file: string): Driver {
  let db: SqliteDb;
  if (typeof Bun !== "undefined") {
    const mod = require_("bun:sqlite") as { Database: new (f: string, o?: { create?: boolean }) => SqliteDb };
    db = new mod.Database(file, { create: true });
  } else {
    // Node 폴백 — node:sqlite (22는 실험 단계라 없으면 여기서 던지고, 공유만 꺼진다)
    const mod = require_("node:sqlite") as { DatabaseSync: new (f: string) => SqliteDb };
    db = new mod.DatabaseSync(file);
  }
  return {
    run: (sql, params = []) => { db.prepare(sql).run(...params); },
    all: (sql, params = []) => db.prepare(sql).all(...params),
    get: (sql, params = []) => db.prepare(sql).get(...params) ?? null,
  };
}

let driver: Driver | null = null;
let dead = false; // 한 번 열기에 실패하면 매 요청마다 다시 시도하지 않는다 (파이썬 _share_dead)

export function stateDir(root: string): string {
  const env = (process.env.STATE_DIRECTORY ?? "").split(path.delimiter)[0];
  return env || path.join(root, "web", ".state");
}

export function shareOk(root: string): boolean {
  if (dead) return false;
  if (driver) return true;
  try {
    const dir = stateDir(root);
    mkdirSync(dir, { recursive: true });
    const d = openDriver(path.join(dir, "share.db"));
    d.run("PRAGMA journal_mode=WAL");
    d.run("CREATE TABLE IF NOT EXISTS share (" +
      "code TEXT PRIMARY KEY, body BLOB NOT NULL, created REAL NOT NULL)");
    driver = d;
    return true;
  } catch (e) {
    dead = true;
    console.error(`share  저장소를 열 수 없어 공유를 끕니다: ${e instanceof Error ? e.message : e}`);
    return false;
  }
}

export function db(): Driver {
  if (!driver) throw new Error("share 저장소가 준비되지 않았다 — shareOk()를 먼저 부른다");
  return driver;
}

let boardReady = false;

export function boardInit(): void {
  if (boardReady) return;
  db().run("CREATE TABLE IF NOT EXISTS board (" +
    "id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, " +
    "nick TEXT NOT NULL, body TEXT NOT NULL, " +
    "reply TEXT, reply_ts REAL, hidden INTEGER NOT NULL DEFAULT 0, " +
    "private INTEGER NOT NULL DEFAULT 0, pw TEXT)");
  for (const col of ["private INTEGER NOT NULL DEFAULT 0", "pw TEXT"]) {
    try {
      db().run(`ALTER TABLE board ADD COLUMN ${col}`);
    } catch {
      /* 이미 있음 */
    }
  }
  boardReady = true;
}

let refReady = false;

export function refInit(): void {
  if (refReady) return;
  db().run("CREATE TABLE IF NOT EXISTS ref (" +
    "day TEXT NOT NULL, host TEXT NOT NULL, url TEXT NOT NULL, " +
    "n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, url))");
  refReady = true;
}
