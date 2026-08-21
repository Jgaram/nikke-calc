// 육성 탭 — 내 육성 조회 + 투자 효율.
//
// app.js의 상태(state·results·profile)와 헬퍼(el·thumb·fingerprint…)를 그대로 쓴다.
// 최상위에서는 정의만 하고, app.js의 main()이 initGrowth()로 깨운다.
//
// 이 화면이 잡은 것 넷 —
//
//  1. **한 줄은 "다음 한 칸"이다.** 목표까지 한 번에 재는 것과는 순위가 갈린다. 예산이
//     작을수록 한계 효율이 맞고, 이미 만렙인 축은 줄 자체가 안 생겨 계산량도 준다.
//     한 칸의 뜻은 축마다 다르다 — 스킬은 1레벨, 돌파·코강은 1칸, 소장품은 다음
//     구간(5·10·15), 장비는 가장 낮은 부위 1강.
//  2. **재화를 하나로 합치지 않는다.** 메뉴얼·뽑기·관리키트·장비 경험치는 서로 대체되지
//     않아 한 단위로 접으면 순위가 거짓이 된다(cost/README.md). 그래서 순위표가
//     **재화별로** 갈린다.
//  3. **캐릭터 단위로만 큐에 넣는다.** 시뮬 1회가 폰에서 6~10초라 25명을 한 번에 돌리면
//     20분이 넘는다. 궁금한 캐릭터를 눌러야 그 캐릭터의 남은 축만 큐에 들어간다.
//  4. **기준선은 그 덱의 계산 딜량이다.** 로드맵 §3단계의 최종형은 실측 딜량 × (1+Δ%)인데
//     실측은 기록 탭(2단계)이 들고 올 값이라 아직 없다. Δ%는 그대로 맞고 억 단위 환산만
//     기준선을 갈아끼우면 되므로, 지금은 계산 딜량으로 두고 화면에 그 사실을 적는다.
//
// 오버로드 옵션(커스텀 모듈) 축은 **여기 없다.** 목표 도달이 아니라 최적정지 문제라
// 계산 구조가 다르고(webapp-roadmap.md §3단계), 정본은 `overload/`다.

let COST = null;          // cost.json — 정본은 cost/, 빌드가 구워 보낸다
let UNGROWN = {};         // 프로필에 없는 캐릭터의 육성 상태 (정본 context/spec.py)
let NO_ITEM = "없음";
let PARTS = [];
let OPTION_LABEL = {};

const SKILL_AXES = [["1", "1스킬"], ["2", "2스킬"], ["3", "버스트"]];
const STAGE_RE = /^(SR|R)(\d{1,2})$/;
// 대성공이 구간 끝으로 보내주므로 중간 레벨을 목표로 잡을 이유가 없다 (cost/README.md).
const COLLECTION_TARGETS = [5, 10, 15];
const GEAR_MAX = 5;
// 프로필은 T9 기업 장비와 오버로드 장비를 구분하지 못한다 — 인게임 API가 둘 다 같은
// tier로 주고, 구분은 붙어 있는 옵션에만 남는데 프로필의 옵션은 4부위 합산이다.
// 솔로레이드에 낼 25명은 사실상 전부 오버로드라 비싼 쪽으로 잡고 그 사실을 줄에 적는다.
const GEAR_TRACK = "오버로드";
const FAV_MAX = 3;

// 육성 탭 화면 상태. 저장은 state.settings에 얹어 간다.
const gv = { view: "mine", sort: "remain", q: "", planOnly: true, res: "all" };
let gError = "";
const gPending = new Set();   // 큐에 있거나 도는 중인 측정 키
let gseq = 0;

// ── 프로필 읽기 ─────────────────────────────────────────────────────────
// 프로필에 없는 이름은 계산도 화면도 미육성으로 본다 (context/spec.py `UNGROWN`).
const growthOf = (name) => profile?.chars?.[name] ?? UNGROWN;
const inProfile = (name) => !!profile?.chars?.[name];

const num = (v, dflt = 0) => (typeof v === "number" ? v : dflt);
const fmt = (n, digits = 0) =>
  n == null ? "—" : n.toLocaleString("ko-KR", { maximumFractionDigits: digits });

// ── 비용 ────────────────────────────────────────────────────────────────
// 전부 cost.json에서 온다. 여기서 표를 다시 적지 않는다 — 정본은 cost/tables.json이고
// `python -m cost.build`가 구운 것을 빌드가 그대로 싣는다.

/** 스킬 레벨 `level`에 **도달하는 데** 드는 메뉴얼 장수 (가중치 적용 = 사실상 노랑칩). */
function manualSheets(level) {
  const t = COST.skill_manual;
  const per = t["레벨도달비용"][String(level)];
  if (!per) return null;
  return Object.entries(t["가중치"]).reduce((s, [g, w]) => s + num(per[g]) * w, 0);
}

/** 장비 강화 `level`에 도달하는 데 드는 장비 경험치. */
const gearExp = (level) => COST.gear["레벨도달비용"][GEAR_TRACK]?.[String(level)] ?? null;

// ── 축 ──────────────────────────────────────────────────────────────────
// 축 하나 = {key, label, from, to, over, res, amount, ...}.
//   over   : 시뮬에 넘길 오버라이드. 프로필 뒤에 얹혀 그 칸만 움직인다
//   res    : 재화 이름. null이면 비용표가 없는 축이라 Δ만 낸다
//   amount : 그 재화의 소모량

function axesOf(name) {
  const e = growthOf(name);
  const out = [];

  const sk = e.skill_levels ?? {};
  for (const [k, label] of SKILL_AXES) {
    const lv = num(sk[k], 1);
    if (lv >= 10) continue;
    out.push({
      key: `sk${k}`, label, from: lv, to: lv + 1,
      over: { skill_levels: { [k]: lv + 1 } },
      res: COST.skill_manual["종류"][k], amount: manualSheets(lv + 1),
    });
  }

  // 돌파와 코어강화는 둘 다 캐릭터 한 장을 먹으므로 같은 재화다 (cost/README.md).
  // 코어강화는 돌파 3 이후에 해금되니 순서가 강제된다 — 다음 한 칸은 언제나 하나뿐이다.
  const bt = num(e.breakthrough);
  const ce = num(e.core_enhancement);
  const draw = { res: COST.breakthrough["재화"], amount: COST.breakthrough["칸당_뽑기"] };
  if (bt < COST.breakthrough["돌파_최대"]) {
    out.push({ key: "bt", label: "돌파", from: bt, to: bt + 1,
               over: { breakthrough: bt + 1 }, ...draw });
  } else if (ce < COST.breakthrough["코어강화_최대"]) {
    out.push({ key: "ce", label: "코어강화", from: ce, to: ce + 1,
               over: { core_enhancement: ce + 1 }, ...draw });
  }

  // 소장품 — **미장착에는 축이 없다.** 소장품 아이템 자체를 얻는 비용은 다루지 않기
  // 때문이다(cost/tables.json `_안다룸`). 0이 아니라 "모른다"라서 줄을 만들지 않는다.
  const stage = String(e.collection_stage ?? NO_ITEM);
  const m = STAGE_RE.exec(stage);
  if (m) {
    const [, grade, lvText] = m;
    const lv = Number(lvText);
    const target = COLLECTION_TARGETS.find((t) => t > lv);
    const row = target == null ? null : COST.collection[grade]?.[`${lv}->${target}`];
    if (row) {
      out.push({
        key: "col", label: "소장품", from: stage, to: `${grade}${target}`,
        over: { collection_stage: `${grade}${target}` },
        res: "관리키트", amount: row.points, kits: row.kits, expected: true,
        note: "대성공이 걸리는 축이라 기대값이다",
      });
    }
  }

  // 장비 강화 — **부위마다 한 축**이다. 강화가 붙는 장비(기업·오버로드)만 축이 된다.
  // T1~T9·미장착 부위의 다음 한 칸은 "장비를 구한다"라 재화가 잡히지 않는다.
  //
  // 가장 낮은 부위 하나로 접지 않는 이유: 다리는 공격력이 0이라(equipment_stats.json)
  // 딜에 거의 안 걸리는데 그래서 늘 가장 낮게 방치된다. 접으면 그 부위가 축을 영영
  // 차지해 정작 오르는 머리·팔이 화면에 못 나온다.
  for (const part of PARTS) {
    const lv = (e.equipment ?? {})[part]?.level;
    if (typeof lv !== "number" || lv >= GEAR_MAX) continue;
    out.push({
      key: `eq:${part}`, label: `장비 강화 · ${part}`, from: lv, to: lv + 1,
      over: { equipment: { [part]: { level: lv + 1 } } },
      res: COST.gear["재화"], amount: gearExp(lv + 1),
      note: `${GEAR_TRACK} 장비 기준 — 프로필이 T9 기업과 구분하지 못한다`,
    });
  }

  // 애장품 — `favorite_stage`가 적힌 캐릭터에만 붙는다(= 인게임에 애장품이 있는 캐릭터).
  // 단계가 스킬 판본을 바꿔 딜에 직접 걸리는데 비용표에는 없어서 Δ만 낸다.
  const fav = e.favorite_stage;
  if (inProfile(name) && typeof fav === "number" && fav < FAV_MAX) {
    out.push({ key: "fav", label: "애장품 단계", from: fav, to: fav + 1,
               over: { favorite_stage: fav + 1 }, res: null, amount: null,
               note: "비용표가 없다 — Δ만 낸다" });
  }
  return out;
}

// ── 측정 ────────────────────────────────────────────────────────────────
// 결과 캐시 키는 덱 지문 뒤에 "누구의 어느 오버라이드인가"를 한 칸 더 붙인 것이다.
// 편성 탭의 지문(원소 5개)과 길이가 갈리므로 서로 섞이지 않고, 5번째 칸이 그대로
// 육성 토큰이라 프로필을 갱신하면 app.js의 pruneResults()가 같이 걷어 간다.
const axisKey = (deck, plan, name, axis) => JSON.stringify([
  deck.names, plan.code, DURATION, plan.corePx, growthToken(deck.names),
  name + ":" + stable(axis.over),
]);

/** 재둔 값이 있으면 Δ를 낸다. 기준선이나 측정치가 없으면 null. */
function delta(deck, plan, name, axis) {
  const base = resultOf(deck, plan);
  const got = base && results[axisKey(deck, plan, name, axis)];
  if (!base || !got) return null;
  const dTotal = got.total - base.total;
  return {
    dTotal,
    pct: base.total ? (dTotal / base.total) * 100 : 0,
    dChar: num(got.chars?.[name]) - num(base.chars?.[name]),
    base: base.total,
  };
}

function queueAxis(deck, plan, name, axis) {
  const key = axisKey(deck, plan, name, axis);
  if (results[key] || gPending.has(key)) return false;
  gPending.add(key);
  queue.push({
    id: `g${++gseq}`,
    gkey: key,
    start() {
      // 큐에서 기다리는 동안 편성안이나 덱이 바뀌었을 수 있다. 지문이 달라졌으면
      // 그 측정은 이제 다른 조건의 것이라 버린다.
      if (!planOf(plan.id) || axisKey(deck, plan, name, axis) !== key) {
        gPending.delete(key);
        return null;
      }
      renderGrowth();
      return {
        names: deck.names, code: plan.code, duration: DURATION, corePx: plan.corePx,
        over: { [name]: axis.over },
      };
    },
    done(data) {
      gPending.delete(key);
      if (data.type === "done") results[key] = data.result;
      else gError = `${name} 측정 실패 — ${data.error}`;
      renderGrowth();
    },
  });
  return true;
}

function measureChar(deck, plan, name) {
  gError = "";
  let n = 0;
  for (const axis of axesOf(name)) if (queueAxis(deck, plan, name, axis)) n++;
  if (n) { renderGrowth(); pump(); }
}

function cancelChar(deck, plan, name) {
  const keys = new Set(axesOf(name).map((axis) => axisKey(deck, plan, name, axis)));
  for (let i = queue.length - 1; i >= 0; i--) {
    if (queue[i].gkey && keys.has(queue[i].gkey)) {
      gPending.delete(queue[i].gkey);
      queue.splice(i, 1);
    }
  }
  renderGrowth();
}

const charPending = (deck, plan, name) =>
  axesOf(name).filter((axis) => gPending.has(axisKey(deck, plan, name, axis))).length;

// ── 대상 편성안 ─────────────────────────────────────────────────────────
// 스냅샷을 뜨지 않고 편성안 id만 붙잡는다. 편성 탭에서 덱을 고치면 여기도 따라와야
// 하고(같은 덱을 두 벌로 들고 있으면 어느 쪽이 진짜인지 알 수 없다), 결과 캐시가
// 어차피 덱 지문에 매여 있어 스냅샷이 값을 하지 않는다.
const targetPlan = () => planOf(state.settings.growthPlanId) ?? null;

function takePlan() {
  state.settings.growthPlanId = activePlan().id;
  gError = "";
  saveAll();
  renderGrowth();
}

// ── 렌더 ────────────────────────────────────────────────────────────────
function renderGrowth() {
  const panel = document.querySelector('[data-panel="growth"]');
  if (!panel || panel.hidden) return;

  const locked = $("#g-locked");
  const mine = $("#g-mine");
  const eff = $("#g-eff");

  let block = "";
  if (!COST) block = "비용표(cost.json)를 받지 못했다 — `python web/build.py`로 다시 빌드한다.";
  else if (!profileOn()) {
    block = "육성 프로필이 필요하다.\n편성 탭 맨 위의 `기준`에서 PC의 profiles/me.json 을 "
          + "고르면 여기가 열린다.";
  }
  locked.hidden = !block;
  locked.textContent = block;
  mine.hidden = !!block || gv.view !== "mine";
  eff.hidden = !!block || gv.view !== "eff";
  for (const b of document.querySelectorAll(".gtab")) {
    b.classList.toggle("on", b.dataset.gtab === gv.view);
  }
  if (block) return;

  if (gv.view === "mine") renderMine();
  else renderEff();
}

// ── 내 육성 ─────────────────────────────────────────────────────────────
function renderMine() {
  const wrap = $("#g-mine");
  wrap.textContent = "";
  wrap.append(accountCard());

  const plan = targetPlan() ?? activePlan();
  const inPlan = new Set(plan.decks.flatMap((d) => d.names).filter(Boolean));

  wrap.append(mineControls(inPlan.size));

  const needle = gv.q.trim();
  let names = Object.keys(profile.chars ?? {});
  if (gv.planOnly) names = [...inPlan];
  if (needle) names = names.filter((n) => n.includes(needle));

  const remain = new Map(names.map((n) => [n, axesOf(n).length]));
  names.sort(gv.sort === "name"
    ? (a, b) => a.localeCompare(b, "ko")
    : (a, b) => remain.get(b) - remain.get(a) || a.localeCompare(b, "ko"));

  const list = el("div", "grows");
  for (const name of names) list.append(mineRow(name, remain.get(name), inPlan.has(name)));
  wrap.append(list);

  const total = Object.keys(profile.chars ?? {}).length;
  wrap.append(el("p", "ghint2",
    `${names.length}명 표시 · 프로필 ${total}명`
    + (gv.planOnly ? ` · 편성안 "${plan.name}"의 ${inPlan.size}명만 보는 중` : "")));
}

function accountCard() {
  const box = el("div", "gcard");
  const meta = profile._meta ?? {};
  const acc = profile._account ?? {};
  const when = String(meta.fetched_at ?? "").slice(0, 16).replace("T", " ");

  box.append(el("div", "gcard-head", "계정"));
  const rows = [
    ["수집", `${when || "?"} · 로스터 ${meta.roster ?? "?"}종`],
    ["콘솔", consoleText(acc.console)],
    ["동기화", acc.synchro_level ? `소대 레벨 ${acc.synchro_level}` : "정보 없음"],
    ["큐브", cubeText(acc.cubes)],
  ];
  for (const [k, v] of rows) {
    const row = el("div", "gkv");
    row.append(el("b", null, k), el("span", null, v));
    box.append(row);
  }
  // 레벨은 프로필에 없다 — 정책이 정하고 웹앱은 언제나 400이다 (webapp-roadmap.md §4).
  box.append(el("p", "ghint2",
    "캐릭터 레벨은 계산에 쓰지 않는다 — 솔로레이드 기준으로 언제나 400이다. "
    + "동기화 소대 레벨은 참고용."));
  for (const w of acc.console_warnings ?? []) box.append(el("p", "gwarn", w));
  return box;
}

/** 콘솔 레벨은 숫자 하나이거나 역할군·기업별 dict다 (calculator/base_stat.console_level). */
function consoleText(c) {
  if (!c) return "정보 없음 — 기본 스펙 값으로 계산한다";
  const part = (label, v) => {
    if (v == null) return null;
    if (typeof v !== "object") return `${label} ${v}`;
    const vals = Object.values(v);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    return lo === hi ? `${label} ${lo}` : `${label} ${lo}~${hi}`;
  };
  return [part("공통", c.common_level), part("클래스", c.class_level),
          part("기업", c.company_level)].filter(Boolean).join(" · ");
}

/** 큐브는 프로필에 담기지 않는다 — 장착 중이던 것에서 관찰된 **보유 하한**뿐이다. */
function cubeText(cubes) {
  const entries = Object.entries(cubes ?? {});
  if (!entries.length) return "관찰된 것 없음";
  return entries.map(([n, lv]) => `${n} Lv${lv}`).join(" · ") + " (관찰 하한)";
}

function mineControls(planCount) {
  const box = el("div", "gbar");

  const search = el("input", "gsearch");
  search.type = "search";
  search.placeholder = "캐릭터 검색";
  search.value = gv.q;
  search.oninput = (e) => { gv.q = e.target.value; renderMine(); };
  box.append(search);

  const row = el("div", "chips");
  row.append(chip("남은 축 순", gv.sort === "remain", () => setG({ sort: "remain" })));
  row.append(chip("이름순", gv.sort === "name", () => setG({ sort: "name" })));
  row.append(chip(`편성 ${planCount}명`, gv.planOnly, () => setG({ planOnly: true })));
  row.append(chip("전체", !gv.planOnly, () => setG({ planOnly: false })));
  box.append(row);
  return box;
}

function setG(patch) {
  Object.assign(gv, patch);
  state.settings.gsort = gv.sort;
  state.settings.gplanOnly = gv.planOnly;
  state.settings.gres = gv.res;
  saveAll();
  renderGrowth();
}

function mineRow(name, remain, inPlan) {
  const e = growthOf(name);
  const row = el("div", "grow");
  if (inPlan) row.classList.add("in-plan");

  const pic = el("div", "gthumb");
  pic.append(thumb(name));
  row.append(pic);

  const body = el("div", "gbody");
  const head = el("div", "ghead");
  head.append(el("span", "gname", name));
  if (!inProfile(name)) head.append(el("span", "gtag warn", "프로필에 없음"));
  else if (e._unsynced) head.append(el("span", "gtag", "동기화 밖"));
  head.append(el("span", "gremain", remain ? `남은 축 ${remain}` : "다 찼다"));
  body.append(head);

  const sk = e.skill_levels ?? {};
  const skText = SKILL_AXES.map(([k]) => num(sk[k], 1)).join(" / ");
  body.append(el("div", "gline",
    `${num(e.breakthrough)}돌 · 코강 ${num(e.core_enhancement)} · 스킬 ${skText}`
    + ` · 호감 ${num(e.affinity, 1)}`));

  const eq = PARTS.map((p) => partText((e.equipment ?? {})[p])).join(" · ");
  const col = String(e.collection_stage ?? NO_ITEM);
  const fav = typeof e.favorite_stage === "number" ? ` · 애장품 ${e.favorite_stage}단계` : "";
  body.append(el("div", "gline", `장비 ${eq} · 소장품 ${col}${fav}`));

  const opts = optionText(e.equip_skills);
  body.append(el("div", "gline opt", opts ? `옵션 ${opts}` : "옵션 없음"));

  row.append(body);
  return row;
}

/** 부위 하나를 짧게. 강화가 붙는 장비만 숫자가 있고 나머지는 등급 그대로다. */
function partText(part) {
  if (!part) return "?";
  if (typeof part.level === "number") return `${part.level}강`;
  return String(part.tier ?? "?");
}

/** 오버로드 옵션 합계. 라벨은 인게임 문구에서 잘라 roster.json이 실어 온다. */
function optionText(skills) {
  const out = [];
  for (const [key, val] of Object.entries(skills ?? {})) {
    const total = Array.isArray(val) ? val.reduce((a, b) => a + b, 0) : num(val);
    if (!total) continue;
    const lines = Array.isArray(val) && val.length > 1 ? `×${val.length} ` : "";
    out.push(`${OPTION_LABEL[key] ?? key} ${lines}${total.toFixed(1)}%`);
  }
  return out.join(" · ");
}

// ── 투자 효율 ───────────────────────────────────────────────────────────
function renderEff() {
  const wrap = $("#g-eff");
  wrap.textContent = "";
  wrap.append(effBar());

  const plan = targetPlan();
  if (!plan) {
    wrap.append(el("p", "todo",
      "편성안을 아직 안 가져왔다.\n편성 탭에서 잴 편성안을 고른 뒤 위의 "
      + "`편성에서 가져오기`를 누른다."));
    return;
  }

  wrap.append(el("p", "gnote",
    "기준선은 그 덱의 계산 딜량이다. 실측 딜량은 기록 탭이 생기면 대신 곱한다 — "
    + "Δ%는 그대로 맞고 억 단위만 갈린다."));
  if (gError) wrap.append(el("p", "gwarn", gError));

  const rows = collectRows(plan);
  wrap.append(rankBox(rows));
  for (const [i, deck] of plan.decks.entries()) wrap.append(deckBox(plan, deck, i));
  wrap.append(excludedBox());
}

function effBar() {
  const box = el("div", "gbar row");
  const plan = targetPlan();
  const face = el("span", "gtarget");
  if (plan) {
    const weak = weakOf(plan);
    if (weak) face.append(elementIcon(weak));
    face.append(el("span", "pname", plan.name));
  } else {
    face.append(el("span", "pname sub", "대상 없음"));
  }
  box.append(face);

  const take = el("button", "mini");
  take.textContent = plan ? "다시 가져오기" : "편성에서 가져오기";
  take.onclick = takePlan;
  box.append(take);
  return box;
}

/** 잰 줄만 모은다 — 순위표는 측정된 것끼리만 줄 세울 수 있다. */
function collectRows(plan) {
  const out = [];
  for (const [i, deck] of plan.decks.entries()) {
    if (!isFull(deck)) continue;
    for (const name of deck.names) {
      for (const axis of axesOf(name)) {
        const d = delta(deck, plan, name, axis);
        if (d) out.push({ deckNo: i + 1, name, axis, d });
      }
    }
  }
  return out;
}

// 재화별로 나눠 세운다. 메뉴얼·뽑기·관리키트·장비 경험치는 서로 대체되지 않아
// 한 단위로 접으면 순위가 거짓이 된다 (cost/README.md §점수).
function rankBox(rows) {
  const box = el("details", "fold gfold");
  box.open = true;
  box.append(el("summary", null, `투자 순위 · 측정 ${rows.length}줄`));
  const body = el("div", "gfold-body");

  const byRes = new Map();
  const free = [];
  for (const r of rows) {
    if (!r.axis.res || !r.axis.amount) { free.push(r); continue; }
    if (!byRes.has(r.axis.res)) byRes.set(r.axis.res, []);
    byRes.get(r.axis.res).push(r);
  }

  if (!rows.length) {
    body.append(el("p", "ghint2", "아직 잰 축이 없다. 아래에서 캐릭터의 `재기`를 누른다."));
  }
  for (const [res, list] of byRes) {
    list.sort((a, b) => rate(b) - rate(a));
    body.append(el("div", "gres", `${res} — ${unitLabel(res)}당 딜 증가`));
    const table = el("div", "grank");
    list.forEach((r, i) => table.append(rankRow(r, i + 1)));
    body.append(table);
  }
  if (free.length) {
    free.sort((a, b) => b.d.dTotal - a.d.dTotal);
    body.append(el("div", "gres", "비용표 없는 축 — Δ만"));
    const table = el("div", "grank");
    free.forEach((r, i) => table.append(rankRow(r, i + 1)));
    body.append(table);
  }
  box.append(body);
  return box;
}

// 재화 한 단위가 너무 작거나(뽑기 1) 너무 커서(장비 경험치 60만) 그대로 나누면 읽히지
// 않는다. 재화마다 사람이 세는 덩어리로 묶는다.
const UNIT = {
  "스킬 메뉴얼": [100, "100장"],
  "버스트 메뉴얼": [100, "100장"],
  "뽑기": [1, "1뽑기"],
  "관리키트": [100, "100점"],
  "장비 경험치": [1e6, "100만"],
};
const unitOf = (res) => UNIT[res]?.[0] ?? 1;
const unitLabel = (res) => UNIT[res]?.[1] ?? "1";
const rate = (r) => (r.d.dTotal / r.axis.amount) * unitOf(r.axis.res);

// 억 단위로 접되, 1억을 못 넘는 값은 자릿수를 늘린다. 한 칸의 Δ는 대개 0.0x억이라
// 편성 탭과 같은 소수 두 자리로는 서로 다른 축이 전부 `0.00`으로 뭉개진다.
const eok2 = (n) => {
  const v = n / 1e8;
  return Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(3);
};
const signed = (n) => (n >= 0 ? "+" : "") + eok2(n);

function rankRow(r, no) {
  const row = el("div", "grankrow");
  row.append(el("span", "gno", `${no}`));
  const pic = el("div", "gthumb sm");
  pic.append(thumb(r.name));
  row.append(pic);

  const body = el("div", "gbody");
  body.append(el("div", "ghead2", `${r.name} · ${r.axis.label} ${r.axis.from}→${r.axis.to}`));
  body.append(el("div", "gline nowrap", `덱 ${r.deckNo} · ${costText(r.axis)}`));
  row.append(body);

  const val = el("div", "gval");
  if (r.axis.res && r.axis.amount) {
    const top = el("div", "gvtop");
    top.append(el("b", null, `${eok2(rate(r))}억`), el("span", null, `/${unitLabel(r.axis.res)}`));
    val.append(top);
  }
  val.append(el("div", "gvsub",
    `${r.d.pct >= 0 ? "+" : ""}${r.d.pct.toFixed(2)}% · ${signed(r.d.dTotal)}억`));
  row.append(val);
  return row;
}

function costText(axis) {
  if (!axis.res) return axis.note ?? "비용표 없음";
  if (axis.kits) {
    const kits = Object.entries(axis.kits)
      .filter(([, v]) => v >= 0.05)
      .map(([g, v]) => `${g} ${fmt(v, 1)}`).join(" · ");
    return `${kits} (기대값)`;
  }
  return `${axis.res} ${fmt(axis.amount)}`;
}

function deckBox(plan, deck, i) {
  const box = el("div", "gdeck");
  const head = el("div", "gdeck-head");
  head.append(el("span", "deck-no", `덱 ${i + 1}`));

  const base = resultOf(deck, plan);
  if (!isFull(deck)) {
    head.append(el("span", "gbase sub", "덱이 덜 찼다"));
  } else if (base) {
    head.append(el("span", "gbase ok", `기준 ${eok(base.total)}억`));
  } else {
    head.append(el("span", "gbase sub", deck.calcState ? "기준 계산 중" : "기준 없음"));
    const btn = el("button", "mini");
    btn.textContent = "기준 계산";
    btn.disabled = !!deck.calcState;
    btn.onclick = () => enqueue(deck.id);
    head.append(btn);
  }
  box.append(head);

  if (!isFull(deck)) return box;
  for (const name of deck.names) box.append(charBox(plan, deck, name, !!base));
  return box;
}

function charBox(plan, deck, name, hasBase) {
  const box = el("details", "gchar");
  const axes = axesOf(name);
  const measured = axes.filter((a) => delta(deck, plan, name, a)).length;
  const pending = charPending(deck, plan, name);

  const sum = el("summary");
  const pic = el("div", "gthumb sm");
  pic.append(thumb(name));
  sum.append(pic);

  const body = el("div", "gbody");
  body.append(el("div", "ghead2", name));
  body.append(el("div", "gline",
    axes.length ? `축 ${axes.length} · 측정 ${measured}/${axes.length}` : "더 올릴 칸이 없다"));
  sum.append(body);

  if (axes.length) {
    const btn = el("button", "mini");
    if (pending) {
      btn.textContent = `중단 (${pending})`;
      btn.classList.add("danger");
      btn.onclick = (e) => { e.preventDefault(); cancelChar(deck, plan, name); };
    } else {
      btn.textContent = measured === axes.length ? "다시 재기" : "재기";
      btn.disabled = !hasBase;
      btn.title = hasBase ? "" : "먼저 이 덱의 기준을 계산한다";
      btn.onclick = (e) => {
        e.preventDefault();
        if (measured === axes.length) {
          for (const a of axes) delete results[axisKey(deck, plan, name, a)];
          saveAll();
        }
        measureChar(deck, plan, name);
      };
    }
    sum.append(btn);
  }
  box.append(sum);

  const list = el("div", "gaxes");
  for (const axis of axes) list.append(axisRow(deck, plan, name, axis));
  if (!axes.length) list.append(el("p", "ghint2", "스킬·돌파·소장품·장비가 전부 상한이다."));
  // 같은 단서가 줄마다 달리면(부위 넷이면 넷) 정작 숫자가 안 읽힌다. 한 번만 적는다.
  for (const note of new Set(axes.map((a) => a.note).filter(Boolean))) {
    list.append(el("p", "gaxnote", note));
  }
  box.append(list);
  return box;
}

function axisRow(deck, plan, name, axis) {
  const row = el("div", "gaxis");
  row.append(el("span", "gaxname", `${axis.label} ${axis.from}→${axis.to}`));
  row.append(el("span", "gaxcost", costText(axis)));

  const d = delta(deck, plan, name, axis);
  const val = el("span", "gaxval");
  if (d) {
    val.textContent = `${d.pct >= 0 ? "+" : ""}${d.pct.toFixed(2)}% · ${signed(d.dTotal)}억`;
    val.classList.add(d.dTotal > 0 ? "ok" : "zero");
    // 버퍼는 자기 딜이 안 늘어도 덱 딜이 오른다. 둘이 갈리면 그것도 답이라 같이 적는다.
    row.title = `대상 캐릭터 딜 ${signed(d.dChar)}억`;
  } else if (gPending.has(axisKey(deck, plan, name, axis))) {
    val.append(el("span", "spin"));
  } else {
    val.textContent = "—";
    val.classList.add("zero");
  }
  row.append(val);
  return row;
}

// 이 표에 **없는** 축을 밝혀 둔다. 안 보이는 것이 "효율이 0"으로 읽히면 안 된다.
// 사유는 cost.json이 들고 있는 문구를 그대로 옮긴다 — 정본은 cost/tables.json이다.
function excludedBox() {
  const box = el("details", "fold gfold");
  box.append(el("summary", null, "빠진 축과 그 이유"));
  const body = el("div", "gfold-body");
  body.append(el("p", "ghint2",
    "오버로드 옵션(커스텀 모듈)은 목표 도달이 아니라 어디서 그만둘지를 고르는 문제라 "
    + "여기 없다. 계산의 정본은 overload/ 이고, 웹앱에는 따로 붙인다."));
  // 비용이 없다고 축까지 없는 건 아니다 — 애장품은 딜에 직접 걸리므로 Δ는 잰다.
  body.append(el("p", "ghint2",
    "아래는 재화를 안 세는 이유다. 딜에 직접 걸리는 축(애장품 단계)은 그래도 재서 "
    + "`비용표 없는 축 — Δ만` 칸에 놓는다 — 순위만 못 매길 뿐이다."));
  for (const [k, why] of Object.entries(COST.missing ?? {})) {
    body.append(el("p", "ghint2", `${k} — 자료 미수집: ${why}`));
  }
  for (const [k, why] of Object.entries(COST.excluded ?? {})) {
    body.append(el("p", "ghint2", `${k} — ${why}`));
  }
  box.append(body);
  return box;
}

// ── 초기화 ──────────────────────────────────────────────────────────────
function initGrowth(roster, costData) {
  COST = costData;
  UNGROWN = roster.ungrown ?? {};
  NO_ITEM = roster.noItem ?? "없음";
  PARTS = roster.parts ?? [];
  OPTION_LABEL = roster.optionLabel ?? {};

  gv.sort = state.settings.gsort ?? gv.sort;
  gv.planOnly = state.settings.gplanOnly ?? gv.planOnly;

  for (const b of document.querySelectorAll(".gtab")) {
    b.onclick = () => { gv.view = b.dataset.gtab; renderGrowth(); };
  }
}
