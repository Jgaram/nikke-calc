// 편성 탭 — 드래그로 덱을 짜고, 다 짠 뒤 눌러서 계산한다.
// 계산은 worker.js가 한다. 여기서는 상태·드래그·큐만 다룬다.

const LS = {
  decks: "nikke.decks.v1", results: "nikke.results.v1", settings: "nikke.settings.v1",
  profile: "nikke.profile.v1",
};
// 속성 아이콘은 색 있는 육각형을 쓴다 (icn_element_*.webp는 흰 실루엣이라 밝은 배경에서 안 보인다)
const ELEMENT_ICON = {
  작열: "icon-code-fire.png", 수냉: "icon-code-water.png", 풍압: "icon-code-wind.png",
  전격: "icon-code-electronic.png", 철갑: "icon-code-iron.png",
};
const BURST_ICON = {
  1: "icn_burst_01.webp", 2: "icn_burst_02.webp", 3: "icn_burst_03.webp",
  A: "icn_burst_all.webp",
};
// 풀 필터 행. 값 목록은 roster.json의 facets(정본 context/roster.py)에서 온다.
const FILTER_ROWS = [
  ["burst", "버스트"], ["element", "속성"], ["corp", "기업"],
  ["weapon", "무기군"], ["cls", "클래스"],
];

// 편성 탭의 세부 탭. 셋 다 **같은 편성안 한 벌**을 본다 — 짜고, 그 25명의 육성을 읽고,
// 무엇을 더 키울지 잰다. 뒤의 둘은 최상위 `육성` 탭이던 것을 여기로 들여온 것이다:
// 하는 말이 전부 "지금 고른 편성안"이라 밖에 두면 대상을 따로 붙잡아야 했다.
const DECK_SUBS = [["build", "편성"], ["status", "육성 상태"], ["eff", "투자 효율"]];
let deckSub = "build";

const DECKS_DEFAULT = 5;  // 솔로레이드는 5덱이다 (webapp-roadmap.md §1)
const BENCH_MAX = 15;
const DURATION = 180;     // 솔로레이드 제한시간. 고를 일이 없어 설정에서 뺐다
const CORE_PX_DEFAULT = 52; // 실측 코어 반경 26px (context/scenarios/명중률 탄착군.md)

let ROSTER = [];
let FACETS = {};
let ELEMENT_COLOR = {};
let WEAK = {};    // 랩쳐 코드 → 그 랩쳐에 우월한 니케 속성
let RAPTURE = {}; // 그 역 — 약점 속성 → 랩쳐 코드
const byName = new Map();

// 고르는 것은 약점 속성이지만, 계산기에 넘기는 것도 캐시 지문에 쓰는 것도 랩쳐 코드다.
// 편성안(plan)은 5덱 한 벌이다. 한 벌만 짜는 화면이 아니라 여러 벌을 놓고 고르는
// 화면이라, 중복 판정도 합계도 편성안 안에서만 센다.
// **약점 속성은 편성안에 딸린다** — 덱이 누구를 상대하느냐가 곧 그 편성안이 무엇인지다.
// settings.code는 새 편성안의 기본값(마지막에 고른 것)으로만 남는다.
const state = {
  settings: { code: "풍압", corePx: 0 }, // code = 랩쳐 속성 (약점 작열)
  plans: [],
  activeId: null,
  bench: [], // 후보 — 계산에 들어가지 않는 대기석. 편성안과 무관하게 하나만 둔다
  filter: { q: "", burst: "all", element: "all", corp: "all", weapon: "all", cls: "all" },
};
let results = {};

// 육성 프로필 — PC의 scraper/profile_fetch.py가 만든 `profiles/<이름>.json`을 파일로 받는다.
// 브라우저가 blablalink에서 직접 받을 길은 없다(webapp-roadmap.md §4). 파일은 이 브라우저를
// 떠나지 않는다. 스위치는 **앱 전역 하나**다 — 내 계정 상태는 하나뿐이다.
let profile = null;      // 파일 내용 그대로
let growth = null;       // 지문용 { key: Map(이름→해시), console: 해시 }
let profileNote = "";    // 칩 줄 아래 안내
let profileBad = false;  // 그 안내가 오류인가
let pendingPrune = "";   // 워커가 받아들이기를 기다리는 안내 문구 (§applyProfile)

// 랩쳐에 우월한 속성. 이 속성 니케는 초상화에 테두리로 표시한다.
const weakOf = (plan) => WEAK[plan?.code] ?? null;
const weakElement = () => weakOf(activePlan());

// ── 저장 ────────────────────────────────────────────────────────────────
const load = (k, fallback) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; }
};
const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
const saveAll = () => {
  save(LS.decks, { plans: state.plans, activeId: state.activeId, bench: state.bench });
  save(LS.settings, state.settings);
  save(LS.results, results);
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const uid = () => Math.random().toString(36).slice(2, 9);
const eok = (n) => (n / 1e8).toFixed(2);

// 덱 지문 — 이름·순서·랩쳐·시간·코어가 같으면 결과가 같다 (기대값 모드는 결정론적).
// 순서가 버스트 우선순위라서 순서까지 지문에 넣는다. 랩쳐와 코어는 편성안이 들고 있으므로
// 같은 덱이라도 편성안이 다르면 지문이 다르다 — 그래야 상대를 바꿔 재는 것이 된다.
//
// 프로필을 켜면 **그 덱 5명의 육성 상태**가 한 칸 더 붙는다. 프로필을 갱신했을 때 낡은
// 결과가 그대로 보이면 안 되기 때문이다. 프로필 전체를 해시로 잡으면 한 명만 키워도 25덱이
// 통째로 무효가 되어 폰에서 3분을 다시 돌려야 한다. 꺼져 있으면 지문 모양이 예전 그대로라
// 쌓아둔 고정 스펙 캐시가 살아남는다.
// 세부 조정(컨트롤·버스트·육성)이 걸린 덱은 **여섯 번째 칸**이 하나 더 붙는다. 조정이
// 없으면 예전 모양 그대로라 쌓아둔 캐시가 살아남고, 조정을 되돌리면 조정 전 결과가
// 그대로 돌아온다. 다섯 번째 칸은 언제나 육성 토큰 자리다 — 조정만 있고 프로필이
// 꺼져 있으면 `null`이 들어간다. 그 자리가 흔들리면 pruneResults가 남의 결과를 지운다.
const fpBase = (deck, plan) => {
  const base = [deck.names, plan.code, DURATION, plan.corePx];
  const g = profileOn() ? growthToken(deck.names) : null;
  const t = tuneToken(deck);
  if (!t) return g ? [...base, g] : base;
  return [...base, g, t];
};
const fingerprint = (deck, plan) => JSON.stringify(fpBase(deck, plan));

const isFull = (deck) => deck.names.every(Boolean);
const resultOf = (deck, plan) => (isFull(deck) ? results[fingerprint(deck, plan)] : null);

const planOf = (id) => state.plans.find((p) => p.id === id);
const activePlan = () => planOf(state.activeId) ?? state.plans[0];
// 계산 큐가 도는 중에 편성안을 바꿔도 결과가 제 덱을 찾아가야 한다 — 전 편성안을 뒤진다.
// 결과를 캐시에 넣으려면 그 덱이 어느 편성안 것인지(=어느 랩쳐인지)까지 알아야 한다.
const locate = (id) => {
  for (const plan of state.plans) {
    const deck = plan.decks.find((d) => d.id === id);
    if (deck) return { plan, deck };
  }
  return null;
};
const deckOf = (id) => locate(id)?.deck ?? null;

// ── 덱 조작 ─────────────────────────────────────────────────────────────
function newDeck(plan) {
  const deck = { id: uid(), names: [null, null, null, null, null] };
  plan.decks.push(deck);
  return deck;
}

// ── 편성안 ──────────────────────────────────────────────────────────────
// 개수 상한은 두지 않는다. 저장되는 것은 이름 배열뿐이고, 결과 캐시가 덱 지문으로
// 잡혀 있어 편성안이 늘어도 계산량이 늘지 않는다.
function uniqueName(base) {
  const taken = new Set(state.plans.map((p) => p.name));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base} ${i}`)) return `${base} ${i}`;
}

// 상대(약점·코어)는 편성안이 들고 있다. 새 벌은 지금 보고 있는 벌의 상대를 물려받는다 —
// 대개는 같은 보스를 두고 다른 편성을 시험하는 것이라서다.
function newPlan(name, decks, from) {
  const plan = {
    id: uid(), name, decks: decks ?? [],
    code: from?.code ?? state.settings.code,
    corePx: from?.corePx ?? state.settings.corePx ?? 0,
  };
  state.plans.push(plan);
  return plan;
}

function addPlan() {
  const plan = newPlan(uniqueName(`편성 ${state.plans.length + 1}`), null, activePlan());
  while (plan.decks.length < DECKS_DEFAULT) newDeck(plan);
  state.activeId = plan.id;
  saveAll();
  render();
}

// 복제가 이 화면의 핵심이다. 덱 지문이 같으면 결과도 같으므로(§fingerprint) 복제한
// 편성안은 계산 없이 딜량이 그대로 따라오고, 손댄 덱만 다시 돌리면 된다.
// 상대도 함께 복제한다 — 같은 편성으로 다른 약점·코어를 재보려면 복제 뒤에 그것만 바꾼다.
function clonePlan(plan) {
  // 세부 조정도 함께 복제한다. 조정은 "이 덱에서 이 니케를 이렇게 굴린다"라 덱에 딸린
  // 값이고, 복제한 벌에서 두 덱만 바꿔 보는 것이 이 화면의 쓰임이다.
  const decks = plan.decks.map((d) => ({
    id: uid(), names: [...d.names], tune: structuredClone(d.tune ?? {}),
  }));
  state.activeId = newPlan(uniqueName(`${plan.name} 사본`), decks, plan).id;
  saveAll();
  render();
}

// 이름 고치기는 드롭다운 자리를 입력 칸으로 바꿔서 한다. prompt는 폰에서 투박하고
// 브라우저에 따라 아예 막힌다.
let renaming = false;

function startRename() {
  const input = $("#plan-name");
  openList(false); // 목록이 열린 채로 이름 칸이 뜨면 무엇을 고치는지가 흐려진다
  input.value = activePlan().name;
  input.hidden = false;
  $("#plan-pick").hidden = true;
  $(".pick-wrap").classList.add("editing");
  $("#plan-rename").textContent = "확인";
  renaming = true;
  input.focus();
  input.select();
}

function endRename(commit) {
  if (!renaming) return;
  renaming = false;
  const input = $("#plan-name");
  const name = input.value.trim();
  input.hidden = true;
  $("#plan-pick").hidden = false;
  $(".pick-wrap").classList.remove("editing");
  $("#plan-rename").textContent = "이름";

  const plan = activePlan();
  if (commit && name && name !== plan.name) {
    plan.name = uniqueName(name); // 같은 이름이 이미 있으면 뒤에 번호가 붙는다
    saveAll();
  }
  render();
}

function deletePlan(plan) {
  if (state.plans.length <= 1) return; // 최소 한 벌은 남긴다
  const filled = plan.decks.some((d) => d.names.some(Boolean));
  if (filled && !confirm(`"${plan.name}"을 삭제할까요?`)) return;
  state.plans = state.plans.filter((p) => p.id !== plan.id);
  if (!planOf(state.activeId)) state.activeId = state.plans[0].id;
  saveAll();
  render();
}

function planStats(plan) {
  let sum = 0, known = 0, pending = 0, busy = 0;
  for (const deck of plan.decks) {
    if (deck.calcState) busy++;
    const res = resultOf(deck, plan);
    if (res) sum += res.total;
    if (res) known++;
    else if (isFull(deck) && !deck.calcState) pending++;
  }
  return { sum, known, pending, busy, count: plan.decks.length };
}

// ── 슬롯 이동 ───────────────────────────────────────────────────────────
// 덱 슬롯과 후보 칸을 같은 규칙으로 다룬다. ref는 {kind:"deck",deckId,idx} 또는
// {kind:"bench",idx}, 풀에서 끌어온 것이면 null이다.
function slotName(ref) {
  if (!ref) return null;
  if (ref.kind === "bench") return state.bench[ref.idx] ?? null;
  return deckOf(ref.deckId)?.names[ref.idx] ?? null;
}

function setDeckSlot(ref, name) {
  const deck = deckOf(ref.deckId);
  if (deck) deck.names[ref.idx] = name ?? null;
}

// 후보 칸은 빈자리를 남기지 않는다 — 항상 앞에서부터 채운다.
function setBenchSlot(idx, name) {
  if (idx < state.bench.length) {
    if (name) state.bench[idx] = name;
    else state.bench.splice(idx, 1);
  } else if (name && state.bench.length < BENCH_MAX) {
    state.bench.push(name);
  }
}

function firstEmptyRef(plan = activePlan()) {
  for (const deck of plan.decks.slice(0, DECKS_DEFAULT)) {
    const idx = deck.names.findIndex((name) => !name);
    if (idx !== -1) return { kind: "deck", deckId: deck.id, idx };
  }
  return null;
}

// 풀에서 짧게 누르면 1번 덱 1번 칸부터 빈자리를 채우고,
// 25자리가 다 찬 뒤에만 후보를 만든다. 이미 올라간 니케는 중복 추가하지 않는다.
function autoPlace(name) {
  const plan = activePlan();
  if (plan.decks.some((deck) => deck.names.includes(name)) || state.bench.includes(name)) return false;
  const ref = firstEmptyRef(plan);
  if (ref) setDeckSlot(ref, name);
  else if (state.bench.length < BENCH_MAX) state.bench.push(name);
  else return false;
  saveAll();
  render();
  return true;
}

function drop(name, from, to) {
  if (to.kind === "bench" && from?.kind === "bench") {
    // 후보 안에서 옮기면 순서만 바꾼다
    const [x] = state.bench.splice(from.idx, 1);
    state.bench.splice(Math.min(to.idx, state.bench.length), 0, x);
    return true;
  }

  let displaced;
  if (to.kind === "bench") {
    const at = Math.min(to.idx, state.bench.length);
    const dupAt = state.bench.indexOf(name);
    if (dupAt !== -1 && dupAt !== at) return false; // 후보에는 같은 니케를 두 번 두지 않는다
    if (at === state.bench.length && state.bench.length >= BENCH_MAX) return false;
    displaced = state.bench[at] ?? null;
    if (displaced === name) return false;
    setBenchSlot(at, name);
  } else {
    displaced = slotName(to);
    if (displaced === name) return false;
    setDeckSlot(to, name);
  }

  // 출발지에는 밀려난 니케가 들어간다 (교환). 풀에서 끌어왔다면
  // 밀려난 니케를 후보로 보존한다. 후보도 찼 경우에는 교체 자체를 막는다.
  if (from) {
    if (from.kind === "bench") setBenchSlot(from.idx, displaced);
    else setDeckSlot(from, displaced);
  } else if (displaced) {
    const empty = firstEmptyRef();
    if (empty) {
      setDeckSlot(empty, displaced);
    } else if (state.bench.includes(displaced) || state.bench.length >= BENCH_MAX) {
      if (to.kind === "bench") setBenchSlot(to.idx, displaced);
      else setDeckSlot(to, displaced);
      return false;
    } else {
      state.bench.push(displaced);
    }
  }
  return true;
}

function clearSlot(ref) {
  if (ref.kind === "bench") state.bench.splice(ref.idx, 1);
  else setDeckSlot(ref, null);
  sweepTunes(activePlan());
  saveAll();
  render();
}

// 덱을 떠난 니케의 세부 조정을 버린다. 조정은 이름으로 잡혀 있어 슬롯 순서를 바꿔도
// (= 버스트 우선순위를 바꿔도) 그대로 따라오지만, 덱에서 아예 빠지면 남을 이유가 없다 —
// 남겨두면 저장소에 쌓이기만 하고, 나중에 그 니케를 다시 넣었을 때 잊고 있던 조정이
// 조용히 되살아난다.
function sweepTunes(plan) {
  for (const deck of plan.decks) {
    if (!deck.tune) continue;
    for (const name of Object.keys(deck.tune)) {
      if (!deck.names.includes(name)) delete deck.tune[name];
    }
    if (!Object.keys(deck.tune).length) delete deck.tune;
  }
}

// 솔로레이드는 덱 간 캐릭터 중복이 불가하다. 막지는 않고 표시만 한다 —
// 대안을 나란히 놓고 비교하는 중일 수 있어서다. 후보는 편성이 아니므로 세지 않는다.
// 편성안이 다르면 같은 니케를 써도 중복이 아니다 — 실제로 낼 편성은 그중 한 벌뿐이다.
function duplicated(plan) {
  const seen = new Map();
  for (const d of plan.decks) {
    for (const n of d.names) {
      if (n) seen.set(n, (seen.get(n) ?? 0) + 1);
    }
  }
  return new Set([...seen].filter(([, c]) => c > 1).map(([n]) => n));
}

function moveDeck(plan, from, to) {
  if (to < 0 || to >= plan.decks.length || from === to) return;
  const [deck] = plan.decks.splice(from, 1);
  plan.decks.splice(to, 0, deck);
  saveAll();
  render();
}

function openDeckPopup(deck, kind, notes = "") {
  document.querySelector(".deck-dialog")?.remove();
  const dialog = el("dialog", "deck-dialog");
  const head = el("div", "dialog-head");
  head.append(el("b", null, kind === "tune" ? "조정" : "특이사항"));
  const close = el("button", "icon-btn", "✕");
  close.title = "닫기";
  close.onclick = () => dialog.close();
  head.append(close);
  dialog.append(head);
  if (kind === "tune") {
    const body = el("div", "tune-body");
    dialog.append(body);
    if (TDEF) tFillDeck(body, deck);
    else body.append(el("p", "thint2", "roster.json이 낡았다 — `python web/build.py`로 다시 빌드한다."));
  } else {
    dialog.append(el("div", "notes-body", notes));
  }
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
  document.body.append(dialog);
  dialog.showModal();
}

// ── 렌더 ────────────────────────────────────────────────────────────────
// 편성 탭은 드래그 한 번에도 통째로 다시 그리는 화면이다. 세부 탭 셋을 전부 그리면
// 덱 그리드(1200노드)와 육성 목록(25행)이 매번 같이 딸려 오므로, 보이는 것만 그린다.
function render() {
  renderPlans();
  if (deckSub === "build" && !$('[data-panel="deck"]').hidden) {
    renderDecks();
    renderCompare();
    renderBench();
  }
  // 육성 화면들은 편성안·프로필·결과 캐시를 그대로 읽으므로 여기서 같이 갱신한다.
  // 숨어 있는 것은 growth.js가 걸러 낸다.
  renderGrowth();
}

function renderDeckSubs() {
  const wrap = $("#deck-subs");
  wrap.textContent = "";
  for (const [key, label] of DECK_SUBS) {
    const b = el("button", key === deckSub ? "gtab on" : "gtab", label);
    b.onclick = () => selectDeckSub(key);
    wrap.append(b);
  }
  for (const [key] of DECK_SUBS) $(`#d-${key}`).hidden = key !== deckSub;
}

function selectDeckSub(key) {
  deckSub = key;
  state.settings.deckSub = key;
  saveAll();
  renderDeckSubs();
  render();
}

// 무엇을 기준으로 계산할지는 **여기서** 고른다. 딜량이 나오는 화면이 곧 그 선택이
// 걸리는 화면이라, 기준을 보는 자리와 고르는 자리가 갈리면 안 된다.
// `내 니케` 탭은 프로필 **파일**을 다루고(넣기·지우기), 이 줄은 그 파일을 계산에 쓸지를
// 다룬다 — 손잡이가 겹치지 않는다.
function renderBaseline() {
  const box = $("#baseline");
  box.textContent = "";
  const on = profileOn();
  box.classList.toggle("on", on);

  box.append(el("span", "bdot"));
  box.append(el("b", "blabel", "기준"));

  if (profile) {
    const chips = el("span", "chips");
    chips.append(chip("기본 스펙", !on, () => toggleProfile(false)));
    chips.append(chip("내 육성", on, () => toggleProfile(true)));
    box.append(chips);
    const when = String(profile._meta?.fetched_at ?? "").slice(0, 10);
    if (on && when) box.append(el("span", "btext", `수집 ${when}`));
  } else {
    box.append(el("span", "btext", "기본 스펙 — 3돌 · 스킬 10/10/10 · 5강 · SR15"));
    const go = el("button", "mini", "내 육성 넣기");
    go.onclick = () => selectTab("mine");
    box.append(go);
  }
}

// 한 벌을 한 줄로 — 약점 아이콘 · 이름 · 합계. 닫힌 자리와 목록이 같은 모양을 쓴다.
// 계산 진행(n/m)과 코어는 넣지 않는다. 그것을 보는 자리는 비교표다.
function planFace(plan, s) {
  const box = document.createDocumentFragment();
  const weak = weakOf(plan);
  if (weak) box.append(elementIcon(weak));
  box.append(el("span", "pname", plan.name));
  if (s.known) box.append(el("span", "pdmg", `${eok(s.sum)}억`));
  return box;
}

function renderPlans() {
  const active = activePlan();
  const pick = $("#plan-pick");
  pick.textContent = "";
  pick.append(planFace(active, planStats(active)));

  const list = $("#plan-list");
  list.textContent = "";
  for (const plan of state.plans) {
    const item = el("button", plan.id === active.id ? "plan-item on" : "plan-item");
    item.type = "button";
    item.setAttribute("role", "option");
    item.append(planFace(plan, planStats(plan)));
    item.onclick = () => { openList(false); selectPlan(plan.id); };
    list.append(item);
  }
  list.hidden = !listOpen;
  $("#plan-del").disabled = state.plans.length <= 1;
}

let listOpen = false;

function openList(open = !listOpen) {
  listOpen = open;
  $("#plan-list").hidden = !open;
  $("#plan-pick").setAttribute("aria-expanded", String(open));
  $(".pick-wrap").classList.toggle("open", open);
}

function selectPlan(id) {
  if (state.activeId === id) return;
  const before = activePlan().code;
  state.activeId = id;
  saveAll();
  // 설정 바는 편성안의 값을 비추므로 항상 다시 그린다. 풀은 약점이 바뀔 때만 —
  // 200장짜리 그리드라 테두리가 그대로면 다시 그릴 이유가 없다.
  renderBar();
  if (activePlan().code !== before) renderPool();
  render();
}

function renderDecks() {
  const wrap = $("#decks");
  wrap.textContent = "";
  const plan = activePlan();
  const dup = duplicated(plan);
  let sum = 0;
  let known = 0;

  plan.decks.forEach((deck, i) => {
    const card = el("div", "deck");
    const head = el("div", "deck-head");
    const order = el("span", "deck-order");
    const up = el("button", "icon-btn order-btn", "▲");
    up.title = "이 덱을 위로";
    up.disabled = i === 0;
    up.onclick = () => moveDeck(plan, i, i - 1);
    const down = el("button", "icon-btn order-btn", "▼");
    down.title = "이 덱을 아래로";
    down.disabled = i === plan.decks.length - 1;
    down.onclick = () => moveDeck(plan, i, i + 1);
    order.append(up, down);
    head.append(order);

    const res = resultOf(deck, plan);
    const dmg = el("span", "deck-dmg");
    if (deck.calcState === "run") dmg.append(el("span", "spin"), el("span", null, " 계산 중"));
    else if (deck.calcState === "wait") dmg.textContent = "대기 중";
    else if (deck.error) { dmg.textContent = deck.error; dmg.classList.add("err"); }
    else if (res) { dmg.textContent = `${eok(res.total)}억`; dmg.classList.add("ok"); }
    else dmg.textContent = isFull(deck) ? "미계산" : "";
    head.append(dmg);

    const tune = el("button", "deck-tool", "조정");
    if (tActive(deck).length) tune.classList.add("on");
    tune.onclick = () => openDeckPopup(deck, "tune");
    head.append(tune);

    if (res?.notes) {
      const notes = el("button", "deck-tool warn", "특이사항");
      notes.onclick = () => openDeckPopup(deck, "notes", res.notes);
      head.append(notes);
    }

    if (res) { sum += res.total; known++; }

    const btn = el("button", "calc", res ? "재계산" : "계산");
    btn.disabled = !isFull(deck) || deck.calcState != null;
    btn.onclick = () => enqueue(deck.id, true);
    head.append(btn);

    card.append(head);

    const slots = el("div", "slots");
    deck.names.forEach((name, idx) => {
      const ref = { kind: "deck", deckId: deck.id, idx };
      slots.append(slot(ref, name, dup.has(name), idx + 1));
    });
    card.append(slots);

    wrap.append(card);
  });

  $("#sum").textContent = known
    ? `계산된 ${known}덱 합계 ${eok(sum)}억`
    : "덱을 짜고 계산을 누르세요";
  $("#dup-warn").textContent = dup.size ? `덱 간 중복: ${[...dup].join(" · ")}` : "";

  // 편성안을 복제하고 몇 덱만 바꾸면 남는 것은 그 몇 덱뿐이다. 하나씩 누르지 않게 한다.
  const { pending } = planStats(plan);
  const all = $("#calc-all");
  all.disabled = !pending;
  all.textContent = pending ? `전부 계산 (${pending})` : "전부 계산";
}

// 편성안별 합계를 한자리에 모은다 — 여러 벌을 재는 것이 이 화면의 목적이라,
// 탭을 옮겨 가며 숫자를 외우게 두지 않는다.
function renderCompare() {
  const body = $("#compare-body");
  body.textContent = "";
  const rows = state.plans.map((plan) => ({ plan, s: planStats(plan) }));
  // 다 계산된 편성안끼리만 최고를 가린다. 반쯤 계산된 벌은 합계가 작을 수밖에 없다.
  const done = rows.filter(({ s }) => s.count && s.known === s.count);
  const best = done.length ? Math.max(...done.map(({ s }) => s.sum)) : null;

  for (const { plan, s } of rows) {
    const row = el("button", "crow");
    if (plan.id === state.activeId) row.classList.add("on");
    row.onclick = () => selectPlan(plan.id);

    const full = s.count && s.known === s.count;
    row.append(el("span", "cmark", full && s.sum === best ? "★" : ""));
    // 상대가 다르면 합계를 나란히 놓는 것 자체가 의미가 달라진다. 줄마다 밝혀 둔다.
    // 약점은 드롭다운과 같은 아이콘을 같은 자리(이름 앞)에 둔다.
    const weak = weakOf(plan);
    row.append(weak ? elementIcon(weak) : el("span", "eicon"));
    row.append(el("span", "cname", plan.name));
    if (plan.corePx) row.append(el("span", "ccore", "코어"));
    if (duplicated(plan).size) row.append(el("span", "cdup", "중복"));
    row.append(el("span", "cprog", s.busy ? "계산 중" : `${s.known}/${s.count}`));

    const dmg = el("span", "cdmg", s.known ? `${eok(s.sum)}억` : "—");
    if (full) dmg.classList.add("ok");
    row.append(dmg);
    body.append(row);
  }
  $("#compare-on").textContent = ` · ${state.plans.length}벌`;
}

// 후보는 25자리가 다 찬 뒤 추가 선택이 있을 때만 보인다.
function renderBench() {
  const bench = $("#bench");
  bench.hidden = !state.bench.length;
  if (!state.bench.length) return;
  const wrap = $("#bench-slots");
  wrap.textContent = "";
  const rows = Math.min(BENCH_MAX, (Math.floor(state.bench.length / 5) + 1) * 5);
  for (let idx = 0; idx < rows; idx++) {
    wrap.append(slot({ kind: "bench", idx }, state.bench[idx] ?? null, false, ""));
  }
  $("#bench-count").textContent = `${state.bench.length} / ${BENCH_MAX}`;
}

function slot(ref, name, dup, placeholder) {
  const node = el("div", "slot");
  node.dataset.ref = JSON.stringify(ref);
  if (name) {
    node.classList.add("filled");
    if (dup) node.classList.add("dup");
    node.append(thumb(name));
    const x = el("button", "slot-x", "✕");
    x.onclick = (e) => { e.stopPropagation(); clearSlot(ref); };
    node.append(x);
    node.addEventListener("pointerdown", (e) => startDrag(e, name, ref));
  } else {
    node.append(el("span", "slot-no", placeholder));
  }
  return node;
}

function thumb(name) {
  const rec = byName.get(name);
  const box = el("div", "thumb");
  if (rec?.element && rec.element === weakElement()) {
    box.classList.add("adv");
    box.style.setProperty("--el", ELEMENT_COLOR[rec.element] ?? "var(--accent)");
  }
  if (rec?.img) {
    const img = el("img");
    img.src = `image/${rec.img}`;
    img.alt = name;
    img.loading = "lazy";
    box.append(img);
  } else {
    box.append(el("span", "noimg", name.slice(0, 2)));
  }
  // 좌상단은 버스트 단계만. 속성은 초상화를 가리지 않고 약점 테두리로 드러난다.
  const badges = el("div", "badges");
  if (BURST_ICON[rec?.burst]) badges.append(badge(BURST_ICON[rec.burst]));
  else if (rec) badges.append(el("span", "badge txt", rec.burst));
  if (badges.childElementCount) box.append(badges);
  return box;
}

function badge(file) {
  const img = el("img", "badge");
  img.src = `image/icon/${file}`;
  img.loading = "lazy";
  return img;
}

function renderPool() {
  const wrap = $("#pool");
  wrap.textContent = "";
  const f = state.filter;
  const needle = f.q.trim();

  const list = ROSTER.filter((r) =>
    FILTER_ROWS.every(([key]) => f[key] === "all" || r[key] === f[key]) &&
    (!needle || r.name.includes(needle)));

  for (const rec of list) {
    const card = el("figure", "pc");
    if (!rec.parsed) card.classList.add("dim");
    card.append(thumb(rec.name));
    card.append(el("figcaption", null, rec.name));
    if (rec.parsed) {
      card.addEventListener("pointerdown", (e) => startDrag(e, rec.name, null));
    } else {
      card.title = "스킬 미파싱 — 계산할 수 없다";
    }
    wrap.append(card);
  }
  $("#pool-count").textContent = `${list.length}명`;
}

// ── 드래그 (포인터 이벤트) ───────────────────────────────────────────────
// 터치에서 HTML5 DnD는 동작하지 않는다. 마우스·터치를 한 경로로 처리한다.
let drag = null;

function startDrag(e, name, from) {
  if (e.button != null && e.button !== 0) return;
  e.preventDefault();

  const ghost = el("div", "ghost");
  ghost.append(thumb(name));
  document.body.append(ghost);
  drag = { name, from, ghost, target: null, moved: false, x0: e.clientX, y0: e.clientY };
  moveGhost(e.clientX, e.clientY);

  document.addEventListener("pointermove", onDragMove);
  document.addEventListener("pointerup", onDragEnd, { once: true });
  document.addEventListener("pointercancel", onDragEnd, { once: true });
}

function moveGhost(x, y) {
  drag.ghost.style.transform = `translate(${x - 32}px, ${y - 32}px)`;
}

function onDragMove(e) {
  if (!drag) return;
  e.preventDefault();
  if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 6) drag.moved = true;
  moveGhost(e.clientX, e.clientY);

  const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest(".slot");
  if (hit !== drag.target) {
    drag.target?.classList.remove("over");
    hit?.classList.add("over");
    drag.target = hit;
  }
}

function onDragEnd() {
  if (!drag) return;
  document.removeEventListener("pointermove", onDragMove);
  const { name, from, target, moved } = drag;
  target?.classList.remove("over");
  drag.ghost.remove();
  drag = null;

  if (target) {
    if (!drop(name, from, JSON.parse(target.dataset.ref))) return;
  } else if (!from && !moved) {
    autoPlace(name);
    return;
  } else {
    // 빈 공간에 놓은 드래그는 취소다. 삭제는 슬롯의 ✕만 담당한다.
    return;
  }
  sweepTunes(activePlan());
  saveAll();
  render();
}

// ── 계산 큐 ─────────────────────────────────────────────────────────────
const worker = new Worker("worker.js");
let ready = false;
// 큐 항목은 **작업(job)**이다 — `{id, start(), done(data)}`. 편성 탭의 덱 계산과 육성
// 탭의 축 측정이 워커 하나를 나눠 쓰므로 큐도 하나여야 한다. 큐가 둘이면 서로를 굶긴다.
// `start()`는 던지기 직전에 불려 워커 메시지를 돌려주고, null이면 그 작업은 버린다.
const queue = [];
let running = null;
let wakeLock = null;

worker.onmessage = ({ data }) => {
  if (data.type === "ready") {
    ready = true;
    $("#status").textContent = `계산 준비됨 (${data.boot.toFixed(1)}초)`;
    pump();
    return;
  }
  if (data.type === "fatal") {
    $("#status").textContent = `계산기 로드 실패: ${data.error}`;
    return;
  }

  // 프로필 갈아끼우기 결과. 형식 검사는 워커의 spec.load_profile이 한다.
  // 받아들여졌을 때만 죽은 캐시를 지운다. 켜고 끄기로 오는 응답은 청소할 것이 없다.
  if (data.type === "profile") {
    if (!pendingPrune) return;
    const dead = pruneResults();
    note(pendingPrune + (dead ? ` · 낡은 결과 ${dead}건 정리` : ""));
    pendingPrune = "";
    saveAll();
    render();
    return;
  }
  if (data.type === "profile-error") {
    pendingPrune = "";   // 거절당했다 — 캐시는 그대로 둔다
    clearProfile(`프로필을 쓸 수 없다 — ${data.error}`, true);
    return;
  }

  const job = running;
  running = null;
  if (job && job.id === data.id) job.done(data);
  saveAll();
  pump();
};

// 덱 하나를 재는 작업. 지문은 **던질 때** 붙잡는다 — 계산이 도는 중에 약점·코어·육성
// 기준을 바꾸면 지문이 달라지므로, 돌아온 결과를 그때 다시 계산하면 다른 조건의 칸에
// 들어간다.
function deckJob(deckId) {
  let key = null;
  return {
    id: deckId,
    start() {
      const found = locate(deckId);
      if (!found) return null;
      const { plan, deck } = found;
      key = fingerprint(deck, plan);
      deck.calcState = "run";
      render();
      return {
        names: deck.names, code: plan.code, duration: DURATION, corePx: plan.corePx,
        ...tunePayload(deck),
      };
    },
    done(data) {
      const deck = deckOf(deckId);
      if (deck) {
        deck.calcState = null;
        if (data.type === "done") {
          deck.error = null;
          results[key] = data.result;
        } else {
          deck.error = data.error;
        }
      }
      render();   // 육성 화면의 기준선도 이 결과다 — render()가 같이 갱신한다
    },
  };
}

function enqueue(deckId, force = false) {
  const found = locate(deckId);
  if (!found) return;
  const { plan, deck } = found;
  if (!isFull(deck)) return;
  if (!force && resultOf(deck, plan)) return;
  if (deck.calcState) return;

  deck.calcState = "wait";
  deck.error = null;
  queue.push(deckJob(deckId));
  render();
  pump();
}

// 캐시가 있는 덱은 건너뛴다 (force=false). 복제한 편성안에서는 바뀐 덱만 돌아간다.
function calcAll() {
  for (const deck of activePlan().decks) enqueue(deck.id);
}

async function pump() {
  if (!ready || running || !queue.length) {
    if (!queue.length && !running) releaseWake();
    return;
  }
  const job = queue.shift();
  // 상대(랩쳐·코어)는 편성안이 들고 있다 — 큐가 도는 중에 다른 벌로 옮겨도 안 흔들린다.
  const msg = job.start();
  if (!msg) return pump();   // 사라진 덱 — 건너뛴다
  // **`running`은 await 앞에서 잡는다.** 한 턴에 여러 번 밀어 넣으면(전부 계산·캐릭터
  // 한 명의 축 여럿) pump가 그만큼 불리는데, 여기서 양보하는 사이에 다음 pump가 맨 위의
  // `running` 검사를 통과해 두 번째 작업까지 던져 버린다. 그러면 워커가 순서대로 답해도
  // `running`은 마지막 것 하나뿐이라 앞선 답이 주인을 못 찾고 버려진다.
  running = job;
  await acquireWake();
  worker.postMessage({ id: job.id, ...msg });
}

// 화면이 꺼지거나 앱을 전환하면 계산이 3배 이상 느려진다 (webapp-roadmap.md §5).
async function acquireWake() {
  if (wakeLock || !navigator.wakeLock) return;
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch { /* 미지원이면 무시 */ }
}
function releaseWake() {
  wakeLock?.release().catch(() => {});
  wakeLock = null;
}

// ── 육성 프로필 ─────────────────────────────────────────────────────────
const profileOn = () => !!(profile && growth && state.settings.profileOn);

// FNV-1a 32비트. 지문에 넣을 짧은 토큰만 있으면 되고, 충돌해도 같은 캐릭터의 다른 육성
// 상태끼리 부딪혀야 하므로 실질적으로 문제가 되지 않는다.
const hash32 = (s) => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(36);
};

// 키 순서와 무관하게 같은 값이면 같은 문자열. `_`로 시작하는 키는 뺀다 — 계산에 들어가지
// 않으므로(spec.deep_merge가 무시한다) `_unsynced`가 바뀌었다고 캐시를 버릴 이유가 없다.
const stable = (v) => {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v).filter((k) => !k.startsWith("_")).sort()
      .map((k) => `${JSON.stringify(k)}:${stable(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
};

function buildGrowth() {
  if (!profile) { growth = null; return; }
  const key = new Map();
  for (const [name, entry] of Object.entries(profile.chars ?? {})) {
    key.set(name, hash32(stable(entry)));
  }
  // 콘솔은 계정 단위라 모든 덱에 걸린다 — 오르면 전체가 무효인 게 맞다. 큐브 관찰치는
  // 계산에 들어가지 않으므로(경고에만 쓴다) 넣지 않는다.
  growth = { key, console: hash32(stable(profile._account?.console ?? null)) };
}

// 프로필에 없는 이름은 "0" — 미육성으로 계산되기 때문이다(spec.py `UNGROWN`).
// 나중에 영입해 프로필에 생기면 토큰이 저절로 바뀌어 그 덱만 무효가 된다.
const growthToken = (names) =>
  names.map((n) => growth.key.get(n) ?? "0").join(",") + "|" + growth.console;

// 지문이 갈리면 옛 결과는 사라지는 게 아니라 다른 키로 남는다. 프로필을 갱신할수록
// 저장소에 쌓이므로, 새로 넣을 때 지금 프로필과 안 맞는 것을 지운다. 고정 스펙 결과
// (원소 4개)는 건드리지 않는다 — 프로필과 무관하게 계속 맞는 값이다.
// 육성 탭의 축 측정(원소 6개)도 5번째 칸이 육성 토큰이라 같은 규칙으로 걸린다.
function pruneResults() {
  if (!growth) return 0;
  let dead = 0;
  for (const k of Object.keys(results)) {
    let parts = null;
    try { parts = JSON.parse(k); } catch { continue; }
    // 다섯 번째 칸이 육성 토큰이다. 없거나(고정 스펙, 원소 4개) `null`이면(세부 조정만
    // 걸린 고정 스펙 결과) 프로필과 무관한 값이라 계속 맞는다 — 건드리지 않는다.
    if (!Array.isArray(parts) || typeof parts[4] !== "string") continue;
    if (parts[4] !== growthToken(parts[0])) { delete results[k]; dead++; }
  }
  return dead;
}

function note(text, bad = false) {
  profileNote = text;
  profileBad = bad;
  renderProfile();
}

// 진짜 형식 검사는 워커의 `spec.load_profile`이 한다(로더는 정본 하나여야 한다).
// 그래서 낙관적으로 적용해 두고, 워커가 거절하면 `profile-error`에서 되돌린다.
// 죽은 캐시 청소는 **워커가 받아들인 뒤에** 한다. 거절당할 파일로 쌓아둔 결과를 버리면
// 되돌릴 방법이 없다.
function applyProfile(data, msg) {
  profile = data;
  buildGrowth();
  state.settings.profileOn = true;
  save(LS.profile, profile);
  saveAll();
  pendingPrune = msg;
  worker.postMessage({ type: "profile", text: JSON.stringify(profile) });
  note(msg);
  render();
}

function clearProfile(msg = "", bad = false) {
  profile = null;
  growth = null;
  state.settings.profileOn = false;
  localStorage.removeItem(LS.profile);
  saveAll();
  worker.postMessage({ type: "profile", text: "" });
  note(msg, bad);
  render();
}

// 끄기는 프로필을 버리지 않는다 — 같은 파일을 다시 고르게 하지 않으려고 들고만 있는다.
function toggleProfile(on) {
  if (on && !profile) return;
  state.settings.profileOn = on;
  saveAll();
  worker.postMessage({ type: "profile", text: on ? JSON.stringify(profile) : "" });
  note("");
  render();
}

async function importProfile(file) {
  let data = null;
  try {
    data = JSON.parse(await file.text());
  } catch {
    return note("JSON을 읽지 못했다 — 파일이 깨졌거나 다른 파일이다.", true);
  }
  if (!data || typeof data !== "object" || !data.chars) {
    return note("`chars`가 없다 — profile_fetch.py가 만든 `profiles/<이름>.json` 이어야 한다 "
                + "(`.raw.json`은 원시 응답이라 안 된다).", true);
  }
  applyProfile(data, `프로필을 불러왔다 (${file.name})`);
}

// 파일 자체에 대한 안내만 낸다. 수집 시각·작전 인원은 바로 아래 계정 카드가 말하고,
// 계산을 뭘로 돌릴지는 편성 탭의 기준 줄이 말한다.
function renderProfile() {
  $("#profile-del").hidden = !profile;
  $("#profile-pick").hidden = false;

  const info = $("#profile-info");
  info.classList.toggle("warn", profileBad);
  const lines = [];
  if (profileNote) lines.push(profileNote);
  if (!profile) {
    lines.push("PC에서 만든 profiles/me.json 을 고른다 · 파일은 이 브라우저에만 남는다");
  }
  info.textContent = lines.join("\n");
  renderBaseline(); // 편성 탭의 기준 줄이 프로필 유무를 함께 비춘다
}

// ── 설정 바 ─────────────────────────────────────────────────────────────
function elementIcon(code, cls = "eicon") {
  const img = el("img", cls);
  img.src = `image/icon/${ELEMENT_ICON[code]}`;
  img.alt = code;
  return img;
}

// label이 없으면 아이콘만 있는 칩이 된다 (버스트 단계는 로마자 이미지로만 쓴다).
function chip(label, on, onclick, icon) {
  const b = el("button", on ? "chip on" : "chip");
  if (icon) b.append(icon);
  if (label) b.append(el("span", null, label));
  b.onclick = onclick;
  return b;
}

// 버스트 아이콘은 흰 글리프라 밝은 배경에서 안 보인다 — 초상화 배지와 같은 어두운 판을 깐다.
function burstIcon(stage) {
  const box = el("span", "bicon");
  const img = el("img");
  img.src = `image/icon/${BURST_ICON[stage]}`;
  img.alt = `버스트 ${stage}`;
  box.append(img);
  return box;
}

// 고르는 것은 랩쳐 속성이 아니라 **약점 속성**이다 — 편성에서 쓰는 값이 그쪽이고,
// 랩쳐 속성까지 같이 보이면 매번 상성표를 거꾸로 짚게 된다.
function renderBar() {
  const codes = $("#code");
  codes.textContent = "";
  const weak = weakElement();
  for (const [e] of FACETS.element ?? []) {
    codes.append(chip(e, e === weak, () => setWeak(e), elementIcon(e)));
  }

  const corePx = activePlan().corePx;
  const core = $("#core");
  core.textContent = "";
  core.append(chip("없음", !corePx, () => setCore(0)));
  core.append(chip("있음", !!corePx, () => setCore(CORE_PX_DEFAULT)));
  const size = $("#core-size");
  size.hidden = !corePx;
  $("#core-px").value = corePx || CORE_PX_DEFAULT;
}

// 약점은 지금 보고 있는 편성안의 것이다. 바꾸면 그 편성안 덱들의 지문이 통째로 바뀌므로
// 딜량은 "그 랩쳐로 이미 돌려본 적이 있는" 덱만 남는다 — 캐시가 살아 있으면 즉시 돌아온다.
function setWeak(element) {
  const code = RAPTURE[element];
  if (!code) return;
  activePlan().code = code;
  state.settings.code = code; // 다음에 만들 편성안의 기본값
  saveAll();
  renderBar();
  renderPool(); // 약점 테두리가 바뀐다
  render();
}

// 코어도 상대의 성질이다 — 약점과 같이 편성안에 딸린다. 덱 순위를 뒤집는 축이라
// (같은 덱 17.1억 → 23.5억) 벌마다 따로 잡을 수 있어야 한다.
function setCore(px) {
  activePlan().corePx = px;
  state.settings.corePx = px; // 다음에 만들 편성안의 기본값
  saveAll();
  renderBar();
  render();
}

function renderFilters() {
  const wrap = $("#filters");
  wrap.textContent = "";
  for (const [key, label] of FILTER_ROWS) {
    const row = el("div", "frow");
    row.append(el("b", null, label));
    const add = (val, text) => {
      let icon = null;
      if (val !== "all") {
        if (key === "element") icon = elementIcon(val);
        else if (key === "burst" && BURST_ICON[val]) { icon = burstIcon(val); text = ""; }
      }
      row.append(chip(text, state.filter[key] === val, () => {
        state.filter[key] = val;
        renderFilters();
        renderPool();
      }, icon));
    };
    add("all", "전체");
    for (const [val, text] of FACETS[key] ?? []) add(val, text);
    wrap.append(row);
  }
  // 접어둔 상태가 기본이라, 걸려 있는 필터는 요약줄에 남겨야 한다
  const on = FILTER_ROWS
    .filter(([k]) => state.filter[k] !== "all")
    .map(([k]) => (FACETS[k] ?? []).find(([v]) => v === state.filter[k])?.[1] ?? state.filter[k]);
  $("#filter-on").textContent = on.length ? ` · ${on.join(" · ")}` : "";
}

// ── 초기화 ──────────────────────────────────────────────────────────────
function bindBar() {
  $("#core-px").onchange = (e) => {
    const px = Math.min(240, Math.max(1, Number(e.target.value) || CORE_PX_DEFAULT));
    setCore(px);
  };

  $("#q").oninput = (e) => { state.filter.q = e.target.value; renderPool(); };

  $("#profile-file").onchange = (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 같은 파일을 다시 골라도 change가 오게
    if (file) importProfile(file);
  };
  $("#profile-del").onclick = () => {
    if (!confirm("불러둔 육성 프로필을 지운다. 파일은 PC에 그대로 있다.")) return;
    clearProfile("프로필을 지웠다 — 기본 스펙으로 계산한다.");
  };

  $("#plan-pick").onclick = () => openList();
  // 목록 밖을 누르면 닫는다. 드래그도 pointerdown으로 시작하므로 여기서 먼저 걷어낸다.
  document.addEventListener("pointerdown", (e) => {
    if (listOpen && !e.target.closest(".plans")) openList(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && listOpen) openList(false); });
  $("#plan-add").onclick = addPlan;

  const name = $("#plan-name");
  // 편집 중에 확인 버튼을 누르면 blur가 먼저 확정할 수 있고, 그러면 이어지는 click이
  // 편집을 다시 연다. pointerdown은 blur보다 먼저 오므로 거기서 상태를 붙잡아 둔다
  // (포커스를 안 뺏는 브라우저와 뺏는 브라우저 양쪽에서 같게 동작한다).
  let wasRenaming = false;
  $("#plan-rename").onpointerdown = (e) => {
    wasRenaming = renaming;
    if (renaming) e.preventDefault();
  };
  $("#plan-rename").onclick = () => {
    if (wasRenaming || renaming) endRename(true);
    else startRename();
    wasRenaming = false;
  };
  name.onblur = () => endRename(true);
  name.onkeydown = (e) => {
    if (e.key === "Enter") endRename(true);
    else if (e.key === "Escape") endRename(false);
  };
  $("#plan-clone").onclick = () => clonePlan(activePlan());
  $("#plan-del").onclick = () => deletePlan(activePlan());
  $("#calc-all").onclick = calcAll;

  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => selectTab(tab.dataset.tab);
  }
}

// 숨어 있는 동안은 안 그리므로(§render), 탭을 열 때 밀린 갱신을 여기서 따라잡는다.
function selectTab(name) {
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("on", t.dataset.tab === name);
  }
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.dataset.panel !== name;
  render();
}

// 저장 형식이 배열(덱만) → {decks,bench} → {plans,activeId,bench} 순으로 바뀌었다.
// 옛 형식은 덱 한 벌짜리 편성안 하나로 감싼다.
function restore() {
  const saved = load(LS.decks, null);
  let decks = null;
  if (Array.isArray(saved)) decks = saved;
  else if (saved?.plans) {
    state.plans = saved.plans.filter((p) => p?.decks);
    state.activeId = saved.activeId;
    state.bench = (saved.bench ?? []).slice(0, BENCH_MAX);
  } else if (saved) {
    decks = saved.decks ?? [];
    state.bench = (saved.bench ?? []).slice(0, BENCH_MAX);
  }
  if (decks) newPlan("편성 1", decks);

  if (!state.plans.length) newPlan("편성 1");
  if (!planOf(state.activeId)) state.activeId = state.plans[0].id;
  for (const p of state.plans) {
    // 약점·코어가 전역 설정이던 시절의 편성안은 그 값을 물려받는다. 지문에서 두 값이
    // 놓이는 자리가 그대로라 쌓아둔 결과 캐시도 그대로 맞는다.
    p.code ??= state.settings.code;
    p.corePx ??= state.settings.corePx ?? 0;
    for (const d of p.decks) { d.calcState = null; d.error = null; }
  }

  // 편성안은 항상 최소 5덱을 갖는다. 예전에 추가한 덱은 데이터 손실을
  // 피하기 위해 지우지 않지만, 이제 화면에서 덱을 추가·삭제할 수는 없다.
  for (const plan of state.plans) {
    while (plan.decks.length < DECKS_DEFAULT) newDeck(plan);
  }
  state.settings.filled5 = true;

  // 지문에 코어가 붙기 전 결과는 코어 없이 돌린 것이다. 키만 옮기면 계속 쓸 수 있다.
  const moved = {};
  for (const [k, v] of Object.entries(results)) {
    let key = k;
    try {
      const parts = JSON.parse(k);
      if (Array.isArray(parts) && parts.length === 3) key = JSON.stringify([...parts, 0]);
    } catch { /* 알 수 없는 키는 그대로 둔다 */ }
    moved[key] = v;
  }
  results = moved;
}

async function main() {
  Object.assign(state.settings, load(LS.settings, {}));
  delete state.settings.duration;      // 전투 시간은 180초 고정이 됐다
  // 투자 효율이 편성 탭으로 들어오면서 대상은 활성 편성안이 됐다 — 따로 붙잡지 않는다
  delete state.settings.growthPlanId;
  delete state.settings.gplanOnly;     // `편성 25명만`은 이제 `육성 상태` 탭 자체다
  delete state.settings.gres;
  delete state.settings.gsort;         // `내 니케`는 이름순 하나다 — 남은 축은 육성 상태 몫
  if (DECK_SUBS.some(([k]) => k === state.settings.deckSub)) deckSub = state.settings.deckSub;
  results = load(LS.results, {});

  // 프로필은 지문에 들어가므로 restore()보다 먼저 복원해야 한다.
  profile = load(LS.profile, null);
  if (profile && !profile.chars) profile = null;   // 손상된 저장분
  if (!profile) state.settings.profileOn = false;
  buildGrowth();
  if (profileOn()) worker.postMessage({ type: "profile", text: JSON.stringify(profile) });

  restore();
  saveAll();

  // 비용표는 육성 탭만 쓰지만 같이 받는다 — 탭을 열 때 기다리게 하면 표가 반쯤 빈 채로
  // 먼저 뜬다. 없어도 앱은 돌아야 하므로(옛 빌드) 실패를 삼킨다.
  const [data, costData] = await Promise.all([
    (await fetch("roster.json")).json(),
    fetch("cost.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  ROSTER = data.chars;
  FACETS = data.facets ?? {};
  ELEMENT_COLOR = data.elementColor ?? {};
  WEAK = data.weak ?? {};
  RAPTURE = Object.fromEntries(Object.entries(WEAK).map(([code, weak]) => [weak, code]));
  // 속성 없는 랩쳐를 뺐다 — 그 코드를 들고 있던 편성안은 첫 속성으로 되돌린다
  if (!WEAK[state.settings.code]) state.settings.code = Object.keys(WEAK)[0];
  for (const p of state.plans) if (!WEAK[p.code]) p.code = state.settings.code;
  for (const r of ROSTER) byName.set(r.name, r);

  bindBar();
  initTune(data);
  initGrowth(data, costData);
  renderDeckSubs();
  renderProfile();
  renderBar();
  renderFilters();
  renderPool();
  render();
}

main();
