// 육성 화면 — 세 자리에 나뉘어 있다.
//
//   내 니케 (최상위 탭)      : 계정 전원의 육성 상태. 편성안과 무관하다
//   편성 › 육성 상태         : 지금 고른 편성안 25명이 어떤 육성으로 계산되는가
//   편성 › 투자 효율         : 그 편성안에서 다음 한 칸이 딜을 얼마나 올리는가
//
// 뒤의 둘은 하는 말이 전부 "지금 고른 편성안"이라 편성 탭 **안에** 산다. 최상위 탭이던
// 시절에는 대상 편성안을 따로 `가져오기`로 붙잡아야 했고, 그 대상과 편성 탭에서 보고
// 있는 벌이 어긋나면 어느 쪽 숫자인지 알 수 없었다. 이제 대상은 언제나 활성 편성안이다.
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
// 장비 강화 한 칸의 비용은 갈래마다 1.5배 갈린다(T9 기업 / 오버로드). 갈래는 프로필이
// 부위마다 `_track`으로 적어 온다 — `scraper/profile_fetch.py`의 `_equipment()`.
// 표식이 없는 **낡은 프로필**만 비싼 쪽으로 잡고 그 사실을 줄에 적는다.
const GEAR_FALLBACK = "오버로드";
const FAV_MAX = 3;

// `내 니케` 목록의 화면 상태. 저장은 state.settings에 얹어 간다.
// scope: "own" = 작전 인원(프로필에 있는 니케)만 / "all" = 로스터 전원 (미보유는 미육성으로 뜬다)
const gv = { q: "", scope: "own" };
let gError = "";
const gPending = new Set();   // 큐에 있거나 도는 중인 측정 키
let gseq = 0;

// ── 프로필 읽기 ─────────────────────────────────────────────────────────
// 프로필에 없는 이름은 계산도 화면도 미육성으로 본다 (context/spec.py `UNGROWN`).
const growthOf = (name) => profile?.chars?.[name] ?? UNGROWN;
const inProfile = (name) => !!profile?.chars?.[name];
// 캐릭터 기업. T9 기업 장비의 +30%가 이것과 장비 제조사가 같을 때만 붙는다.
const corpOf = (name) => byName.get(name)?.corp;

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

/** 장비 강화 `level`에 도달하는 데 드는 장비 경험치. 갈래(`track`)마다 표가 다르다. */
const gearExp = (track, level) =>
  COST.gear["레벨도달비용"][track]?.[String(level)] ?? null;

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
  //
  // 강화가 붙는 갈래는 둘이다 — 오버로드(`level`만)와 T9 기업(`tier`+`corp`+`level`).
  // 둘 다 `level` 한 칸이 축이고 비용 갈래만 `_track`으로 갈린다. 일반 T1~T9·미장착은
  // `level`이 없어 여기서 저절로 빠진다 (그 부위의 다음 한 칸은 "장비를 구한다"라
  // 재화가 안 잡힌다). 갈래 이야기는 `장비-오버로드-구분.md`.
  for (const part of PARTS) {
    const eq = (e.equipment ?? {})[part] ?? {};
    const lv = eq.level;
    if (typeof lv !== "number" || lv >= GEAR_MAX) continue;
    const track = eq._track ?? GEAR_FALLBACK;
    out.push({
      key: `eq:${part}`, label: `장비 강화 · ${part}`, from: lv, to: lv + 1,
      over: { equipment: { [part]: { level: lv + 1 } } },
      res: COST.gear["재화"], amount: gearExp(track, lv + 1),
      note: eq._track ? undefined
        : `${GEAR_FALLBACK} 장비 기준 — 갈래 표식이 없는 낡은 프로필이라 비싼 쪽으로 잡았다. `
          + `프로필을 다시 받으면 부위마다 갈린다`,
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

// `내 니케`는 193줄을 검색 한 글자마다 다시 그린다. axesOf는 캐릭터마다 비용표를 훑으므로
// 줄 수를 세는 데만 그것을 193번 도는 것이 렌더 시간의 대부분이었다(데스크탑 71ms).
// 축 목록은 프로필이 바뀌지 않는 한 그대로라 세어 둔다 — 프로필 객체가 통째로 갈리면
// (applyProfile) 참조가 달라져 저절로 버려진다.
let axesMemo = { src: null, count: new Map() };

function axesCount(name) {
  if (axesMemo.src !== profile) axesMemo = { src: profile, count: new Map() };
  let n = axesMemo.count.get(name);
  if (n === undefined) axesMemo.count.set(name, n = axesOf(name).length);
  return n;
}

// ── 측정 ────────────────────────────────────────────────────────────────
// 결과 캐시 키는 덱 지문 뒤에 "누구의 어느 오버라이드인가"를 한 칸 더 붙인 것이다.
// 편성 탭의 지문(원소 4~6개)보다 길어 서로 섞이지 않고, 5번째 칸이 그대로 육성 토큰이라
// 프로필을 갱신하면 app.js의 pruneResults()가 같이 걷어 간다.
//
// 6번째 칸은 그 덱의 **세부 조정**이다. 기준선(`resultOf`)은 조정이 걸린 값이므로, 축을
// 재는 쪽도 같은 조정 위에서 재야 Δ가 같은 조건의 차이가 된다. 조정이 없으면 `null`이라
// 이미 쌓아둔 측정치는 그대로 살아 있다.
const axisKey = (deck, plan, name, axis) => JSON.stringify([
  deck.names, plan.code, DURATION, plan.corePx, growthToken(deck.names),
  tuneToken(deck) || null, name + ":" + stable(axis.over),
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
        // 덱에 걸린 세부 조정 위에 이 축 한 칸을 더 얹는다. 기준선이 조정된 값이므로
        // 여기서 조정을 빼면 Δ가 "조정을 되돌린 차이"까지 함께 재게 된다.
        ...tunePayload(deck, { [name]: axis.over }),
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

// ── 렌더 ────────────────────────────────────────────────────────────────
// 프로필이 없으면 육성 이야기를 할 수 없다. 그때 계산은 **기본 스펙**으로 도는데 그건
// 3돌·스킬 10/10/10·5강·SR15라 이미 다 찬 상태다 — 다음 한 칸이 하나도 안 생기고,
// 육성 상태랍시고 전원 만렙을 보여주게 된다. 그래서 잠근다.
// 두 종류의 잠금이 있다.
//
//  `내 니케`는 **내 데이터를 읽는 화면**이라 파일만 있으면 열린다. 편성 탭의 기준 스위치는
//  계산을 무엇으로 돌릴지를 정할 뿐이라 여기와 무관하다 — 기본 스펙으로 계산하는 중에도
//  내 육성이 어떤지는 봐야 한다.
//
//  편성 › 육성 상태·투자 효율은 **계산 이야기**라 기준이 내 육성일 때만 열린다. 기본 스펙은
//  3돌·스킬 10/10/10·5강·SR15로 이미 다 찬 상태라, 다음 한 칸이 하나도 안 생기고 육성
//  상태랍시고 전원 만렙을 보여주게 된다.
function growthBlock(calc) {
  if (!COST) return "비용표(cost.json)를 받지 못했다 — `python web/build.py`로 다시 빌드한다.";
  if (!profile) {
    return "육성 프로필이 필요하다.\n`내 니케` 탭에서 PC의 profiles/me.json 을 고르면 열린다.";
  }
  if (calc && !state.settings.profileOn) {
    return "지금 기준이 `기본 스펙`이라 육성 이야기를 할 수 없다.\n"
         + "위의 `기준`을 `내 육성`으로 바꾸면 열린다.";
  }
  return "";
}

/** 잠금 문구를 띄우고 true를 준다. 열려 있으면 컨테이너를 비우고 false.
 *  `calc`는 이 화면이 계산 기준에 매이는가 — 편성 안의 두 화면만 그렇다. */
function blocked(wrap, calc = false) {
  wrap.textContent = "";
  const block = growthBlock(calc);
  if (block) wrap.append(el("p", "todo", block));
  return !!block;
}

// 세 화면이 흩어져 있어도 갱신 통로는 하나다. 숨어 있는 것은 여기서 걸러 낸다 —
// 편성 탭은 드래그 한 번에도 render()가 도는 화면이라, 안 보이는 목록까지 매번 그리면
// 25행·193행이 통째로 딸려 온다.
function renderGrowth() {
  if (!$('[data-panel="mine"]').hidden) renderMine();
  if ($('[data-panel="deck"]').hidden) return;
  if (deckSub === "status") renderStatus();
  else if (deckSub === "eff") renderEff();
}

// ── 내 니케 ─────────────────────────────────────────────────────────────
// 편성안과 무관한 계정 전체 조회다. 여기서 세는 `남은 축`은 편성 탭의 투자 효율이
// **잴 수 있는 줄 수**와 같은 것이라, 굳이 재보지 않아도 누가 덜 컸는지는 여기서 보인다.
function renderMine() {
  const wrap = $("#g-mine");
  if (blocked(wrap)) return;

  wrap.append(accountCard());
  // 이 탭은 기준 스위치와 무관하게 열리므로, 지금 계산이 뭘로 도는지는 짚어 준다.
  if (!state.settings.profileOn) {
    wrap.append(el("p", "gwarn",
      "편성 탭의 기준이 `기본 스펙`이라 딜량 계산에는 이 육성이 쓰이지 않는다."));
  }
  wrap.append(mineControls());

  const owned = Object.keys(profile.chars ?? {});
  const pool = gv.scope === "all" ? ROSTER.map((r) => r.name) : owned;
  const needle = gv.q.trim();
  const names = needle ? pool.filter((n) => n.includes(needle)) : [...pool];
  names.sort((a, b) => a.localeCompare(b, "ko"));

  // 남은 축은 여기서 세지 않는다 — 투자 효율 쪽 개념이라 조회 화면에 끌어오지 않는다.
  const list = el("div", "grows");
  for (const name of names) list.append(mineRow(name, null));
  wrap.append(list);

  wrap.append(el("p", "ghint2",
    `${names.length}명 표시 · 작전 인원 ${owned.length}명 / 로스터 ${ROSTER.length}명`));
}

function accountCard() {
  const box = el("div", "gcard");
  const meta = profile._meta ?? {};
  const acc = profile._account ?? {};
  const when = String(meta.fetched_at ?? "").slice(0, 16).replace("T", " ");

  box.append(el("div", "gcard-head", "계정"));
  // 값은 프로필이 적어 온 그대로다 — 여기서 보정하지 않는다. 인게임 화면과 ±1이 나면
  // 그건 표시 문제가 아니라 수집 쪽 이야기라 `scraper/profile_fetch.py`가 답할 일이다.
  const rows = [
    ["수집", when || "?"],
    ["작전 인원", `${meta.roster ?? "?"}명`],
    ["싱크로 레벨", acc.synchro_level ?? "정보 없음"],
    ["콘솔", consoleText(acc.console)],
    ["큐브", cubeText(acc.cubes)],
  ];
  for (const [k, v] of rows) {
    const row = el("div", "gkv");
    row.append(el("b", null, k), el("span", null, String(v)));
    box.append(row);
  }
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

/** 큐브는 프로필에 담기지 않는다 — 장착 중이던 것에서 관찰된 **보유 하한**뿐이다.
 *  그 단서는 `_cubes_note`가 들고 있고 화면에는 목록만 적는다. */
function cubeText(cubes) {
  const entries = Object.entries(cubes ?? {});
  if (!entries.length) return "관찰된 것 없음";
  return entries.map(([n, lv]) => `${n} Lv${lv}`).join(" · ");
}

function mineControls() {
  const box = el("div", "gbar");

  const search = el("input", "gsearch");
  search.type = "search";
  search.placeholder = "캐릭터 검색";
  search.value = gv.q;
  search.oninput = (e) => { gv.q = e.target.value; renderMine(); };
  box.append(search);

  const row = el("div", "chips");
  // 미보유도 볼 수 있어야 한다 — 계산이 미보유를 미육성으로 그냥 돌리므로
  // (webapp-roadmap.md §1b) "지금 뽑으면 얼마나 키워야 하나"가 그대로 읽힌다.
  row.append(chip("작전 인원", gv.scope === "own", () => setG({ scope: "own" })));
  row.append(chip("로스터 전체", gv.scope === "all", () => setG({ scope: "all" })));
  box.append(row);
  return box;
}

function setG(patch) {
  Object.assign(gv, patch);
  state.settings.gscope = gv.scope;
  saveAll();
  renderMine();
}

/** 한 줄이 캐릭터 하나. `flag`는 줄에 얹을 CSS 클래스(중복 표시 등)다.
 *
 * `remain`(남은 축)은 **편성 › 육성 상태에서만** 붙인다. `내 니케`는 계정을 읽는
 * 화면이라 투자 효율 쪽 개념을 끌어오지 않는다 — 거기서는 null로 부른다.
 */
function mineRow(name, remain, flag) {
  const e = growthOf(name);
  const row = el("div", "grow");
  if (flag) row.classList.add(flag);

  const pic = el("div", "gthumb");
  pic.append(thumb(name));
  row.append(pic);

  const body = el("div", "gbody");
  const head = el("div", "ghead");
  head.append(el("span", "gname", name));
  if (!inProfile(name)) head.append(el("span", "gtag warn", "프로필에 없음"));
  else if (e._unsynced) head.append(el("span", "gtag", "동기화 밖"));
  if (remain != null) {
    head.append(el("span", "gremain", remain ? `남은 축 ${remain}` : "다 찼다"));
  }
  body.append(head);

  const sk = e.skill_levels ?? {};
  const skText = SKILL_AXES.map(([k]) => num(sk[k], 1)).join(" / ");
  body.append(el("div", "gline",
    `돌파 ${num(e.breakthrough)} · 코강 ${num(e.core_enhancement)} · 스킬 ${skText}`
    + ` · 호감도 ${num(e.affinity, 1)}`));

  const eq = PARTS.map((p) => partText((e.equipment ?? {})[p])).join(" · ");
  const col = String(e.collection_stage ?? NO_ITEM);
  const fav = typeof e.favorite_stage === "number" ? ` · 애장품 ${e.favorite_stage}단계` : "";
  body.append(el("div", "gline", `장비 ${eq} · 소장품 ${col}${fav}`));
  // 기업이 안 맞는 기업 장비는 +30%를 못 받는다. 인게임에서도 손해라 그대로 알린다.
  const offCorp = PARTS.filter((p) => {
    const q = (e.equipment ?? {})[p];
    return q?.corp && q.corp !== corpOf(name);
  });
  if (offCorp.length) {
    body.append(el("div", "gline warn",
      `기업이 안 맞는 기업 장비 ${offCorp.join("·")} — 제조사 보너스(+30%)를 못 받는다`));
  }

  const opts = optionText(e.equip_skills);
  body.append(el("div", "gline opt", opts ? `장비 효과 ${opts}` : "장비 효과 없음"));

  row.append(body);
  return row;
}

/** 부위 하나를 짧게. 강화가 붙는 갈래만 숫자가 있고 나머지는 등급 그대로다.
 *
 * T9 기업 장비는 제조사까지 적는다 — 캐릭터 기업과 같을 때만 +30%가 붙어서
 * (`calculator/base_stat.py`의 `_equip_stat`) 어느 기업 장비인지가 스탯을 바꾼다.
 */
function partText(part) {
  if (!part) return "?";
  if (part.corp) return `${part.tier} ${part.corp} ${part.level ?? 0}강`;
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

// ── 이 편성의 육성 상태 ─────────────────────────────────────────────────
// 편성 탭에서 딜량을 볼 때 "그 숫자가 어떤 육성으로 나온 것인가"에 답하는 자리다.
// 계정 전체를 보는 `내 니케`와 달리 여기는 **이 편성안 25명**만, 덱 순서 그대로 본다 —
// 어느 덱의 누가 덜 컸는지가 편성을 고치는 근거가 되기 때문이다.
function renderStatus() {
  const wrap = $("#d-status");
  if (blocked(wrap, true)) return;

  const plan = activePlan();
  const dup = duplicated(plan);
  const names = plan.decks.flatMap((d) => d.names).filter(Boolean);
  const short = [...new Set(names.filter((n) => !inProfile(n)))];
  // 덱 칸 수로 센다. 중복이 있으면 그 니케는 두 번 서므로 줄도 두 번 나오고, 위의 합계와
  // 덱별 합계가 어긋나면 안 된다 — 중복은 경고할 일이지 숫자를 숨길 일이 아니다.
  const total = names.reduce((s, n) => s + axesCount(n), 0);

  wrap.append(el("p", "gnote",
    "이 편성안의 딜량이 어떤 육성으로 나온 값인지를 보는 자리다. "
    + `${names.length}칸 · 남은 축 ${total}개 — 무엇을 먼저 채울지는 \`투자 효율\`이 잰다.`));
  if (short.length) {
    wrap.append(el("p", "gwarn",
      `프로필에 없는 니케 ${short.length}명(${short.join(" · ")})은 미육성으로 계산된다 — `
      + "돌파 0 · 스킬 1/1/1 · 장비 미장착."));
  }

  for (const [i, deck] of plan.decks.entries()) {
    const box = el("div", "gdeck");
    const head = el("div", "gdeck-head");
    head.append(el("span", "deck-no", `덱 ${i + 1}`));

    const base = resultOf(deck, plan);
    const filled = deck.names.filter(Boolean);
    head.append(el("span", "gremain",
      `남은 축 ${filled.reduce((s, n) => s + axesCount(n), 0)}`));
    if (base) head.append(el("span", "gbase ok", `${eok(base.total)}억`));
    else head.append(el("span", "gbase sub", isFull(deck) ? "미계산" : "덱이 덜 찼다"));
    box.append(head);

    if (!filled.length) {
      box.append(el("p", "ghint2", "빈 덱이다."));
    } else {
      const list = el("div", "grows");
      // 슬롯 순서가 곧 버스트 우선순위다. 정렬하지 않고 덱에 놓인 그대로 읽는다.
      for (const name of filled) {
        list.append(mineRow(name, axesCount(name), dup.has(name) ? "dup" : null));
      }
      box.append(list);
    }
    wrap.append(box);
  }
}

// ── 투자 효율 ───────────────────────────────────────────────────────────
// 대상은 언제나 **활성 편성안**이다. 예전에는 `가져오기`로 따로 붙잡았는데, 그 대상과
// 편성 탭에서 보고 있는 벌이 어긋나면 화면의 숫자가 어느 벌 것인지 알 수 없었다.
function renderEff() {
  const wrap = $("#d-eff");
  if (blocked(wrap, true)) return;

  const plan = activePlan();
  wrap.append(el("p", "gnote",
    "기준선은 그 덱의 계산 딜량이다. 실측 딜량은 기록 탭이 생기면 대신 곱한다 — "
    + "Δ%는 그대로 맞고 억 단위만 갈린다."));
  if (gError) wrap.append(el("p", "gwarn", gError));

  const rows = collectRows(plan);
  wrap.append(rankBox(rows));
  for (const [i, deck] of plan.decks.entries()) wrap.append(deckBox(plan, deck, i));
  wrap.append(excludedBox());
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
  pic.title = r.name;   // 이름을 글자로 안 적는 대신 초상화가 답한다
  pic.append(thumb(r.name));
  row.append(pic);

  // 이름은 적지 않는다 — 초상화가 이미 누구인지 말하고, 이 표에서 고르는 것은 사람이
  // 아니라 **어느 칸에 재화를 넣을까**다. 이름이 앞에 서면 축이 뒤로 밀려 잘린다.
  const body = el("div", "gbody");
  body.append(el("div", "ghead2", `${r.axis.label} ${r.axis.from}→${r.axis.to}`));
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

  gv.scope = state.settings.gscope ?? gv.scope;
}
