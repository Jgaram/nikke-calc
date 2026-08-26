// POST /api/sim — 계약은 context/SERVER-CONTRACT.md §4. 오류 문장은 파이썬 서버와 한 글자도
// 다르면 안 된다(회귀 스크립트가 대조한다).
import { cleanConfig, cleanControl, cleanCubes, cleanEnemy, noBurstNames, num } from "./clean.js";
import { InputError, runRequestBatch, type Job } from "./simcore.js";

export const MAX_DECKS = 12;
export const MAX_DURATION = 600.0;
export const LV_MAX = 1400;
export const SIM_SLOTS = 1;
export const SIM_QUEUE_MAX = 12;
export const SIM_WAIT_MAX = 30.0;

/** 대기열 거절 — 서버가 429로 답한다 (파이썬 BusyError에 해당). */
export class BusyError extends Error {}

// ── 입장 제한 (`_run_sim_now`) — 슬롯 1, 대기 포함 12, 30초 대기 상한 ────────
let running = 0;
let waiting = 0;
const wakers: Array<() => void> = [];

function acquire(timeoutMs: number): Promise<boolean> {
  if (running < SIM_SLOTS) {
    running += 1;
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    let done = false;
    const waker = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      running += 1;
      resolve(true);
    };
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      const i = wakers.indexOf(waker);
      if (i >= 0) wakers.splice(i, 1);
      resolve(false);
    }, timeoutMs);
    wakers.push(waker);
  });
}

function release(): void {
  running -= 1;
  const next = wakers.shift();
  if (next) next();
}

export function simBusy(): number {
  return running + waiting;
}

/** 입장 제한을 지나 계산을 돌린다. 거절은 BusyError(→429), 입력 오류는 InputError(→400). */
async function runSimNow(jobs: Job[], rejectIfBusy: boolean, onBusy429: () => void): Promise<unknown[]> {
  if (rejectIfBusy && running > 0) {
    onBusy429();
    throw new BusyError("서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시 시도하세요.");
  }
  const busy = running + waiting;
  if (busy >= SIM_QUEUE_MAX) {
    onBusy429();
    throw new BusyError(`계산 대기열이 가득 찼습니다 (진행·대기 ${busy}건). 잠시 후 다시 눌러 주세요.`);
  }
  waiting += 1;
  let got: boolean;
  try {
    got = await acquire(SIM_WAIT_MAX * 1000);
  } finally {
    waiting -= 1;
  }
  if (!got) {
    onBusy429();
    throw new BusyError("계산 대기가 너무 길어졌습니다 — 잠시 후 다시 시도하세요.");
  }
  try {
    return await runRequestBatch(jobs);
  } finally {
    release();
  }
}

const isDict = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** 요청 본문 → job 목록 (검증·정제 — 계약 §4). 오류는 InputError(문장 그대로 400). */
export function buildJobs(b: Record<string, unknown>): Job[] {
  const decks = (b.decks ?? []) as unknown;
  if (!Array.isArray(decks) || decks.length === 0) throw new InputError("decks가 비었다");
  if (decks.length > MAX_DECKS) throw new InputError(`덱이 너무 많다 (${decks.length} > ${MAX_DECKS})`);
  for (const d of decks) {
    if (!Array.isArray(d) || d.length < 1 || d.length > 5 || !d.every(Boolean)) {
      throw new InputError("각 덱은 1~5명의 캐릭터 이름 배열이어야 한다 (빈 슬롯이 있으면 계산하지 않는다)");
    }
  }
  const durationRaw = b.duration || 180.0; // 파이썬 `float(b.get("duration") or 180.0)`
  const duration = typeof durationRaw === "number" ? durationRaw : Number(durationRaw);
  if (!(1.0 <= duration && duration <= MAX_DURATION)) {
    throw new InputError(`duration이 범위를 벗어났다 (1~${MAX_DURATION.toFixed(0)})`);
  }
  const code = (b.code || null) as string | null;
  const profile = b.profile ?? null; // 프로필 검증은 코어 조립이 한다 — 첫 덱 오류가 곧 프로필 오류다

  const enemy = cleanEnemy(b.enemy);
  const configOver = cleanConfig(b.config);
  const controls = Array.isArray(b.controls) ? b.controls : [];
  const cubes = Array.isArray(b.cubes) ? b.cubes : [];
  const levels = (Array.isArray(b.levels) ? b.levels : []).map((v) =>
    v !== null && v !== undefined ? num(v, 1, LV_MAX, "int") : null
  );
  const codes = (Array.isArray(b.codes) ? b.codes : []).map((c) => (c ? String(c).slice(0, 8) : null));
  const enemies = Array.isArray(b.enemies) ? b.enemies.map(cleanEnemy) : [];
  const configs = Array.isArray(b.configs) ? b.configs.map(cleanConfig) : [];

  const jobs: Job[] = [];
  decks.forEach((d: unknown[], i: number) => {
    const rawCtrl = i < controls.length ? controls[i] : null;
    const noBurst = noBurstNames(rawCtrl);
    const over: Record<string, Record<string, unknown>> = cleanControl(rawCtrl) ?? {};
    for (const [nm, cb] of Object.entries(cleanCubes(i < cubes.length ? cubes[i] : null) ?? {})) {
      // 파이썬 `over.setdefault(nm, {}).update(cb)` — 있던 키 뒤에 cube가 붙고, 겹치면 cube가 덮는다
      over[nm] = { ...(over[nm] ?? {}), ...cb };
    }
    const dLevel = i < levels.length ? levels[i] : null;
    if (dLevel) {
      for (const nm of d) {
        if (typeof nm === "string" && nm) {
          over[nm] = { ...(over[nm] ?? {}), level: dLevel };
        }
      }
    }
    const dCode = i < codes.length ? codes[i] : null;
    const dEnemy = i < enemies.length && enemies[i] ? enemies[i] : enemy;
    const dConfig = i < configs.length && Object.keys(configs[i] ?? {}).length ? configs[i] : configOver;
    jobs.push({
      names: d.map((n) => String(n)),
      code: dCode || code,
      duration,
      profile,
      enemy: dEnemy,
      config_over: noBurst.length ? { ...dConfig, no_burst_chars: noBurst } : dConfig,
      control: Object.keys(over).length ? over : null,
    });
  });
  return jobs;
}

export async function handleSim(
  b: Record<string, unknown>,
  rejectIfBusy: boolean,
  onBusy429: () => void
): Promise<{ results: unknown[] }> {
  const jobs = buildJobs(b);
  const t0 = performance.now();
  const results = await runSimNow(jobs, rejectIfBusy, onBusy429);
  // 파이썬 run_jobs_native와 같은 자리 — 배치 시간을 덱 수로 나눠 각 결과의 **마지막 키**로 붙인다
  const sec = (performance.now() - t0) / 1000 / Math.max(1, jobs.length);
  for (const r of results) {
    (r as Record<string, unknown>).sec = sec;
  }
  return { results };
}
