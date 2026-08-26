// 정적 서빙 — web/dist. 캐시 규칙·i18n gzip 선응답은 계약 §0·§3 그대로.
// 파이썬 SimpleHTTPRequestHandler와 다른 점(의도): 디렉터리 목록을 내지 않는다(404).
import { promises as fs } from "node:fs";
import path from "node:path";

const LONG_CACHE = [".webp", ".png", ".jpg", ".jpeg", ".svg", ".woff2", ".ico"];
// 파이썬 3.12 mimetypes와 같은 값 (정적에는 charset을 붙이지 않는다 — 파이썬도 안 붙인다)
const MIME: Record<string, string> = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".webp": "image/webp",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/vnd.microsoft.icon",
  ".woff2": "font/woff2",
  ".txt": "text/plain",
  ".zip": "application/zip",
  ".gz": "application/gzip",
  ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm",
};

export function cacheControlFor(rawPath: string): string {
  const [p, q] = rawPath.split("?", 2);
  const low = p.toLowerCase();
  const tagged = low.startsWith("/i18n/") && low.endsWith(".js") && (q ?? "").startsWith("v=");
  if (LONG_CACHE.some((ext) => low.endsWith(ext)) || tagged) {
    return "public, max-age=604800, immutable";
  }
  return "no-store";
}

export type StaticHit =
  | { kind: "file"; body: Buffer; type: string }
  | { kind: "gzip_i18n"; body: Buffer }
  | { kind: "miss" };

/** dist 안의 파일을 찾는다. 경로 탈출은 miss. `/` → index.html. */
export async function lookup(dist: string, pathname: string, acceptGzip: boolean): Promise<StaticHit> {
  let p = decodeURIComponent(pathname);
  // i18n 사전은 빌드가 옆에 둔 .gz(≈80KB)를 받는 쪽이 받아 준다면 그걸 준다
  const route = p.replace(/\/+$/, "") || "/";
  if (acceptGzip && route.startsWith("/i18n/") && route.endsWith(".js")) {
    const name = route.slice("/i18n/".length);
    if (["en.js", "ja.js", "zh.js"].includes(name)) {
      try {
        const body = await fs.readFile(path.join(dist, "i18n", name + ".gz"));
        return { kind: "gzip_i18n", body };
      } catch {
        /* .gz가 없으면 일반 경로로 */
      }
    }
  }
  if (p === "/" || p === "") p = "/index.html";
  const full = path.normalize(path.join(dist, p));
  if (!full.startsWith(path.normalize(dist + path.sep))) return { kind: "miss" };
  try {
    const st = await fs.stat(full);
    if (!st.isFile()) return { kind: "miss" };
    const body = await fs.readFile(full);
    const type = MIME[path.extname(full).toLowerCase()] ?? "application/octet-stream";
    return { kind: "file", body, type };
  } catch {
    return { kind: "miss" };
  }
}
