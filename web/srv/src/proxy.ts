// 파이썬 사이드카 프록시 — 아직 TS로 안 옮긴 라우트(/api/cp·/api/atk·/api/squad/*·/api/fetch와
// job events/result)는 **기존 파이썬 서버를 내부 포트로 띄워 그대로 통과**시킨다. 응답(문장·상태·헤더)이
// 코드 복제 없이 파이썬과 동일해지고, 라우트를 하나씩 TS로 옮길 때마다 프록시 목록에서 빼면 된다.
//
// 주의(계약 §9): 게이트 판정용 호스트는 X-Forwarded-Host로 넘긴다(파이썬 from_our_page가 Origin 접미사를
// 이것과 비교한다). SSE는 본문을 스트림으로 통과시킨다. 사이드카가 죽어 있으면 502 — 파이썬 단일 서버에는
// 없던 실패 모드다(로그로 드러낸다).
import type { Context } from "hono";

const HOP = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "host"]);

export async function proxyTo(c: Context, sidecar: string): Promise<Response> {
  const url = new URL(c.req.url);
  const target = sidecar + url.pathname + url.search;
  const method = c.req.method;

  const headers = new Headers();
  for (const [k, v] of Object.entries(c.req.header())) {
    if (!HOP.has(k.toLowerCase())) headers.set(k, v as string);
  }
  const origHost = c.req.header("host");
  if (origHost && !headers.has("x-forwarded-host")) headers.set("x-forwarded-host", origHost);

  const hasBody = method !== "GET" && method !== "HEAD" && c.req.raw.body !== null;
  let res: Response;
  try {
    res = await fetch(target, {
      method,
      headers,
      body: hasBody ? c.req.raw.body : undefined,
      redirect: "manual",
      // @ts-expect-error — Node(undici)는 스트림 본문에 duplex가 필요하다; Bun은 무시한다
      duplex: hasBody ? "half" : undefined,
    });
  } catch (e) {
    console.error(`[proxy] 사이드카 응답 없음 (${url.pathname}): ${e instanceof Error ? e.message : e}`);
    return c.body(JSON.stringify({ error: "서버 오류입니다 — 잠시 후 다시 시도하세요." }), 502, {
      "Content-Type": "application/json; charset=utf-8",
    });
  }

  const out = new Headers();
  res.headers.forEach((v, k) => {
    if (!HOP.has(k) && k !== "content-length") out.set(k, v);
  });
  return new Response(res.body, { status: res.status, headers: out });
}

/** 사이드카의 /api/health에서 아직 이식 안 된 기능 플래그를 얻는다 (1초 안에 못 받으면 전부 꺼짐). */
export async function sidecarHealth(sidecar: string): Promise<Record<string, unknown> | null> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 1000);
    const res = await fetch(sidecar + "/api/health", { signal: ctl.signal });
    clearTimeout(t);
    if (!res.ok) return null;
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}
