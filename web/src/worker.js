// 계산 워커 — Pyodide로 계산기를 그대로 돌린다.
// 메인 스레드에서 돌리면 덱당 6~10초 동안 UI가 얼어붙는다 (webapp-roadmap.md §5).

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
importScripts(PYODIDE + "pyodide.js");

// 계산기는 손대지 않는다. 여기서 하는 일은 build_squad → simulate 호출뿐이다.
const PY = `
import json, os, sys, time
sys.path.insert(0, "/home/pyodide")

from context import spec as char_spec
from calculator.timeline import simulate

# 육성 프로필(2.5층). 없으면 고정 스펙으로 돈다. 레벨 정책은 건드리지 않는다 —
# 웹앱은 언제나 400이다 (webapp-roadmap.md §4).
PROFILE = None
PROFILE_DIR = "/home/pyodide/profiles"


def set_profile(text):
    """프로필 JSON 문자열을 갈아끼운다. 빈 값이면 고정 스펙으로 돌아간다.

    파일로 써서 \`spec.load_profile\`에 맡기는 이유는 **로더가 정본 하나여야** 하기
    때문이다. 형식 검사(육성이 아닌 키 거부 등)를 웹앱이 다시 적으면 규칙이 둘로 갈린다.
    """
    global PROFILE
    if not text:
        PROFILE = None
        return ""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(os.path.join(PROFILE_DIR, "me.json"), "w", encoding="utf-8") as f:
        f.write(text)
    PROFILE = char_spec.load_profile("me")
    return json.dumps({
        "name": PROFILE.name,
        "fetched_at": PROFILE.meta.get("fetched_at"),
        "roster": PROFILE.meta.get("roster"),
    }, ensure_ascii=False)


def _burst_text(v):
    """버스트 패턴 값을 이탈 보고 한 줄로. 값 어휘는 \`timeline._pattern_rank\`가 정본이다."""
    if v is None:
        return "패턴 없음 (평소 순서)"
    if isinstance(v, str) and v.startswith("every:"):
        return f"{v.split(':', 1)[1]}의 배수 사이클 우선"
    if isinstance(v, list) and not v:
        return "가급적 안 씀 (다른 후보가 전부 쿨일 때만)"
    return f"{', '.join(str(x) for x in v)}번째 사이클 우선"


def run_one(names, code, duration, core_px, over_json="", burst_json=""):
    """덱 하나를 돌린다. \`over_json\`은 캐릭터별 오버라이드 {이름: {육성키: 값}}.

    육성 탭이 축 하나를 한 칸 올려 재는 자리이고, 세부 조정이 컨트롤·육성을 손보는
    통로이기도 하다. 오버라이드는 **프로필 뒤**에 얹히므로 (\`build_char\`의 합성 순서)
    지금 육성 상태 위에서 그 칸만 움직인 모습이 된다.

    \`burst_json\`은 버스트 패턴 {이름: 값}. 값은 \`"every:3"\` · 사이클 목록 · \`[]\`(안 씀) ·
    \`null\`(패턴 없음)이다. **\`over\`가 아니라 config로 넘긴다** — \`spec.burst_pattern_of\`는
    \`data/char_defaults.json\`에 이름으로 등록된 패턴만 받아들이는데, 세부 조정은 유저가
    그 자리에서 만든 값도 다루기 때문이다. \`build_config\`가 호출자 config를 레이어보다
    뒤에 놓으므로(\`{**pats, **cfg}\`) 여기 적힌 이름은 레이어 기본 패턴을 덮는다.
    """
    names = [str(n) for n in names]
    over = json.loads(over_json) if over_json else None
    burst = json.loads(burst_json) if burst_json else None
    t = time.perf_counter()
    if PROFILE is not None:
        # 프로필 객체는 계속 재사용된다. 미육성 대체 목록은 **이 덱의 것만** 실어야 한다.
        PROFILE.ungrown.clear()
    squad = char_spec.build_squad(names, chars=over, profile=PROFILE)
    cfg = {"duration": float(duration), "rng_mode": "expected"}
    if burst:
        # 레이어가 걸어 둔 패턴 이름을 캐릭터 dict에서 뗀다. 계산에는 아래 config가
        # 이기는데(\`build_config\`), 이름을 남겨 두면 이탈 보고가 "레이어 패턴 전담"과
        # "지정 가급적 안 씀"을 나란히 적어 서로 반대되는 말을 하게 된다.
        for c in squad:
            if c["name"] in burst:
                c.pop("burst_pattern", None)
        cfg["burst_pattern"] = burst
    config = char_spec.build_config(squad, cfg)
    # 나머지는 DEFAULT_ENEMY가 채운다. core_px는 0이면 코어 없는 보스다.
    enemy = {"code": code or None, "core_px": float(core_px or 0)}
    r = simulate(squad, config=config, enemy=enemy, verbose=False)
    # 기본 스펙 이탈은 결과와 함께 보고해야 한다 (AGENTS.md §Simulation invariants).
    # 프로필을 끼면 그 사실·미육성 대체·낮은 스킬 레벨 경고도 여기에 함께 온다.
    notes = char_spec.format_deviations(squad, profile=PROFILE).strip()
    # 컨트롤을 끄는 오버라이드는 \`None\`이다(재귀 병합이라 \`{}\`로는 레이어가 안 지워진다).
    # 보고에 그대로 나오면 "없음 → None"이 되어 아무것도 안 바뀐 것처럼 읽힌다.
    notes = notes.replace("→ None", "→ 끔")
    # 버스트 패턴은 config로 가므로 캐릭터 dict 비교에 안 잡힌다. 조용히 빠지면 결과가
    # "평소 순서로 돌린 값"으로 읽히므로 여기서 직접 싣는다.
    if burst:
        lines = "\\n".join(f"  {n}: {_burst_text(v)}" for n, v in burst.items())
        notes = (notes + "\\n" if notes else "") + "버스트 패턴 지정\\n" + lines
    return json.dumps({
        "sec": time.perf_counter() - t,
        "total": r.squad_total,
        "chars": r.char_total,
        "notes": notes,
        # 어느 시점의 육성으로 잰 값인지. 기록 탭이 나중에 쓴다 (webapp-roadmap.md §1b)
        "fetchedAt": PROFILE.meta.get("fetched_at") if PROFILE is not None else None,
    }, ensure_ascii=False)
`;

let runOne = null;
let setProfile = null;

async function boot() {
  const t0 = performance.now();
  const pyodide = await loadPyodide({ indexURL: PYODIDE });

  const buf = await (await fetch("repo.zip")).arrayBuffer();
  pyodide.unpackArchive(buf, "zip");
  await pyodide.runPythonAsync(PY);
  runOne = pyodide.globals.get("run_one");
  setProfile = pyodide.globals.get("set_profile");

  postMessage({ type: "ready", boot: (performance.now() - t0) / 1000 });
}

const booting = boot().catch((e) => {
  postMessage({ type: "fatal", error: String(e.message || e) });
});

onmessage = async (ev) => {
  await booting;
  if (!runOne) return; // boot 실패 — fatal은 이미 보냈다

  // 프로필 갈아끼우기. 메시지는 도착 순서대로 처리되므로, 메인 스레드가 계산보다 먼저
  // 보내기만 하면 그 계산은 새 프로필로 돈다.
  if (ev.data.type === "profile") {
    try {
      const meta = setProfile(ev.data.text || "");
      postMessage({ type: "profile", meta: meta ? JSON.parse(meta) : null });
    } catch (e) {
      // load_profile은 형식이 어긋나면 SystemExit로 끊는다. 그 문구를 그대로 올리되,
      // 파이썬 예외 이름과 가상 FS 경로는 뗀다 — 유저가 고른 파일 이름이 아니라 헷갈린다.
      const msg = String(e.message || e).split("\n").filter(Boolean).pop()
        .replace(/^SystemExit:\s*/, "")
        .replace(/^\/home\/pyodide\/profiles\/[^:]+:\s*/, "");
      postMessage({ type: "profile-error", error: msg });
    }
    return;
  }

  const { id, names, code, duration, corePx, over, burst } = ev.data;
  try {
    const raw = runOne(names, code, duration, corePx,
                       over ? JSON.stringify(over) : "",
                       burst ? JSON.stringify(burst) : "");
    postMessage({ type: "done", id, result: JSON.parse(raw) });
  } catch (e) {
    // 미파싱 캐릭터 등은 ValueError로 온다. 조용히 0을 만들지 않고 그대로 올린다.
    postMessage({ type: "error", id, error: String(e.message || e).split("\n").pop() });
  }
};
