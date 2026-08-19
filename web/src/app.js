// 편성 탭 — 드래그로 덱을 짜고, 다 짠 뒤 눌러서 계산한다.
// 계산은 worker.js가 한다. 여기서는 상태·드래그·큐만 다룬다.

const LS = { decks: "nikke.decks.v1", results: "nikke.results.v1", settings: "nikke.settings.v1" };
const CODES = ["", "작열", "수냉", "풍압", "전격", "철갑"];
const ELEMENT_ICON = {
  작열: "icn_element_fire.webp", 수냉: "icn_element_water.webp", 풍압: "icn_element_wind.webp",
  전격: "icn_element_elect.webp", 철갑: "icn_element_iron.webp",
};
const BURST_ICON = { 1: "icn_burst_01.webp", 2: "icn_burst_02.webp", 3: "icn_burst_03.webp" };

let ROSTER = [];
const byName = new Map();

const state = {
  settings: { code: "풍압", duration: 180 },
  decks: [],
  filter: { q: "", element: "all", burst: "all" },
};
let results = {};

// ── 저장 ────────────────────────────────────────────────────────────────
const load = (k, fallback) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; }
};
const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
const saveAll = () => {
  save(LS.decks, state.decks);
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

// 덱 지문 — 이름·순서·랩쳐·시간이 같으면 결과가 같다 (기대값 모드는 결정론적).
// 순서가 버스트 우선순위라서 순서까지 지문에 넣는다.
const fingerprint = (deck) =>
  JSON.stringify([deck.names, state.settings.code, state.settings.duration]);

const isFull = (deck) => deck.names.every(Boolean);
const resultOf = (deck) => (isFull(deck) ? results[fingerprint(deck)] : null);

// ── 덱 조작 ─────────────────────────────────────────────────────────────
function newDeck() {
  state.decks.push({ id: uid(), names: [null, null, null, null, null] });
  saveAll();
  renderDecks();
}

function place(name, deckId, idx) {
  const deck = state.decks.find((d) => d.id === deckId);
  if (!deck) return;
  const at = deck.names.indexOf(name);
  if (at === idx) return;
  const displaced = deck.names[idx];
  deck.names[idx] = name;
  if (at !== -1) deck.names[at] = displaced; // 같은 덱 안에서 옮기면 자리 교환
  saveAll();
  renderDecks();
}

function clearSlot(deckId, idx) {
  const deck = state.decks.find((d) => d.id === deckId);
  if (!deck) return;
  deck.names[idx] = null;
  saveAll();
  renderDecks();
}

// 솔로레이드는 덱 간 캐릭터 중복이 불가하다. 막지는 않고 표시만 한다 —
// 대안을 나란히 놓고 비교하는 중일 수 있어서다.
function duplicated() {
  const seen = new Map();
  for (const d of state.decks) {
    for (const n of d.names) {
      if (n) seen.set(n, (seen.get(n) ?? 0) + 1);
    }
  }
  return new Set([...seen].filter(([, c]) => c > 1).map(([n]) => n));
}

// ── 렌더 ────────────────────────────────────────────────────────────────
function renderDecks() {
  const wrap = $("#decks");
  wrap.textContent = "";
  const dup = duplicated();
  let sum = 0;
  let known = 0;

  state.decks.forEach((deck, i) => {
    const card = el("div", "deck");
    const head = el("div", "deck-head");
    head.append(el("span", "deck-no", `덱 ${i + 1}`));

    const res = resultOf(deck);
    const dmg = el("span", "deck-dmg");
    if (deck.calcState === "run") dmg.append(el("span", "spin"), el("span", null, " 계산 중"));
    else if (deck.calcState === "wait") dmg.textContent = "대기 중";
    else if (deck.error) { dmg.textContent = deck.error; dmg.classList.add("err"); }
    else if (res) { dmg.textContent = `${eok(res.total)}억`; dmg.classList.add("ok"); }
    else dmg.textContent = isFull(deck) ? "미계산" : "";
    head.append(dmg);

    if (res) { sum += res.total; known++; }

    const btn = el("button", "calc", res ? "재계산" : "계산");
    btn.disabled = !isFull(deck) || deck.calcState != null;
    btn.onclick = () => enqueue(deck.id, true);
    head.append(btn);

    const del = el("button", "icon-btn", "✕");
    del.title = "덱 삭제";
    del.onclick = () => {
      state.decks = state.decks.filter((d) => d.id !== deck.id);
      saveAll();
      renderDecks();
    };
    head.append(del);
    card.append(head);

    const slots = el("div", "slots");
    deck.names.forEach((name, idx) => {
      const slot = el("div", "slot");
      slot.dataset.deck = deck.id;
      slot.dataset.idx = idx;
      if (name) {
        slot.classList.add("filled");
        if (dup.has(name)) slot.classList.add("dup");
        slot.append(thumb(name));
        const x = el("button", "slot-x", "✕");
        x.onclick = (e) => { e.stopPropagation(); clearSlot(deck.id, idx); };
        slot.append(x);
        slot.addEventListener("pointerdown", (e) => startDrag(e, name, { deckId: deck.id, idx }));
      } else {
        slot.append(el("span", "slot-no", idx + 1));
      }
      slots.append(slot);
    });
    card.append(slots);

    if (res?.notes) card.append(el("div", "notes", res.notes));
    wrap.append(card);
  });

  const add = el("button", "add-deck", "+ 덱 추가");
  add.onclick = newDeck;
  wrap.append(add);

  $("#sum").textContent = known
    ? `계산된 ${known}덱 합계 ${eok(sum)}억`
    : "덱을 짜고 계산을 누르세요";
  $("#dup-warn").textContent = dup.size ? `덱 간 중복: ${[...dup].join(" · ")}` : "";
}

function thumb(name) {
  const rec = byName.get(name);
  const box = el("div", "thumb");
  if (rec?.img) {
    const img = el("img");
    img.src = `image/${rec.img}`;
    img.alt = name;
    img.loading = "lazy";
    box.append(img);
  } else {
    box.append(el("span", "noimg", name.slice(0, 2)));
  }
  const badges = el("div", "badges");
  if (BURST_ICON[rec?.burst]) badges.append(badge(BURST_ICON[rec.burst]));
  if (ELEMENT_ICON[rec?.element]) badges.append(badge(ELEMENT_ICON[rec.element]));
  box.append(badges);
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
  const { q, element, burst } = state.filter;
  const needle = q.trim();

  const list = ROSTER.filter((r) =>
    (element === "all" || r.element === element) &&
    (burst === "all" || r.burst === burst) &&
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

function onDragEnd(e) {
  if (!drag) return;
  document.removeEventListener("pointermove", onDragMove);
  const { name, from, target, moved } = drag;
  target?.classList.remove("over");
  drag.ghost.remove();
  drag = null;

  if (target) {
    place(name, target.dataset.deck, Number(target.dataset.idx));
  } else if (from && moved) {
    clearSlot(from.deckId, from.idx); // 슬롯 밖으로 끌어내면 비운다
  }
}

// ── 계산 큐 ─────────────────────────────────────────────────────────────
const worker = new Worker("worker.js");
let ready = false;
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

  const deck = state.decks.find((d) => d.id === data.id);
  if (deck) {
    deck.calcState = null;
    if (data.type === "done") {
      deck.error = null;
      results[fingerprint(deck)] = data.result;
    } else {
      deck.error = data.error;
    }
  }
  running = null;
  saveAll();
  renderDecks();
  pump();
};

function enqueue(deckId, force = false) {
  const deck = state.decks.find((d) => d.id === deckId);
  if (!deck || !isFull(deck)) return;
  if (!force && resultOf(deck)) return;
  if (deck.calcState) return;

  deck.calcState = "wait";
  deck.error = null;
  queue.push(deckId);
  renderDecks();
  pump();
}

async function pump() {
  if (!ready || running || !queue.length) {
    if (!queue.length && !running) releaseWake();
    return;
  }
  const deckId = queue.shift();
  const deck = state.decks.find((d) => d.id === deckId);
  if (!deck) return pump();

  await acquireWake();
  running = deckId;
  deck.calcState = "run";
  renderDecks();
  worker.postMessage({
    id: deckId,
    names: deck.names,
    code: state.settings.code,
    duration: state.settings.duration,
  });
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

// ── 초기화 ──────────────────────────────────────────────────────────────
function bindBar() {
  const sel = $("#code");
  for (const c of CODES) {
    const o = el("option", null, c || "속성 없음");
    o.value = c;
    sel.append(o);
  }
  sel.value = state.settings.code;
  sel.onchange = () => { state.settings.code = sel.value; saveAll(); renderDecks(); };

  const dur = $("#duration");
  dur.value = state.settings.duration;
  dur.onchange = () => {
    state.settings.duration = Number(dur.value) || 180;
    saveAll();
    renderDecks();
  };

  $("#q").oninput = (e) => { state.filter.q = e.target.value; renderPool(); };

  for (const btn of document.querySelectorAll("#filters button")) {
    btn.onclick = () => {
      const { key, val } = btn.dataset;
      state.filter[key] = val;
      for (const b of document.querySelectorAll(`#filters button[data-key="${key}"]`)) {
        b.classList.toggle("on", b === btn);
      }
      renderPool();
    };
  }

  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => {
      for (const t of document.querySelectorAll(".tab")) t.classList.toggle("on", t === tab);
      for (const p of document.querySelectorAll(".panel")) {
        p.hidden = p.dataset.panel !== tab.dataset.tab;
      }
    };
  }
}

async function main() {
  Object.assign(state.settings, load(LS.settings, {}));
  state.decks = load(LS.decks, []);
  results = load(LS.results, {});
  for (const d of state.decks) { d.calcState = null; d.error = null; }
  if (!state.decks.length) newDeck();

  const data = await (await fetch("roster.json")).json();
  ROSTER = data.chars;
  for (const r of ROSTER) byName.set(r.name, r);

  bindBar();
  renderPool();
  renderDecks();
}

main();
