// 세부 조정 — 덱 상자 안에서 니케 한 명씩 **어떻게 굴릴지**를 손본다.
//
// 편성 탭은 지금까지 "누구를 넣는가"만 다뤘다. 그런데 같은 5명이라도 컨트롤을 잡느냐,
// 버스트를 몇 번째 사이클에 쓰느냐, 스킬을 어디까지 올렸다고 치느냐에 따라 딜량이 갈린다.
// 그 손잡이가 PC 러너에만 있어서, 폰에서는 "지금 이 육성 그대로, 컨트롤 없이" 한 가지
// 경우만 잴 수 있었다.
//
// **자리는 덱 상자 안이다.** 조정은 "이 덱에서 이 니케를 이렇게 굴린다"라 덱에 딸린
// 값이고(같은 니케라도 옆에 누가 서느냐에 따라 정답이 갈린다), 계산 버튼과 같은 상자
// 안에 있어야 누르기 전에 무엇을 재는지가 보인다. 계산 탭을 따로 두면 편성과 조정이
// 서로 다른 화면으로 갈려 "지금 화면의 딜량이 어느 조정의 것인가"를 다시 묻게 된다.
//
// 이 화면이 잡은 것 넷 —
//
//  1. **손대지 않은 칸은 저장하지 않는다.** 고른 값이 기준(프로필 또는 기본 스펙)과 같아지면
//     그 칸을 지운다. 덱 지문이 조정 전 모양으로 돌아가므로 **쌓아둔 결과 캐시가 그대로
//     되살아난다** — 되돌리기가 곧 "다시 계산"이 아니다.
//  2. **기준이 무엇인지 화면이 먼저 말한다.** 같은 `스킬 10`이라도 프로필을 켜면 "내 계정이
//     실제로 10"이고 끄면 "기본 스펙이 10"이다. 선택지 첫 줄이 언제나 `기본 (지금 값)`이다.
//  3. **컨트롤은 세 갈래다.** 기본(캐릭터 레이어가 정한 대로) · 켬 · **끔**. 끔이 따로
//     필요한 이유는 앨리스·아인처럼 레이어가 톡톡이를 이미 켜 둔 캐릭터가 있어서다
//     (`context/CONTROL.md §캐릭터별 기본 컨트롤`). 끄려면 `null`을 얹는다 — 재귀 병합이라
//     `{}`로는 지워지지 않는다.
//  4. **버스트 패턴은 config로 간다.** `spec.burst_pattern_of`는 `char_defaults.json`에
//     이름으로 등록된 패턴만 받는데, 여기서는 유저가 그 자리에서 만든 `n의 배수`도 다뤄야
//     한다. `build_config`가 호출자 config를 레이어보다 뒤에 놓으므로 값이 그대로 이긴다
//     (§worker.js `run_one`).
//
// 계산기는 한 줄도 고치지 않았다. 여기서 만드는 것은 전부 `spec.build_char`의 `over`와
// `timeline`이 이미 읽는 `config["burst_pattern"]`이다.

let TDEF = null;        // roster.json defaultChar — 1층 기본 스펙 중 조정 대상 칸
let TCUBES = {};        // 큐브 이름 → {levels, unsupported}
let TSTAGES = [];       // 고를 수 있는 소장품 단계
let TPARTS = [];        // 장비 부위
let TOPT = {};          // 오버로드 옵션 키 → 한글 이름
let TAFF_MAX = 40;

const TSKILL_AXES = [["1", "스킬 1"], ["2", "스킬 2"], ["3", "버스트 스킬"]];
const TSKILL_MAX = 10;
const TGEAR_MAX = 5;
const TFAV_MAX = 3;
// 돌파·코어강화 상한은 비용표가 들고 있다. 표가 없는 낡은 빌드에서도 화면은 떠야 하므로
// 값이 없을 때만 여기 숫자를 쓴다 (정본은 `cost/tables.json`).
const TBT_MAX = 3;
const TCE_MAX = 7;

// 톡톡이를 직접 켤 때의 출발값. 캐릭터 레이어가 쓰는 3.6은 "추천"이고, 여기는 유저가
// 자기 손 속도를 적는 자리라 별개다. 실질 범위는 3.0(미숙련)~4.2(숙련 상한)이고
// 4.5발/초는 게임이 강제하는 하한(220ms)이라 그 위는 사람이 낼 수 없다
// (`context/CONTROL.md §톡톡이`).
const TTAP_DEFAULT = 3.6;
const TTAP_LIMIT = 4.5;

// 컨트롤 어휘. 정본은 `context/CONTROL.md §설정 스키마`이고 값을 실제로 읽는 것은
// `calculator/timeline.py`다. 여기 있는 것은 **고를 수 있는 것의 목록**뿐이라 값 자체를
// 다시 정의하지 않는다 — 기본값(lead·release 등)은 계산기가 채운다.
const THOLD_POLICIES = [
  ["own_full_burst", "본인 풀버스트 홀드"],
  ["charge_hold_after_fb", "풀버스트 후 홀드"],
];
const TRELOAD_POLICIES = [
  ["before_fb_end", "풀버스트 종료 전"],
  ["into_fb", "풀버스트 진입 맞춤"],
];
// 차지형(SR·RL)에만 걸리는 컨트롤. 다른 무기군에 줘도 계산기가 무시하지만
// (`timeline`의 `fire_mode == "charge"` 검사) 고를 수 있게 두면 화면이 거짓말이 된다.
const TCHARGE_WEAPONS = new Set(["SR", "RL"]);

// 펼쳐 둔 상자. 덱 하나는 `deck.id`, 니케 하나는 `deck.id|이름`이다.
// **저장하지 않는다** — 어느 상자를 열어 뒀는지는 지금 이 화면의 상태지 편성의 일부가
// 아니다. 대신 렌더를 넘어 살아 있어야 한다 (편성 탭은 드래그 한 번에도 다시 그린다).
const tOpen = new Set();

// ── 상태 읽고 쓰기 ──────────────────────────────────────────────────────
// deck.tune[이름] = { over: {...}, burst: {...} }
//   over  : `spec.build_char`의 오버라이드. 육성 칸과 `control`이 여기 들어간다
//   burst : 버스트 패턴 선택. `{mode}` 모양으로 들고 있다가 계산 직전에 값으로 편다
//           (§tBurstValue). 값으로 바로 저장하면 `every:1`이 등록 패턴 `매번`인지
//           유저가 적은 1인지 구분되지 않아 화면으로 되돌릴 수가 없다

const tuneOf = (deck, name) => deck.tune?.[name] ?? null;

function tEntry(deck, name) {
  if (!deck.tune) deck.tune = {};
  if (!deck.tune[name]) deck.tune[name] = { over: {} };
  return deck.tune[name];
}

/** 조정이 하나도 남지 않은 항목을 걷어낸다. 빈 껍데기가 남으면 지문이 안 돌아간다. */
function tCommit(deck, name) {
  const e = deck.tune?.[name];
  if (!e) return;
  if (!Object.keys(e.over).length && !e.burst) delete deck.tune[name];
  if (deck.tune && !Object.keys(deck.tune).length) delete deck.tune;
  saveAll();
  render();
}

/** `over`의 한 칸을 적거나(기준과 다르면) 지운다(같으면). `base`가 되돌리기 지점이다. */
function tPut(deck, name, path, value, base) {
  const e = tEntry(deck, name);
  if (value === base || value == null) tDrop(e.over, path);
  else tAssign(e.over, path, value);
  tCommit(deck, name);
}

function tAssign(obj, path, value) {
  let cur = obj;
  for (const k of path.slice(0, -1)) cur = cur[k] ??= {};
  cur[path.at(-1)] = value;
}

/** 값을 지우고 **빈 부모까지 같이** 걷어낸다. `{equipment:{}}`가 남으면 조정이 없는데도
 *  지문이 조정된 모양으로 남는다. */
function tDrop(obj, path) {
  const [head, ...rest] = path;
  if (!(head in obj)) return;
  if (!rest.length) { delete obj[head]; return; }
  tDrop(obj[head], rest);
  if (!Object.keys(obj[head]).length) delete obj[head];
}

// ── 기준값 ──────────────────────────────────────────────────────────────
// 조정하지 않았을 때 그 니케가 어떤 값으로 계산되는가. 합성 순서는 계산기와 같다 —
// 기본 스펙(1층) → 캐릭터 레이어(2층) → 육성 프로필(2.5층). 오버라이드(3층)는 그 위다.
//
// 여기서 프로필을 **켜져 있을 때만** 본다는 점이 growth.js의 `growthOf`와 다르다.
// 저쪽은 육성 화면이라 기준이 언제나 내 육성이지만, 여기는 계산 화면이라 `기본 스펙`으로
// 돌고 있으면 되돌리기 지점도 기본 스펙이어야 한다.

const tLayer = (name) => byName.get(name)?.layer ?? {};
const tProf = (name) => (profileOn() ? (profile?.chars?.[name] ?? UNGROWN) : {});

function tBase(name) {
  const p = tProf(name);
  return {
    breakthrough: p.breakthrough ?? TDEF.breakthrough,
    core_enhancement: p.core_enhancement ?? TDEF.core_enhancement,
    affinity: p.affinity ?? TDEF.affinity,
    skill: (k) => p.skill_levels?.[k] ?? TDEF.skill_levels[k],
    // 프로필의 장비는 부위별 dict다. 강화 칸이 없는 부위(일반 T1~T9·미장착)는 `level`이
    // 아예 없으므로 `null`로 답한다 — 그런 부위는 조정도 막는다(§tGrowthSection).
    gear: (part) => (profileOn()
      ? (p.equipment?.[part]?.level ?? null)
      : TDEF.equipment[part]),
    collection_stage: p.collection_stage ?? TDEF.collection_stage,
    favorite_stage: p.favorite_stage ?? TDEF.favorite_stage,
    // 큐브는 프로필에 담기지 않는다 — 자유롭게 갈아끼우는 축이라 육성이 아니라 케이스가
    // 정한다(`context/spec.py §UNGROWN`). 그래서 기준은 언제나 기본 스펙이다.
    cube: TDEF.cube,
    // 오버로드 수치는 캐릭터 레이어가 기본 스펙을 덮는 자리이기도 하다.
    opt: (k) => tSum(p.equip_skills?.[k]) ?? tSum(tLayer(name).equip_skills?.[k])
                ?? tSum(TDEF.equip_skills[k]) ?? 0,
  };
}

/** 오버로드 옵션 한 칸. 프로필은 줄별 리스트, 기본 스펙은 합계 하나로 적는다. */
function tSum(v) {
  if (v == null) return null;
  return Array.isArray(v) ? v.reduce((a, b) => a + b, 0) : v;
}

const tRound = (n) => Math.round(n * 100) / 100;

// ── 계산에 넘길 모양 ────────────────────────────────────────────────────

/** 이 덱에 실제로 걸린 조정. 덱을 떠난 니케는 세지 않는다. */
function tActive(deck) {
  const out = [];
  for (const name of deck.names) {
    const e = name && deck.tune?.[name];
    if (e && (Object.keys(e.over).length || e.burst)) out.push([name, e]);
  }
  return out;
}

/** 저장된 선택 → `timeline`이 읽는 버스트 패턴 값. 등록 패턴이 사라졌으면 null. */
function tBurstValue(name, b) {
  if (!b) return undefined;
  if (b.mode === "skip") return [];        // 어느 사이클도 차례가 아니다 = 가급적 안 씀
  if (b.mode === "none") return null;      // 패턴 없음 — 평소 순서(슬롯 순서)로 돌아간다
  if (b.mode === "every") return `every:${b.n}`;
  return tLayer(name).patterns?.[b.name] ?? null;
}

/** 워커에 실을 조각. `extra`는 그 위에 한 겹 더 얹을 오버라이드 {이름: {...}}다
 *  (투자 효율이 축 하나를 올려 잴 때 쓴다 — 조정된 덱에서도 기준선이 어긋나면 안 된다). */
function tunePayload(deck, extra) {
  const over = {};
  const burst = {};
  for (const [name, e] of tActive(deck)) {
    if (Object.keys(e.over).length) over[name] = structuredClone(e.over);
    if (e.burst) burst[name] = tBurstValue(name, e.burst);
  }
  for (const [name, v] of Object.entries(extra ?? {})) {
    over[name] = over[name] ? tMerge(over[name], v) : structuredClone(v);
  }
  const out = {};
  if (Object.keys(over).length) out.over = over;
  // 빈 객체도 JS에서는 참이라 그대로 보내면 워커가 `burst_pattern: {}`를 config에 넣는다.
  if (Object.keys(burst).length) out.burst = burst;
  return out;
}

/** `spec.deep_merge`와 같은 규칙 — dict끼리만 재귀하고 나머지는 뒤가 이긴다. */
function tMerge(a, b) {
  const out = structuredClone(a);
  for (const [k, v] of Object.entries(b)) {
    out[k] = (v && typeof v === "object" && !Array.isArray(v)
              && out[k] && typeof out[k] === "object" && !Array.isArray(out[k]))
      ? tMerge(out[k], v) : structuredClone(v);
  }
  return out;
}

/** 덱 지문에 붙는 조정 토큰. 조정이 없으면 빈 문자열이라 지문이 예전 모양 그대로다. */
function tuneToken(deck) {
  const rows = tActive(deck).map(([name, e]) =>
    `${name}:${stable(e.over)}:${JSON.stringify(tBurstValue(name, e.burst) ?? null)}`);
  return rows.length ? hash32(rows.join("|")) : "";
}

// ── 렌더 ────────────────────────────────────────────────────────────────
// 편성 탭은 드래그 한 번에도 통째로 다시 그리는 화면이다. 조정 패널을 늘 그리면 덱 5개 ×
// 니케 5명 × 손잡이 수십 개가 매번 딸려 오므로, **펼친 상자만** 속을 채운다.

function tuneBox(deck) {
  const box = el("details", "tune");
  // 기본 스펙 표가 없으면 되돌리기 지점을 모른다 — 낡은 빌드다. 화면을 반쯤 열어 두느니
  // 무엇을 해야 하는지만 적는다.
  if (!TDEF) {
    box.append(el("summary", null, "세부 조정"),
               el("p", "thint2", "roster.json이 낡았다 — `python web/build.py`로 다시 빌드한다."));
    return box;
  }
  const open = tOpen.has(deck.id);
  box.open = open;

  const on = tActive(deck);
  const sum = el("summary");
  sum.append(el("span", "tlabel", "세부 조정"));
  if (on.length) sum.append(el("span", "ttag", `${on.length}명`));
  else sum.append(el("span", "thint", "컨트롤 · 버스트 · 육성"));
  box.append(sum);

  const body = el("div", "tune-body");
  box.append(body);
  const fill = () => tFillDeck(body, deck);
  if (open) fill();
  box.ontoggle = () => {
    if (box.open) tOpen.add(deck.id); else tOpen.delete(deck.id);
    if (box.open && !body.childElementCount) fill();
  };
  return box;
}

function tFillDeck(body, deck) {
  body.textContent = "";
  const filled = deck.names.filter(Boolean);
  if (!filled.length) {
    body.append(el("p", "thint2", "덱이 비어 있다. 니케를 넣으면 여기서 손볼 수 있다."));
    return;
  }
  // 슬롯 순서가 곧 버스트 우선순위다. 정렬하지 않고 덱에 놓인 그대로 읽는다.
  for (const name of filled) body.append(tCharBox(deck, name));

  if (tActive(deck).length) {
    const foot = el("div", "tfoot");
    const reset = el("button", "mini danger", "이 덱 조정 전부 되돌리기");
    reset.onclick = () => {
      delete deck.tune;
      saveAll();
      render();
    };
    foot.append(reset);
    // 되돌리면 조정 전 지문으로 돌아가므로, 그때 계산해 둔 값이 있으면 그대로 살아난다.
    foot.append(el("span", "thint2", "되돌리면 조정 전에 계산해 둔 딜량이 다시 보인다"));
    body.append(foot);
  }
}

function tCharBox(deck, name) {
  const key = `${deck.id}|${name}`;
  const box = el("details", "tchar");
  const open = tOpen.has(key);
  box.open = open;

  const e = tuneOf(deck, name);
  const sum = el("summary");
  const pic = el("div", "gthumb sm");
  pic.append(thumb(name));
  sum.append(pic);

  const head = el("div", "gbody");
  head.append(el("div", "ghead2", name));
  head.append(el("div", "tsum", tSummary(deck, name)));
  sum.append(head);
  if (e) sum.append(el("span", "ttag", "조정됨"));
  box.append(sum);

  const body = el("div", "tbody");
  box.append(body);
  const fill = () => {
    body.textContent = "";
    body.append(tControlSection(deck, name));
    body.append(tBurstSection(deck, name));
    body.append(tGrowthSection(deck, name));
  };
  if (open) fill();
  box.ontoggle = () => {
    if (box.open) tOpen.add(key); else tOpen.delete(key);
    if (box.open && !body.childElementCount) fill();
  };
  return box;
}

/** 접힌 줄이 말해야 하는 것은 "무엇이 기본과 다른가"다. 값까지 다 적으면 줄이 잘린다. */
function tSummary(deck, name) {
  const e = tuneOf(deck, name);
  if (!e) return "기본대로 계산한다";
  const out = [];
  const ctrl = Object.keys(e.over.control ?? {});
  if (ctrl.length) out.push(`컨트롤 ${ctrl.length}`);
  if (e.burst) out.push(`버스트 ${tBurstLabel(name, e.burst)}`);
  const growth = Object.keys(e.over).filter((k) => k !== "control").length;
  if (growth) out.push(`육성 ${growth}`);
  return out.join(" · ");
}

function tBurstLabel(name, b) {
  if (b.mode === "skip") return "가급적 안 씀";
  if (b.mode === "none") return "패턴 없음";
  if (b.mode === "every") return `${b.n}의 배수`;
  return b.name;
}

// ── 컨트롤 ──────────────────────────────────────────────────────────────
// 손잡이 하나가 세 갈래다 — 기본 · 켬 · 끔. `기본`은 그 칸을 아예 적지 않는 것이라
// 레이어가 정한 대로 가고(조합 조건부 규칙까지 포함), `끔`은 `null`을 얹어 레이어를
// 덮는다. 재귀 병합이라 `{}`로는 지워지지 않기 때문이다(`spec.build_char §no_layer`).

function tControlSection(deck, name) {
  const box = el("section", "tsec");
  box.append(el("h4", null, "컨트롤"));

  const layer = tLayer(name);
  const has = Object.keys(layer.control ?? {});
  const lines = [];
  lines.push(has.length
    ? `기본: ${has.map((k) => TCTRL_LABEL[k] ?? k).join(" · ")}`
    : "기본: 없음 (자동 사격)");
  if (layer.controlRules) {
    lines.push("스쿼드 조합에 따라 기본 컨트롤이 더 붙는다 — 판정은 계산기가 한다");
  }
  box.append(el("p", "tnote", lines.join(" · ")));

  const weapon = byName.get(name)?.weapon;
  const grid = el("div", "tgrid");
  if (TCHARGE_WEAPONS.has(weapon)) {
    grid.append(tTapRow(deck, name));
    grid.append(tPolicyRow(deck, name, "hold", "홀드", THOLD_POLICIES, { lead: 0.5 }));
  }
  grid.append(tPolicyRow(deck, name, "reload", "장전컨", TRELOAD_POLICIES, {}));
  grid.append(tPolicyRow(deck, name, "cover", "버스트 엄폐컨",
                         [["own_full_burst", "본인 풀버스트"]], {}));
  box.append(grid);

  if (!TCHARGE_WEAPONS.has(weapon)) {
    box.append(el("p", "thint2", `톡톡이·홀드는 차지 무기(SR·RL) 전용이다 — ${weapon}에는 안 걸린다.`));
  }
  // 여러 명을 동시에 조작하는 계산은 사람 손 하나가 낼 수 있는 것보다 위다. 숨기지 않는다.
  box.append(el("p", "twarn",
    "여러 니케를 한꺼번에 컨트롤한 값은 실제 조작보다 유리한 상한이다 — 손은 하나다."));
  return box;
}

const TCTRL_LABEL = { tap_fire: "톡톡이", hold: "홀드", reload: "장전컨", cover: "엄폐컨" };

/** 컨트롤 한 칸의 지금 상태 — "base"(안 적음) · "on" · "off". */
function tCtrlMode(deck, name, key) {
  const v = tuneOf(deck, name)?.over.control;
  if (!v || !(key in v)) return "base";
  return v[key] === null ? "off" : "on";
}

function tSetCtrl(deck, name, key, value) {
  const e = tEntry(deck, name);
  if (value === undefined) tDrop(e.over, ["control", key]);
  else tAssign(e.over, ["control", key], value);
  tCommit(deck, name);
}

/** 기본 · 켬 · 끔 칩 셋. `onOn`이 `켬`을 눌렀을 때 얹을 값을 만든다. */
function tModeChips(deck, name, key, onOn) {
  const mode = tCtrlMode(deck, name, key);
  const row = el("div", "chips tchips");
  row.append(chip("기본", mode === "base", () => tSetCtrl(deck, name, key, undefined)));
  row.append(chip("켬", mode === "on", () => tSetCtrl(deck, name, key, onOn())));
  row.append(chip("끔", mode === "off", () => tSetCtrl(deck, name, key, null)));
  return row;
}

function tRow(label) {
  const row = el("div", "trow");
  row.append(el("b", "tkey", label));
  return row;
}

function tTapRow(deck, name) {
  const row = tRow("톡톡이");
  const cur = tuneOf(deck, name)?.over.control?.tap_fire;
  const rate = cur?.rate ?? TTAP_DEFAULT;
  row.append(tModeChips(deck, name, "tap_fire", () => ({ rate })));

  if (tCtrlMode(deck, name, "tap_fire") === "on") {
    const box = el("div", "tsub");
    const input = el("input", "tnum");
    input.type = "number";
    input.step = "0.1";
    input.min = "0.1";
    input.max = "20";
    input.value = String(rate);
    // 값 확정은 `change`로 받는다. 한 글자마다 다시 그리면 덱 전체가 새로 그려지면서
    // 입력 칸의 포커스가 날아간다.
    input.onchange = () => {
      const v = Number(input.value);
      if (!Number.isFinite(v) || v <= 0) return;
      tSetCtrl(deck, name, "tap_fire", { rate: v });
    };
    box.append(input, el("span", "tunit", "발/초"));
    // 커뮤니티는 10초당 발수(`N톡톡이`)로 부른다. 입력은 발/초로 받되 환산을 같이 적는다.
    const hint = el("span", "thint2", `≈ ${Math.round(rate * 10)}톡톡이`
      + (rate > TTAP_LIMIT ? " · 게임 하한(220ms)을 넘는 값이다" : ""));
    if (rate > TTAP_LIMIT) hint.classList.add("bad");
    box.append(hint);
    row.append(box);
  }
  return row;
}

function tPolicyRow(deck, name, key, label, policies, extra) {
  const row = tRow(label);
  const cur = tuneOf(deck, name)?.over.control?.[key];
  const picked = cur?.policy ?? policies[0][0];
  row.append(tModeChips(deck, name, key, () => ({ policy: picked, ...extra })));

  if (tCtrlMode(deck, name, key) === "on" && policies.length > 1) {
    const sel = el("select", "tsel");
    for (const [v, text] of policies) {
      const opt = el("option", null, text);
      opt.value = v;
      sel.append(opt);
    }
    sel.value = picked;
    sel.onchange = () => {
      // 홀드는 정책마다 권장 lead가 다르다 (`CONTROL.md §설정 스키마`).
      const lead = key === "hold" ? (sel.value === "own_full_burst" ? 0.5 : 0.1) : undefined;
      tSetCtrl(deck, name, key,
               lead == null ? { policy: sel.value } : { policy: sel.value, lead });
    };
    row.append(sel);
  }
  return row;
}

// ── 버스트 패턴 ─────────────────────────────────────────────────────────
// 슬롯 순서가 곧 우선순위지만, 그것만으로는 "3사이클마다 한 번만 쓴다"를 표현할 수 없다.
// 패턴은 후보에서 빼는 게 아니라 **뒤로 미는 것**이라(`timeline._pattern_rank`), 대신 쓸
// 사람이 없거나 쿨이면 그냥 예정대로 나간다 — 단계가 막히지 않는다.

function tBurstSection(deck, name) {
  const box = el("section", "tsec");
  box.append(el("h4", null, "버스트 패턴"));

  const layer = tLayer(name);
  const registered = Object.keys(layer.patterns ?? {});
  box.append(el("p", "tnote", layer.pattern
    ? `기본: ${layer.pattern} — 조합이 맞을 때만 걸린다`
    : "기본: 없음 — 슬롯 순서가 곧 우선순위다"));

  const b = tuneOf(deck, name)?.burst;
  const sel = el("select", "tsel wide");
  const opts = [
    ["", "기본 (레이어가 정한 대로)"],
    ...registered.map((p) => [`p:${p}`, `${p} (등록 패턴)`]),
    ["every", "n의 배수 사이클에 우선 사용"],
    ["skip", "가급적 안 씀"],
    ["none", "패턴 없음 (슬롯 순서대로)"],
  ];
  for (const [v, text] of opts) {
    const opt = el("option", null, text);
    opt.value = v;
    sel.append(opt);
  }
  sel.value = !b ? "" : b.mode === "pattern" ? `p:${b.name}` : b.mode;

  const every = tuneOf(deck, name)?.burst?.n ?? 3;
  sel.onchange = () => {
    const e = tEntry(deck, name);
    const v = sel.value;
    if (!v) delete e.burst;
    else if (v === "every") e.burst = { mode: "every", n: every };
    else if (v.startsWith("p:")) e.burst = { mode: "pattern", name: v.slice(2) };
    else e.burst = { mode: v };
    tCommit(deck, name);
  };
  box.append(sel);

  if (b?.mode === "every") {
    const row = el("div", "tsub");
    const input = el("input", "tnum");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.value = String(b.n);
    input.onchange = () => {
      const n = Math.max(1, Math.trunc(Number(input.value) || 1));
      tEntry(deck, name).burst = { mode: "every", n };
      tCommit(deck, name);
    };
    row.append(el("span", "tunit", "매"), input, el("span", "tunit", "사이클마다 우선"));
    box.append(row);
  }
  if (b?.mode === "skip") {
    box.append(el("p", "thint2",
      "같은 단계의 다른 후보가 전부 쿨일 때만 나간다 — 단계가 막히지는 않는다."));
  }
  return box;
}

// ── 육성 ────────────────────────────────────────────────────────────────
// "이만큼 키우면 얼마나 오르나"를 그 자리에서 재는 손잡이다. 투자 효율 탭이 **다음 한 칸**을
// 재화당 효율로 줄 세우는 자리라면, 여기는 임의의 지점을 찍어 보는 자리다.
// 기본값이 곧 되돌리기 지점이라 선택지 첫 줄이 언제나 `기본 (지금 값)`이다.

function tGrowthSection(deck, name) {
  const box = el("details", "tsec tgrow");
  box.append(el("summary", null, "육성"));
  const body = el("div", "tgrid");
  const base = tBase(name);
  const over = tuneOf(deck, name)?.over ?? {};

  const btMax = COST?.breakthrough?.["돌파_최대"] ?? TBT_MAX;
  const ceMax = COST?.breakthrough?.["코어강화_최대"] ?? TCE_MAX;
  body.append(tNumSelect(deck, name, "돌파", ["breakthrough"],
                         over.breakthrough, base.breakthrough, 0, btMax));
  body.append(tNumSelect(deck, name, "코어강화", ["core_enhancement"],
                         over.core_enhancement, base.core_enhancement, 0, ceMax));

  for (const [k, label] of TSKILL_AXES) {
    body.append(tNumSelect(deck, name, label, ["skill_levels", k],
                           over.skill_levels?.[k], base.skill(k), 1, TSKILL_MAX));
  }

  for (const part of TPARTS) {
    const b = base.gear(part);
    const row = tNumSelect(deck, name, `장비 · ${part}`, ["equipment", part, "level"],
                           over.equipment?.[part]?.level, b, 0, TGEAR_MAX);
    // 강화 칸이 없는 부위(일반 T1~T9·미장착)는 다음 한 칸이 "장비를 구한다"라, 강화
    // 단계를 얹으면 있지도 않은 장비를 강화한 모습이 된다. 아예 막는다.
    if (b == null) {
      row.textContent = "";
      row.append(el("b", "tkey", `장비 · ${part}`));
      row.append(el("span", "thint2", "강화가 붙는 장비가 아니다 (일반 등급·미장착)"));
    }
    body.append(row);
  }

  body.append(tListSelect(deck, name, "소장품", ["collection_stage"],
                          over.collection_stage, base.collection_stage,
                          TSTAGES.map((s) => [s, s])));
  body.append(tNumSelect(deck, name, "애장품 단계", ["favorite_stage"],
                         over.favorite_stage, base.favorite_stage, 0, TFAV_MAX));
  body.append(tNumSelect(deck, name, "호감도", ["affinity"],
                         over.affinity, base.affinity, 1, TAFF_MAX));
  body.append(tCubeRow(deck, name, over, base));

  const opt = el("details", "tsec topt");
  opt.append(el("summary", null, "오버로드 옵션 수치"));
  const grid = el("div", "tgrid");
  for (const [k, label] of Object.entries(TOPT)) {
    grid.append(tOptRow(deck, name, k, label, over, base));
  }
  grid.append(el("p", "thint2",
    "합계 %를 그대로 적는다 — 줄 수가 아니다. 차지형이 아니면 차지 옵션은 안 걸린다."));
  opt.append(grid);
  box.append(body, opt);
  return box;
}

/** 정수 한 칸을 고르는 줄. 첫 옵션이 기준값이라 고르면 조정이 지워진다. */
function tNumSelect(deck, name, label, path, cur, base, lo, hi) {
  const opts = [];
  for (let v = lo; v <= hi; v++) opts.push([v, String(v)]);
  return tPick(deck, name, label, path, cur, base, opts);
}

const tListSelect = (deck, name, label, path, cur, base, opts) =>
  tPick(deck, name, label, path, cur, base, opts);

function tPick(deck, name, label, path, cur, base, opts) {
  const row = tRow(label);
  const sel = el("select", "tsel");
  const baseText = opts.find(([v]) => v === base)?.[1] ?? String(base);
  const head = el("option", null, `기본 (${baseText})`);
  head.value = "";
  sel.append(head);
  for (const [v, text] of opts) {
    if (v === base) continue;   // 기준값은 위 줄이 이미 들고 있다
    const opt = el("option", null, text);
    opt.value = String(v);
    sel.append(opt);
  }
  sel.value = cur == null ? "" : String(cur);
  sel.onchange = () => {
    const raw = sel.value;
    const v = raw === "" ? base : (typeof base === "number" ? Number(raw) : raw);
    tPut(deck, name, path, v, base);
  };
  row.append(sel);
  if (cur != null) row.classList.add("on");
  return row;
}

function tCubeRow(deck, name, over, base) {
  const row = tRow("하모니 큐브");
  const cur = over.cube;
  const cubeName = cur?.name ?? base.cube.name;
  const meta = TCUBES[cubeName];

  const pick = el("select", "tsel");
  for (const n of Object.keys(TCUBES)) {
    const opt = el("option", null, n);
    opt.value = n;
    pick.append(opt);
  }
  pick.value = cubeName;

  const lv = el("select", "tsel");
  for (const n of meta?.levels ?? [base.cube.level]) {
    const opt = el("option", null, `Lv${n}`);
    opt.value = String(n);
    lv.append(opt);
  }
  const level = cur?.level ?? base.cube.level;
  lv.value = String(level);

  const set = (nextName, nextLevel) => {
    const m = TCUBES[nextName];
    // 큐브를 바꾸면 그 큐브에 없는 레벨일 수 있다. 있는 것 중 가장 높은 것으로 내린다.
    const ok = m?.levels.includes(nextLevel) ? nextLevel : (m?.levels[0] ?? nextLevel);
    const same = nextName === base.cube.name && ok === base.cube.level;
    tPut(deck, name, ["cube"], same ? base.cube : { name: nextName, level: ok }, base.cube);
  };
  pick.onchange = () => set(pick.value, level);
  lv.onchange = () => set(cubeName, Number(lv.value));
  row.append(pick, lv);

  if (cur) row.classList.add("on");
  // 고유 효과가 계산에 안 들어가는 큐브는 그 사실을 숨기지 않는다. 스탯과 우월 코드는
  // 붙으므로 고르는 것 자체는 의미가 있고, 표시된 효과 수치만 결과에 안 잡힌다.
  if (meta?.unsupported) {
    row.append(el("span", "thint2 bad", `고유 효과 미반영 — ${meta.unsupported}`));
  }
  return row;
}

function tOptRow(deck, name, key, label, over, base) {
  const row = tRow(label);
  const b = tRound(base.opt(key));
  const cur = tSum(over.equip_skills?.[key]);
  const input = el("input", "tnum");
  input.type = "number";
  input.step = "0.01";
  input.min = "0";
  input.value = String(cur ?? b);
  input.onchange = () => {
    const v = Number(input.value);
    if (!Number.isFinite(v) || v < 0) return;
    tPut(deck, name, ["equip_skills", key], tRound(v), b);
  };
  row.append(input, el("span", "tunit", "%"));
  row.append(el("span", "thint2", `기본 ${b}%`));
  if (cur != null) row.classList.add("on");
  return row;
}

// ── 초기화 ──────────────────────────────────────────────────────────────
function initTune(roster) {
  TDEF = roster.defaultChar ?? null;
  TCUBES = roster.cubes ?? {};
  TSTAGES = roster.collectionStages ?? [];
  TPARTS = roster.parts ?? [];
  TOPT = roster.optionLabel ?? {};
  TAFF_MAX = roster.affinityMax ?? TAFF_MAX;
}
