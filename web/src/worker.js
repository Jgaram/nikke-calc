// 계산 워커 — Pyodide로 계산기를 그대로 돌린다.
// 메인 스레드에서 돌리면 덱당 6~10초 동안 UI가 얼어붙는다 (webapp-roadmap.md §5).

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
importScripts(PYODIDE + "pyodide.js");

// 계산기는 손대지 않는다. 여기서 하는 일은 build_squad → simulate 호출뿐이다.
const PY = `
import json, sys, time
sys.path.insert(0, "/home/pyodide")

from context import spec as char_spec
from calculator.timeline import simulate


def run_one(names, code, duration, core_px):
    names = [str(n) for n in names]
    t = time.perf_counter()
    squad = char_spec.build_squad(names)
    config = char_spec.build_config(squad, {
        "duration": float(duration), "rng_mode": "expected",
    })
    # 나머지는 DEFAULT_ENEMY가 채운다. core_px는 0이면 코어 없는 보스다.
    enemy = {"code": code or None, "core_px": float(core_px or 0)}
    r = simulate(squad, config=config, enemy=enemy, verbose=False)
    return json.dumps({
        "sec": time.perf_counter() - t,
        "total": r.squad_total,
        "chars": r.char_total,
        # 기본 스펙 이탈은 결과와 함께 보고해야 한다 (AGENTS.md §Simulation invariants)
        "notes": char_spec.format_deviations(squad).strip(),
    }, ensure_ascii=False)
`;

let runOne = null;

async function boot() {
  const t0 = performance.now();
  const pyodide = await loadPyodide({ indexURL: PYODIDE });

  const buf = await (await fetch("repo.zip")).arrayBuffer();
  pyodide.unpackArchive(buf, "zip");
  await pyodide.runPythonAsync(PY);
  runOne = pyodide.globals.get("run_one");

  postMessage({ type: "ready", boot: (performance.now() - t0) / 1000 });
}

const booting = boot().catch((e) => {
  postMessage({ type: "fatal", error: String(e.message || e) });
});

onmessage = async (ev) => {
  const { id, names, code, duration, corePx } = ev.data;
  await booting;
  if (!runOne) return; // boot 실패 — fatal은 이미 보냈다

  try {
    const raw = runOne(names, code, duration, corePx);
    postMessage({ type: "done", id, result: JSON.parse(raw) });
  } catch (e) {
    // 미파싱 캐릭터 등은 ValueError로 온다. 조용히 0을 만들지 않고 그대로 올린다.
    postMessage({ type: "error", id, error: String(e.message || e).split("\n").pop() });
  }
};
