// /api/sim 입력 정제 — web/server.py의 _num/_clean_enemy/_clean_config/_clean_control/_clean_cubes/
// _no_burst_names와 **같은 결과**를 내야 한다(context/SERVER-CONTRACT.md §4). 조용히 자르거나 버린다.
// 파이썬 int()/float()의 문자열·불리언 수용까지 흉내 낸다(불리언은 파이썬에서 int의 부분형이다).

export function pyFloat(v: unknown): number | null {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const t = v.trim();
    if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) return Number(t);
    const low = t.toLowerCase();
    if (low === "inf" || low === "+inf" || low === "infinity") return Infinity;
    if (low === "-inf" || low === "-infinity") return -Infinity;
    return null; // 파이썬 float()이 ValueError를 내는 입력
  }
  return null; // None·dict·list → TypeError
}

export function pyInt(v: unknown): number | null {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return Math.trunc(v); // int(3.9) = 3 (0 쪽으로 절단)
  if (typeof v === "string") {
    const t = v.trim();
    if (/^[+-]?\d+$/.test(t)) return Number(t);
    return null;
  }
  return null;
}

/** `_num(v, lo, hi, cast)` — 범위로 자르고, 못 읽으면 null. */
export function num(v: unknown, lo: number, hi: number, cast: "int" | "float" = "float"): number | null {
  const x = cast === "int" ? pyInt(v) : pyFloat(v);
  if (x === null) return null;
  return Math.max(lo, Math.min(hi, x));
}

const ENEMY_NUM: Record<string, [number, number]> = { def: [0, 9_999_999], core_px: [0, 400] };
const CONFIG_NUM: Record<string, [number, number]> = {
  first_burst_time: [0.0, 60.0],
  burst_switch_delay: [0.0, 3.0],
  burst_reenter_delay: [0.0, 5.0],
  part_break_interval: [0.0, 180.0],
  max_burst_count: [1, 60],
  burst_regen_time: [0.5, 30.0],
};
const WEAPONS = ["AR", "SMG", "SG", "SR", "RL", "MG"] as const;

const isDict = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export function cleanEnemy(e: unknown): Record<string, unknown> | null {
  if (!isDict(e)) return null;
  const out: Record<string, unknown> = {};
  if (e.code) out.code = String(e.code).slice(0, 8);
  for (const [k, [lo, hi]] of Object.entries(ENEMY_NUM)) {
    if (k in e) {
      const v = num(e[k], lo, hi, "int");
      if (v !== null) out[k] = v;
    }
  }
  if ("has_parts" in e) out.has_parts = Boolean(e.has_parts);
  const w = e.optimal_range_weapons;
  if (Array.isArray(w)) out.optimal_range_weapons = WEAPONS.filter((x) => w.includes(x));
  const wc = e.weapon_coeff;
  if (isDict(wc)) {
    const cleaned: Record<string, number> = {};
    for (const k of WEAPONS) {
      const v = num(wc[k], 0.1, 1.5, "float");
      if (v !== null && v !== 1.0) cleaned[k] = v;
    }
    if (Object.keys(cleaned).length) out.weapon_coeff = cleaned;
  }
  return Object.keys(out).length ? out : null;
}

export function cleanConfig(c: unknown): Record<string, number> {
  if (!isDict(c)) return {};
  const out: Record<string, number> = {};
  for (const [k, [lo, hi]] of Object.entries(CONFIG_NUM)) {
    if (k in c) {
      const v = num(c[k], lo, hi, k === "max_burst_count" ? "int" : "float");
      if (v !== null) out[k] = v;
    }
  }
  return out;
}

const CTRL_KEYS = ["tap_fire", "reload", "cover", "hold"] as const;

export function cleanControl(c: unknown): Record<string, Record<string, unknown>> | null {
  if (!isDict(c)) return null;
  const out: Record<string, Record<string, unknown>> = {};
  for (const [name, v] of Object.entries(c)) {
    if (!isDict(v)) continue;
    const entry: Record<string, unknown> = {};
    const ctrl: Record<string, unknown> = {};
    for (const k of CTRL_KEYS) {
      if (isDict(v[k])) ctrl[k] = v[k];
    }
    if (Object.keys(ctrl).length) entry.control = ctrl;
    const bp = v.burst_pattern;
    if (typeof bp === "string" && bp && bp.length <= 40) {
      if (bp.startsWith("every:")) {
        // 파이썬 split(":", 1)[1] — 첫 콜론 뒤 **전부** (JS split의 limit과 의미가 다르다)
        const raw = bp.slice("every:".length);
        const n = /^[+-]?\d+$/.test(raw.trim()) ? Number(raw.trim()) : 0;
        if (1 <= n && n <= 99) entry.burst_pattern = `every:${n}`;
      } else {
        entry.burst_pattern = bp === "안 씀" ? null : bp;
      }
    } else if (
      Array.isArray(bp) && bp.length > 0 && bp.length <= 40 &&
      bp.every((x) => Number.isInteger(x) && typeof x === "number" && 1 <= x && x <= 999)
    ) {
      entry.burst_pattern = [...new Set(bp as number[])].sort((a, b) => a - b);
    }
    if (v.burst_first === true) entry.burst_first = true;
    if (Object.keys(entry).length) out[String(name)] = entry;
  }
  return Object.keys(out).length ? out : null;
}

export function cleanCubes(c: unknown): Record<string, { cube: { name: string; level: number } }> | null {
  if (!isDict(c)) return null;
  const out: Record<string, { cube: { name: string; level: number } }> = {};
  for (const [name, v] of Object.entries(c)) {
    if (!isDict(v)) continue;
    const nm = v.name;
    if (typeof nm !== "string" || !nm || nm.length > 40) continue;
    const lv = num(v.level, 0, 15, "int");
    if (lv === null) continue;
    out[String(name).slice(0, 40)] = { cube: { name: nm, level: lv } };
  }
  return Object.keys(out).length ? out : null;
}

export function noBurstNames(c: unknown): string[] {
  if (!isDict(c)) return [];
  return Object.entries(c)
    .filter(([, v]) => isDict(v) && v.no_burst === true)
    .map(([n]) => String(n).slice(0, 40))
    .slice(0, 5);
}
