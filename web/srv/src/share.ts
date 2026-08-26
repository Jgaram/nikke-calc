// 편성 공유 — share_clean 화이트리스트·저장·조회·삭제 (계약 §5·§6, 파이썬 server.py와 같은 의미).
import { randomBytes } from "node:crypto";
import { deflateSync, inflateSync } from "node:zlib";

import { db, shareOk } from "./db.js";
import { InputError } from "./simcore.js";
import { MAX_DECKS, MAX_DURATION } from "./sim.js";

export const SHARE_TTL = 86400.0;
export const SHARE_MAX_BODY = 32 * 1024;
export const SHARE_MAX_CHARS = 8;

const isDict = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** `_share_num` — 범위를 벗어나면 자르지 않고 거절한다 (400). */
function shareNum(v: unknown, lo: number, hi: number): number {
  if (typeof v === "boolean" || typeof v !== "number") throw new InputError("숫자가 아닌 값이 있습니다");
  if (!(lo <= v && v <= hi)) throw new InputError("값이 범위를 벗어났습니다");
  return v;
}

/** `share_clean` — 클라이언트가 무엇을 보내든 여기 적힌 키만 저장된다. */
export function shareClean(obj: unknown): Record<string, unknown> {
  if (!isDict(obj)) throw new InputError("공유할 내용이 아닙니다");
  const decksIn = obj.decks;
  if (!Array.isArray(decksIn) || decksIn.length === 0) throw new InputError("공유할 덱이 없습니다");
  if (decksIn.length > MAX_DECKS) throw new InputError(`덱이 너무 많습니다 (최대 ${MAX_DECKS})`);

  const decks: Record<string, unknown>[] = [];
  for (const d of decksIn) {
    if (!isDict(d)) throw new InputError("덱 모양이 아닙니다");
    const namesIn = d.names;
    if (!Array.isArray(namesIn) || namesIn.length === 0) throw new InputError("덱에 니케가 없습니다");
    const names: (string | null)[] = [];
    for (const n of namesIn.slice(0, SHARE_MAX_CHARS)) {
      if (n === null || n === undefined) names.push(null);
      else if (typeof n === "string" && n.length > 0 && n.length <= 40) names.push(n);
      else throw new InputError("니케 이름이 아닙니다");
    }
    const charsIn = (d.chars ?? {}) as unknown;
    if (!isDict(charsIn) || Object.keys(charsIn).length > SHARE_MAX_CHARS) {
      throw new InputError("니케별 딜 모양이 아닙니다");
    }
    const chars: Record<string, number> = {};
    for (const [k, v] of Object.entries(charsIn)) {
      if (typeof k !== "string" || k.length === 0 || k.length > 40) throw new InputError("니케 이름이 아닙니다");
      chars[k] = shareNum(v, 0, 1e18);
    }
    const one: Record<string, unknown> = {
      names,
      total: shareNum(d.total, 0, 1e18),
      chars,
    };
    const w = d.weak;
    if (typeof w === "string" && w.length > 0 && w.length <= 8) one.weak = w;
    decks.push(one);
  }

  const code = obj.code;
  if (code !== null && code !== undefined && !(typeof code === "string" && code.length <= 8)) {
    throw new InputError("속성 코드가 아닙니다");
  }
  const modeRaw = obj.mode;
  const mode = modeRaw === "solo" || modeRaw === "union" ? modeRaw : null;
  const out: Record<string, unknown> = {
    v: 1,
    code: code || null,
    duration: shareNum(obj.duration, 1, MAX_DURATION),
    total: shareNum(obj.total, 0, 1e18),
    decks,
  };
  if (mode) out.mode = mode;
  return out;
}

/** `share_put` — (코드, 만료시각). 실패는 RuntimeError(→502)에 해당하는 일반 Error. */
export function sharePut(clean: Record<string, unknown>): { code: string; expires: number } {
  const now = Date.now() / 1000;
  const body = deflateSync(Buffer.from(JSON.stringify(clean), "utf-8"), { level: 9 });
  db().run("DELETE FROM share WHERE created < ?", [now - SHARE_TTL]);
  for (let i = 0; i < 8; i++) {
    const code = randomBytes(6).toString("base64url"); // secrets.token_urlsafe(6)와 같은 자모·길이
    try {
      db().run("INSERT INTO share (code, body, created) VALUES (?, ?, ?)", [code, body, now]);
      return { code, expires: now + SHARE_TTL };
    } catch {
      continue; // 같은 코드가 이미 있다 — 다시 뽑는다
    }
  }
  throw new Error("공유 코드를 만들지 못했습니다 — 잠시 후 다시 시도하세요.");
}

export function shareGet(code: string): unknown | null {
  const row = db().get("SELECT body, created FROM share WHERE code = ?", [code]);
  if (!row) return null;
  if (Date.now() / 1000 - Number(row.created) > SHARE_TTL) return null; // 만료분은 다음 쓰기가 치운다
  const buf = row.body as Uint8Array;
  return JSON.parse(inflateSync(Buffer.from(buf)).toString("utf-8"));
}

export function shareDel(code: string): boolean {
  const before = db().get("SELECT 1 AS x FROM share WHERE code = ?", [code]);
  db().run("DELETE FROM share WHERE code = ?", [code]);
  return before !== null;
}

const SHARE_CODE = /^[A-Za-z0-9_-]{4,16}$/;

export function shareCode(raw: unknown): string {
  const code = String(raw ?? "");
  if (!SHARE_CODE.test(code)) throw new InputError("공유 코드가 아닙니다");
  return code;
}

export { shareOk };
