// 피드백 보드 — 목록(비공개는 껍데기)·등록(허니팟·중복 갈음)·비밀번호 열람·관리 op (계약 §5).
import { pbkdf2Sync, randomBytes, timingSafeEqual } from "node:crypto";

import { boardInit, db } from "./db.js";

const KEYS = ["id", "ts", "kind", "nick", "body", "reply", "reply_ts", "hidden", "private"] as const;

export function boardList(admin: boolean, before: number | null, limit: number): Record<string, unknown>[] {
  boardInit();
  const cond: string[] = [];
  const args: unknown[] = [];
  if (!admin) cond.push("hidden = 0");
  if (before !== null) {
    cond.push("ts < ?");
    args.push(before);
  }
  let q = "SELECT id, ts, kind, nick, body, reply, reply_ts, hidden, private FROM board ";
  if (cond.length) q += "WHERE " + cond.join(" AND ") + " ";
  q += "ORDER BY ts DESC LIMIT ?";
  const lim = Math.max(1, Math.min(Math.trunc(limit), 200));
  const rows = db().all(q, [...args, lim]);
  const out = rows.map((r) => {
    const it: Record<string, unknown> = {};
    for (const k of KEYS) it[k] = r[k] ?? null;
    return it;
  });
  if (!admin) {
    for (const it of out) {
      if (it.private) {
        // 비공개 글은 본문·답변을 빼고 껍데기만 — 답변이 달렸다는 사실만 알린다
        it.has_reply = Boolean(it.reply);
        it.body = "";
        it.reply = null;
      }
    }
  }
  return out;
}

function boardHash(pw: string, salt?: Buffer): string {
  const s = salt ?? randomBytes(16);
  const dk = pbkdf2Sync(Buffer.from(pw, "utf-8"), s, 200_000, 32, "sha256");
  return s.toString("hex") + "$" + dk.toString("hex");
}

export function boardView(bid: string, pw: string): Record<string, unknown> | null {
  boardInit();
  const r = db().get(
    "SELECT id, ts, kind, nick, body, reply, reply_ts, pw FROM board WHERE id = ? AND private = 1 AND hidden = 0",
    [bid]
  );
  if (!r || typeof r.pw !== "string" || !r.pw.includes("$")) return null;
  const [saltHex, want] = (r.pw as string).split("$", 2);
  const got = boardHash(pw, Buffer.from(saltHex, "hex")).split("$", 2)[1];
  const a = Buffer.from(want, "utf-8");
  const b = Buffer.from(got, "utf-8");
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  const it: Record<string, unknown> = {};
  for (const k of ["id", "ts", "kind", "nick", "body", "reply", "reply_ts"]) it[k] = r[k] ?? null;
  it.private = 1;
  return it;
}

export function boardAdd(kind: string, nick: string, body: string, priv: boolean, pw: string): string {
  boardInit();
  const bid = randomBytes(4).toString("hex"); // uuid4().hex[:8]과 같은 분포
  // 같은 본문 재전송(더블클릭·스팸)은 조용히 기존 글로 갈음 — 공개 글끼리만 대조한다
  if (!priv) {
    const dup = db().get("SELECT id FROM board WHERE body = ? AND private = 0 AND ts > ?",
      [body, Date.now() / 1000 - 3600]);
    if (dup) return String(dup.id);
  }
  const pwHash = priv ? boardHash(pw) : null;
  db().run("INSERT INTO board (id, ts, kind, nick, body, private, pw) VALUES (?,?,?,?,?,?,?)",
    [bid, Date.now() / 1000, kind, nick, body, priv ? 1 : 0, pwHash]);
  return bid;
}

export function boardAdmin(op: string, bid: string, body: string): boolean {
  boardInit();
  if (op === "reply") {
    const r = db().get("SELECT 1 AS x FROM board WHERE id = ?", [bid]);
    db().run("UPDATE board SET reply = ?, reply_ts = ? WHERE id = ?",
      [body || null, body ? Date.now() / 1000 : null, bid]);
    return r !== null;
  }
  if (op === "hide" || op === "unhide") {
    const r = db().get("SELECT 1 AS x FROM board WHERE id = ?", [bid]);
    db().run("UPDATE board SET hidden = ? WHERE id = ?", [op === "hide" ? 1 : 0, bid]);
    return r !== null;
  }
  if (op === "del") {
    const r = db().get("SELECT 1 AS x FROM board WHERE id = ?", [bid]);
    db().run("DELETE FROM board WHERE id = ?", [bid]);
    return r !== null;
  }
  return false;
}
