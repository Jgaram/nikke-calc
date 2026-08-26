// 계산 코어 어댑터 — 파이썬 쪽 web/simcore.py와 같은 역할. 확장 모듈(nikke_node.node)은
// 저장소에 없다(gitignore) — 서버에서 빌드해 web/ 옆에 둔다. 못 열면 available()이 false고,
// 그 요청은 실패로 끝난다(다른 경로로 대신 답하지 않는다 — 운영 결정).
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require_ = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
// web/srv/src → web/ (빌드 산출물 dist/srv.mjs에서는 web/srv/dist → web/)
const WEB = path.resolve(HERE, "..", "..");

type Core = {
  loadData(dir: string, threads?: number): void;
  simulateRequestBatchJson(reqs: string[]): Promise<string[]>;
  poolThreads(): number;
  version(): string;
};

let core: Core | null = null;
let loadError: string | null = null;

export function available(dataDir: string, threads = 0): boolean {
  if (core) return true;
  if (loadError) return false;
  try {
    const mod = require_(path.join(WEB, "nikke_node.node")) as Core;
    mod.loadData(dataDir, threads);
    core = mod;
    return true;
  } catch (e) {
    loadError = e instanceof Error ? `${e.constructor.name}: ${e.message}` : String(e);
    return false;
  }
}

export function getLoadError(): string | null {
  return loadError;
}

/** 사용자 입력 오류 — 서버가 400으로 답한다 (파이썬 ValueError에 해당). */
export class InputError extends Error {}

export type Job = {
  names: string[];
  code: string | null;
  duration: number;
  profile: unknown | null;
  enemy: unknown | null;
  config_over: unknown | null;
  control: unknown | null;
};

/** 덱 job 여러 개를 코어가 한 번에 조립·계산·요약 — 입력 순서. 스펙 오류는 InputError로. */
export async function runRequestBatch(jobs: Job[]): Promise<unknown[]> {
  if (!core) throw new Error("계산 코어가 준비되지 않았다 — available(dataDir)를 먼저 부른다");
  const reqs = jobs.map((j) => JSON.stringify(j));
  let outs: string[];
  try {
    outs = await core.simulateRequestBatchJson(reqs);
  } catch (e) {
    let msg = e instanceof Error ? e.message : String(e);
    // 코어는 «[i] 문장»으로 어느 덱인지 붙여 준다 — 사용자에게는 문장만 (web/simcore.py와 같은 규약)
    if (/^\[\d+\] /.test(msg)) {
      throw new InputError(msg.replace(/^\[\d+\] /, ""));
    }
    throw e instanceof Error ? e : new Error(msg);
  }
  return outs.map((o) => JSON.parse(o));
}
