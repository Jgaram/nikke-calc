// 계산 워커 — Pyodide로 계산기를 그대로 돌린다.
// 메인 스레드에서 돌리면 덱당 10초 동안 UI가 얼어붙는다 (webapp-roadmap.md §5).
//
// 실측(2026-08-21, 180초 5인 1덱 기대값 모드): 부팅 2.8s · 계산 10.5s.
// 같은 조건 네이티브 파이썬이 6.5s라 **Pyodide는 1.6배**다. 서버 계산을 켜면 5덱을
// 코어 수만큼 병렬로 돌려 더 빠르지만, 이 워커만으로도 서버 없이 전부 동작한다.
//
// 변환(raw → 육성 프로필)도 여기서 한다. `scraper/profile_convert.py`를 **그대로**
// import한다 — JS로 다시 구현하면 CLI와 어긋난다.

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
importScripts(PYODIDE + "pyodide.js");

// 계산기는 손대지 않는다. 여기서 하는 일은 build_squad → simulate 호출과 변환뿐이다.
const PY = `
import json, sys, time
sys.path.insert(0, "/home/pyodide")
sys.path.insert(0, "/home/pyodide/scraper")   # profile_convert는 패키지가 아니다

from context import spec as char_spec
from calculator.timeline import simulate
from profile_convert import build_profile
from profile_csv import build_profile_from_csv


def _profile(profile_json):
    """프로필 JSON 문자열 → GrowthProfile. 빈 값이면 None(고정 스펙).

    **불러온 프로필도 파일과 같은 게이트를 지난다** — 운용 키(control·burst_pattern)가
    육성 프로필에 섞이면 조용히 다른 걸 계산하므로 spec.profile_from_dict가 끊는다.

    프로필에 없는 캐릭터(미보유·수집 뒤 영입)는 spec.UNGROWN으로 계산되고 그 사실이
    결과에 실린다 — CLI·웹 양쪽 다 같다(예전엔 CLI만 에러, 웹만 allow_unowned=True로
    갈랐는데, 그 스위치 자체가 없어졌다).
    """
    if not profile_json:
        return None
    data = json.loads(profile_json)
    return char_spec.profile_from_dict(data, where="불러온 프로필")


def run_one(names, code, duration, profile_json=None,
            enemy_json=None, config_json=None, control_json=None,
            cubes_json=None):
    """전투 조건(enemy·config)은 **UI가 준 것만** 덮는다. 안 주면 계산기 기본값이 남는다.

    control_json은 클라이언트 모양 {캐릭명: {reload:..., burst_pattern:...}}이다.
    build_squad 오버라이드에서 컨트롤 키는 "control" 아래 있어야 하므로 여기서 감싼다 —
    서버(_clean_control)와 같은 변환이다. **예전엔 안 감싸고 그대로 넘겨서 브라우저
    엔진만 컨트롤을 조용히 무시했다** (컨트롤 키가 최상위에 얹혀 계산기가 안 읽었다).
    컨트롤·버스트 주기·선버는 운용이라 육성 프로필에 넣지 않는다 (context/spec.py가 막는다).

    (이 파일의 파이썬은 JS 템플릿 리터럴 안에 있다 — **백틱을 쓰면 리터럴이 끊긴다.**)
    """
    names = [str(n) for n in names]
    t = time.perf_counter()
    prof = _profile(profile_json)
    ctrl = json.loads(control_json) if control_json else None
    over = {}
    for nm, v in (ctrl or {}).items():
        if not isinstance(v, dict):
            continue
        entry = {}
        c = {k: v[k] for k in ("tap_fire", "reload", "cover", "hold")
             if isinstance(v.get(k), dict)}
        if c:
            entry["control"] = c
        bp = v.get("burst_pattern")
        if isinstance(bp, str) and bp:
            entry["burst_pattern"] = None if bp == "안 씀" else bp
        elif isinstance(bp, list) and bp:
            entry["burst_pattern"] = [int(x) for x in bp]
        if v.get("burst_first") is True:
            entry["burst_first"] = True
        if entry:
            over[nm] = entry
    # 큐브는 «칸에 붙는 설정»이라 컨트롤이 아니라 따로 온다. 캐릭터 오버라이드의
    # cube로 넣으면 프로필 층(계정 보유 최고)보다 우선한다 — 서버와 같은 규약이다.
    # (이 블록은 JS 템플릿 리터럴 안이라 **백틱을 쓰면 안 된다** — SITE.md §밟은 지뢰)
    for nm, cb in (json.loads(cubes_json) if cubes_json else {}).items():
        if isinstance(cb, dict) and cb.get("name") and cb.get("level") is not None:
            over.setdefault(nm, {})["cube"] = {"name": str(cb["name"]),
                                               "level": int(cb["level"])}
    # 버스트 금지는 캐릭터 스펙이 아니라 **전투 설정**이라 config로 간다
    # (timeline._rebuild_burst_order가 후보에서 뺀다). 서버 경로와 같은 규약이다.
    no_burst = [nm for nm, v in (ctrl or {}).items()
                if isinstance(v, dict) and v.get("no_burst") is True]
    squad = char_spec.build_squad(names, over or None, profile=prof)
    over = json.loads(config_json) if config_json else {}
    config = char_spec.build_config(squad, {
        **over,
        "duration": float(duration), "rng_mode": "expected",
        **({"no_burst_chars": no_burst} if no_burst else {}),
    })
    enemy = json.loads(enemy_json) if enemy_json else ({"code": code} if code else None)
    r = simulate(squad, config=config, enemy=enemy, verbose=False)
    # 니케별 내역 — 총딜 하나로는 «왜 이 딜인지»를 못 읽는다.
    # 기본공격/스킬 비중·히트 수·크리 횟수는 히트 목록에 이미 다 들어 있다.
    from calculator.sim_result import _is_normal, summarize_top_atk, dps_timeline, burst_cycles
    detail = {}
    for _nm in r.char_total:
        _h = [e for e in r.hits if e.caster == _nm]
        _n = sum(e.damage for e in _h if _is_normal(e))
        detail[_nm] = {"total": r.char_total[_nm], "normal": _n,
                       "skill": r.char_total[_nm] - _n,
                       "hits": len(_h),
                       "crit": sum(getattr(e, "crit_frac", 0.0) for e in _h)}
    return json.dumps({
        "sec": time.perf_counter() - t,
        "total": r.squad_total,
        "chars": r.char_total,
        "detail": detail,
        # 「최종 공격력이 가장 높은 아군」 대상 버프가 누구에게 갔나 (미란다 애장품 등).
        # 시뮬이 verbose 없이도 모으므로 **여기 얹는 데 추가 비용이 없다** —
        # 이것 때문에 계산을 한 번 더 돌리지 않아도 된다.
        "top_atk": summarize_top_atk(r),
        # 기본 스펙 이탈은 결과와 함께 보고해야 한다 (AGENTS.md §Simulation invariants)
        "notes": char_spec.format_deviations(squad, profile=prof,
                                             show_profile_header=False,
                                             growth_as_cards=True,
                                             hide_cube=True).strip(),
        # 스킬 레벨·애장품 단계·미육성 — 위 notes에서 문장으로 안 나가는 대신
        # 여기로 원자료가 온다. 결과 화면이 초상화 카드로 그린다(유저 피드백:
        # 글로 쭉 나열하지 말고 버프 대상처럼 칸으로 보여 달라).
        "growth_flags": prof.growth_flags([c["name"] for c in squad]) if prof else None,
        # 타임라인 — 결과 화면 하단의 확인용 그래프 하나가 쓴다. 저장은 안 한다
        # (기록에는 안 실린다, collectDecks() 참고). hits는 이미 다 갖고 있으므로
        # 구간별로 접는 데 비용이 거의 없다.
        "timeline": dps_timeline(r),
        "burst_cycles": burst_cycles(r),
    }, ensure_ascii=False)


def convert(raw_json, maps_json, name):
    """블라링크 원시 응답 → 육성 프로필. 수집 경로(북마클릿·서버)와 무관하게 같은 함수다."""
    profile, notices = build_profile(json.loads(raw_json), json.loads(maps_json), name)
    return json.dumps({"profile": profile, "notices": notices}, ensure_ascii=False)


def convert_csv(text, maps_json, name):
    """레츠도로 CSV → 육성 프로필. 로그인이 필요 없는 세 번째 수집 경로다."""
    profile, notices = build_profile_from_csv(text, json.loads(maps_json), name)
    return json.dumps({"profile": profile, "notices": notices}, ensure_ascii=False)


def validate(profile_json):
    """불러온·수정한 프로필이 육성 프로필로 성립하는지. 실패 이유를 문자열로 돌려준다."""
    try:
        _profile(profile_json)
        return json.dumps({"ok": True})
    except BaseException as e:      # SystemExit도 잡는다 — spec이 그걸로 끊는다
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def base_atk_of(names_json, profile_json):
    """니케들의 **소지 공격력**만 돌려준다. 시뮬을 돌리지 않는다 — 표 조회뿐이라 즉시다.
    브라우저의 «최공 대상 즉시 계산»이 분모로 쓴다.

    브라우저에서 다시 구하지 않는 이유: 레벨·돌파·장비·큐브·콘솔·소장품·애장품·호감도가
    모두 얽힌 표 계산이라, 옮겨 적으면 **두 곳이 조용히 갈린다.** 계산기 것을 그대로 쓴다.
    """
    from calculator.base_stat import calc_base_stats
    names = json.loads(names_json)
    prof = None
    if profile_json:
        prof = char_spec.profile_from_dict(json.loads(profile_json),
                                           where="전달된 프로필")
    squad = char_spec.build_squad([str(n) for n in names], None, profile=prof)
    # 공증(오버로드 공격력 증가)도 함께 준다. 브라우저가 스펙에서 직접 읽으면 «고정 스펙»
    # 에서는 값이 없어(프로필이 없으니) 조용히 0이 되고, 예측이 실제보다 낮게 나온다.
    return json.dumps({c["name"]: {
        "atk": round(calc_base_stats(c).get("atk", 0.0)),
        "atk_pct": float((c.get("equip_skills") or {}).get("atk_pct") or 0.0),
    } for c in squad}, ensure_ascii=False)
`;

let py = null;
let runOne = null;
let convertFn = null;
let convertCsvFn = null;
let validateFn = null;
let baseAtkFn = null;
let mapsJson = null; // profile_maps.json 원문. 변환 때만 필요해서 지연 로드한다

async function boot() {
  const t0 = performance.now();
  const pyodide = await loadPyodide({ indexURL: PYODIDE });

  const buf = await (await fetch("repo.zip")).arrayBuffer();
  pyodide.unpackArchive(buf, "zip");
  await pyodide.runPythonAsync(PY);
  py = pyodide;
  runOne = pyodide.globals.get("run_one");
  convertFn = pyodide.globals.get("convert");
  convertCsvFn = pyodide.globals.get("convert_csv");
  validateFn = pyodide.globals.get("validate");
  baseAtkFn = pyodide.globals.get("base_atk_of");

  postMessage({ type: "ready", boot: (performance.now() - t0) / 1000 });
}

const booting = boot().catch((e) => {
  postMessage({ type: "fatal", error: String(e.message || e) });
});

// 조회표는 빌드가 구워 둔 것을 쓴다 (CDN을 1+N회 때리지 않게 — web/build.py 참조).
async function maps() {
  if (mapsJson == null) mapsJson = await (await fetch("profile_maps.json")).text();
  return mapsJson;
}

onmessage = async (ev) => {
  const msg = ev.data;
  const kind = msg.type || "sim"; // type 없는 예전 형식은 계산 요청으로 본다
  await booting;
  if (!runOne) return; // boot 실패 — fatal은 이미 보냈다

  try {
    if (kind === "sim") {
      const raw = runOne(msg.names, msg.code, msg.duration, msg.profile || null,
                        msg.enemy ? JSON.stringify(msg.enemy) : null,
                        msg.config ? JSON.stringify(msg.config) : null,
                        msg.control ? JSON.stringify(msg.control) : null,
                        msg.cubes ? JSON.stringify(msg.cubes) : null);
      postMessage({ type: "done", id: msg.id, result: JSON.parse(raw) });
    } else if (kind === "convert") {
      const raw = convertFn(msg.raw, await maps(), msg.name || "me");
      postMessage({ type: "converted", id: msg.id, ...JSON.parse(raw) });
    } else if (kind === "convert_csv") {
      const raw = convertCsvFn(msg.text, await maps(), msg.name || "me");
      postMessage({ type: "converted", id: msg.id, ...JSON.parse(raw) });
    } else if (kind === "base_atk") {
      const raw = baseAtkFn(JSON.stringify(msg.names || []), msg.profile || null);
      postMessage({ type: "base_atk", id: msg.id, atk: JSON.parse(raw) });
    } else if (kind === "validate") {
      const raw = validateFn(msg.profile);
      postMessage({ type: "validated", id: msg.id, ...JSON.parse(raw) });
    } else {
      postMessage({ type: "error", id: msg.id, error: `알 수 없는 요청: ${kind}` });
    }
  } catch (e) {
    // 미파싱 캐릭터·프로필 형식 오류 등은 예외로 온다. 조용히 0을 만들지 않고 그대로 올린다.
    postMessage({ type: "error", id: msg.id, error: String(e.message || e).split("\n").pop() });
  }
};
