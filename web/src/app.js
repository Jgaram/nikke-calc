// 편성 탭 — 인게임 스쿼드 편성의 UX를 옮긴다.
//   덱 번호 01~05 → 그 덱의 5슬롯 → 아래 로스터에서 채운다.
//   채우는 방법은 **누르기와 끌기 둘 다**다 (모바일에서 드래그만이면 못 쓴다).
//   카드마다 톱니 버튼 → 그 니케 하나의 육성만 고치고, 되돌릴 수 있다.
//
// 계산은 서버(/api/sim, 코어 수만큼 병렬) 또는 이 브라우저(Pyodide 워커) 중에서 고른다.
// 육성 스펙과 기록은 이 브라우저의 localStorage에만 있다 — 서버에 보관하지 않는다.

const LS = {
  decks: "nikke.decks.v2",
  results: "nikke.results.v2",
  settings: "nikke.settings.v2",
  profiles: "nikke.profiles.v1",
  records: "nikke.records.v1",
  presets: "nikke.presets.v2",
  whatsNew: "nikke.whatsnew.v1",
  notice: "nikke.notice.v1",
  fbMine: "nkl.fbMine",};

// 저장 개수 상한. localStorage는 오리진당 5MB 남짓이고, 실측으로 자리를 차지하는 것은
// **스펙과 기록**이다 (계정 하나 ≈ 120KB · 기록 한 건 ≈ 6.5KB). 프리셋은 한 장이 1KB
// 안쪽(덱 하나 155B)이라 100개를 둬도 100KB다 — 넉넉히 열어 두고, 대신 스펙 쪽을 조인다.
const PRESET_MAX = 100;
const PRESET_NAME_MAX = 24;
// 스펙 10개면 약 1.2MB — 한도의 4분의 1이다. 이보다 늘리면 기록·결과 캐시와 부딪친다.
const PROFILE_MAX = 10;

// 공유본 스키마 판. **담기는 것이 바뀌면 올린다** — 옛 링크를 새 뜻으로 읽지 않게.
const SHARE_V = 1;

const DECK_COUNT = 5;
const SLOTS = 5;
const CODES = ["", "작열", "수냉", "풍압", "전격", "철갑"];
// **위크포인트 → 적 코드.** 계산기는 «적의 속성»(`enemy.code`)을 받고, 니케 코드가
// 그 적에게 우월할 때만 ⑦ 우월 코드가 붙는다 (`calculator/damage.py _CODE_ADVANTAGE`).
// 인게임 레이드 화면이 알려 주는 건 «약점 코드» — **데려갈 속성**이다. 둘은 서로
// 반대 방향이라, 고른 값을 그대로 적 코드로 넘기면 엉뚱한 속성이 이득을 본다.
//   위크포인트 전격 → 적은 수냉 → 전격 니케가 우월  (전격 ▶ 수냉)
const WEAK_TO_ENEMY = {
  전격: "수냉", 수냉: "작열", 작열: "풍압", 풍압: "철갑", 철갑: "전격",
};
/** 지금 고른 위크포인트에 해당하는 **적 코드**. 계산에 넘길 값은 늘 이것이다. */
const enemyCode = () => WEAK_TO_ENEMY[state.settings.code] || null;
// 색이 들어간 육각 코드 아이콘 (63×73 RGBA). 흰 글리프판(icn_element_*.webp)과 달리
// 그 자체로 색·모양을 다 갖고 있어 배지에 그대로 얹는다.
// 돌파 별·코강 배지는 **실물 에셋**이다. 직접 ★을 찍으면 폰트가 그리는 모양이라
// 인게임과 다르고, 코강 링은 아예 글자로 만들 수 없다.
//   nk-star-on/off.png — blablalink가 쓰는 금색·회색 4각 별 (테두리까지 들어 있다)
//   nk-evolve.png      — 코강 숫자가 들어앉는 마젠타 링
const STAR_ON = "nk-star-on.png";
const STAR_OFF = "nk-star-off.png";
// 등급별 별 개수. blablalink `star-GXlUU28h.js`와 같은 값이다.
const RARE_STARS = { SSR: 3, SR: 2, R: 0 };

/** 돌파·코강 → 별 개수와 코강 숫자.
 *
 *  인게임은 **돌파와 코강을 한 눈금(limit_break)으로 이어 센다.** 별이 먼저 차고,
 *  넘치는 만큼이 코강 숫자이며, 10이면 MAX다. blablalink `star-GXlUU28h.js`의 계산을
 *  그대로 옮겼다 — 우리 식으로 다시 세면 SR·R에서 어긋난다. */
function starInfo(rare, grade, core) {
  const max = RARE_STARS[rare] ?? 3;
  const lb = (grade || 0) + (core || 0);
  return {
    max,
    active: Math.min(lb, max),
    breakNum: lb >= 10 ? "MAX" : (lb > max ? lb - max : 0),
  };
}

const ELEMENT_ICON = {
  작열: "icon-code-fire.png", 수냉: "icon-code-water.png",
  풍압: "icon-code-wind.png", 전격: "icon-code-electronic.png",
  철갑: "icon-code-iron.png",
};
const CORP_ICON = {
  엘리시온: "icn_corp_01.webp", 미실리스: "icn_corp_02.webp",
  테트라: "icn_corp_03.webp", 필그림: "icn_corp_04.webp",
  어브노말: "icn_corp_05.webp",
};
const CLASS_ICON = {
  화력형: "icn_class_attacker.webp", 방어형: "icn_class_defender.webp",
  지원형: "icn_class_supporter.webp",
};
// 버스트 — 인게임 글리프(`icn_burst_*`)를 그대로 쓴다. 로마자를 글자로 찍으면
// 폰트가 그리는 모양이라 인게임과 다르고, 올라운더(A)는 아예 글자가 없다.
const BURST_ICON = {
  1: "icn_burst_01.webp", 2: "icn_burst_02.webp",
  3: "icn_burst_03.webp", A: "icn_burst_all.webp",
};
const BURST_ROMAN = { 1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", A: "A" };
// 역할군 → 결과 차트의 범주형 색. **고정 순서이며 순환하지 않는다** (dataviz 규칙).
// 세 색은 검증기 6검사를 통과한 조합이다 (tokens.css 주석 참조).
const CLASS_COLOR = {
  화력형: "var(--cat-attacker)", 방어형: "var(--cat-defender)", 지원형: "var(--cat-supporter)",
};
const WEAPONS = ["AR", "SMG", "SG", "SR", "RL", "MG"];
// 칩 순서는 **인게임 표시 순서로 고정**한다. 로스터 등장 순서로 두면
// 「지원형·화력형·방어형」처럼 뒤죽박죽이 되어 눈이 자리를 못 외운다.
const CLASS_ORDER = ["화력형", "방어형", "지원형"];
const CODE_ORDER = ["작열", "수냉", "풍압", "전격", "철갑"];
// 인게임 기업 표시 순서 (`context/roster.py` CORP_ICON과 같다)
const CORP_ORDER = ["엘리시온", "미실리스", "테트라", "필그림", "어브노말"];

// 인게임 표기 그대로 쓴다 — 줄임말을 만들면 게임 화면과 대조가 안 된다.
const OL_OPTS = [
  ["atk_pct", T("공격력")],
  ["element_bonus", T("우월 코드 대미지")],
  ["max_ammo_pct", T("최대 장탄 수")],
  ["crit_rate", T("크리티컬 확률")],
  ["crit_dmg", T("크리티컬 피해량")],
  ["charge_speed_pct", T("차지 속도")],   // 인게임 옵션 이름 그대로. 값이 클수록 차지가 빠르다
  ["charge_dmg_pct", T("차지 대미지")],
  ["accuracy_pct", T("명중률")],
  ["def_pct", T("방어력")],
];
const OL_LABEL = Object.fromEntries(OL_OPTS);
// 이 둘만 인게임이 단계별로 따로 반올림한다 → 줄별 리스트로 낸다
// (GAMEPLAY.md §무기 메카닉 · profile_convert.PER_LINE_KEYS와 같은 집합이어야 한다)
const PER_LINE = new Set(["max_ammo_pct", "charge_speed_pct"]);
const PARTS = ["머리", "몸통", "팔", "다리"];
const EQUIP_KEYS = OL_OPTS.map(([k]) => k);
const COLL_STAGES = ["없음", ...Array.from({ length: 16 }, (_, i) => `R${i}`),
  ...Array.from({ length: 16 }, (_, i) => `SR${i}`)];

// «내 순서»는 사용자가 나중에 직접 만든다 — 지금은 넣지 않는다.
// 정렬 기준은 **네 개**로 줄였다. 등급·한계돌파·호감도는 값이 몇 가지뿐이라
// 200명을 줄 세우는 데 쓸모가 없었다(대부분 같은 칸에 뭉친다). 남긴 것은
// 인게임에서 쓰던 둘(전투력·이름)과, 딜을 실제로 가르는 둘
// (**우월코드**, **우코+공증 합(우공합)**)이다.
// 레벨은 넣지 않는다 — 솔로레이드는 400 고정이라 전원 같다.
const SORTS = [
  ["combat", T("전투력")], ["name", T("이름")], ["elem", T("우월코드")], ["elematk", T("우공합")],
];

// 전투 조건 기본값 — **계산기의 DEFAULT_ENEMY / DEFAULT_CONFIG와 같아야 한다**
// (calculator/timeline.py). 다르면 UI를 안 건드려도 기본 결과가 달라진다.
//
// `def` 31784는 2026-08-24 실측으로 재확인했다. 솔로레이드 «사치스러운 거미»에서
// 목단(AR·펠릿 1개·우월코드 없음, 큐브 미장착)의 비크리 몸통 평타 10,454로 역산하면
// 30,939이고, 같은 방어력으로 드레이크를 예측하면 48,770 대 실측 48,015(오차 1.5%)로
// 맞는다. 한때 33,700으로 고쳤던 적이 있는데, 그건 같은 실측값을 **큐브 Lv15 착용**으로
// 잘못 가정하고 역산한 값이라 되돌렸다.
const BATTLE_DEFAULT = {
  def: 31784, core_px: 0, has_parts: false, part_break_interval: 0,
  optimal_range_weapons: [],
  // 무기군별 평타 실전 계수. 시뮬은 모든 탄이 명중한다고 가정하지만 실전은 탄퍼짐으로
  // 새는 탄이 있다 — 2026-08-24 거미 솔레 실측: SG −7~25% (5명·2덱), SMG −19~21%
  // (리타·리틀 머메이드). 평타에만 곱하고 스킬·변신 대미지는 건드리지 않는다.
  weapon_coeff: { AR: 1, SMG: 0.8, SG: 0.9, SR: 1, RL: 1, MG: 1 },
  max_burst_count: 0,            // 0 = 무제한(null)
  first_burst_time: 3.0, burst_switch_delay: 0.1, burst_reenter_delay: 0.5,
};

let ROSTER = [];
const byName = new Map();
// 「최종 공격력이 가장 높은 아군」에게 버프를 거는 니케. 빌드가 파싱 데이터에서 굽는다
// (`web/build.py _top_atk_casters`). 이 중 하나가 덱에 있을 때만 진단을 띄운다.
let TOP_ATK_CASTERS = new Set();
let MAPS = null;          // profile_maps.json — 오버로드 표·큐브 이름·큐브 효능
let HEALTH = { sim: false, fetch: false };

// 클래스·코드·무기·버스트는 **다중 선택**이다 (인게임과 같다). 빈 배열 = 필터 없음.
const defaultFilter = () => ({ q: "", burst: [], element: [], cls: [], weapon: [], corp: [],
                               sort: "combat", asc: false, parsed: true, favOnly: false,
                               favItem: false });

const state = {
  settings: {
    code: "풍압", duration: 180, deck: 0, profileId: "",
    mode: "solo",          // solo | union — 유니온은 아직 로컬 전용(HEALTH.union)
    engine: "auto",        // auto | server | local
    fpanel: false,
    fastMode: false,       // 배치모드 — 마지막으로 켜 둔 상태 그대로 다음에도 연다
  },
  decks: [],
  filter: defaultFilter(),
  // 전투력 계산기(coop) 전용 필터. **편성과 독립**이다 — 예전엔 필터 바 DOM을 그대로
  // 옮겨 쓰면서 상태(state.filter)까지 공유해, 편성에서 걸어 둔 필터가 전투력
  // 계산기의 고르는 화면까지 그대로 새어 들었다(실측: 편성에서 속성을 좁혀 두면
  // 전투력 계산기에서 다른 속성 캐릭터가 안 보임). 화면(DOM)은 계속 공유하되
  // (`moveFilterBar`), 상태만 갈라 각자 기억하게 한다.
  coopFilter: defaultFilter(),
  profiles: {},
  favs: [],               // 즐겨찾기 — 등록 순서가 «내 순서»다
  records: [],
  presets: [],            // 편성만 담는 프리셋 — 계산 결과는 records가 담당한다
  battle: { ...BATTLE_DEFAULT },
};
let shared = null;        // 공유 링크로 받은 편성 (`/s?c=…`). 평소에는 null이다
let presetFilter = "all"; // 프리셋 목록 필터: all | single | bundle
let results = {};
let picked = null;
let ctrlOpen = null;      // 컨트롤을 펼친 니케 이름        // 누르기로 고른 카드 (모바일 경로)
// 배치모드 — 화면만 바꾼다. 데이터는 그대로 state.decks라서 껐다 켜도
// 편성이 그대로다. 모드 자체는 state.settings.fastMode로 저장되어 `boot()`가
// 복원한다(유저 피드백: 새로고침해도 켜 둔 상태 그대로 열려야 한다).
let fastMode = false;

// ── 저장 ────────────────────────────────────────────────────────────────
const load = (k, fb) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? fb; } catch { return fb; }
};
const save = (k, v) => {
  try { localStorage.setItem(k, JSON.stringify(v)); }
  catch (e) { setStatus(T("저장 실패 — 저장 공간이 찼을 수 있습니다: {name}", { name: e.name })); }
};
const saveAll = () => {
  save(LS.decks, state.decks);
  // `_battle`은 **솔로 것**이다. 유니온 상자를 여기에 쓰면 솔로 설정이 조용히 덮인다.
  // 유니온 일체(편성·큐브·컨트롤·레벨·전투 조건)는 `_union`에 통째로 따로 담는다.
  save(LS.settings, { ...state.settings, _filter: state.filter, _coopFilter: state.coopFilter,
                     _favs: state.favs,
                     _filterV: state.settings._filterV, _battle: state.battle,
                     _union: state.union || null });
  save(LS.results, results);
  save(LS.profiles, state.profiles);
  save(LS.records, state.records);
  save(LS.presets, state.presets);
};

const $ = (sel, root = document) => root.querySelector(sel);
// 글자는 여기서 **번역된다**(i18n.js의 `T`). UI 문구도 니케 이름도 한국어 원문이
// 키라, 만드는 자리마다 감쌀 필요가 없다. 사전에 없는 글자는 그대로 나간다.
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = typeof text === "string" ? T(text) : text;
  return n;
};
const uid = () => Math.random().toString(36).slice(2, 9);
const eok = (n) => (n / 1e8).toFixed(2);
/** 진행 문구. **비어 있으면 배지가 사라진다** — 늘 떠 있는 라벨이 아니라
 *  기다려야 하는 동안만 보이는 표시다. 헤더 오른쪽에 흘리면 글자가 길어질 때마다
 *  옆 버튼들이 밀린다. */
/** 지금 떠 있는 진행 문구 (없으면 빈 문자열). */
const statusText = () => {
  const box = $("#busy");
  return box && !box.hidden ? ($("#busy-text").textContent || "") : "";
};

const setStatus = (t, spin = true) => {
  const box = $("#busy");
  if (!box) return;
  box.hidden = !t;
  if (!t) return;
  $("#busy-text").textContent = T(t);      // 서버가 준 한국어 문장도 사전에 있으면 바뀐다
  // 돌아가는 원은 «기다리는 중»이라는 뜻이다. 안내문에까지 붙이면 아무 일도 안 하는데
  // 뭔가 도는 것처럼 보인다.
  box.querySelector(".busy-spin").hidden = !spin;
};

/** 잠깐 떴다 사라지는 알림.
 *
 *  `setStatus`는 «기다리는 중»을 나타내는 배지라 지우는 사람이 있어야 한다. 저장처럼
 *  기다릴 것이 없는 일에 그대로 쓰면 **문구가 화면에 박힌다.** 그래서 시간이 지나면
 *  스스로 걷어내되, 그 사이 계산 같은 진짜 «기다림»이 시작됐으면 건드리지 않는다. */
function flashStatus(text, ms = 2600) {
  setStatus(text, false);
  setTimeout(() => {
    if ($("#busy-text")?.textContent === text) setStatus("", false);
  }, ms);
}

// ── 그 자리에서 묻기 ────────────────────────────────────────────────────
// `confirm()`·`prompt()`를 쓰지 않는다. 브라우저 대화상자는 **무엇에 대한 물음인지**를
// 화면에서 떼어 놓는다 — 「이 기록을 지웁니다」만 떠 있으면 어느 기록인지 확인할 방법이
// 없다. 카드 안에서 물으면 지울 대상이 바로 위에 보이고, 생김새도 사이트와 같다.
//
// 두 함수가 같은 자리(`.inline-ask`)를 쓰고, 한 카드에 하나만 열린다.

/** 이미 열려 있는 물음을 걷어낸다. 한 번에 하나만 열려 있어야 한다. */
function closeAsk(host) {
  for (const x of (host || document).querySelectorAll(".inline-ask")) x.remove();
}

/** 그 자리에서 «정말?»을 묻는다. `onOk`는 확인을 누르면 불린다. */
function askInline(host, text, okLabel, onOk) {
  if (!host) return;
  const open = host.querySelector(".inline-ask");
  closeAsk(document);
  if (open) return;                       // 같은 버튼을 다시 누르면 접는다
  const bar = el("div", "inline-ask");
  bar.append(el("span", "inline-ask-t", text));
  const acts = el("div", "inline-ask-acts");
  acts.append(mkBtn(T("취소"), "btn-ghost", () => bar.remove()));
  acts.append(mkBtn(okLabel, "btn-alert", () => { bar.remove(); onOk(); }));
  bar.append(acts);
  host.append(bar);
  bar.querySelector(".btn-alert").focus();
}

/** 이름을 그 자리에서 고친다. 엔터로 저장, Esc로 취소. */
function askRename(host, label, current, max, onOk) {
  if (!host) return;
  const open = host.querySelector(".inline-ask");
  closeAsk(document);
  if (open) return;
  const bar = el("div", "inline-ask");
  bar.append(el("span", "inline-ask-t", label));
  const inp = el("input", "inline-ask-in");
  inp.type = "text";
  inp.maxLength = max;
  inp.autocomplete = "off";
  inp.value = current;
  inp.setAttribute("aria-label", label);
  bar.append(inp);
  const acts = el("div", "inline-ask-acts");
  const commit = () => {
    const v = inp.value.trim().slice(0, max);
    if (!v) return;
    bar.remove();
    onOk(v);
  };
  acts.append(mkBtn(T("취소"), "btn-ghost", () => bar.remove()));
  acts.append(mkBtn(T("저장"), "btn-primary", commit));
  bar.append(acts);
  inp.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    else if (e.key === "Escape") { e.preventDefault(); bar.remove(); }
  };
  host.append(bar);
  inp.focus();
  inp.select();
}

function mkBtn(label, cls, onclick, disabled = false) {
  const b = el("button", `btn ${cls}`, label);
  b.type = "button";
  b.disabled = disabled;
  b.onclick = onclick;
  return b;
}

function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" });
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${String(filename).replace(/[\\/:*?"<>|]/g, "_")}.json`;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 30000);
}

// ── 프로필: 원본(fetched) + 수정본(edits) 2층 ───────────────────────────
// 원본은 절대 고치지 않는다. 그래야 니케 하나만 동기화 값으로 되돌릴 수 있고,
// 다시 싱크해도 수정본이 살아남는다.
function deepMerge(base, over) {
  if (!over) return structuredClone(base);
  const out = structuredClone(base);
  for (const [k, v] of Object.entries(over)) {
    out[k] = (v && typeof v === "object" && !Array.isArray(v)
      && out[k] && typeof out[k] === "object" && !Array.isArray(out[k]))
      ? deepMerge(out[k], v) : structuredClone(v);
  }
  return out;
}

const activeRec = () => state.profiles[state.settings.profileId] || null;
const mergedProfile = () => {
  const rec = activeRec();
  return rec ? deepMerge(rec.fetched, rec.edits) : null;
};

/** 니케 한 명의 병합된 육성값. 편집 시트와 카드 배지가 쓴다. */
function charSpec(name) {
  const rec = activeRec();
  if (!rec) return null;
  const base = rec.fetched?.chars?.[name];
  if (!base) return null;
  return deepMerge(base, rec.edits?.chars?.[name]);
}
const isEdited = (name) => !!activeRec()?.edits?.chars?.[name];

// ── 코스튬(스킨) ────────────────────────────────────────────────────────
// 블라 프로필이 캐릭터마다 **장착 중인 코스튬 id**를 준다(`_costume`). `_` 접두
// 키라 시뮬에는 안 넘어간다 — 외형뿐이라 딜에는 아무 영향이 없다.
// 그 id로 그림을 찾는 표는 로스터에 구워져 온다(`web/build.py _costumes_for`):
//   rec.costumes = { "30017": {name, img, face, full?, fbb?} }
//
// `charSpec()`을 안 쓴다 — 카드 200장을 그릴 때마다 deepMerge를 돌릴 값이 아니고,
// 코스튬은 카드 톱니(수정 층)에서 건드리는 값도 아니다. 두 층만 직접 본다.
function costumeOf(rec, name) {
  if (!rec?.costumes) return null;
  const a = activeRec();
  const cid = a?.edits?.chars?.[name]?._costume ?? a?.fetched?.chars?.[name]?._costume;
  return cid ? rec.costumes[cid] || null : null;
}
/** 초상화(256×512) 경로. 스킨을 입고 있으면 그 그림. */
function artSrc(rec, name) {
  return `image/${costumeOf(rec, name)?.img || rec?.img || ""}`;
}
/** 정사각 얼굴 카드(68×68). 스킨 얼굴이 없으면 기본 얼굴 → 초상화 순으로 물러난다. */
function faceSrc(rec, name) {
  return `image/${costumeOf(rec, name)?.face || rec?.face || rec?.img || ""}`;
}
/** 툴팁에 붙일 스킨 이름. 기본 코스튬이면 빈 문자열. */
function skinNote(rec, name) {
  const c = costumeOf(rec, name);
  return c?.name ? ` · ${c.name}` : "";
}

// 32비트 해시 — 수정본이 바뀌면 결과 캐시를 무효화하는 데만 쓴다
function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
/** 스펙 지문. **이게 없으면 스펙을 바꿔도 옛 결과가 그대로 보인다.** */
function profSig() {
  const rec = activeRec();
  if (!rec) return "fixed";
  return `${rec.id}@${rec.fetched?._meta?.fetched_at || "?"}#${hash(JSON.stringify(rec.edits || {}))}`;
}

// `cubes`는 **칸에 붙는다** — 니케 육성이 아니라 그 자리에 끼울 큐브라서다(큐브는
// 인게임에서 자유롭게 갈아끼우는 자원이다). 25칸(5덱×5)이 니케와 별개로 존재하고,
// 자리를 맞바꾸면 큐브도 같이 따라간다(`place`). 덱 순서 변경은 덱 객체를 통째로
// 스왑하므로 그것만으로 큐브 세트가 함께 움직인다. `null`이면 기본값(계정 보유 최고
// → 없으면 러너 기본 렐릭 베어 Lv15).
// 큐브칸 기본값. 재장전 큐브가 사실상 표준이라 여기가 기준선이다.
const CUBE_DEFAULT = { name: "렐릭 베어 큐브", level: 15 };
// 고르개 순서 — 자주 쓰는 것부터. 여기 없는 큐브는 뒤에 가나다순으로 붙는다.
const CUBE_ORDER = ["렐릭 베어 큐브",       // 재장전 속도
                    "택티컬 베어 큐브",     // 탄환 충전
                    "렐릭 디스트로이 큐브", // 파츠 대미지
                    "렐릭 디바이드 큐브"];  // 분배 대미지

const newDeck = () => ({ names: Array(SLOTS).fill(null), control: {},
                         cubes: Array(SLOTS).fill(null) });
const deckOf = (i) => state.decks[i] || (state.decks[i] = newDeck());
/** 지금 모드의 덱 수. 유니온은 3덱(같은 보스를 여러 덱으로 쳐도 된다). */

/** 유니온 덱이 겨눈 보스의 약점 속성. 솔로는 이 함수를 쓰지 않는다. */
const uWeak = (d) => d?.weak || null;   // null = 아직 안 고름

/** 유니온에서 쓸 니케 레벨. 실제로는 동기화 소대 레벨이지만 계산기이므로 바꿀 수 있다.
 *  비워 두면 프로필의 동기화 레벨을 쓰고, 그것도 없으면 기본 스펙(400)이 남는다. */
function unionLevel() {
  const v = Number(U().level);
  if (Number.isFinite(v) && v > 0) return Math.round(v);
  const sync = activeRec()?.fetched?._account?.synchro_level;
  return Number.isFinite(sync) && sync > 0 ? sync : null;
}

/** 레벨을 캐릭터 오버라이드로 얹는다 — 엔진이 `over[이름].level`을 마지막에 먹는다. */
function levelOver(d, base) {
  if (modeNow() !== "union") return base;
  const lv = unionLevel();
  if (!lv) return base;
  const out = { ...(base || {}) };
  for (const nm of d.names) if (nm) out[nm] = { ...(out[nm] || {}), level: lv };
  return out;
}
const isFull = (d) => d.names.every(Boolean);
// 결과 스키마 판. **계산 결과의 뜻이나 모양이 바뀌면 반드시 올린다.**
// 지문에 안 들어 있으면 이미 저장된 결과가 새 뜻의 결과인 척 그대로 남는다.
//   w2 — 위크포인트가 «적 코드»에서 «데려갈 속성»으로 바뀜
//   c3 — 니케별 내역에 기대 크리율(crit_frac 합)이 들어옴
//   c5 — 결과에 최공 대상 버프 진단(top_atk)이 붙음. 옛 캐시에는 그 필드가 없다
//   c6 — 그 진단에 «최저공 타게팅»(리버렐리오 차지 속도)이 함께 들어옴 (`kind`)
// c10: 「투사체 폭발 대미지 ▲」 판정을 출생 무기 기준으로 — 변신으로 RL이 된
//      사격(나유타 등)은 못 받는다. 해당 조합 편성의 결과가 바뀐다.
// c9: 차지 배율(④)을 곱연산에서 가산으로 수정 — 풀차지 배율 + 차지 대미지 %p.
//     차지 무기(SR·RL)가 낀 모든 편성의 결과가 내려간다 (실측 정합).
// c8: 안 고른 칸의 큐브가 프로필(계정 보유 최고)로 새던 것을 편성 기본값으로 고정 —
//     큐브를 한 번도 안 만진 편성의 결과가 바뀐다.
// c7: 무기군 평타 계수(weapon_coeff) 도입 — SG 기본 0.9라 기본 상태의 결과가 바뀐다.
// 엔진·기본값이 바뀔 때 이 값을 올리지 않으면 캐시가 옛 엔진의 숫자를 재계산 없이
// 보여 준다 (2026-08-24 재장전 수정 때 실제로 겪음).
const CALC_V = "c10";
const fingerprint = (d) =>
  JSON.stringify([d.names, CALC_V, state.settings.code, durationNow(), profSig(),
                  battleSig(), ctrlSig(d), cubeSig(d)]);

/** 큐브 지문. **여기 안 들어가면 큐브를 바꿔도 옛 결과가 그대로 보인다** — 이 앱에서
 *  가장 조용히 틀리는 종류의 버그다(SITE.md §결과 캐시와 지문). 아무 칸도 안 건드리면
 *  짧은 문자열이라 옛 캐시와 호환된다. */
function cubeSig(d) {
  const cu = d.cubes || [];
  if (!cu.some(Boolean)) return "def";
  return JSON.stringify(cu.map((c) => (c ? [c.name, c.level] : 0)));
}

/** 고를 수 있는 큐브 목록(표시 순서)과 그중 기본으로 보이는 것.
 *  `cubeCell`의 화면과 `cubePayload`의 계산이 **같은 답**을 쓰게 하는 단일 출처다. */
function cubeChoices() {
  const names = Object.keys(MAPS?.cube_info || {}).filter((c) => c !== "공통").sort();
  const head = CUBE_ORDER.filter((c) => names.includes(c));
  const ordered = [...head, ...names.filter((c) => !head.includes(c))];
  const def = ordered.includes(CUBE_DEFAULT.name) ? CUBE_DEFAULT.name : ordered[0];
  return { names, ordered, def };
}

/** 그 칸에 **실제로 적용되는** 큐브. 아직 안 고른 칸은 화면에 보이는 기본값이 답이다.
 *  예전에는 안 고른 칸을 계산에서 빼 버려서, 프로필 층(계정에서 관찰된 보유 최고
 *  큐브)이 대신 들어갔다 — 카드에는 «렐릭 베어 Lv15»가 보이는데 계산은 다른 큐브로
 *  도는 상태였다. 편성에 보이는 것이 곧 계산에 들어가는 것이어야 한다. */
function cubeOf(d, i) {
  if (d.cubes?.[i]) return d.cubes[i];
  const { def } = cubeChoices();
  return def ? { name: def, level: CUBE_DEFAULT.level } : null;
}

/** 칸 큐브 → {니케 이름: {name, level}}. 니케가 있는 칸은 **항상** 실린다. */
function cubePayload(d) {
  const out = {};
  (d.names || []).forEach((nm, i) => {
    if (!nm) return;
    const c = cubeOf(d, i);
    if (c) out[nm] = { name: c.name, level: c.level };
  });
  return Object.keys(out).length ? out : null;
}

/** 컨트롤 지문. 아무것도 안 켜면 짧은 문자열이라 옛 캐시와 호환된다. */
function ctrlSig(d) {
  const c = d.control || {};
  const on = Object.keys(c).filter((n) => d.names.includes(n) && Object.keys(c[n] || {}).length);
  if (!on.length) return "auto";
  return JSON.stringify(on.sort().map((n) => [n, c[n]]));
}
/** 전투 조건 지문. 기본값과 같으면 짧은 문자열이라 옛 캐시와 호환된다. */
function battleSig() {
  const b = battleNow();
  const diff = Object.keys(BATTLE_DEFAULT).filter((k) => {
    const a = b[k], d = BATTLE_DEFAULT[k];
    if (Array.isArray(d)) return JSON.stringify([...a].sort()) !== JSON.stringify(d);
    if (d && typeof d === "object") return JSON.stringify(a) !== JSON.stringify(d);
    return a !== d;
  });
  return diff.length ? diff.map((k) => `${k}=${JSON.stringify(b[k])}`).join(",") : "def";
}

/** 계산기에 넘길 enemy / config. 기본값과 같은 항목은 보내지 않는다. */
/** 계산기에 넘길 «적·전투 조건». 덱을 주면 **그 덱의** 설정으로 만든다 —
 *  유니온은 줄마다 보스도 설정도 다르므로 덱 없이 부르면 안 된다. */
function battlePayload(d = null) {
  const b = battleFor(d);
  const enemy = {
    code: d ? enemyCodeFor(d) : enemyCode(),
    def: b.def, core_px: b.core_px, has_parts: b.has_parts,
    optimal_range_weapons: [...b.optimal_range_weapons],
    weapon_coeff: { ...b.weapon_coeff },
  };
  const config = {
    duration: durationNow(),
    first_burst_time: b.first_burst_time,
    burst_switch_delay: b.burst_switch_delay,
    burst_reenter_delay: b.burst_reenter_delay,
    part_break_interval: b.part_break_interval,
  };
  // 0은 «무제한»이라는 뜻이고 계산기에서는 null이다 — 0을 그대로 보내면 한 번도 못 쓴다
  if (b.max_burst_count > 0) config.max_burst_count = b.max_burst_count;
  return { enemy, config };
}
const resultOf = (d) => (isFull(d) ? results[fingerprint(d)] : null);
const pendingDecks = () => [...Array(deckCountNow()).keys()]
  .filter((i) => isFull(deckAt(i)) && !resultOf(deckAt(i)));

/** 니케별 딜을 **배치 순서**로 늘어놓는다 — 딜 순 아님. 편성을 보면서 대조하려는
 *  화면(결과·기록 상세·복사)이 전부 이 순서를 쓴다. `chars`에 없는 이름은 빼고,
 *  `names`에 없는 이례적인 키(있을 일은 없지만)는 뒤에 붙여 값을 잃지 않는다. */
function charsByFormation(names, chars) {
  chars = chars || {};
  const order = (names || []).filter(Boolean);
  const inOrder = order.filter((nm) => nm in chars).map((nm) => [nm, chars[nm]]);
  const extra = Object.entries(chars).filter(([nm]) => !order.includes(nm));
  return [...inOrder, ...extra];
}

// ── 덱 조작 ─────────────────────────────────────────────────────────────
function place(name, deckIdx, slotIdx) {
  const d = deckOf(deckIdx);
  const at = d.names.indexOf(name);
  if (at === slotIdx) return;
  const displaced = d.names[slotIdx];
  // 덮어썼으면 그 칸에서 되돌릴 수 있어야 한다 — 빈 칸이 안 생겨 실수를 더 못 알아챈다
  sSnap(displaced && displaced !== name ? T("{displaced} → {name} 교체", { displaced, name }) : T("{name} 배치", { name }),
        displaced && displaced !== name ? { deckIdx, idx: slotIdx } : null);
  d.names[slotIdx] = name;
  // 큐브칸은 자리에 붙지만 **자리를 맞바꾸면 같이 따라간다** — 그래야 「이 니케에
  // 이 큐브」라는 짝이 드래그 뒤에도 유지된다(deckOf 주석).
  d.cubes ||= Array(SLOTS).fill(null);
  if (at !== -1) {
    d.names[at] = displaced;   // 같은 덱 안에서 옮기면 자리 교환
    [d.cubes[at], d.cubes[slotIdx]] = [d.cubes[slotIdx], d.cubes[at]];
  } else {
    // 다른 덱에 이미 있던 걸 끌어왔으면 그 자리에 원래 있던 아이(displaced)를
    // 보낸다 — 두 덱에 걸쳐 자리를 맞바꾼다. 그냥 비우면(null) 놓인 자리에
    // 있던 니케가 사라진 것처럼 보인다(유저 피드백: 서로 바뀌어야지 사라지면
    // 안 된다). 대상 칸이 비어 있었으면(displaced가 null) 그대로 비워 둔다 —
    // 5덱 배치 모드는 25칸이 한 화면에 있어 덱 간 드래그가 가능하다.
    for (let i = 0; i < DECK_COUNT; i++) {
      if (i === deckIdx) continue;
      const other = deckOf(i);
      const oi = other.names.indexOf(name);
      if (oi !== -1) {
        other.names[oi] = displaced;
        other.cubes ||= Array(SLOTS).fill(null);
        [other.cubes[oi], d.cubes[slotIdx]] = [d.cubes[slotIdx], other.cubes[oi]];
        break;
      }
    }
  }
  saveAll();
  renderAll();
}

/** 누르기 경로 — 활성 덱의 첫 빈 슬롯에 넣는다. 꽉 찼으면 '고른 상태'로 둔다. */
function tapPlace(name) {
  // 유니온은 자기 저장소로 간다 — 솔로 덱을 건드리면 안 된다
  if (modeNow() === "union") return uTapPlace(name);
  const d = deckOf(state.settings.deck);
  const at = d.names.indexOf(name);
  if (at !== -1) { d.names[at] = null; saveAll(); renderAll(); return; }  // 다시 누르면 뺀다
  const empty = d.names.indexOf(null);
  if (empty !== -1) { place(name, state.settings.deck, empty); picked = null; return; }
  picked = picked === name ? null : name;
  setStatus(picked ? T("{picked} — 놓을 슬롯을 누르세요", { picked }) : "", false);
  renderAll();
}

/** 유니온 누르기 — 세 줄을 위에서부터 훑어 첫 빈 칸에 넣는다.
 *  이미 어딘가에 있으면 뺀다(솔로와 같은 손버릇). 중복 편성은 불가라 한 명은 한 자리다. */
function uTapPlace(name) {
  uSnap(T("{name} 배치/빼기", { name }));
  for (let i = 0; i < UNION_DECKS; i++) {
    const at = uDeck(i).names.indexOf(name);
    if (at !== -1) { uDeck(i).names[at] = null; saveAll(); renderAll(); return; }
  }
  for (let i = 0; i < UNION_DECKS; i++) {
    const empty = uDeck(i).names.indexOf(null);
    if (empty !== -1) {
      uDeck(i).names[empty] = name; picked = null; saveAll(); renderAll();
      slamSlot(i, empty);            // 눌러서 담아도 «쾅»은 똑같이 난다
      return;
    }
  }
  picked = picked === name ? null : name;
  setStatus(picked ? T("{picked} — 놓을 칸을 누르세요", { picked }) : "", false);
  renderAll();
}

// 솔로 되돌리기 — 유니온과 **같은 규약, 다른 상자**다. 한 번 실수로 빼면 다시 짜기가
// 성가신 것은 어느 쪽이나 같다. 계산 결과는 이름으로 찾으므로(fingerprint) 되돌리면
// 옛 결과가 그대로 다시 붙는다.
const SUNDO_MAX = 40;
let sUndo = [];

/** 바꾸기 직전의 5덱을 찍는다. `at`은 «그 자리에서 되돌릴 수 있는 일»의 좌표다. */
function sSnap(label, at = null) {
  if (modeNow() === "union") return;
  sUndo.push({ label, at, decks: JSON.parse(JSON.stringify(state.decks)) });
  if (sUndo.length > SUNDO_MAX) sUndo.shift();
}

/** 그 칸이 «방금 손댄 자리»인가 — 맞으면 되돌리기 단추가 뜬다. */
function sUndoSpotAt(deckIdx, idx) {
  const top = sUndo[sUndo.length - 1];
  return top?.at && top.at.deckIdx === deckIdx && top.at.idx === idx ? top : null;
}

function sUndoLast() {
  const last = sUndo.pop();
  if (!last) return;
  state.decks = last.decks.map((d) => ({ ...d, names: [...d.names] }));
  picked = null;
  saveAll();
  renderAll();
  flashStatus(T("되돌렸습니다 — {label}", { label: last.label }));
}

function clearSlot(deckIdx, slotIdx) {
  const who = deckOf(deckIdx).names[slotIdx];
  if (who) sSnap(T("{who} 빼기", { who }), { deckIdx, idx: slotIdx });
  deckOf(deckIdx).names[slotIdx] = null;
  saveAll();
  renderAll();
}

// 솔로레이드는 덱 간 중복이 불가하다. 풀에서 잠그되 경고도 함께 남긴다.
function duplicated() {
  const seen = new Map();
  const decks = modeNow() === "union" ? U().decks : state.decks;
  for (const d of decks) for (const n of d.names) if (n) seen.set(n, (seen.get(n) ?? 0) + 1);
  return new Set([...seen].filter(([, c]) => c > 1).map(([n]) => n));
}

function toggleFav(name) {
  const i = state.favs.indexOf(name);
  if (i === -1) state.favs.push(name);
  else state.favs.splice(i, 1);
  saveAll();
  renderPools();          // 전투력 계산기 격자도 같은 카드를 쓴다 — 한쪽만 그리면 표시가 어긋난다
}

// ── 카드 ────────────────────────────────────────────────────────────────
function card(name, opts = {}) {
  const rec = byName.get(name);
  const sp = charSpec(name);
  const fig = el("figure", "nk");
  fig.dataset.name = name;
  // 등급은 **색**이다 (SSR 금색 · SR 보라 · R 파랑). 텍스트 배지를 두지 않는다.
  if (rec?.rare) fig.dataset.rare = rec.rare;
  fig.ondragstart = () => false;
  if (opts.dim) fig.classList.add("dim");
  if (opts.on) fig.classList.add("on");
  if (opts.dup) fig.classList.add("dup");
  if (picked === name) fig.classList.add("picked");
  if (opts.usedIn) fig.classList.add("used");
  // 인접 버프는 카드 하나하나에 테두리를 두르지 않는다 — **묶인 3명 전체**를
  // 사각형 하나로 감싼다(`renderSlots()`의 `.adj-frame`). 카드마다 따로 두르면
  // 「셋이 한 무리」라는 느낌이 안 살고 뭘 여러 번 두른 것처럼 산만해진다.
  // 인게임처럼 우상단에 파티 번호. 지금 덱이면 그 번호, 다른 덱이면 그 덱 번호다.
  fig.tabIndex = opts.dim ? -1 : 0;

  // 5덱 배치 모드 — 얼굴만 보이는 정사각형 카드. 배지·이름 띠·별을 다 걷어내
  // 25칸을 한 화면에 욱여넣는다. 이름은 title 툴팁으로만 남는다.
  //
  // 초상화(256×512)를 잘라 억지로 정사각형을 만들지 않는다 — 캐릭터마다 머리
  // 위치가 달라 하나의 크롭 기준으로는 다 안 맞았다(유저 피드백: 얼굴이 잘려
  // 보인다). 대신 인게임 스쿼드 목록이 실제로 쓰는 68×68 얼굴 카드(`rec.face`,
  // scraper/cdn_face.py 수집)를 그대로 쓴다 — 이미 정사각으로 잘 잡혀 있다.
  if (opts.compact) {
    fig.classList.add("compact");
    fig.title = name + skinNote(rec, name);
    const art = el("div", "nk-art");
    if (rec?.face || rec?.img) {
      const img = el("img");
      img.src = faceSrc(rec, name);
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.draggable = false;
      art.append(img);
    } else art.append(el("span", "nk-noart", name));
    fig.append(art);
    if (opts.dmg != null) fig.append(el("span", "nk-dmg", `${I18N.dmg(opts.dmg)}`));
    return fig;
  }

  // 오른쪽은 왼쪽 배지 레일과 짝을 이루는 우리 쪽 레일이다 — 위에서부터
  // 파티 번호(인게임 위치) · 즐겨찾기 · 설정. 예전엔 설정이 우하단이라 MAX 배지와
  // 겹쳤다.
  // 오른쪽 레일 — 위에서부터 ⚙ · ★. 파티 번호는 좌측 배지 레일 맨 위로 갔다
  // (⚙가 우상단을 쓰므로 겹친다).
  const railR = el("div", "nk-rail-r");
  fig.append(railR);

  const art = el("div", "nk-art");
  if (rec?.img) {
    const img = el("img");
    img.src = artSrc(rec, name);
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.draggable = false;                // 네이티브 이미지 드래그가 포인터를 가로챈다
    img.width = 256; img.height = 512;    // 레이아웃이 이미지 도착을 기다리지 않게
    art.append(img);
  } else art.append(el("span", "nk-noart", name));
  fig.append(art);

  // 좌측 배지 레일 — 속성 · 버스트 · 역할군 · 소장품(우리가 더한 슬롯)
  // 인게임 좌측 레일: 속성 → 역할군 → 버스트 → 애장품 하트. 네 육각이다.
  const rail = el("div", "nk-rail");
  if (opts.party) rail.append(el("span", "nk-party", `P${opts.party}`));
  rail.append(badgeImg(ELEMENT_ICON[rec?.element], rec?.element, "bdg-code"));
  rail.append(badgeImg(CLASS_ICON[rec?.cls], rec?.cls, "bdg-cls"));
  rail.append(badgeImg(BURST_ICON[rec?.burst], T("버스트 {v}", { v: rec?.burst ?? "?" }), "bdg-burst"));
  if (sp) {
    const g = gradeBadge(sp, name);
    if (g) rail.append(g);
  }
  fig.append(rail);

  // 하단 — 인게임 구조: 사선으로 잘린 어두운 띠에 **기업 엠블럼이 별 뒤로 깔리고**,
  // 그 아래 줄이 이름이다. 맨 아래 등급색 마감선이 카드를 닫는다.
  // (버프 뱃지는 여기 안 들어간다 — foot의 사선 마스크가 상자 밖으로 나간 것을
  // 지워 버려서 다른 자리에 따로 띄운다. 아래 `opts.adj` 블록 참고.)
  const foot = el("div", "nk-foot");
  const line1 = el("div", "nk-line1");
  if (sp) {
    const st = starInfo(rec?.rare, sp.breakthrough, sp.core_enhancement);
    const stars = el("div", "nk-stars");
    for (let i = 0; i < st.max; i++) {
      const im = el("img");
      im.src = `image/icon/${i < st.active ? STAR_ON : STAR_OFF}`;
      im.alt = ""; im.draggable = false;
      stars.append(im);
    }
    if (st.max) line1.append(stars);
    if (st.breakNum) {
      const c = el("span", "nk-core" + (st.breakNum === "MAX" ? " max" : ""),
                   st.breakNum === "MAX" ? "MAX" : String(st.breakNum));
      c.title = T("돌파 {v} · 코어 강화 {v1}", { v: sp.breakthrough ?? 0, v1: sp.core_enhancement ?? 0 });
      line1.append(c);
    }
  }
  foot.append(line1);

  const nm = el("div", "nk-nm");
  const track = el("span");
  track.append(el("i", null, name));
  nm.append(track);
  foot.append(nm);
  fig.append(foot);

  // 뱃지는 **이웃한테만** 단다 — 본인 카드는 테두리(위 `adjbuff` 클래스)로만
  // 「무리에 묶여 있다」를 표시하고, 「내가 내 버프를 받는다」는 새삼스러운 뱃지는
  // 안 붙인다.
  const adjOthers = opts.adj?.filter((h) => !h.self) || [];
  if (adjOthers.length) {
    // 별·코강 줄 바로 위에 뜨는 버프 뱃지 — **`foot`의 형제**로 붙인다. `foot` 안에
    // 넣으면 사선 마스크(`clip-path`)가 상자 밖으로 나간 부분을 지워 버리고(실측:
    // 사라져 안 보임), 그 자리를 만들려고 `foot` 안에서 높이를 늘리면 사선 띠
    // 모양까지 같이 바뀐다(실측: 커지거나 작아짐). 형제로 두면 `foot`는 원래
    // 모양 그대로고, 뱃지는 `foot` 위(z-index)에서 마스크를 안 타 잘리지 않는다.
    // 정확한 높이(별 줄 바로 위)는 카드가 실제로 화면에 붙은 뒤에만 잴 수 있어
    // `positionAdjBuffs()`가 삽입 직후 한 번 더 잡아 준다.
    const buffs = el("div", "nk-buffs");
    const byCaster = new Map();
    for (const h of adjOthers) if (!byCaster.has(h.caster)) byCaster.set(h.caster, h.buffs);
    for (const [caster, cbuffs] of byCaster) {
      // 육각 하나에 글자 하나 — «루»(루주)·«플»(플로라)처럼 첫 글자만으로 누구인지
      // 짐작이 간다. 정확한 버프 이름은 툴팁에 있다.
      const b = el("span", "bdg bdg-adj", caster.slice(0, 1));
      b.title = T("{caster}의 양옆 버프 — {v}", { caster, v: (cbuffs || []).join(" · ") });
      const sig = ADJ_COLOR[caster];
      if (sig) b.style.setProperty("--adj-c", sig);
      buffs.append(b);
    }
    fig.append(buffs);
    fig._adjBuffs = buffs;
  }

  if (sp) {
    const cog = el("button", "nk-cog" + (isEdited(name) ? " edited" : ""), "⚙");
    cog.type = "button";
    cog.title = isEdited(name) ? T("육성 수정됨 — 눌러서 보기") : T("이 니케만 육성 수정");
    cog.onclick = (e) => { e.stopPropagation(); openSheet(name); };
    // 오른쪽 레일 맨 아래. 스쿼드에서는 ✕ 아래, 로스터에서는 ★ 아래에 선다.
    railR.append(cog);
  }
  if (!opts.inSlot) {
    // 즐겨찾기 — 인게임 로스터 카드의 북마크 자리(우상단)를 그대로 쓴다
    const fav = el("button", "nk-fav" + (state.favs.includes(name) ? " on" : ""), "★");
    fav.type = "button";
    fav.title = T("즐겨찾기 — 위쪽 ★ 버튼을 켜면 즐겨찾기한 니케만 보입니다");
    fav.onclick = (e) => { e.stopPropagation(); toggleFav(name); };
    railR.append(fav);
  }

  return fig;
}

function badgeImg(file, title, extra = "") {
  const s = el("span", "bdg" + (extra ? " " + extra : ""));
  if (file) {
    const i = el("img");
    i.src = `image/icon/${file}`;
    i.alt = "";
    i.draggable = false;
    s.append(i);
  }
  if (title) s.title = title;
  return s;
}

/** 소장품/애장품 배지 — **인게임 아이템 그림 그대로**.
 *
 *  그림은 `scraper/cdn_icons.py`가 인게임 CDN(`/icon/favoriteitem/*`)에서 뽑아 온 것이고,
 *  조회는 두 갈래다. 애장품(SSR)은 캐릭터 전용이라 **이름**으로, 소장품(R·SR)은 무기군
 *  공용이라 **등급+무기군**으로 찾는다 — CSV에는 아이템 id가 없어 id로는 못 찾는다.
 *  그림을 못 찾으면 예전처럼 색 다이아로 물러난다. */
/** 인게임과 같은 **하트 육각**. 배경/하트 색 조합이 곧 상태다.
 *
 *  | 상태                    | 육각 배경 | 하트   |
 *  |-------------------------|-----------|--------|
 *  | R·SR **15레벨 미만**    | 흰색      | 등급색 |
 *  | **R15 · SR15** (만레벨) | 등급색    | 흰색   |
 *  | **애장품 3단계**        | 검정      | 주황   |
 *  | 애장품 1·2단계          | 흰색      | 주황   |
 *
 *  실제 아이템 그림(`image/icon/si_favoriteitem_*`)도 갖고 있지만, 20px 배지에 인형·
 *  커피잔 그림을 넣으면 정보가 아니라 장식으로 보인다. 그림은 자리가 있는 편집 시트에서 쓴다. */
function gradeBadge(sp, name) {
  const fav = sp.favorite_stage;
  if (fav != null && fav > 0) {
    // 애장품 등급은 늘 SSR(주황)이고, 만단계(3)에서만 배경이 검정으로 뒤집힌다
    return favBadge(fav >= 3 ? "max-ssr" : "sub", "var(--color-grade-ssr)",
                    T("애장품 {fav}단계", { fav }));
  }
  const st = sp.collection_stage;
  if (!st || st === "없음") return null;
  const m = /^(SSR|SR|R)(\d*)$/.exec(st);
  const grade = m ? m[1] : "R";
  const lv = m && m[2] ? Number(m[2]) : 0;
  const color = grade === "SSR" ? "var(--color-grade-ssr)"
    : grade === "SR" ? "var(--color-grade-sr)" : "var(--color-grade-r)";
  return favBadge(lv >= 15 ? "max" : "sub", color, T("소장품 {st}", { st }));
}


function favBadge(mode, color, title) {
  const s = el("span", "bdg bdg-fav");
  s.dataset.fav = mode;
  s.title = title;
  s.style.setProperty("--grade", color);
  return s;
}


// ── 렌더 ────────────────────────────────────────────────────────────────
function renderAll() {
  renderBench();
  renderDeckTabs(); renderSlots(); renderScore(); renderPools(); renderResults();
  buildControl(); renderTopAtk(); renderLowAtk(); renderCompWarn();
  if (fastMode) { renderFastGrid(); renderFastTotal(); }
}

/** 5명이 다 찼을 때만 — 편성이 «성립은 하지만 놓친 게 있는» 흔한 실수 세 가지를
 *  본다. 계산 없이 이름만 보고 즉시 답할 수 있는 것들만 다룬다(정확한 값은
 *  계산 결과가 답한다는 이 앱의 다른 진단들과 같은 방침).
 *
 *  ① 약점 저지 — 캐릭터 속성이 지금 고른 약점 코드와 하나도 안 맞으면
 *     상성 우월 보너스를 통째로 못 받는다.
 *  ② 버스트 쿨타임 감소 — 아군 전체에게 주는 니케가 하나도 없으면 사이클이
 *     길어져 3버가 밀린다(계산은 정상이지만 실전에서 체감이 다르다는 뜻).
 *  ③ 풀버스트 순환 — 1·2·3단계 버스트가 다 있어야 풀버스트가 열린다
 *     (`burstStages`, 리버렐리오 진단이 이미 쓰던 것과 같은 판정). */
function renderCompWarn() {
  const box = $("#deck-compwarn");
  if (!box) return;
  const d = deckOf(state.settings.deck);
  const names = d.names.filter(Boolean);
  if (names.length < 5) { box.hidden = true; box.textContent = ""; return; }

  const warns = [];
  if (state.settings.code) {
    const hasElem = names.some((n) => byName.get(n)?.element === state.settings.code);
    if (!hasElem) warns.push(T("약점 {code}에 우월한 속성이 없습니다.", { code: state.settings.code }));
  }
  if (!names.some((n) => CDR_CASTERS.has(n))) {
    warns.push(T("아군 전체 버스트 쿨타임 감소가 없습니다 — 3버 순번이 밀릴 수 있습니다."));
  }
  const bs = burstStages(names);
  if (!bs.ok) {
    warns.push(T("{v} 버스트가 없어 풀버스트가 열리지 않습니다.", { v: bs.missing.map((x) => x + T("단계")).join("·") }));
  }

  box.textContent = "";
  if (!warns.length) { box.hidden = true; return; }
  box.hidden = false;
  for (const w of warns) box.append(el("p", "squad-warn-line", w));
}

function renderDeckTabs() {
  const wrap = $("#deck-tabs");
  wrap.textContent = "";
  for (let i = 0; i < DECK_COUNT; i++) {
    const d = deckOf(i);
    const on = i === state.settings.deck;
    const btn = el("button", "deck-tab" + (on ? " on" : "") + (isFull(d) ? " filled" : ""),
      String(i + 1).padStart(2, "0"));
    btn.type = "button";
    btn.dataset.deck = String(i);
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(on));
    btn.title = T("덱 {v} — {v1}/5명 — 끌어서 순서를 바꿀 수 있습니다", { v: i + 1, v1: d.names.filter(Boolean).length });
    btn.onclick = () => { state.settings.deck = i;
      picked = null; saveAll(); renderAll(); };
    // 탭 번호 자체가 덱 순서를 바꾸는 손잡이다(유저 피드백: 배치모드 옆 오른쪽
    // 위 숫자로 옮기게 해 달라 — 결과 탭의 합계 알약이 아니라 **이 탭**이었다).
    btn.addEventListener("pointerdown", (e) => startDeckDrag(e, i, ".deck-tab"));
    wrap.append(btn);
  }
}

/** 카드가 DOM에 실제로 붙은 **뒤에** 부른다. 붙기 전에는 `.nk-line1`의 좌표를
 *  잴 수 없다(떨어져 있는 노드는 크기가 전부 0으로 나온다) — 그래서 `card()`
 *  안에서는 자리만 만들어 두고, 삽입 직후 이 함수가 실측해서 자리를 잡는다. */
function positionAdjBuffs(fig) {
  const buffs = fig?._adjBuffs;
  const line1 = fig?.querySelector(".nk-line1");
  if (!buffs || !line1) return;
  const fr = fig.getBoundingClientRect(), lr = line1.getBoundingClientRect();
  buffs.style.bottom = `${Math.round(fr.bottom - lr.top) + 2}px`;
}

/** 큐브 한 칸. **카드 바로 아래, 컨트롤 막대 위**에 붙는다 — 큐브는 니케 육성이
 *  아니라 그 자리에 끼울 자원이라 카드 톱니(육성 수정)가 아니고, 슬롯 밖에 따로
 *  두면 컨트롤 막대가 사이에 끼어 어느 카드 것인지 안 보인다(실측으로 겪었다).
 *  「지금 실제로 무엇을 끼고 있나」는 전투력 계산기가 보여 준다. */
function cubeCell(d, i) {
  // 렐릭 베어(재장전)를 맨 위에 두고 나머지는 가나다순. «기본» 같은 빈 항목은 두지
  // 않는다 — 화면에 보이는 것과 계산에 들어가는 것이 항상 같아야 한다.
  const { names, ordered } = cubeChoices();
  {
    // 계산에 들어가는 값과 **같은 함수**로 고른다 (cubeOf) — 둘이 갈라지면 카드에
    // 보이는 큐브와 실제 계산이 어긋난다
    const cur = cubeOf(d, i);
    // 니케 이름은 안 적는다 — 바로 위 카드가 곧 그 정보다. 툴팁에만 남긴다.
    const cell = el("div", "cube-cell" + (cur ? " on" : ""));
    cell.title = d.names[i] || T("{v}번 칸", { v: i + 1 });
    // **큐브 이름 대신 효과로 적는다** — 「렐릭 베어」가 무슨 큐브인지 외우고 있는
    // 사람은 없다. 고르는 자리에는 «재장전 속도»처럼 오르는 스탯이 보여야 한다.
    const NAMES = ordered.map((c) => [c, cubeStatLabel(c)]);
    const nameSel = selectEl(NAMES, cur?.name ?? "", (v) => {
      d.cubes[i] = { name: v, level: cur?.level ?? CUBE_DEFAULT.level };
      saveAll(); refreshSlots(); renderResults();
    }, !ordered.length);
    // 이름 툴팁 = **무엇이 오르는 큐브인지**(효과 종류). 레벨 툴팁은 그 레벨의 실제
    // 수치다 — 이름만 보고는 재장전인지 공격인지 알 수 없고, 목록에서 고르는 자리에
    // 그 정보가 없으면 매번 다른 화면을 찾아봐야 한다.
    nameSel.title = cur ? cubeEffect(cur.name, cur.level) : "";
    cell.append(nameSel);
    // 레벨은 **항상 보인다.** 큐브를 고른 뒤에야 나타나면 「레벨도 정해야 한다」는 걸
    // 모른 채 지나가고, 칸 폭도 그때그때 달라져 줄이 흔들린다.
    // Lv0 = 미장착. 진짜로 큐브를 안 끼고 도는 편성을 표현할 수 있어야 한다
    // (계산기도 레벨 0을 플랫 스탯 0·스킬 없음으로 받는다 — base_stat.py).
    const LVS = [[0, T("미장착")], ...Array.from({ length: 15 }, (_, k) => [k + 1, `Lv${k + 1}`])];
    const lvSel = selectEl(LVS, cur?.level ?? 15, (v) => {
      // 큐브를 아직 안 골랐으면 레벨만 바꿔도 의미가 없다 — 기본 큐브를 함께 채운다.
      const nm = cur?.name || names[0];
      if (nm) d.cubes[i] = { name: nm, level: Number(v) };
      saveAll(); refreshSlots(); renderResults();
    }, !names.length);
    lvSel.title = cur
      ? cubeEffect(cur.name, cur.level)
      : T("큐브를 고르면 이 레벨의 수치가 적용됩니다");
    cell.append(lvSel);
    return cell;
  }
}

function renderSlots() {
  const deckIdx = state.settings.deck;
  const wrap = $("#slots");
  const d = deckOf(deckIdx);
  const dup = duplicated();
  const adj = adjHitsIn(d.names);
  wrap.textContent = "";
  d.names.forEach((name, idx) => {
    const slot = el("div", "slot" + (name ? " has" : ""));
    let cell = null;                    // 찬 슬롯은 [카드 + 컨트롤 막대]로 감싼다
    slot.dataset.deck = String(deckIdx);
    slot.dataset.idx = String(idx);
    if (name) {
      // 스쿼드 안은 이미 "편성됨"이 자명하다 — 시안 체크는 로스터 쪽에만 단다
      slot.append(card(name, { dup: dup.has(name), inSlot: true, adj: adj.get(name) }));
      const x = el("button", "slot-x", "✕");
      x.type = "button";
      x.title = T("슬롯 비우기");
      x.onclick = (e) => { e.stopPropagation(); clearSlot(deckIdx, idx); };
      slot.append(x);
      slot.querySelector(".nk").addEventListener("pointerdown",
        (e) => startDrag(e, name, { deckIdx, idx }));
      // 카드 아래 «확장» — 이 니케의 컨트롤을 슬롯 줄 바로 밑에 펼친다.
      // 덱 전체를 한 목록으로 늘어놓는 것보다, 고칠 니케 자리에서 여는 편이 짧다.
      const more = el("button", "slot-more" + (ctrlOpen === name ? " on" : ""));
      more.type = "button";
      more.title = T("{name} 컨트롤 설정", { name });
      const on = Object.keys(d.control?.[name] || {}).length;
      more.append(el("span", null, on ? T("컨트롤 {on}", { on }) : T("컨트롤")));
      more.append(el("i", null, "▾"));
      if (on) more.classList.add("has");
      more.onclick = (e) => {
        e.stopPropagation();
        patDraft = null;
        ctrlOpen = ctrlOpen === name ? null : name;
        renderAll(); buildControl();
      };
      // 슬롯은 카드 비율로 고정돼 있다 — 막대를 그 안에 넣으면 카드가 눌리므로
      // 슬롯과 막대를 함께 감싸 세로로 쌓는다. **`slot.onclick`은 아래에서 그대로
      // 걸린다** — 여기서 일찍 빠져나가면 찬 슬롯에 다른 니케를 못 놓는다.
      cell = el("div", "slot-wrap");
      // 카드 → 큐브 → 컨트롤 순. 큐브를 컨트롤 아래에 두면 카드와 떨어져
      // 어느 니케 것인지 안 보인다(실측으로 겪었다).
      cell.append(slot, cubeCell(d, idx), more);
    } else {
      slot.append(el("span", "slot-no", "+"));
      cell = el("div", "slot-wrap");
      // 빈 칸도 큐브칸·컨트롤이 **자리에 그대로 있다.** 채워질 때 생겨나면 줄 높이가
      // 흔들리고 무엇이 들어올 자리인지도 안 읽힌다 — 누를 사람이 없을 뿐이라
      // 진짜 «비활성 버튼»으로 둔다(모양이 아니라 상태로 말한다).
      const gap = el("button", "slot-more slot-more-gap");
      gap.type = "button";
      gap.disabled = true;
      gap.append(el("span", null, "컨트롤"), el("i", null, "▾"));
      cell.append(slot, cubeCell(d, idx), gap);
    }
    // 방금 여기서 빼거나 바꿨다면 **그 자리에서** 되돌린다
    const sSpot = sUndoSpotAt(deckIdx, idx);
    if (sSpot) {
      slot.classList.add("has-undo");
      const back = el("button", "u-undo", "↩");
      back.type = "button";
      back.title = T("{label} — 되돌리기", { label: sSpot.label });
      back.onclick = (e) => { e.stopPropagation(); sUndoLast(); };
      slot.append(back);
    }
    slot.onclick = () => {
      if (picked) { place(picked, deckIdx, idx); picked = null; setStatus(""); }
    };
    wrap.append(cell || slot);
    // DOM에 붙은 **뒤**에만 잴 수 있다 — 그래서 `card()` 안이 아니라 여기서 부른다.
    if (name && adj.get(name)?.length) positionAdjBuffs(slot.querySelector(".nk"));
  });

  // 인접 버프 무리 — 캐스터+양옆을 사각형 하나로 감싼다. `.slots`가 그리드라
  // 칸 번호(1-based)만 지정하면 그 사이 간격까지 포함해 깔끔하게 이어진다.
  for (const g of adjGroupsIn(d.names)) {
    const frame = el("div", "adj-frame");
    frame.style.gridColumn = `${g.lo + 1} / ${g.hi + 2}`;
    frame.style.setProperty("--adj-frame-c", ADJ_COLOR[g.caster] || "var(--color-info)");
    frame.title = T("{caster}의 양옆 버프 무리", { caster: g.caster });
    wrap.append(frame);
    // **카드(초상화)까지만** — 칸 전체 높이로 두면 그 밑의 «컨트롤» 펼침 버튼까지
    // 덮인다. 그건 캐릭터가 아니라 우리 UI라 감쌀 이유가 없다. DOM에 붙은 뒤에만
    // 실제 카드 높이를 잴 수 있어(`positionAdjBuffs`와 같은 이유) 여기서 잰다.
    const anyCard = wrap.children[g.lo]?.querySelector(".nk");
    if (anyCard) frame.style.height = `${anyCard.getBoundingClientRect().height}px`;
  }

  const res = resultOf(d);
  $("#deck-total").textContent = d.calcState === "run" ? T("계산 중…")
    : d.error ? T("오류") : res ? `${I18N.dmg(res.total)}` : isFull(d) ? T("미계산") : "—";
  $("#deck-notes").textContent = d.error ? d.error : (res?.notes || "");
  renderGrowthFlags(d.error ? null : res?.growth_flags);

  const btn = $("#deck-calc");
  btn.disabled = !isFull(d) || !!d.calcState;
  btn.dataset.state = d.calcState === "run" ? "loading" : "";
  // 이미 나온 덱을 다시 누르는 건 «재계산»이다 — 같은 라벨이면 눌러도 아무 일이
  // 없는 것처럼 보인다(계산 목록에서 걸러지므로 실제로도 아무 일이 없었다).
  btn.textContent = res ? T("덱 재계산") : T("덱 계산");

  // 전체 계산 — 아직 결과가 없는 '꽉 찬' 덱이 있을 때만 누를 수 있다
  const todo = pendingDecks();
  const nDecks = deckCountNow();
  const anyRunning = [...Array(nDecks).keys()].some((i) => deckAt(i).calcState);
  for (const sel of ["#deck-calc-all", "#res-calc", "#fast-calc-all"]) {
    const all = $(sel);
    if (!all) continue;
    // 다 계산했으면 «전체 재계산»으로 바뀐다 — 같은 라벨로 비활성만 시키면
    // 스펙을 손본 뒤 다시 돌릴 방법이 없다.
    const ready = [...Array(nDecks).keys()].filter((i) => isFull(deckAt(i)));
    const done = ready.length && !todo.length;
    all.disabled = anyRunning || !ready.length;
    all.dataset.state = anyRunning ? "loading" : "";
    // 유니온의 «전체 계산»은 말 그대로 **전부** 다시 돈다. 세 줄이 한 출격 묶음이라
    // 「2줄만 계산」 같은 건 뜻이 없다 — 묶음 총딜을 보려고 누르는 버튼이다.
    if (modeNow() === "union") {
      all.textContent = T("전체 계산");
      all.dataset.force = "1";
    } else {
      all.textContent = done ? T("전체 재계산 ({length}덱)", { length: ready.length })
        : todo.length > 1 ? T("전체 계산 ({length}덱)", { length: todo.length }) : T("전체 계산");
      all.dataset.force = done ? "1" : "";
    }
  }

  // 계산해 둔 덱이 하나라도 있으면 «결과 보기»를 보여 준다 — 계산 버튼만 누르고
  // 결과 탭까지 직접 눌러 넘어가야 하는 걸음을 줄인다.
  const goto = $("#deck-goto-result");
  if (goto) {
    goto.hidden = ![...Array(DECK_COUNT).keys()].some((i) => resultOf(deckOf(i)));
  }
}

function renderScore() {
  const dup = duplicated();
  let sum = 0, known = 0;
  const each = el("div", "score-each");
  for (let i = 0; i < DECK_COUNT; i++) {
    const r = resultOf(deckOf(i));
    if (r) { sum += r.total; known++; }
    // 덱 번호는 알약의 **자리**가 이미 말해 준다 — 숫자를 붙이면 값이 파묻힌다.
    const pill = el("span", "score-pill" + (r ? " on" : ""),
                    r ? `${I18N.dmg(r.total)}` : "—");
    pill.title = T("{v}덱 — 끌어서 순서를 바꿀 수 있습니다", { v: String(i + 1).padStart(2, "0") });
    pill.dataset.deck = String(i);
    pill.addEventListener("pointerdown", (e) => startDeckDrag(e, i, ".score-pill"));
    each.append(pill);
  }
  const box = $("#score");
  box.textContent = "";
  box.append(el("span", null, T("{known}/{DECK_COUNT}덱 합계", { known, DECK_COUNT })),
             el("b", null, known ? `${I18N.dmg(sum)}` : "—"), each);
  $("#dup-warn").textContent = dup.size
    ? T("덱 간 중복: {v} — 솔로레이드에서는 불가능한 편성입니다", { v: [...dup].join(" · ") }) : "";
}

// ── 배치모드 ───────────────────────────────────────────────────────────
// 설정을 다 걷어내고 25칸(5덱×5인)을 한 화면에 펼쳐 빠르게 채우는 전용 화면.
// **state.decks를 그대로 그리는 것뿐**이라 일반 화면과 데이터가 둘일 일이
// 없다 — 껐다 켜도(`setFastMode`) 편성이 그대로다. 모드 자체는 켠 채로
// 새로고침해도 그대로 열리게 저장한다(유저 피드백) — `boot()`가 복원한다.
function applyFastModeDom(on) {
  document.querySelector('.panel[data-panel="deck"]')?.classList.toggle("fast-on", on);
  // 감싸는 div 대신 항목마다 직접 숨긴다 — 감싸면 그 자체가 하나의 flex
  // 아이템이 되어 좁은 화면에서 줄바꿈이 이상하게 갈렸다(유저 피드백: 스펙·콘솔이
  // 엉뚱하게 버튼 쪽으로 딸려 보임). 각자 원래 자리에서 개별로 사라지게 한다.
  for (const el of document.querySelectorAll(".hide-in-fast")) el.hidden = on;
  $("#deck-tabs").hidden = on;
  $("#squad-wrap").hidden = on;
  $("#fast-wrap").hidden = !on;
  // 켜져 있든 꺼져 있든 같은 파랑(btn-primary)이다 — 색으로 상태를 가르지
  // 않는다(유저 피드백: «일반 모드로»와 같은 색으로). 글자만 바뀐다.
  $("#fast-toggle-label").textContent = on ? T("✕ 일반 모드로") : T("배치모드");
  $("#fast-toggle-new").hidden = on;   // 새 기능 표는 켠 뒤엔(써 봤으니) 필요 없다
}

function setFastMode(on) {
  fastMode = on;
  state.settings.fastMode = on;
  applyFastModeDom(on);
  moveEngineRow(on);          // 배치모드에서는 «전체 계산»과 같은 줄로 간다
  picked = null;
  setStatus("");
  saveAll();
  renderAll();
}

// ── 덱 순서 바꾸기 (배치모드 줄 · 일반 모드 합계 알약 둘 다 공용) ─────────
// 카드 드래그(`startDrag`)와 다른 길이다 — 여기서 옮기는 건 니케가 아니라
// **덱 통째**(이름·컨트롤·계산 결과까지)다. 잡은 덱과 놓은 덱을 통째로
// 맞바꾼다 — 사이에 낀 덱들을 밀지 않는다(카드 슬롯 교환과 같은 결).
let deckDrag = null;

function startDeckDrag(e, deckIdx, selector) {
  if (e.pointerType === "touch") return;   // 손가락 드래그는 로스터 넘기기와 겹친다
  if (e.button != null && e.button !== 0) return;
  e.preventDefault();
  const src = document.querySelector(`${selector}[data-deck="${deckIdx}"]`);
  const rect = src?.getBoundingClientRect();
  // 원본을 통째로 복제해 커서를 따라다니는 유령을 띄운다 — «줄 자체가 같이
  // 움직이는» 느낌을 주기 위해서다(유저 피드백). pointer-events:none이라
  // elementFromPoint가 이 유령이 아니라 그 밑의 진짜 줄/알약을 잡는다.
  const ghost = el("div", "deck-drag-ghost");
  if (src) {
    ghost.append(src.cloneNode(true));
    ghost.style.width = `${rect.width}px`;
  }
  document.body.append(ghost);
  deckDrag = {
    from: deckIdx, target: null, selector, ghost,
    offX: rect ? e.clientX - rect.left : 0, offY: rect ? e.clientY - rect.top : 0,
  };
  moveDeckGhost(e.clientX, e.clientY);
  src?.classList.add("deck-dragging");
  document.addEventListener("pointermove", onDeckDragMove);
  document.addEventListener("pointerup", onDeckDragEnd, { once: true });
}

const moveDeckGhost = (x, y) => {
  deckDrag.ghost.style.transform = `translate(${x - deckDrag.offX}px, ${y - deckDrag.offY}px)`;
};

function onDeckDragMove(e) {
  if (!deckDrag) return;
  moveDeckGhost(e.clientX, e.clientY);
  const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest(deckDrag.selector);
  const hitIdx = hit ? Number(hit.dataset.deck) : null;
  if (hitIdx === deckDrag.target) return;
  document.querySelectorAll(deckDrag.selector)
    .forEach((r) => r.classList.remove("deck-drop-target"));
  if (hit && hitIdx !== deckDrag.from) hit.classList.add("deck-drop-target");
  deckDrag.target = hitIdx;
}

function onDeckDragEnd() {
  document.removeEventListener("pointermove", onDeckDragMove);
  document.querySelectorAll(".fg-row, .score-pill, .deck-tab")
    .forEach((r) => r.classList.remove("deck-dragging", "deck-drop-target"));
  const drag = deckDrag;
  deckDrag = null;
  drag?.ghost.remove();
  if (!drag || drag.target == null || drag.target === drag.from) return;
  const a = drag.from, b = drag.target;
  [state.decks[a], state.decks[b]] = [state.decks[b], state.decks[a]];
  saveAll();
  renderAll();
}

/** 25칸 그리드. 슬롯은 `.slot`을 그대로 쓰므로 기존 드래그(`startDrag`→
 *  `onDragEnd`→`place`)가 `dataset.deck`/`dataset.idx`만 보고 그대로 먹힌다 —
 *  덱마다 따로 만들 이유가 없었다. */
function renderFastGrid() {
  const wrap = $("#fast-grid");
  if (!wrap) return;
  wrap.textContent = "";
  const dup = duplicated();
  for (let di = 0; di < DECK_COUNT; di++) {
    const d = deckOf(di);
    const res = resultOf(d);
    const row = el("div", "fg-row");
    row.dataset.deck = String(di);
    // 줄 손잡이는 **줄 전체**다 — 번호칸·총딜칸뿐 아니라 총딜 오른쪽으로
    // 남는 빈 공간(줄은 `.stage` 폭까지 늘어나는데 내용은 그보다 짧다)도
    // 눈에는 같은 줄로 보이니 거기서도 잡혀야 한다(유저 피드백: 거기도
    // 드래그 되는 것처럼 보이는데 안 된다). 카드(cells)와 계산 버튼만 뺀다 —
    // 나머지 어디를 눌러도 이 줄을 잡는다. 라벨·총딜칸에 따로 달았던 손잡이는
    // 이걸로 대체한다(둘 다 있으면 버블링으로 두 번 잡혀 고스트가 겹친다).
    row.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".fg-slot, .fg-row-calc")) return;
      startDeckDrag(e, di, ".fg-row");
    });
    const label = el("div", "fg-row-label", String(di + 1).padStart(2, "0"));
    row.append(label);
    const cells = el("div", "fg-cells");
    d.names.forEach((name, idx) => {
      const slot = el("div", "slot fg-slot" + (name ? " has" : ""));
      slot.dataset.deck = String(di);
      slot.dataset.idx = String(idx);
      if (name) {
        // 계산해 둔 결과가 있으면 얼굴 카드 아래에 이 니케의 딜을 바로 얹는다
        // (`res.chars`는 니케별 총딜 — 각각 딜량을 보고 싶다는 요청).
        const c = card(name, { compact: true, inSlot: true, dup: dup.has(name),
                               dmg: res?.chars?.[name] });
        slot.append(c);
        c.addEventListener("pointerdown", (e) => startDrag(e, name, { deckIdx: di, idx }));
        const x = el("button", "slot-x", "✕");
        x.type = "button";
        x.title = T("슬롯 비우기");
        x.onclick = (e) => { e.stopPropagation(); clearSlot(di, idx); };
        slot.append(x);
      } else {
        slot.append(el("span", "slot-no", "+"));
      }
      slot.onclick = () => {
        if (picked) { place(picked, di, idx); picked = null; setStatus(""); }
      };
      cells.append(slot);
    });
    row.append(cells);
    // 총딜 바로 위에 이 덱만 다시 계산하는 버튼 — «전체 재계산»까지 안 가도
    // 이 덱 하나만 손봤을 때 바로 반영해 볼 수 있다(유저 피드백).
    const totalWrap = el("div", "fg-total-wrap");
    const totalCol = el("div", "fg-total-col");
    const calcBtn = el("button", "fg-row-calc", res ? T("재계산") : T("계산"));
    calcBtn.type = "button";
    calcBtn.disabled = !isFull(d) || !!d.calcState;
    calcBtn.onclick = () => calcDecks([di], true);
    totalCol.append(calcBtn, el("span", "fg-row-total",
      d.calcState === "run" ? T("계산 중…") : d.error ? T("오류")
        : res ? `${I18N.dmg(res.total)}` : isFull(d) ? T("미계산") : "—"));
    totalWrap.append(totalCol);
    row.append(totalWrap);
    wrap.append(row);
  }
}

function renderFastTotal() {
  const box = $("#fast-total");
  if (!box) return;
  let sum = 0, known = 0;
  for (let i = 0; i < DECK_COUNT; i++) {
    const r = resultOf(deckOf(i));
    if (r) { sum += r.total; known++; }
  }
  // 「몇 덱 합계」는 옅게, **숫자만 튀게** — 여기서 제일 궁금한 건 라벨이 아니라 값이다.
  box.textContent = "";
  box.append(el("span", "fast-total-label", T("{known}/{DECK_COUNT}덱 합계", { known, DECK_COUNT })),
             el("b", "fast-total-val", known ? `${I18N.dmg(sum)}` : "—"));
}

/** 필터·정렬을 적용한 로스터. **편성과 전투력 계산기가 같은 규칙(이 함수)을 쓰지만
    상태는 각자다** — `f`를 명시하지 않으면 편성 쪽(state.filter)을 본다. 전투력
    계산기는 state.coopFilter를 넘겨 받는다(편성에서 건 필터가 새어 들면 안 된다). */
function filteredRoster(ignoreParsed = false, f = state.filter) {
  const needle = f.q.trim();
  const any = (arr, v) => !arr.length || arr.includes(v);
  const list = ROSTER.filter((r) =>
    // 「계산 가능」 필터는 **딜 계산용**이다 — 전투력은 스킬 파싱과 무관하므로
    // 전투력 계산기는 이 조건을 건너뛰고 보유 니케 전원을 보여 준다.
    (ignoreParsed || !f.parsed || r.parsed) &&
    (!f.favOnly || state.favs.includes(r.name)) &&
    any(f.burst, String(r.burst)) &&
    any(f.element, r.element) &&
    any(f.cls, r.cls) &&
    any(f.weapon, r.weapon) &&
    any(f.corp, r.corp) &&
    (!f.favItem || hasFavItem(r.name)) &&
    (!needle || r.name.includes(needle)));
  const cmp = sorter(f.sort);
  return list.slice().sort(f.asc === false ? (a, b) => cmp(b, a) : cmp);
}

/** 두 화면의 카드 격자를 함께 다시 그린다 (필터는 공유다). */
function renderPools() {
  renderPool();
  if ($("#coop-pool")) renderCoopPool();
}

function renderPool() {
  const wrap = $("#pool");
  wrap.textContent = "";
  // 유니온은 **자기 저장소만** 본다. 솔로 5덱이 쓰는 이름을 유니온에서 잠그면
  // (실측) 스무 명 남짓이 통째로 «사용 중»이 되어 아예 안 올라간다 — 둘은 서로
  // 다른 콘텐츠이므로 중복 규칙도 각자 안에서만 따진다.
  const union = modeNow() === "union";
  const f = union ? uFilter() : state.filter;

  // 유니온은 «지금 고른 덱»이 없다 — 세 줄 15칸이 한 화면에 다 보이고, 어느 줄에
  // 있든 다시 누르면 빠진다. 그래서 잠그는 이름이 없고 «어느 줄에 있나»만 알린다.
  const uAt = new Map();
  if (union) {
    U().decks.forEach((d, di) => {
      for (const n of d.names) if (n) uAt.set(n, di + 1);
    });
  }

  // 5덱 배치 모드는 «지금 고른 덱»이 없다 — 25칸이 한 화면에 다 보이므로
  // 「어느 덱에 있나」만 따진다(있으면 잠금, 없으면 클릭으로 집는다).
  const cur = union ? new Set(uAt.keys())
    : fastMode ? new Set() : new Set(deckOf(state.settings.deck).names.filter(Boolean));
  // 다른 덱이 쓰는 이름은 잠근다 — 솔로레이드는 덱 간 중복이 불가하다.
  // 현재 덱 멤버는 잠그지 않는다 (다시 눌러 빼야 하므로).
  const usedElsewhere = new Map();
  if (!union) {
    state.decks.forEach((d, di) => {
      if (!fastMode && di === state.settings.deck) return;
      for (const n of d.names) if (n) usedElsewhere.set(n, di + 1);
    });
  }

  const list = filteredRoster(false, f);
  const cmp = sorter(f.sort);
  // 전투력은 스펙에서 온다 — `_combat`을 담기 전에 저장해 둔 스펙이라면 전원 0이라
  // 정렬이 «아무 일도 안 한 것처럼» 보인다. 조용히 이름순으로 두지 않고 말해 준다.
  const combatBlind = f.sort === "combat" && list.length
    && !list.some((r) => growNum(r.name, (sp) => sp._combat ?? 0));
  if (combatBlind) {
    wrap.append(el("p", "pool-note",
      T("이 스펙에는 전투력이 없습니다 — 다시 받아 오면 전투력순으로 정렬됩니다.")));
  }

  for (const rec of list) {
    const usedIn = usedElsewhere.get(rec.name);
    const inCur = cur.has(rec.name);
    const c = card(rec.name, {
      compact: fastMode,
      dim: !rec.parsed || !!usedIn, on: inCur, usedIn,
      party: union ? (uAt.get(rec.name) || 0)
        : inCur ? state.settings.deck + 1 : usedIn || 0,
    });
    // 유니온에서는 아래 목록도 **속성색 액자**로 든다 — 줄마다 우월 속성을 세 명씩
    // 채워야 해서, 고르는 자리에서부터 속성이 눈에 걸려야 한다. 솔로는 등급색
    // 그대로 둔다(그쪽은 속성보다 등급·중복이 먼저 읽혀야 하는 화면이다).
    if (union && CODE_VAR[rec.element]) {
      // 액자(--frame)는 **건드리지 않는다** — 아래 목록에서는 등급·편성 상태가 먼저
      // 읽혀야 한다. 속성색은 «지금 이 보스를 치는 속성» 강조에만 따로 쓴다.
      c.style.setProperty("--hit-c", CODE_VAR[rec.element]);
      c.dataset.elem = rec.element;   // 보스를 꽂을 때 이 속성만 골라 훑는다
      // 목록은 자주 다시 그려진다(칸에 넣을 때마다). 켜 둔 표시를 여기서 다시
      // 입히지 않으면 니케 하나 넣자마자 불이 꺼져 버린다.
      if (rec.element === litElem) c.classList.add("lit");
    }
    if (!rec.parsed) {
      c.title = T("스킬 미파싱 — 계산할 수 없습니다");
    } else if (usedIn) {
      c.title = fastMode ? T("{name} — 덱 {usedIn}에 있음", { name: rec.name, usedIn })
        : T("덱 {usedIn}에서 사용 중 — 덱 간 중복은 불가합니다", { usedIn });
    } else if (fastMode && !union) {
      // 여기서는 «놓을 자리»가 화면에 25칸이나 있어 바로 넣을 수 없다 —
      // 집어 두면(picked) 25칸 그리드에서 원하는 칸을 눌러 넣는다.
      c.onclick = () => {
        picked = picked === rec.name ? null : rec.name;
        setStatus(picked ? T("{picked} — 놓을 칸을 누르세요", { picked }) : "", false);
        renderAll();
      };
      c.addEventListener("pointerdown", (e) => startDrag(e, rec.name, null));
    } else {
      c.onclick = () => tapPlace(rec.name);
      c.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tapPlace(rec.name); }
      };
      c.addEventListener("pointerdown", (e) => startDrag(e, rec.name, null));
    }
    wrap.append(c);
  }
  $("#pool-count").textContent = T("{length}명", { length: list.length });
  markOverflow();
}

/** 정렬 비교기. «내 순서»는 즐겨찾기 등록 순서를 그대로 쓴다. */
function sorter(kind) {
  const ko = (a, b) => String(a).localeCompare(String(b), "ko");
  if (kind === "fav") {
    return (a, b) => {
      const ia = state.favs.indexOf(a.name), ib = state.favs.indexOf(b.name);
      if (ia !== -1 || ib !== -1) {
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      }
      return ko(a.name, b.name);
    };
  }
  if (kind === "weapon") {
    return (a, b) => WEAPONS.indexOf(a.weapon) - WEAPONS.indexOf(b.weapon) || ko(a.name, b.name);
  }
  // 육성값으로 세는 정렬. 여기 비교기는 **언제나 오름차순**이다 — 방향은 `asc`
  // 하나가 정한다. 예전엔 여기서 미리 내림차순으로 뒤집어 놓아 `asc`가 뜻하는 것과
  // 화면이 반대였다(▼인데 작은 값이 위로 왔다). 큰 값을 먼저 보여 주는 «자연스러운
  // 기본»은 정렬을 고르는 자리에서 `asc = false`로 준다. 스펙이 없는 니케는 0이다.
  const num = {
    // 인게임 전투력. 프로필의 `_combat`(UI 전용 키)에 담겨 온다 — 딜과 순위가
    // 같지는 않지만, 유저가 인게임에서 보던 숫자라 목록 기본 정렬로 쓴다.
    combat: (r) => growNum(r.name, (sp) => sp._combat ?? 0),
    elem: (r) => growNum(r.name, (sp) => sp.equip_skills?.element_bonus ?? 0),
    elematk: (r) => growNum(r.name,
      (sp) => (sp.equip_skills?.element_bonus ?? 0) + (sp.equip_skills?.atk_pct ?? 0)),
  }[kind];
  if (num) return (a, b) => num(a) - num(b) || ko(a.name, b.name);
  const key = { name: "name", burst: "burst", element: "element", cls: "cls" }[kind] || "name";
  return (a, b) => ko(a[key], b[key]) || ko(a.name, b.name);
}

/** 육성 스펙에서 숫자 하나를 꺼낸다. 스펙이 없으면 0 (정렬에서 맨 아래로 간다). */
/** 애장품(SSR 소장품)을 낀 니케인가. 인게임 «애장품» 필터와 같은 뜻이다. */
function hasFavItem(name) {
  const sp = charSpec(name);
  return !!(sp && (sp.favorite_stage ?? 0) > 0);
}

function growNum(name, pick) {
  const sp = charSpec(name);
  if (!sp) return 0;
  const v = pick(sp);
  return typeof v === "number" && isFinite(v) ? v : 0;
}

/** 넘치는 이름만 흐르게 한다. 넘친 양을 재서 넘겨야 딱 그만큼만 움직인다 —
 *  비율(-100%)로 하면 이름 길이에 따라 너무 가거나 덜 간다. */
function markOverflow() {
  const SPEED = 34;                       // 초당 픽셀. 체감 속도를 한 값으로 묶는다
  for (const nm of document.querySelectorAll(".nk-nm")) {
    const track = nm.firstElementChild;
    const first = track?.firstElementChild;
    if (!first) continue;
    const over = first.offsetWidth > nm.clientWidth + 1;
    // 사본은 넘칠 때만 둔다 — 안 넘치면 두 벌이 나란히 보여 이상하다
    while (track.children.length > (over ? 2 : 1)) track.lastElementChild.remove();
    if (over && track.children.length === 1) track.append(el("i", null, first.textContent));
    nm.classList.toggle("over", over);
    if (over) {
      // 한 벌(여백 포함) 폭을 지나는 시간. -50%가 정확히 한 벌이다.
      nm.style.setProperty("--dur", `${Math.max(2, (track.scrollWidth / 2) / SPEED).toFixed(1)}s`);
    } else {
      nm.style.removeProperty("--dur");
    }
  }
}

// ── 드래그 (포인터 이벤트 — 마우스·터치 한 경로) ────────────────────────
let drag = null;
// 터치에서 끌기는 **위쪽 슬롯에서만** 된다.
//
// 터치에서는 «끌기»와 «넘기기»가 같은 동작이다. 어느 쪽을 줄지는 `touch-action`으로
// **제스처가 시작되기 전에** 정해야 한다 — 잡은 뒤에 바꿔 봐야 소용이 없어서,
// 길게 누르기로 갈라 보려던 시도는 손가락을 움직이는 순간 브라우저가 스크롤을
// 시작하며 `pointercancel`로 풀려 버렸다(게다가 길게 누르면 기본 메뉴가 떴다).
//
// 그래서 자리로 가른다:
//   위 슬롯 (5장, 한 줄)   → `touch-action: none` · 끌어서 자리 바꾸기
//   아래 로스터 (200장)    → `touch-action: pan-y` · 넘기기. 배치는 탭으로
//
// 로스터는 스크롤이 생명이고 슬롯은 스크롤할 게 없다. 그리고 아래쪽에는 이미
// 온전한 길이 있다 — 탭하면 빈 슬롯에 들어가고, 꽉 찼으면 «놓을 슬롯을 누르세요».
// 카드를 길게 누르면 브라우저가 «이미지 복사·새 탭으로 열기» 메뉴를 띄운다.
// 카드는 그림이 아니라 버튼이라 그 메뉴가 뜰 자리가 아니다.
document.addEventListener("contextmenu", (e) => {
  if (e.target.closest(".nk")) e.preventDefault();
});

function startDrag(e, name, from) {
  // `from`이 있으면 슬롯에서 집은 것이다. 로스터(아래)에서 손가락으로 집는 것만 막는다.
  if (e.pointerType === "touch" && !from) return;
  if (e.button != null && e.button !== 0) return;
  if (e.target.closest(".nk-cog, .slot-x, .nk-fav")) return;
  beginDrag(e.clientX, e.clientY, name, from);
}

function beginDrag(x, y, name, from) {
  const ghost = el("div", "ghost");
  ghost.append(card(name, { inSlot: true, compact: fastMode }));
  document.body.append(ghost);
  drag = { name, from, ghost, target: null, moved: false, x0: x, y0: y };
  moveGhost(x, y);
  document.addEventListener("pointermove", onDragMove, { passive: false });
  document.addEventListener("pointerup", onDragEnd, { once: true });
  document.addEventListener("pointercancel", onDragEnd, { once: true });
}
const moveGhost = (x, y) => { drag.ghost.style.transform = `translate(${x - 36}px, ${y - 48}px)`; };

function onDragMove(e) {
  if (!drag) return;
  if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 6) drag.moved = true;
  if (!drag.moved) return;
  e.preventDefault();
  moveGhost(e.clientX, e.clientY);
  const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest(".slot, .u-slot");
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
  if (!moved) return;                      // 안 움직였으면 클릭으로 넘긴다
  // 유니온 칸(.u-slot)은 **자기 저장소**에 꽂는다 — 솔로 place()는 state.decks를
  // 만지므로 여기로 오면 유니온에서 끈 것이 솔로 덱에 들어간다.
  if (target?.classList.contains("u-slot")) {
    uDrop(name, Number(target.dataset.udeck), Number(target.dataset.idx), from);
  } else if (target) {
    place(name, Number(target.dataset.deck), Number(target.dataset.idx));
  } else if (from) {
    if (from.union) {
      // 칸 밖으로 끌어내 버리는 것도 «뺀 것»이다 — 그 자리에서 되돌릴 수 있어야 한다
      uSnap(T("{name} 빼기", { name }), { deckIdx: from.deckIdx, idx: from.idx });
      uDeck(from.deckIdx).names[from.idx] = null;
      saveAll(); renderAll();
    }
    else clearSlot(from.deckIdx, from.idx);          // 슬롯 밖으로 끌어내면 비운다
  }
}

/** 유니온 칸에 꽂는다. 유니온도 줄 간 중복이 불가하므로 같은 이름이 다른 줄에
 *  있으면 먼저 뺀다. 칸에서 칸으로 끌면 **자리를 맞바꾼다** — 채워 둔 줄을 다시
 *  짤 때 하나씩 비우고 넣는 수고를 없앤다. */
function uDrop(name, deckIdx, idx, from) {
  // 자리를 **덮어썼으면** 그 칸에서 되돌릴 수 있어야 한다. 실수로 바꾼 것이
  // 빼는 것보다 알아채기 어렵다 — 빈 칸이 생기지 않아 눈에 안 걸린다.
  const had = uDeck(deckIdx).names[idx];
  uSnap(had && had !== name ? T("{had} → {name} 교체", { had, name }) : T("{name} 배치", { name }),
        had && had !== name ? { deckIdx, idx } : null);
  const dst = uDeck(deckIdx);
  const held = dst.names[idx];
  if (from?.union) {
    const src = uDeck(from.deckIdx);
    src.names[from.idx] = held;            // 맞바꾸기(빈 칸이면 그대로 비워진다)
  } else {
    for (let i = 0; i < UNION_DECKS; i++) {
      const at = uDeck(i).names.indexOf(name);
      if (at !== -1) uDeck(i).names[at] = null;
    }
  }
  dst.names[idx] = name;
  picked = null;
  saveAll();
  renderAll();
  slamSlot(deckIdx, idx);
}

// ── 계산 ────────────────────────────────────────────────────────────────
// 지문은 빌드가 이 파일에 박는다(`web/build.py stamp_assets`). app.js만 새로 받고
// worker.js가 낡으면 계산 쪽이 조용히 어긋난다.
const ASSET_V = "dev";
// ── 워커 풀 ─────────────────────────────────────────────────────────────
// 브라우저 계산은 **방문자 기기**에서 돈다. 워커 하나로 5덱을 줄 세우면 코어가 몇
// 개든 덱당 12초씩 60초가 걸린다 — 남는 코어를 쓰면 그대로 이득이다.
//
// 다만 워커 하나가 Pyodide 인스턴스 하나다(수백 MB). 무작정 늘리면 저사양 PC와
// 휴대폰이 메모리로 죽는다. 그래서 두 가지로 묶는다:
//   - 코어 수 - 1 (UI가 쓸 코어를 하나 남긴다)
//   - `deviceMemory`로 어림한 상한 (안 알려 주는 브라우저는 보수적으로 잡는다)
// 그리고 **필요할 때만 늘린다** — 덱 하나만 계산하는 사람이 5인분 메모리를 쓸 이유가 없다.
// 워커 하나가 Pyodide 인스턴스 하나이고 메모리를 200~300MB쯤 쓴다. 데스크톱은 5개
// (1GB 안팎)를 아무렇지 않게 견디지만 **모바일은 다르다** — 특히 iOS WebKit은 탭이
// 그만큼 쓰면 경고 없이 통째로 죽인다. 안드로이드도 데스크톱보다 빠듯하다.
// 그래서 갈림길은 «모바일이냐» 하나다.
//
// 사양을 숫자로 재려던 건 되돌렸다. `deviceMemory`·`performance.memory`는 **크롬 전용**
// 이라 사파리·파이어폭스에서는 기본값으로 떨어져 멀쩡한 데스크톱까지 묶었다.
// 모르는 값으로 성능을 깎느니 넉넉히 쓰고, 감당 못 하면 아래 «죽으면 물러서기»가
// 받아 낸다 — 그래도 안 되면 사용자가 「서버」로 바꾸면 된다.
//
// iPadOS 13부터 UA가 «Macintosh»로 나오고 `Mobi`도 없다. 그래서 터치 포인트를 같이
// 봐야 아이패드를 놓치지 않는다 (터치 맥북은 없으므로 오검출 걱정이 없다).
const IS_MOBILE = /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);

const poolCapacity = () => (IS_MOBILE ? 2 : DECK_COUNT);

// 상한은 **줄어들 수 있다.** 워커가 죽으면(대개 메모리) 하나로 물러선다 — 재 본 값이
// 틀렸다는 증거가 실제로 나왔을 때 고집할 이유가 없다.
let poolMax = poolCapacity();

const pool = [];
const pending = new Map();
let workerReady = false;

function spawnWorker() {
  const rec = { w: new Worker(`worker.js?v=${ASSET_V}`), busy: 0 };
  rec.w.onmessage = ({ data }) => {
    if (data.type === "ready") { workerReady = true; renderEngine(); return; }
    if (data.type === "fatal") {
      failPending(T("브라우저 계산기를 불러오지 못했습니다: {error}", { error: data.error }));
      return;
    }
    const p = pending.get(data.id);
    if (!p) return;
    pending.delete(data.id);
    rec.busy = Math.max(0, rec.busy - 1);
    p(data);
  };
  rec.w.onerror = (e) => failPending(
    T("브라우저 계산기가 멈췄습니다 ({v}) — 새로고침해 주세요. ", { v: e.message || T("워커 오류") })
    + T("계산만 필요하다면 위에서 「서버」로 바꿔도 됩니다."));
  rec.w.onmessageerror = () => failPending(
    T("브라우저 계산기와의 통신이 깨졌습니다 — 새로고침해 주세요."));
  pool.push(rec);
  return rec;
}
// **미리 띄우지 않는다.** 워커를 만드는 순간 Pyodide가 부팅하며 200~300MB를 잡는데,
// 서버 계산만 쓰는 사람에게는 그게 통째로 낭비다(모바일에서는 탭이 죽는 원인이 된다).
// 브라우저 계산을 **고르거나 실제로 계산할 때** 처음 띄운다 — 부팅 1.8초는 그때
// «준비 중…»으로 알린다.
function warmWorker() {
  if (!pool.length) spawnWorker();
}

/** 일을 맡길 워커. 노는 게 있으면 그걸 쓰고, 다 바쁘면 상한까지 늘린다. */
function pickWorker() {
  if (!pool.length) spawnWorker();
  const idle = pool.find((r) => r.busy === 0);
  if (idle) return idle;
  if (pool.length < poolMax) return spawnWorker();
  return pool.reduce((a, b) => (a.busy <= b.busy ? a : b));
}

// 워커가 통째로 죽으면(Pyodide 내려받기 실패·CSP 차단·메모리) **아무 답도 안 온다.**
// 그러면 기다리던 약속이 영영 안 끝나 화면은 «계산 중…»에 멈추고, 그 사이 재계산
// 버튼은 잠겨 있어 새로고침 말고는 빠져나갈 길이 없다. 기다리는 것들을 모두 실패로
// 마감해 이유를 보여 준다.
// 이 워커는 **계산만 하는 게 아니다** — 블라링크에서 받아 온 raw를 육성 프로필로
// 바꾸는 일도 여기서 한다. 그래서 워커가 죽으면 동기화도 같이 실패하는데, 문구가
// 「계산이 멈췄다」뿐이면 동기화 화면에서 엉뚱한 말이 뜬다. 그리고 변환은 서버
// 경로가 없으므로 «서버로 바꿔 보라»는 안내도 그때는 틀린 말이 된다.
function failPending(msg) {
  // 워커가 죽었다 — 재 본 상한이 이 기기에는 과했다는 뜻이다. 하나로 줄이고,
  // 여분은 정리한다. 실패한 덱은 부른 쪽이 한 번 더 시도한다.
  if (poolMax > 1) {
    poolMax = 1;
    for (const r of pool.slice(1)) r.w.terminate();
    pool.length = Math.min(pool.length, 1);
  }
  for (const [id, res] of [...pending]) {
    pending.delete(id);
    res({ type: "error", id, error: msg, workerDied: true });
  }
  for (const r of pool) r.busy = 0;
  setStatus(msg);
}

// 답이 아예 안 오는 경우까지 막는다. 브라우저 계산은 첫 실행에 Pyodide를 내려받느라
// 오래 걸릴 수 있어 넉넉히 잡되, **무한정 기다리지는 않는다.**
const WORKER_TIMEOUT = 300000;

function askWorker(msg) {
  return new Promise((res) => {
    const id = uid();
    warmWorker();
    const rec = pickWorker();
    rec.busy += 1;
    const timer = setTimeout(() => {
      if (!pending.has(id)) return;
      pending.delete(id);
      rec.busy = Math.max(0, rec.busy - 1);
      res({ type: "error", id,
            error: T("브라우저 계산기가 응답하지 않습니다 — 새로고침하거나 ")
                   + T("계산은 「서버」로 바꿔 보세요.") });
    }, WORKER_TIMEOUT);
    pending.set(id, (data) => { clearTimeout(timer); res(data); });
    rec.w.postMessage({ ...msg, id });
  });
}

/** 실제로 쓸 엔진. `auto`는 서버가 있으면 서버다 (실측 3배 빠르다). */
const engine = () => {
  const e = state.settings.engine;
  if (e === "server") return HEALTH.sim ? "server" : "local";
  if (e === "local") return "local";
  return HEALTH.sim ? "server" : "local";
};

function renderEngine() {
  const eng = engine();
  $("#eng-server").classList.toggle("on", eng === "server");
  $("#eng-local").classList.toggle("on", eng === "local");
  $("#eng-server").disabled = !HEALTH.sim;
  // 실측 초는 기기마다 달라 표기하지 않는다 — **어떻게 도는지**를 있는 그대로 적는다.
  // 「코어 수만큼 병렬」이라고 적어 뒀었는데 사실이 아니다: 서버는 요청을 한 번에
  // 하나씩 처리하고(`SIM_SLOTS`), 그 한 요청 안에서 덱만 워커 수만큼 나눠 돈다.
  $("#eng-server").title = HEALTH.sim
    ? T("서버에서 계산합니다 — 덱 {v}개까지 동시에 돌리고, ", { v: HEALTH.jobs || 1 })
      + T("요청은 한 번에 하나씩 차례로 처리합니다 (밀리면 대기 순번을 보여 줍니다). ")
      + T("육성 데이터가 서버로 전송됩니다.")
    : T("이 배포판에는 계산 서버가 없습니다");
  $("#eng-local").title =
    T("이 브라우저에서 계산합니다 — 덱 {v}개를 동시에 돌리고, ", { v: poolCapacity() })
    + T("서버로 아무것도 보내지 않습니다.");
  // 준비 상태를 늘 띄워 둘 이유가 없다 — 아직 못 쓰는 동안만 알린다.
  // (배지는 드래그 안내 같은 **그때그때 생기는 말**을 위한 자리다.)
  // `renderEngine`은 계산 중에도 불린다(덱 하나 끝날 때마다 `renderAll`). 그때
  // 무조건 `setStatus("")`를 하면 **진행 문구를 제가 지워 버린다** — 계산이 도는데
  // 화면에는 아무 말이 없어 멈춘 것처럼 보였다. 그래서 이 함수는 **제가 띄운 문구만**
  // 건드린다.
  const own = T("브라우저 계산 준비 중…");
  if (eng === "local" && !workerReady) {
    warmWorker();                          // 고른 순간부터 부팅해 둔다
    setStatus(own);
  } else if (statusText() === own) {
    setStatus("");
  }
  if (eng === "local") warmWorker();
}

/** 서버 작업 하나를 이벤트 스트림으로 따라간다 → 결과 배열.
 *  대기 중에는 순번을, 도는 중에는 그 사실을 상태줄에 옮긴다. */
// 서버 작업(계산·조회)은 둘 다 **줄에 세우고 id만** 준다. 긴 POST로 기다리면 대기
// 순번을 보여 줄 수 없고 타임아웃에도 걸린다. 이벤트 스트림으로 진행을 받다가
// 끝나면 결과를 가져온다.
//
// `say(state, pos)`가 진행 문구를 만든다 — 계산과 조회는 같은 기계를 쓰되 사람에게는
// 다른 말을 해야 한다. 끝나면 **반드시 `say("idle")`로 진행 표시를 지운다** — 안 지우면
// 계산이 다 끝났는데도 «계산 중…» 띠가 남는다.
function jobEvents(kind, jobId, say) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/${kind}/events?id=${encodeURIComponent(jobId)}`);
    let got = false;
    const done = () => { got = true; es.close(); say("idle", 0); };
    es.onmessage = async (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.state === "queued" || m.state === "running") {
        say(m.state, m.pos);
      } else if (m.state === "done") {
        done();
        // 조회 결과는 스트림에 안 실려 온다 (340KB) — 따로 받아 온다.
        if (m.results !== undefined) return resolve(m.results);
        try {
          const r = await fetch(`/api/${kind}/result?id=${encodeURIComponent(jobId)}`);
          const j = await r.json();
          if (j.error) throw new Error(j.error);
          resolve(j.results);
        } catch (e) { reject(e); }
      } else if (m.state === "error") {
        done(); reject(new Error(m.error));
      }
    };
    es.onerror = () => {
      if (got) return;                       // 정상 종료 뒤에도 한 번 온다
      es.close();
      say("idle", 0);
      reject(new Error(T("서버와의 연결이 끊겼습니다 — 잠시 후 다시 시도하세요.")));
    };
  });
}

const simEvents = (jobId) => jobEvents("sim", jobId, (state, pos) => setStatus(
  state === "idle" ? ""
    : state === "running" ? T("서버에서 계산 중…")
      : (pos > 1 ? T("서버 대기 중 — 앞에 {v}건", { v: pos - 1 }) : T("서버 대기 중…"))));

/** 블라링크 조회를 줄에 세우고 결과(raw)를 받아 온다. `note`로 진행을 알린다. */
async function fetchQueued(body, note) {
  const r = await fetch("/api/fetch", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  const out = await jobEvents("fetch", j.job, (state, pos) => {
    if (state === "idle") return;            // 마무리 문구는 부른 쪽이 쓴다
    note(state === "running" ? T("블라블라링크에서 받는 중…")
      : (pos > 1 ? T("대기 중 — 앞에 {v}건", { v: pos - 1 }) : T("대기 중…")));
  });
  return out.raws;               // 지역별 raw 목록 — 계정에 한섭·일섭이 둘 다 걸리면 2개
}

async function calcDecks(idxs, force = false) {
  // 덱을 모드별로 집는다. **유니온은 줄마다 보스도 레이드 설정도 다르므로**
  // 덱 하나하나에 제 조건을 붙여 보낸다(솔로는 셋 다 같아 예전과 값이 같다).
  const jobs = idxs.filter((i) => isFull(deckAt(i)) && (force || !resultOf(deckAt(i))));
  if (!jobs.length) return;
  for (const i of jobs) { deckAt(i).calcState = "run"; deckAt(i).error = null; }
  renderAll();
  const profile = mergedProfile();
  const duration = durationNow();
  const code = enemyCode();
  const payloads = jobs.map((i) => battlePayload(deckAt(i)));

  if (engine() === "server") {
    try {
      const { enemy, config } = payloads[0];
      const r = await fetch("/api/sim", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decks: jobs.map((i) => deckAt(i).names), code, duration,
                               profile, enemy, config,
                               codes: jobs.map((i) => enemyCodeFor(deckAt(i))),
                               enemies: payloads.map((p) => p.enemy),
                               configs: payloads.map((p) => p.config),
                               controls: jobs.map((i) => ctrlPayload(deckAt(i))),
                               cubes: jobs.map((i) => cubePayload(deckAt(i))) }),
      });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      // 서버는 줄에 세우고 id만 준다 — 결과는 이벤트 스트림으로 받는다.
      // 긴 POST로 기다리면 대기 순번을 보여 줄 수 없고 타임아웃에도 걸린다.
      const out = await simEvents(j.job);
      jobs.forEach((i, k) => {
        const d = deckAt(i);
        d.calcState = null;
        results[fingerprint(d)] = out[k];
      });
    } catch (e) {
      // 서버가 죽었으면 조용히 브라우저로 떨어지지 않는다 — 이유를 보여 준다
      jobs.forEach((i) => { deckAt(i).calcState = null; deckAt(i).error = String(e.message || e); });
    }
    saveAll(); renderAll();
    return;
  }

  // 덱들을 **한꺼번에 던진다.** 워커 풀이 노는 코어만큼 나눠 맡고, 남으면 줄을 선다.
  // 그리고 무슨 일이 벌어지는지 말해 준다 — 버튼만 돌고 아무 문구가 없으면 멈춘
  // 건지 도는 건지 알 수가 없다. 끝난 덱은 기다리지 않고 그때그때 화면에 올린다.
  const pj = profile ? JSON.stringify(profile) : null;
  const total = jobs.length;
  let done = 0;
  const say = () => setStatus(total > 1
    ? T("브라우저에서 계산 중… {done}/{total}덱 (동시 {v})", { done, total, v: Math.min(total, poolMax) })
    : T("브라우저에서 계산 중…"));
  say();
  try {
    await Promise.all(jobs.map(async (i, k) => {
      const d = deckAt(i);
      const bp = payloads[k];
      const ask = () => askWorker({ type: "sim", names: d.names,
                                    code: enemyCodeFor(d) || code, duration, profile: pj,
                                    enemy: bp.enemy, config: bp.config,
                                    control: ctrlPayload(d), cubes: cubePayload(d) });
      let data = await ask();
      // 워커가 죽어서 실패한 것이라면 상한이 이미 1로 줄었다 — 한 번은 다시 해 본다.
      if (data.workerDied) data = await ask();
      d.calcState = null;
      if (data.type === "done") results[fingerprint(d)] = data.result;
      else d.error = data.error;
      done += 1;
      say();
      saveAll(); renderAll();
    }));
  } finally {
    // 중간에 실패해도 «계산 중»이 남으면 안 된다
    setStatus("");
  }
}

// ── 결과 탭 ─────────────────────────────────────────────────────────────
// 헤드라인은 합계(히어로 숫자), 덱별은 가로 스택 바다. 세그먼트 색은 **역할군** 3색이고
// 캐릭터 정체성으로 칠하지 않는다 — 덱마다 멤버가 달라 25색이 필요해지기 때문이다.
function renderResults() {
  const rows = [];
  let sum = 0;
  const nDecks = deckCountNow();
  for (let i = 0; i < nDecks; i++) {
    const d = deckAt(i);
    const r = resultOf(d);
    if (r) sum += r.total;
    rows.push({ i, names: d.names, res: r, full: isFull(d) });
  }
  const known = rows.filter((r) => r.res).length;

  $("#res-total").textContent = known ? `${I18N.dmg(sum)}` : "—";
  const p = activeRec();
  // 유니온은 조건이 **줄마다 다르다** — 한 줄로 뭉뚱그리면 어느 줄 얘기인지 알 수 없다.
  // 보스와 방어력을 줄별로 늘어놓고, 모두가 함께 쓰는 것(시간·스펙·엔진)만 뒤에 붙인다.
  const condHead = modeNow() === "union"
    ? [...Array(nDecks).keys()].map((i) => {
        const d = uDeck(i);
        const w = uWeak(d);
        const b = battleFor(d);
        return T("{v}줄 {v1}", { v: i + 1, v1: w ? (bossOf(w)?.name || w) : T("보스 없음") })
             + T("(방 {v})", { v: Number(b.def || 0).toLocaleString() });
      }).join(" · ") + " · "
    : T("약점 {v} · ", { v: state.settings.code || T("없음") })
      + T("방어력 {v} · ", { v: battleNow().def.toLocaleString() })
      + (battleNow().core_px ? T("코어 {v}px · ", { v: battleNow().core_px }) : T("코어 없음 · "))
      + (() => {  // 1.0이 아닌 평타 계수만 밝힌다 — 보정 섞인 숫자를 이론치로 오해하지 않게
          const c = battleNow().weapon_coeff || {};
          const parts = WEAPONS.filter((w) => c[w] != null && c[w] !== 1)
                               .map((w) => `${w}×${c[w]}`);
          return parts.length ? T("계수 {v} · ", { v: parts.join(" ") }) : "";
        })();
  $("#res-cond").textContent =
    condHead
    + T("{v}초 · ", { v: durationNow() })
    + T("스펙 {v} · 계산 {known}/{nDecks}{v1} · ", { v: p ? p.name : T("고정"), known, nDecks, v1: modeNow() === "union" ? T("줄") : T("덱") })
    + (engine() === "server" ? T("서버") : T("브라우저"));
  const dup = duplicated();
  $("#res-dup").textContent = dup.size
    ? T("덱 간 중복: {v} — ", { v: [...dup].join(" · ") })
      + T("{v}에서는 불가능한 편성입니다", { v: modeNow() === "union" ? T("유니온 레이드") : T("솔로레이드") }) : "";

  // 역할군 범례는 없앤다. 이제 색은 **누구인지**를 가리키고(덱 슬롯 색), 이름은
  // 막대와 아래 상세에 직접 적히므로 색만으로 전달하지 않는다.
  const lg = $("#res-legend");
  if (lg) { lg.textContent = ""; lg.hidden = true; }

  const max = Math.max(1, ...rows.map((r) => r.res?.total || 0));
  const bars = $("#res-bars");
  bars.textContent = "";
  for (const row of rows) {
    const wrap = el("div", "bar-row");
    const head = el("div", "bar-head");
    head.append(el("span", "bar-no", String(row.i + 1).padStart(2, "0")));
    head.append(el("span", null, row.names.filter(Boolean).map(T).join(" · ") || T("빈 덱")));
    head.append(el("span", "bar-total", row.res ? `${I18N.dmg(row.res.total)}` : "—"));
    wrap.append(head);

    if (!row.res) {
      wrap.append(el("div", "bar-empty",
        row.full ? T("미계산") : T("5명을 채우면 계산할 수 있습니다")));
    } else {
      const track = el("div", "bar-track");
      track.style.width = `${(row.res.total / max) * 100}%`;
      for (const [nm, dmg] of charsByFormation(row.names, row.res.chars)) {
        const seg = el("div", "bar-seg");
        const pctv = (dmg / row.res.total) * 100;
        seg.style.flex = `${Math.max(pctv, 0.5)}`;
        seg.style.background = deckColor(row.names, nm);   // 상세·도넛과 같은 색
        seg.title = `${T(nm)} — ${I18N.dmg(dmg)} (${pctv.toFixed(1)}%)`;
        // 좁은 세그먼트에 이름을 넣으면 넘친다 — 넉넉할 때만 직접 라벨을 붙인다
        if (pctv >= 14) seg.append(el("span", null, `${T(nm)} ${pctv.toFixed(0)}%`));
        track.append(seg);
      }
      wrap.append(track);
    }
    bars.append(wrap);
  }

  // 덱별 상세 — 기록 탭과 **같은 렌더러**를 쓴다. 두 곳이 다르게 보이면 어느 쪽이
  // 맞는지 매번 확인해야 한다.
  const det = $("#res-detail");
  if (det) {
    det.textContent = "";
    const packed = {
      decks: rows.filter((r) => r.res).map((r) => ({
        names: r.names, total: r.res.total, chars: r.res.chars,
        detail: r.res.detail || null, notes: r.res.notes || "",
        // 확인용 — **기록에는 안 실린다**(collectDecks가 이 둘을 안 담는다).
        // 계산할 때마다 새로 나오는 진행 로그일 뿐 저장할 값이 아니다.
        timeline: r.res.timeline || null, burstCycles: r.res.burst_cycles || null,
      })),
      total: sum,
      duration: durationNow(),
    };
    if (packed.decks.length) det.append(recDetail(packed));
    else det.append(el("p", "prose prose-sm", "아직 계산한 덱이 없습니다."));
  }
}

// ── 최공 대상 즉시 계산 ─────────────────────────────────────────────────
// 「자신을 제외한 최종 공격력이 가장 높은 아군 N기에게」 계열 버프는 **대상이 갈리면
// 딜이 통째로 달라진다.** 미란다 애장품이 대표다.
//
// **순위를 바꾸는 값은 몇 개뿐이다.** 아군 전체에게 똑같이 들어가는 버프는 모두의
// 공격력을 같은 비율로 올리므로 순위를 못 바꾼다. 남는 것은 셋:
//
//   ① 소지 공격력            — 계산기에게 한 번 물어 캐시한다 (시뮬 아님, 표 조회)
//   ② 오버로드 공격력 증가    — 스펙에 이미 있다
//   ③ 자기 버스트 자버프      — **그 사이클의 3버만** 받는다. 이게 순위를 뒤집는다
//
// 그래서 3버 후보마다 «그 사람이 버스트를 쓰는 사이클»을 따로 세운다.
// 순서는 인게임과 같다: 미란다 버스트(파워 업!)가 **먼저** 걸리고 — 그때는 3버 자버프가
// 아직 없다 — 그 다음 풀버스트 시작 시점에 웨이크업!의 «1발 크확»이 정해진다.
//
// 조건부 버프(중첩·체력·명중 횟수)와 «시전자 기준» 버프는 세지 않는다. 그래서 이 값은
// **예측**이고, 화면이 그렇게 밝힌다. 정확한 값은 덱을 계산하면 결과에 실려 오는
// 진단(`top_atk`)이 답한다 — 그쪽은 계산기 엔진이 실제로 돌린 결과다.

let TOP_ATK_BUFFS = {};      // 이름 → {buff, pct, slots, excl, timing}
let SELF_BURST_ATK = {};     // 이름 → 자기 버스트 자버프 공격력 %
let DEALER_ATK_FLAT = {};    // 이름 → «버스트 쓴 아군»에게 주는 시전자 공격력 비례 %
let SELF_FB_ATK = {};        // 이름 → 풀버스트가 열리면 켜지는 자기 공격력 % (매 사이클)
let LOW_ATK_CASTERS = new Set();  // 「최종 공격력이 가장 «낮은» 3버」에게 거는 니케
let LOW_ATK_BUFFS = {};      // 이름 → {buff, stat, pct, slots}
let ADJ_CASTERS = new Set();  // 루주·플로라처럼 «양옆 아군»에게 거는 니케
let ADJ_BUFFS = {};           // 이름 → [버프 이름, …]
// 뱃지 색 — **본인 일러에서 실측한 색**이다(초상화 화소를 채도·명도로 걸러
// 가장 많이 나온 색 순으로 뽑았다). 루주는 와인레드(#9f1313·#9f1359 계열이
// 압도적), 플로라는 보라(#6d3b9f가 다른 색보다 3배 이상 많음)로 각각 정체성이
// 뚜렷했다. 목록에 없는 캐스터는 `--color-info`(청록)로 물러난다.
const ADJ_COLOR = { "루주": "#9c1a3e", "플로라": "#7d46c2" };
let CDR_CASTERS = new Set();  // **아군 전체**에게 버스트 쿨타임 감소를 주는 니케
const atkCache = new Map();  // `${profSig()}|${이름}` → {atk, atk_pct}
let atkInflight = null;      // 같은 조회가 겹치면 하나로 묶는다
let atkError = "";           // 실패 이유. **비워 두지 않는다** — 「읽는 중…」에서 멈추면
                             // 왜 멈췄는지 알 방법이 없다 (실제로 그렇게 막혔다)

const topAtkCastersIn = (d) => (d.names || []).filter((n) => n && TOP_ATK_CASTERS.has(n));

/** 루주·플로라 같은 «양옆 아군» 버프 — 계산이 아니라 **배치 규칙**이라 스탯 비교
 *  없이 항상 정해진다. 그래서 최공 대상처럼 진단 패널을 열지 않고 **슬롯 카드에
 *  바로 표시한다.** 양쪽 다 받는다는 걸 모르는 사람이 많다는 게 만든 이유다.
 *
 *  빈 슬롯은 뛰어넘는다 — 실제 전투에서 스쿼드는 **채운 자리만으로** 좁혀지므로,
 *  UI에 뚫린 빈 칸을 이웃으로 세면 «채웠으면 옆에 있었을 사람»을 놓친다. */
function adjHitsIn(names) {
  const filled = (names || []).filter(Boolean);
  const hits = new Map();          // 이름 → [{caster, buffs, self}]
  filled.forEach((caster, i) => {
    if (!ADJ_CASTERS.has(caster)) return;
    // 실제로는 본인도 받지만(엔진 코드에 `[caster] + adj`로 그렇게 있다) — **뱃지는**
    // 이웃에게만 단다(「본인이 자기 버프를 받는다」는 새삼스러운 정보라서). 무리
    // 전체를 하나로 묶어 보여 주는 테두리는 `adjGroupsIn()`이 따로 맡는다.
    const reach = new Set([caster]);
    if (i > 0) reach.add(filled[i - 1]);
    if (i < filled.length - 1) reach.add(filled[i + 1]);
    const buffs = ADJ_BUFFS[caster] || [];
    for (const n of reach) {
      if (!hits.has(n)) hits.set(n, []);
      hits.get(n).push({ caster, buffs, self: n === caster });
    }
  });
  return hits;
}

/** 캐스터별 «묶을 슬롯 범위» — 물리적 자리(빈 칸 없이 채운 덱) 기준으로 캐스터
 *  본인 칸부터 양옆 칸까지를 [시작, 끝] 인덱스로 돌려준다. 이웃이 없으면(양쪽 다
 *  비었거나 캐스터 혼자) 묶을 것이 없어 제외한다 — 테두리 한 칸짜리는 의미가 없다. */
function adjGroupsIn(names) {
  const out = [];
  (names || []).forEach((caster, i) => {
    if (!caster || !ADJ_CASTERS.has(caster)) return;
    const lo = names[i - 1] ? i - 1 : i;
    const hi = names[i + 1] ? i + 1 : i;
    if (lo === hi) return;
    out.push({ caster, lo, hi });
  });
  return out;
}
/** 오버로드 공격력 증가. **계산기가 준 값을 쓴다** — 스펙에서 직접 읽으면
 *  «고정 스펙»(프로필 없음)에서 조용히 0이 되어 예측이 실제보다 낮아진다. */
/** 풀버스트가 열리는 편성인가. **1·2·3단계가 다 있어야** 1→2→3으로 이어져 열린다.
 *
 *  이 판정이 없으면 화면이 조용히 헛말을 한다: 2단계가 없는 편성에서 «3버가 자기
 *  버스트에 받습니다»라고 적으면, 애초에 풀버스트가 없어서 웨이크업!의 1발 크확이
 *  발동하지도 않는데 마치 잘 돌아가는 것처럼 읽힌다. */
function burstStages(names) {
  const have = new Set();
  const base = [];
  for (const n of names) {
    const b = String(byName.get(n)?.burst || "");
    if (!b) continue;
    // "A" — 1·2·3버 전부 대체 가능한 와일드카드(레드 후드). 있으면 무조건 다 채워진다.
    if (b === "A") { have.add("1"); have.add("2"); have.add("3"); continue; }
    base.push([n, b]);
  }
  // 라피 : 레드 후드 — 기본은 3버지만, 1버 아군이 없으면 3버 대신 1버로 전환된다
  // (`burst_stage_override:1`, 조건 `no_burst1_ally`). 즉 1버·3버 동시 커버가 아니다.
  const hasBurst1 = base.some(([, b]) => b === "1");
  for (const [n, b] of base) {
    have.add(n === "라피 : 레드 후드" && !hasBurst1 ? "1" : b);
  }
  return { have, ok: have.has("1") && have.has("2") && have.has("3"),
           missing: ["1", "2", "3"].filter((x) => !have.has(x)) };
}

/** 실제로 **순번이 오는** 3단계 버스트 니케.
 *
 *  3버가 셋이어도 다 나가지 않는다. 버스트 쿨이 40초이고 사이클이 20초면 둘로 매
 *  사이클이 덮이므로 셋째는 영영 안 나간다(실측: 리버렐리오↔홍련:흑영만 교대하고
 *  에이다는 한 번도 안 나갔다). 이걸 모르고 «에이다가 3버인 사이클»까지 예측하면
 *  일어나지 않는 상황을 근거로 화면이 말하게 된다.
 *
 *  순번은 **덱 배치 순서**를 따른다 (실측과 일치). 쿨이 섞여 있으면 가장 짧은 쿨로
 *  본다 — 그쪽이 순번을 결정한다. */
function activeB3(names) {
  const b3 = names.filter((n) => String(byName.get(n)?.burst) === "3");
  if (b3.length <= 1) return b3;
  const cds = b3.map((n) => Number(byName.get(n)?.cd) || 40);
  const cycle = Math.max(20, ...names
    .filter((n) => String(byName.get(n)?.burst) !== "3")
    .map((n) => Number(byName.get(n)?.cd) || 20));
  const need = Math.max(1, Math.ceil(Math.min(...cds) / cycle));
  return b3.slice(0, need);
}

const olAtkPct = (n, sig) => Number(atkCache.get(`${sig}|${n}`)?.atk_pct || 0);

/** 소지 공격력을 계산기에게 물어 캐시한다. **시뮬이 아니라 표 조회**라 즉시 끝난다.
 *  브라우저에서 다시 구하지 않는 이유는 `base_atk_of` 주석 참고 — 두 곳이 갈린다. */
async function fillBaseAtk(names) {
  const sig = profSig();
  const need = names.filter((n) => n && !atkCache.has(`${sig}|${n}`));
  if (!need.length) return true;
  // **겹친 요청을 버리지 않는다.** 예전에는 «이미 떠 있으면 false»로 돌려보냈는데,
  // 그러면 뒤늦게 온 렌더가 다시 그릴 기회를 잃고 「읽는 중…」에서 영구히 멈췄다.
  if (atkInflight) return atkInflight;
  atkInflight = (async () => {
    try {
      let atk = null;
      if (engine() === "server") {
        const r = await fetch("/api/atk", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ names: need, profile: mergedProfile() }),
        });
        const j = await r.json().catch(() => ({ error: T("서버 응답을 읽지 못했습니다 ({status})", { status: r.status }) }));
        if (j.error) throw new Error(j.error);
        atk = j.atk;
      } else {
        const j = await askWorker({ type: "base_atk", names: need,
                                    profile: mergedProfile()
                                      ? JSON.stringify(mergedProfile()) : null });
        if (j.type === "error") throw new Error(j.error);
        if (!j.atk) throw new Error(T("브라우저 계산기가 소지 공격력을 돌려주지 않았습니다")
                                    + T(" — 새로고침하거나 계산을 «서버»로 바꿔 보세요."));
        atk = j.atk;
      }
      for (const [n, v] of Object.entries(atk || {})) {
        // 옛 모양(숫자 하나)도 받아 준다 — 서버와 워커가 갈릴 때 조용히 0이 되지 않게
        atkCache.set(`${sig}|${n}`, typeof v === "number" ? { atk: v, atk_pct: 0 } : v);
      }
      atkError = "";
      return true;
    } catch (e) {
      atkError = String(e.message || e);
      return false;
    } finally {
      atkInflight = null;
    }
  })();
  return atkInflight;
}

/** 3버 `dealer`가 버스트를 쓰는 사이클의 최공 순위. 시뮬 없이 위 셋만 더한다. */
function estimateTopAtk(names, caster, dealer) {
  const sig = profSig();
  const buff = TOP_ATK_BUFFS[caster];
  const pool = names.filter((n) => n && (!buff?.excl || n !== caster));
  const base = {};
  for (const n of pool) {
    const v = atkCache.get(`${sig}|${n}`);
    if (v == null) return null;                    // 아직 못 받았다
    base[n] = v.atk;
  }

  // ① 미란다 버스트 시점 — 3버 자버프는 **아직 없다** (버스트 순서: 1단계 → 3단계)
  const pct1 = {};
  for (const n of pool) pct1[n] = olAtkPct(n, sig);
  const atk1 = pool.map((n) => ({ n, v: base[n] * (1 + pct1[n] / 100) }))
    .sort((a, b) => b.v - a.v);
  const powered = new Set(atk1.slice(0, buff?.slots || 1).map((x) => x.n));

  // ② 풀버스트 시작 시점 — 3버 자버프 + 파워 업! + «버스트 쓴 아군» 지원이 얹힌다.
  //    에이다 「은밀한 지원」·크라운 「원 포 올」은 **시전자 공격력 비례 고정값**이라
  //    곱이 아니라 덧셈으로 붙는다 (`_effective_atk`와 같은 식).
  let flat = 0;
  const flatFrom = [];
  for (const n of names) {
    const v = Number(DEALER_ATK_FLAT[n] || 0);
    const bv = atkCache.get(`${sig}|${n}`)?.atk;
    if (!v || !bv) continue;
    flat += bv * v / 100;
    flatFrom.push(n);
  }
  const rows = pool.map((n) => {
    const pct = pct1[n]
      // 풀버스트가 열리면 켜지는 자버프는 **누가 3버든** 걸린다 (리버렐리오 +160%)
      + Number(SELF_FB_ATK[n] || 0)
      // 자기 버스트로 켜지는 자버프는 그 사이클의 3버만 (아인 +70.12%, 에이다 +40%)
      + (n === dealer ? Number(SELF_BURST_ATK[n] || 0) : 0)
      + (powered.has(n) ? Number(buff?.pct || 0) : 0);
    const add = n === dealer ? flat : 0;      // 그 사이클에 3단계 버스트를 쓴 사람에게
    return { name: n, base: base[n], pct, flat: add,
             atk: base[n] * (1 + pct / 100) + add,
             powered: powered.has(n), selfBurst: n === dealer && !!SELF_BURST_ATK[n],
             selfFb: !!SELF_FB_ATK[n], supported: add > 0 };
  }).sort((a, b) => b.atk - a.atk);

  // **몇 명이 받는지는 버프마다 다르다** — 맥스웰·미란다는 2명이다. rows[0]만 보면
  // 실제로 받은 2번째 사람이 「예측과 다름」으로 잘못 표시된다(실측: 2번·3번 덱).
  const slots = Math.max(1, buff?.slots || 1);
  const winners = rows.slice(0, slots).map((r) => r.name);
  const winSet = new Set(winners);
  const cutRow = rows[Math.min(slots, rows.length) - 1];
  const cut = cutRow ? cutRow.atk : 0;
  for (const r of rows) {
    r.got = winSet.has(r.name);
    r.need = r.got ? null : (r.base > 0 ? (cut - r.atk) / r.base * 100 : null);
    r.tie = !r.got && r.need != null && r.need <= 0;
  }
  return { dealer, winner: winners[0] || null, winners, powered: [...powered],
           flatFrom, rows };
}

/** 편성 탭 아래 줄. 덱을 짜는 즉시 나온다 — 버튼도, 계산도 필요 없다. */
function renderTopAtk() {
  const box = $("#deck-topatk");
  if (!box) return;
  const d = deckOf(state.settings.deck);
  const casters = topAtkCastersIn(d);
  // **실험 스위치(`lab`)를 타지 않는다.** 최공 대상 버프(미란다·나가·맥스웰·
  // 소다:트윙클링 바니·앨리스)는 값이 확정적이라 운영에서도 그대로 보인다.
  // 차속 대상(`renderLowAtk` — 리버렐리오)도 같은 근거로 상용에 노출한다.
  if (!casters.length) { box.hidden = true; box.textContent = ""; return; }
  box.hidden = false;
  box.textContent = "";

  const caster = casters[0];
  const names = d.names.filter(Boolean);
  box.append(el("span", "topatk-k", T("{caster} 버프 대상", { caster })));

  // 소지 공격력이 아직 없으면 받아 오고 다시 그린다. **성공·실패 모두 다시 그린다** —
  // 실패한 채 「읽는 중…」으로 남으면 무엇이 잘못됐는지 알 수가 없다.
  if (names.some((n) => !atkCache.has(`${profSig()}|${n}`))) {
    if (atkError) {
      box.append(el("span", "topatk-sum warn", T("소지 공격력을 읽지 못했습니다 — {atkError}", { atkError })));
      box.append(mkBtn(T("다시"), "btn-ghost", () => { atkError = ""; renderTopAtk(); }));
      return;
    }
    box.append(el("span", "topatk-note", "소지 공격력을 읽는 중…"));
    fillBaseAtk(names).then(() => renderTopAtk());
    return;
  }

  // 풀버스트가 안 열리는 편성이면 예측이 의미가 없다 — 그것부터 말한다
  const st = burstStages(names);
  if (!st.ok) {
    box.append(el("span", "topatk-sum warn",
      T("{v} 버스트가 없어 풀버스트가 열리지 않습니다", { v: st.missing.map((x) => x + T("단계")).join("·") })));
    box.append(el("span", "topatk-note",
      T("풀버스트 시작 시 걸리는 버프(웨이크업! 등)는 발동하지 않습니다.")));
    return;
  }

  // **다툴 상대가 없으면 띄우지 않는다.** 후보가 한 명이면 그 사람이 받는 것이
  // 자명해서 볼 것이 없다 — 미미르의 «아인-에이다»·«나유타-헬름»처럼 두 딜러가
  // 최공 1위를 다투는 상황이 이 진단이 답하는 질문이다.
  const rivals = names.filter((n) => n !== caster);
  if (rivals.length < 2) { box.hidden = true; box.textContent = ""; return; }

  // **순번이 오는 3버만** 본다 — 안 나가는 사람의 사이클을 예측하면 헛말이 된다
  const b3 = activeB3(names);
  const scen = (b3.length ? b3 : [null]).map((x) => estimateTopAtk(names, caster, x))
    .filter(Boolean);
  if (!scen.length) { box.append(el("span", "topatk-note", "계산할 수 없습니다.")); return; }

  const res = resultOf(d);
  const done = (res?.top_atk || []).filter((c) => (c.kind || "top") === "top");

  // 대상이 여러 명인 버프(맥스웰·미란다는 2명)는 **그 사람이 목록에 들었는지**로
  // 본다. `winner`(1명)로만 재면 2번째로 받는 사람 몫이 통째로 안 잡힌다.
  const miss = scen.filter((x) => x.dealer && !x.winners.includes(x.dealer));
  const predText = miss.length
    ? (() => { const m = miss.map((x) => x.dealer).join(" · ");
                return T("{m}{v} 자기 버스트에 못 받습니다", { m, v: eun(m) }); })()
    : (b3.length ? T("3버가 자기 버스트에 받습니다") : T("{v}가 받습니다", { v: scen[0].winners.join(" · ") }));

  // 계산 결과가 있으면 **그쪽이 정답이다.** 예측과 갈리면 그 사실을 눈에 보이게 한다 —
  // 조용히 다른 말을 하게 두면 어느 쪽을 믿어야 하는지 알 수 없다.
  let text = predText, warn = miss.length > 0, differs = false;
  if (done.length) {
    const missed = done.filter((c) => c.dealer_got === false);
    const cyc = missed.reduce((n, c) => n + (c.cycles?.length || 0), 0);
    text = missed.length
      ? T("3버가 못 받은 사이클 {cyc}회 — {v}", { cyc, v: [...new Set(missed.map((c) => c.dealer))].join(" · ") })
      : T("모든 사이클에서 그 사이클의 3버가 받았습니다");
    warn = missed.length > 0;
    // 여러 명이 받는 버프는 **명단 전체**를 맞춰야 한다 — 한 명만 보면 2번째 자리가
    // 갈려도 안 걸린다(실측: 2번 덱 맥스웰·3번 덱 미란다가 매번 「예측과 다름」으로
    // 잘못 떴었다. rows[0] 한 명만 보고 세던 게 원인).
    const predWho = [...new Set(scen.flatMap((x) => x.winners))].sort().join(",");
    const realWho = [...new Set(done.flatMap((c) => c.chosen))].sort().join(",");
    differs = predWho !== realWho;
  }
  box.append(el("span", "topatk-sum " + (warn ? "warn" : "ok"), text));
  if (differs) box.append(diffFlag());
  box.append(mkBtn(T("예측"), "btn-ghost", () => openTopAtkInstant(caster, scen)));
  if (done.length) {
    box.append(mkBtn(T("계산 결과"), "btn-primary",
      () => openTopAtk(T("{caster} 버프 대상 — 계산 결과", { caster }), done)));
  }
}

/** 「예측과 계산 결과가 다르다」 표식. **눈에 걸려야 한다** — 둘이 갈렸는데 조용하면
 *  화면의 어느 숫자를 믿어야 하는지 알 수 없다. */
function diffFlag() {
  const f = el("span", "diff-flag");
  f.append(el("b", null, "⚠"));
  f.append(el("span", null, "예측과 다름"));
  f.title = T("예측은 순번·조건부 버프를 완전히 세지 못합니다. 계산 결과가 실제 값입니다.");
  return f;
}

function openTopAtkInstant(caster, scen) {
  const dlg = $("#topatk-sheet");
  const body = $("#topatk-body");
  if (!dlg || !body) return;
  $("#topatk-t").textContent = T("{caster} 버프 대상 (예측)", { caster });
  body.textContent = "";

  const buff = TOP_ATK_BUFFS[caster] || {};
  body.append(el("p", "prose prose-sm",
    T("{caster} 「{v}」는 «자신을 제외한 최종 공격력이 가장 높은 아군", { caster, v: buff.buff || "" })
    + T(" {v}기»에게 공격력 {v1}%를 겁니다. 그 뒤 풀버스트가", { v: buff.slots || 1, v1: buff.pct || 0 })
    + T(" 시작될 때, 그때까지의 최종 공격력으로 «1발 크리티컬 확률»의 주인이 정해집니다.")));

  for (const s of scen) {
    // 몇 명이 받는지는 버프마다 다르다(맥스웰·미란다는 2명) — «그 사람이 목록에
    // 들었는지»로 봐야 한다. `winner` 한 명만 대조하면 2번째 자리가 놓친다.
    const dealerGot = s.dealer && s.winners.includes(s.dealer);
    const blk = el("div", "ta-case" + (s.dealer && !dealerGot ? " miss" : ""));
    const h = el("div", "ta-case-h");
    h.append(el("span", "ta-cyc",
      s.dealer ? T("{dealer}{v} 버스트하는 사이클", { dealer: s.dealer, v: ga(s.dealer) }) : T("3버 없음")));
    h.append(el("span", "ta-dealer", T("받는 사람: {v}", { v: s.winners.join(" · ") || "-" })));
    if (s.dealer) {
      h.append(el("span", "ta-mark" + (dealerGot ? " ok" : " miss"),
        dealerGot ? T("✔ 3버가 받음") : T("✘ 3버가 못 받음")));
    }
    blk.append(h);
    for (const r of s.rows) {
      const row = el("div", "ta-row" + (r.got ? " got" : ""));
      row.append(faceOne(r.name));
      const nm = el("span", "ta-nm", r.name);
      if (r.selfBurst) nm.append(el("i", "cmp-tag in", "버스트 자버프"));
      if (r.selfFb) nm.append(el("i", "cmp-tag in", "풀버스트 자버프"));
      if (r.supported) nm.append(el("i", "cmp-tag in", "버스트 지원"));
      if (r.powered) nm.append(el("i", "cmp-tag in", buff.buff || T("버프")));
      row.append(nm);
      const v = el("span", "ta-atk", Math.round(r.atk).toLocaleString("ko-KR"));
      v.title = T("소지 {v} × (1 + {v1}%)", { v: r.base.toLocaleString("ko-KR"), v1: r.pct.toFixed(1) })
        + (r.flat ? ` + ${Math.round(r.flat).toLocaleString("ko-KR")}` : "");
      row.append(v);
      if (r.got) row.append(el("span", "ta-need got", "받음"));
      else if (r.tie) row.append(el("span", "ta-need tie", "동점 — 순서로 밀림"));
      else row.append(el("span", "ta-need", T("공증 +{v}%p 필요", { v: r.need.toFixed(1) })));
      blk.append(row);
    }
    body.append(blk);
  }

  const tail = el("p", "prose prose-sm", "이 값은 ");
  tail.append(el("b", null, "예측"));
  tail.append(el("span", null,
    T("입니다 — 소지 공격력 · 오버로드 공증 · 자기 버스트 자버프 · «버스트를 쓴 아군»")
    + T(" 지원까지 셉니다. 중첩·체력·명중 횟수에 걸린 버프는 빠집니다.")
    + T(" 덱을 계산하면 «계산 결과» 버튼이 생기고, 그쪽이 실제로 돌린 값입니다.")));
  body.append(tail);

  $("#topatk-x").onclick = () => dlg.close();
  $("#topatk-close").onclick = () => dlg.close();
  if (!dlg.open) dlg.showModal();
}

// ── 최저공 타게팅 ───────────────────────────────────────────────────────
// 리버렐리오 「차분한 수심 4」: 「풀 버스트 타임 시작 시 **최종 공격력이 가장 낮은**
// 기본 버스트 3단계 아군 1기에게 시전자 기준 차지 속도 ▲」.
//
// **최공의 반대다.** 받으려면 공격력이 더 «낮아야» 한다. 차지형(RL·SR)에게는 차지 속도가
// 곧 딜이라 이 한 자리가 크게 갈리는데, 리버렐리오 자신이 풀버스트 자버프 +160%를 갖고
// 있어서 대개 자기가 최저에서 빠진다 — 그래서 3버가 둘이면 상대가 받는다.

const lowAtkCastersIn = (d) => (d.names || []).filter((n) => n && LOW_ATK_CASTERS.has(n));

/** 3버 `dealer`가 버스트하는 사이클에서 «최저공 3버»가 누구인가. */
function estimateLowAtk(names, caster, dealer) {
  const sig = profSig();
  // 후보는 **기본 버스트 3단계 아군**이다. 시전자도 제외 문구가 없어 후보에 든다.
  const pool = names.filter((n) => n && String(byName.get(n)?.burst) === "3");
  if (!pool.length) return null;
  const rows = [];
  for (const n of pool) {
    const c = atkCache.get(`${sig}|${n}`);
    if (c == null) return null;
    const pct = Number(c.atk_pct || 0)
      + Number(SELF_FB_ATK[n] || 0)
      + (n === dealer ? Number(SELF_BURST_ATK[n] || 0) : 0);
    rows.push({ name: n, base: c.atk, pct, atk: c.atk * (1 + pct / 100) });
  }
  rows.sort((a, b) => a.atk - b.atk);          // 낮은 쪽이 먼저다
  const slots = LOW_ATK_BUFFS[caster]?.slots || 1;
  const win = new Set(rows.slice(0, slots).map((r) => r.name));
  const cut = rows[slots - 1]?.atk ?? 0;
  for (const r of rows) {
    r.got = win.has(r.name);
    // 받으려면 «내려야» 한다 — 부호가 최공과 반대다
    r.drop = r.got ? null : (r.base > 0 ? (r.atk - cut) / r.base * 100 : null);
  }
  // `winners`도 같이 준다 — 지금은 대상이 늘 1명(리버렐리오)이라 `winner` 한 명으로도
  // 맞지만, 최공 대상 쪽에서 같은 가정 때문에 2명짜리 버프(맥스웰·미란다)가 「예측과
  // 다름」으로 잘못 뜬 적이 있다. 대상 수가 늘어도 조용히 같은 문제가 재발하지 않게
  // 여기도 처음부터 명단으로 둔다.
  return { dealer, winner: rows[0]?.name || null, winners: [...win], rows };
}

function renderLowAtk() {
  const box = $("#deck-lowatk");
  if (!box) return;
  box.textContent = "";
  const d = deckOf(state.settings.deck);
  const casters = lowAtkCastersIn(d);
  // **실험 스위치(`lab`)를 타지 않는다.** 최공 대상(renderTopAtk)과 같은 근거다 —
  // 리버렐리오 차속 대상도 조건부·중첩 없이 결정되는 값이라 상용에 내놔도 된다.
  if (!casters.length) { box.hidden = true; return; }
  box.hidden = false;

  const caster = casters[0];
  const names = d.names.filter(Boolean);
  const info = LOW_ATK_BUFFS[caster] || {};
  box.append(el("span", "topatk-k", T("{caster} 차속 대상", { caster })));

  // **최저를 가릴 상대가 있어야 띄운다.** 3버가 한 명이면 그 사람이 받는 것이 자명하다
  // — 미미르의 «흑련-리버렐리오»처럼 리버렐리오와 다른 3버가 같이 있을 때의 질문이다.
  const b3all = names.filter((n) => String(byName.get(n)?.burst) === "3");
  if (b3all.length < 2) { box.hidden = true; box.textContent = ""; return; }

  const st = burstStages(names);
  if (!st.ok) {
    box.append(el("span", "topatk-sum warn",
      T("{v} 버스트가 없어 풀버스트가 열리지 않습니다", { v: st.missing.map((x) => x + T("단계")).join("·") })));
    return;
  }
  if (names.some((n) => !atkCache.has(`${profSig()}|${n}`))) {
    box.append(el("span", "topatk-note", "소지 공격력을 읽는 중…"));
    fillBaseAtk(names).then(() => renderLowAtk());
    return;
  }
  // **순번이 오는 3버만** 가정한다 (`activeB3`) — 안 나가는 사람의 사이클을 세면
  // 화면이 일어나지 않는 상황을 근거로 말한다
  const b3 = activeB3(names);
  const scen = (b3.length ? b3 : [null]).map((x) => estimateLowAtk(names, caster, x))
    .filter(Boolean);
  if (!scen.length) {
    box.append(el("span", "topatk-note", "3단계 버스트 아군이 없어 대상이 없습니다."));
    return;
  }
  const res2 = resultOf(d);
  const done2 = (res2?.top_atk || []).filter((c) => c.kind === "low");
  // 계산 결과가 있으면 **그쪽이 정답이다** — 예측은 순번·조건부 버프를 완전히 세지 못한다
  const predWho = [...new Set(scen.flatMap((x) => x.winners))];
  const who = done2.length ? [...new Set(done2.flatMap((c) => c.chosen))] : predWho;
  const w = who.join(" · ");
  box.append(el("span", "topatk-sum ok", T("{w}{v} 받습니다", { w, v: ga(w) })));
  if (done2.length
      && predWho.slice().sort().join(",") !== who.slice().sort().join(",")) {
    box.append(diffFlag());
  }
  box.append(mkBtn(T("예측"), "btn-ghost", () => openLowAtk(caster, info, scen)));
  if (done2.length) {
    box.append(mkBtn(T("계산 결과"), "btn-primary",
      () => openTopAtk(T("{caster} 차속 대상 — 계산 결과", { caster }), done2)));
  }
}

function openLowAtk(caster, info, scen) {
  const dlg = $("#topatk-sheet");
  const body = $("#topatk-body");
  if (!dlg || !body) return;
  $("#topatk-t").textContent = T("{caster} 차속 대상 (예측)", { caster });
  body.textContent = "";
  const lead = el("p", "prose prose-sm",
    T("{caster} 「{v}」는 풀버스트 시작 시 ", { caster, v: info.buff || "" }));
  lead.append(el("b", null, "최종 공격력이 가장 낮은 3단계 버스트 아군"));
  lead.append(el("span", null,
    T(" {v}기에게 시전자 기준 차지 속도 {v1}%를 겁니다.", { v: info.slots || 1, v1: info.pct || 0 })
    + T(" 최공 버프와 **반대**라, 받으려면 공격력이 더 낮아야 합니다.")));
  lead.textContent = lead.textContent.replace(/\*\*/g, "");
  body.append(lead);

  for (const s of scen) {
    const blk = el("div", "ta-case");
    const h = el("div", "ta-case-h");
    h.append(el("span", "ta-cyc",
      s.dealer ? T("{dealer}{v} 버스트하는 사이클", { dealer: s.dealer, v: ga(s.dealer) }) : T("3버 없음")));
    h.append(el("span", "ta-dealer", T("받는 사람: {v}", { v: s.winners.join(" · ") || "-" })));
    blk.append(h);
    for (const r of s.rows) {
      const row = el("div", "ta-row" + (r.got ? " got" : ""));
      row.append(faceOne(r.name));
      const nm = el("span", "ta-nm", r.name);
      if (SELF_FB_ATK[r.name]) nm.append(el("i", "cmp-tag out", "풀버스트 자버프"));
      if (r.name === s.dealer && SELF_BURST_ATK[r.name]) {
        nm.append(el("i", "cmp-tag out", "버스트 자버프"));
      }
      row.append(nm);
      const v = el("span", "ta-atk", Math.round(r.atk).toLocaleString("ko-KR"));
      v.title = T("소지 {v} × (1 + {v1}%)", { v: r.base.toLocaleString("ko-KR"), v1: r.pct.toFixed(1) });
      row.append(v);
      row.append(el("span", "ta-need" + (r.got ? " got" : ""),
        r.got ? T("받음") : T("공증 −{v}%p 내려야", { v: r.drop.toFixed(1) })));
      blk.append(row);
    }
    body.append(blk);
  }
  body.append(el("p", "prose prose-sm",
    T("차지 속도는 차지형(RL·SR)에게 곧 딜입니다. 이 값은 예측이며, 자버프가 큰 니케는")
    + T(" 최저에서 빠지므로 대개 상대가 받습니다.")));
  $("#topatk-x").onclick = () => dlg.close();
  $("#topatk-close").onclick = () => dlg.close();
  if (!dlg.open) dlg.showModal();
}

// 진단은 **로컬 직접 접속에서만** 보인다 (`/api/health`의 `lab`). 서버가 판정하므로
// 코드가 배포에 딸려 가도 운영에서는 나오지 않는다 — 화면 스위치로 가릴 필요가 없다.
const labOn = () => !!HEALTH.lab;

// ── 레이드 모드 (솔로 / 유니온) ─────────────────────────────────────────
// 화면은 하나를 공유하고 **데이터만 갈아 끼운다** — 편성·큐브·컨트롤·결과 UI가
// 똑같은데 화면을 복제하면 고칠 곳이 두 배가 된다. 모드별로 덱·결과·전투 조건을
// 따로 들고 있다가 토글이 통째로 바꿔치기한다.
//
// 유니온 레이드: 속성 5종은 **고정**이고(안의 보스만 바뀐다) 그중 3개를 골라
// 덱 3개로 친다. 4렙은 마지막 속성 하나로 고정이라 그 판은 속성을 못 고른다.
const UNION_CODES = ["전격", "수냉", "작열", "풍압", "철갑"];
// 속성색 토큰 — 슬롯 아래 상태 바가 «무엇을 겨눴나»를 색으로도 말한다
const CODE_VAR = { 작열: "var(--code-fire)", 수냉: "var(--code-water)",
                   풍압: "var(--code-wind)", 전격: "var(--code-elec)",
                   철갑: "var(--code-iron)" };
const UNION_DECKS = 3;

// 유니온 레이드 회차별 보스 — 다섯 속성이 **한 번씩** 배정된다(순서는 회차마다
// 다르고, 같은 랩처가 회차에 따라 다른 속성으로 나온다). 레벨이 올라도 라인업은
// 그대로고 체력만 오른다. 4단계는 5번째 보스 하나만 남는다.
// [속성, 그림 파일(image/boss/*.webp), 이름] — 실측 출처는 research/blablalink.
const UNION_SEASONS = [
  { id: 1000035, label: "S35", start: "2025-12-04",
    bosses: [["작열", "ecg002", "듀얼 링 [H.S.T.A.]"], ["수냉", "ecg006", "스프레드 [P.S.I.D.]"], ["전격", "eba001", "스톰 브링어 [Z.E.U.S.]"], ["풍압", "mca003_re", "리빌드 핑거즈 [A.N.M.I]"], ["철갑", "ebg002_dmtr", "마테리얼H [D.M.T.R.]"]] },
  { id: 1000036, label: "S36", start: "2026-01-01",
    bosses: [["수냉", "mcg005", "닥터 [P.S.I.D.]"], ["작열", "mcg006", "헤비메탈 [H.S.T.A.]"], ["철갑", "bbg004_dmtr_intercept", "크라켄 [D.M.T.R.]"], ["전격", "eca001_re", "리빌드 오벨리스크 [Z.E.U.S.]"], ["풍압", "mbg004_anmi", "모더니아 [A.N.M.I.]"]] },
  { id: 1000037, label: "S37", start: "2026-01-29",
    bosses: [["풍압", "bcg002", "레이턴스 [A.N.M.I.]"], ["철갑", "mca003", "핑거즈 [D.M.T.R.]"], ["전격", "mbg002", "그레이브 디거 [Z.E.U.S.]"], ["수냉", "ecg005_re", "리빌드 빅 토르소 [P.S.I.D.]"], ["작열", "bbg003", "블랙스미스 [H.S.T.A.]"]] },
  { id: 1000038, label: "S38", start: "2026-03-05",
    bosses: [["전격", "bcg003", "포터 [Z.E.U.S.]"], ["작열", "eca003", "플레이트 [H.S.T.A.]"], ["수냉", "ebg001", "랜드 이터 [P.S.I.D.]"], ["풍압", "mca003_re", "리빌드 핑거즈 [A.N.M.I]"], ["철갑", "ebg002_dmtr", "마테리얼H [D.M.T.R.]"]] },
  { id: 1000039, label: "S39", start: "2026-04-09",
    bosses: [["작열", "ecg006", "스프레드 [H.S.T.A.]"], ["수냉", "xcg002", "크리스탈 아머 [P.S.I.D.]"], ["철갑", "bbg004_dmtr_intercept", "크라켄 [D.M.T.R.]"], ["풍압", "bcg001_re", "리빌드 쿠쿰버 [A.N.M.I.]"], ["전격", "eba001", "스톰 브링어 [Z.E.U.S.]"]] },
  { id: 1000040, label: "S40", start: "2026-05-14",
    bosses: [["철갑", "bca002", "두리안 [D.M.T.R.]"], ["작열", "mcg006", "헤비메탈 [H.S.T.A.]"], ["풍압", "mbg004_anmi", "모더니아 [A.N.M.I.]"], ["전격", "mcg004_re", "리빌드 벌컨R [Z.E.U.S.]"], ["수냉", "mbg001_psid", "알트아이젠 [P.S.I.D.]"]] },
  { id: 1000041, label: "S41", start: "2026-06-11",
    bosses: [["풍압", "mca001", "시니스터 [A.N.M.I.]"], ["철갑", "mcg007", "레플리카 레드 슈즈 [D.M.T.R.]"], ["작열", "mba002", "니힐리스타 [H.S.T.A.]"], ["수냉", "ecg005_re", "리빌드 빅 토르소 [P.S.I.D.]"], ["전격", "bbg006_zeus", "울트라 [Z.E.U.S.]"]] },
  { id: 1000042, label: "S42", start: "2026-07-09",
    bosses: [["작열", "bca002", "두리안 [H.S.T.A.]"], ["전격", "mcg005", "닥터 [Z.E.U.S.]"], ["수냉", "mbg001_psid", "알트아이젠 [P.S.I.D.]"], ["풍압", "eca001_re", "리빌드 오벨리스크 [A.N.M.I.]"], ["철갑", "bbg004_dmtr_intercept", "크라켄 [D.M.T.R.]"]] },
  { id: 1000043, label: "S43", start: "2026-07-30",
    bosses: [["수냉", "bcg005", "선바스 [P.S.I.D.]"], ["풍압", "eca003", "플레이트 [A.N.M.I.]"], ["작열", "bbg002", "토커티브 [H.S.T.A.]"], ["철갑", "mca003_re", "리빌드 핑거즈 [D.M.T.R]"], ["전격", "ebg002", "마테리얼H [Z.E.U.S.]"]] },
];

/** 지금 볼 회차. 아직 고르개가 없으니 **가장 최근 회차**를 쓴다 — 저장값이 있으면
    그쪽이 우선이라, 나중에 회차 고르개를 붙여도 이 함수만 그대로 쓰면 된다. */
// 「커스텀」 회차 — 아직 안 나온 회차를 직접 짜 보는 자리다. 실제 회차 표는 건드리지
// 않고 **저장소에 따로** 든다(U().custom). 처음에는 가장 최근 회차를 베껴 두어,
// 비어 있는 화면 대신 «고칠 것이 있는 화면»에서 시작한다.
const CUSTOM_SEASON = "custom";
function customSeason() {
  U().custom ||= {
    id: CUSTOM_SEASON,
    label: T("커스텀"),
    start: T("직접 설정"),
    bosses: UNION_SEASONS[UNION_SEASONS.length - 1].bosses.map((b) => [...b]),
  };
  return U().custom;
}

/** 그 회차에 내가 골라 둔 보스 셋. **회차마다 따로 기억한다** — 회차가 바뀌면
 *  보스 라인업이 통째로 바뀌므로, 지난 회차에 걸어 둔 배정이 그대로 남아 있으면
 *  「고른 적 없는데 뭔가 꽂혀 있다」가 된다. 고른 적 없는 회차는 빈 채로 시작한다. */
function seasonPicks(id = unionSeason().id) {
  return (U().picks[String(id)] ||= [null, null, null]);
}

/** 줄에 꽂힌 보스를 그 회차의 기억과 맞춘다. 회차를 바꿀 때 부른다. */
function applySeasonPicks() {
  const picks = seasonPicks();
  for (let i = 0; i < UNION_DECKS; i++) uDeck(i).weak = picks[i] || null;
}

function unionSeason() {
  const want = U().season;
  if (want === CUSTOM_SEASON) return customSeason();
  return UNION_SEASONS.find((s) => s.id === want) || UNION_SEASONS[UNION_SEASONS.length - 1];
}

/** 속성 하나에 걸린 이번 회차 보스: {code, art, name}. 회차마다 속성당 하나뿐이다. */
function bossOf(code) {
  const row = unionSeason().bosses.find((b) => b[0] === code);
  return row ? { code, art: row[1], name: row[2] } : null;
}
const unionOn = () => !!HEALTH.union;

// 유니온은 **자기 데이터를 따로 든다.** 솔로의 state.decks/results는 한 글자도
// 건드리지 않는다 — 두 콘텐츠는 덱 수도, 보스도, 레벨 정책도 다르다.
function U() {
  state.union ||= { decks: null, level: null, results: {}, battle: null,
                    duration: 180, code: "풍압", profileId: null };
  state.union.decks ||= Array.from({ length: UNION_DECKS }, () => newDeck());
  // 레이드 설정(방어력·코어·적정거리·무기 계수·버스트 사이클)도 **따로 든다**.
  // 솔로와 같은 상자를 쓰면 한쪽을 만질 때 다른 쪽 결과가 조용히 바뀐다.
  state.union.battle ||= { ...BATTLE_DEFAULT, optimal_range_weapons: [],
                           weapon_coeff: { ...BATTLE_DEFAULT.weapon_coeff } };
  // 레이드 설정은 **줄마다 따로** 든다. 세 줄이 서로 다른 보스를 치므로 방어력도
  // 코어도 적정거리도 같을 이유가 없다. 예전에 한 벌만 쓰던 값(state.union.battle)이
  // 있으면 그것을 씨앗으로 세 줄에 나눠 심는다 — 저장해 둔 설정을 잃지 않는다.
  for (const d of state.union.decks) {
    d.battle ||= { ...state.union.battle,
                   optimal_range_weapons: [...(state.union.battle.optimal_range_weapons || [])],
                   weapon_coeff: { ...(state.union.battle.weapon_coeff || {}) } };
  }
  // 검색·필터도 따로 든다. 화면(필터 바 DOM)은 솔로와 같은 것을 쓰지만 **상태를
  // 나눠** 유니온에서 건 필터가 편성으로 새어 들지 않는다 — 전투력 계산기가
  // state.coopFilter로 하는 것과 같은 방식이다.
  state.union.filter ||= defaultFilter();
  // 회차별 보스 기억. **여기서 한 번만** 옮겨 심는다 — 회차를 바꾼 뒤에 심으면
  // 지금 줄에 꽂힌 것이 «새로 고른 회차»의 기억으로 들어가, 고른 적 없는 회차에
  // 보스가 생기고 원래 회차는 비어 버린다(실측).
  if (!state.union.picks) {
    const sid = state.union.season ?? UNION_SEASONS[UNION_SEASONS.length - 1].id;
    state.union.picks = { [String(sid)]: state.union.decks.map((d) => d.weak || null) };
  }
  // 예전에 저장된 엉뚱한 값(이미지 주소 등)을 걷어낸다 — 남아 있으면 보스 이름
  // 자리에 그대로 뜬다. 모르는 값은 «안 고름»으로 되돌린다.
  for (const d of state.union.decks) {
    if (d.weak && !UNION_CODES.includes(d.weak)) d.weak = null;
  }
  return state.union;
}

/** 유니온이 쓰는 필터 상자. */
const uFilter = () => U().filter;

/** 빈 칸을 눌러 여는 «고르기» 시트의 필터. 아래 목록과 **따로 든다** — 한 명 찾으려고
 *  건 조건이 목록에 그대로 남으면, 시트를 닫고 나서 «왜 몇 명 안 보이지»가 된다. */
const pickFilter = () => (U().pickFilter ||= defaultFilter());

// 지금 고르기 시트가 채우려는 자리. null이면 닫혀 있다.
let pickAt = null;

const BURST_CHIPS = [["1", "Ⅰ"], ["2", "Ⅱ"], ["3", "Ⅲ"], ["4", "Λ"]];

// 보스 속성 → **그 보스를 치는 속성**. WEAK_TO_ENEMY(치는 쪽 → 맞는 쪽)의 역방향이다.
// 원본 데이터(blablalink `nikke_list_v2.json`의 weak_element_id)로 확인한 사슬:
//   수냉 ▶ 작열 ▶ 풍압 ▶ 철갑 ▶ 전격 ▶ 수냉
const COUNTER_OF = Object.fromEntries(
  Object.entries(WEAK_TO_ENEMY).map(([hit, hurt]) => [hurt, hit]));

// 유니온 한 줄에 우월 속성이 이만큼은 있어야 한다 — 그 아래면 경고를 띄운다.
const UNION_COUNTER_MIN = 3;

/** 두 줄의 편성을 **통째로 맞바꾼다.** 보스는 줄에 남는다 — 「이 보스는 그대로 두고
 *  편성만 다른 줄로」가 실제로 하고 싶은 일이다. */
function uSwapDecks(i, j) {
  if (i === j || i < 0 || j < 0 || i >= UNION_DECKS || j >= UNION_DECKS) return;
  uSnap(T("{v}·{v1}번 줄 편성 맞바꾸기", { v: i + 1, v1: j + 1 }));
  const a = uDeck(i), b = uDeck(j);
  for (const k of ["names", "cubes", "control"]) {
    const t = a[k]; a[k] = b[k]; b[k] = t;
  }
  saveAll();
  renderAll();
}

/** 빈 칸을 눌러 «고르기» 시트를 연다. 검색과 필터만 있고 육성 수정은 없다 —
 *  여기서 할 일은 «찾아서 꽂기» 하나뿐이다. */
function openPick(deckIdx, idx) {
  const dlg = $("#pick-sheet");
  if (!dlg) return;
  pickAt = { deckIdx, idx };
  const f = pickFilter();
  f.q = "";                                  // 열 때마다 검색어는 비운다
  $("#pick-title").textContent = T("{v}번 줄 {v1}번 자리", { v: deckIdx + 1, v1: idx + 1 });
  renderPick();
  if (!dlg.open) dlg.showModal();
  $("#pick-q")?.focus();
}

function closePick() {
  pickAt = null;
  const dlg = $("#pick-sheet");
  if (dlg?.open) dlg.close();
}

/** 시트 안의 칩·목록을 지금 필터로 다시 그린다. */
function renderPick() {
  const f = pickFilter();
  const q = $("#pick-q");
  if (q && document.activeElement !== q) q.value = f.q;

  const burst = $("#pick-burst");
  if (burst) {
    burst.textContent = "";
    for (const [v, label] of BURST_CHIPS) {
      const b = el("button", "chip" + (f.burst.includes(v) ? " on" : ""), label);
      b.type = "button";
      b.onclick = () => {
        f.burst = f.burst.includes(v) ? f.burst.filter((x) => x !== v) : [...f.burst, v];
        saveAll(); renderPick();
      };
      burst.append(b);
    }
  }

  const elem = $("#pick-elem");
  if (elem) {
    elem.textContent = "";
    for (const code of UNION_CODES) {
      const b = el("button", "chip chip-elem" + (f.element.includes(code) ? " on" : ""));
      b.type = "button";
      b.title = code;
      b.style.setProperty("--code-c", CODE_VAR[code] || "var(--color-stage-line)");
      const file = ELEMENT_ICON[code];
      if (file) { const im = el("img"); im.src = `image/icon/${file}`; im.alt = code; b.append(im); }
      else b.append(el("span", null, code));
      b.onclick = () => {
        f.element = f.element.includes(code)
          ? f.element.filter((x) => x !== code) : [...f.element, code];
        saveAll(); renderPick();
      };
      elem.append(b);
    }
  }

  const wrap = $("#pick-pool");
  if (!wrap) return;
  wrap.textContent = "";
  // 이미 다른 줄에 들어간 이름은 **잠근다** — 유니온도 줄 간 중복이 불가하다.
  const used = new Map();
  U().decks.forEach((d, di) => {
    for (const n of d.names) if (n) used.set(n, di + 1);
  });
  const list = filteredRoster(false, f);
  for (const rec of list) {
    const at = used.get(rec.name);
    const c = card(rec.name, { dim: !rec.parsed || !!at, usedIn: at, party: at || 0 });
    // 고르는 자리다 — 육성 수정(⚙)·즐겨찾기(★)는 여기서 치운다
    c.querySelector(".nk-cog")?.remove();
    c.querySelector(".nk-fav")?.remove();
    if (CODE_VAR[rec.element]) c.style.setProperty("--frame", CODE_VAR[rec.element]);
    if (!rec.parsed) {
      c.title = T("스킬 미파싱 — 계산할 수 없습니다");
    } else if (at) {
      c.title = T("{at}번 줄에서 사용 중 — 줄 간 중복은 불가합니다", { at });
    } else {
      c.onclick = () => {
        if (!pickAt) return;
        const prev = uDeck(pickAt.deckIdx).names[pickAt.idx];
        uSnap(prev && prev !== rec.name ? T("{prev} → {name} 교체", { prev, name: rec.name }) : T("{name} 배치", { name: rec.name }),
              prev && prev !== rec.name ? { ...pickAt } : null);
        uDeck(pickAt.deckIdx).names[pickAt.idx] = rec.name;
        const { deckIdx, idx } = pickAt;
        closePick();
        saveAll(); renderAll();
        slamSlot(deckIdx, idx);
      };
    }
    wrap.append(c);
  }
  const n = $("#pick-count");
  if (n) n.textContent = T("{length}명", { length: list.length });
}

/** 니케 한 명의 컨트롤을 모달로 연다. 패널은 한 벌뿐이라 데려왔다 돌려보낸다. */
function openUnionCtrl(name) {
  const cp = $("#ctrl-panel"), dlg = $("#ctrl-sheet"), host = $("#ctrl-host");
  if (!cp || !dlg || !host) return;
  uCtrlOpen = name;
  host.append(cp);
  cp.hidden = false;
  $("#ctrl-title").textContent = T("{name} — 컨트롤", { name });
  buildControl();
  renderBench();
  if (!dlg.open) dlg.showModal();
}

/** 닫으면 패널을 제자리(솔로 편성 상자)로 돌려보낸다 — 모달 안에 두고 오면
 *  솔로에서 컨트롤을 펼쳐도 아무것도 안 나온다. */
function closeUnionCtrl() {
  const cp = $("#ctrl-panel"), dlg = $("#ctrl-sheet");
  const home = document.querySelector("#squad-wrap .squad");
  uCtrlOpen = null;
  if (cp && home) { home.append(cp); cp.hidden = true; }
  if (dlg?.open) dlg.close();
  renderBench();
}

/** 그 줄의 레이드 설정 패널을 연다. 패널은 **한 벌뿐**이고 줄마다 갈아 끼운다 —
 *  복제하면 입력칸이 세 벌이 되어 어느 것이 진짜인지 알 수 없게 된다. */
function openRowBattle(i) {
  const bp = $("#btpanel"), dlg = $("#raid-sheet"), host = $("#raid-host");
  if (!bp || !dlg || !host) return;
  uBattleRow = i;
  uBattleOpen = true;
  // 워크벤치를 **아래로 늘리지 않는다** — 세 줄이 한 화면에 보이는 것이 이 화면의
  // 요점이라, 설정이 줄 사이를 벌리면 화면이 무너진다. 패널을 모달로 데려온다.
  host.append(bp);
  bp.hidden = false;
  $("#raid-title").textContent =
    T("{v}번 줄 레이드 설정 — {v1}", { v: i + 1, v1: bossOf(uWeak(uDeck(i)))?.name || uWeak(uDeck(i)) || T("보스 없음") });
  buildBattle();                      // 입력칸을 그 줄의 값으로 다시 채운다
  syncBattleChrome();
  if (!dlg.open) dlg.showModal();
  renderBench();
}

/** 모달을 닫는다. 패널은 제자리(.fwrap)로 돌려보낸다 — 모달 안에 두고 오면
 *  솔로의 «레이드 설정»을 눌러도 아무것도 안 열린다. */
function closeRowBattle() {
  const bp = $("#btpanel"), dlg = $("#raid-sheet");
  const home = document.querySelector("#bt-toggle")?.closest(".fwrap");
  uBattleOpen = false;
  if (bp && home) { home.append(bp); bp.hidden = true; }
  if (dlg?.open) dlg.close();
  renderBench();
}

/** 레이드 설정이 기본값에서 벗어났나 — 줄 버튼에 표시를 달 때 쓴다. */
function battleChanged(b) {
  if (!b) return false;
  for (const k of Object.keys(BATTLE_DEFAULT)) {
    const a = b[k], c = BATTLE_DEFAULT[k];
    if (Array.isArray(c)) { if ((a || []).length !== c.length) return true; continue; }
    if (c && typeof c === "object") {
      for (const w of Object.keys(c)) if ((a || {})[w] !== c[w]) return true;
      continue;
    }
    if (a !== c) return true;
  }
  return false;
}

/** 방금 놓은 것을 «쾅» 하고 알린다. renderBench()가 DOM을 통째로 새로 그리므로
 *  **다시 그린 뒤**에 불러야 한다 — 먼저 붙이면 그 노드가 사라진다.
 *  애니메이션은 CSS가 들고, 여기서는 색과 시작 신호만 준다. */
/** 화면 연출을 켤지. **솔로·유니온이 같은 값을 본다** — 끄고 켜는 자리가 둘인데
 *  값이 따로 놀면 «껐는데 저쪽은 튄다»가 된다. 끄면 연출을 **아예 시작하지 않고**
 *  결과만 조용히 바꾼다(중간에 멈추면 자국이 남는다). */
const fxOn = () => state.settings.fx !== false;

/** 끈 상태를 문서에 새긴다 — CSS 쪽 연출은 `:root[data-fx="off"]`가 통째로 멎힌다. */
function applyFx() {
  document.documentElement.dataset.fx = fxOn() ? "on" : "off";
  const b = $("#fx-toggle");
  if (!b) return;
  b.setAttribute("aria-pressed", String(!fxOn()));
  b.textContent = fxOn() ? "✦" : "✧";
  b.title = fxOn() ? T("화면 효과 끄기") : T("화면 효과 켜기");
}

function replay(node, cls) {
  if (!node || !fxOn()) return;
  node.classList.remove(cls);
  void node.offsetWidth;              // 리플로우 — 같은 자리에 연달아 놓아도 다시 튄다
  node.classList.add(cls);
  node.addEventListener("animationend", () => node.classList.remove(cls), { once: true });
}

/** 보스를 꽂은 순간 — 그 줄을 오른쪽으로 훑고, 아래 로스터가 **약점 속성색**으로
 *  잠깐 물든다. 「이 줄엔 이 속성을 넣어라」가 글자가 아니라 몸으로 읽힌다. */
function slamRow(i, code) {
  if (!fxOn()) return;
  const want = COUNTER_OF[code];
  const c = CODE_VAR[want] || CODE_VAR[code] || "var(--color-accent)";
  const row = $("#bench-rows")?.children[i];
  if (row) { row.style.setProperty("--slam-c", c); replay(row, "slam"); }
  // 아래 목록은 **통째로 물들이지 않는다.** 그 보스를 치는 속성 카드만 왼쪽에서
  // 오른쪽으로 차례로 불이 들어온다 — 「이 중에 골라라」가 화면에서 바로 짚인다.
  const roster = document.querySelector(".roster");
  if (roster) { roster.style.setProperty("--slam-c", c); replay(roster, "wash"); }
  if (!want) return;
  // 먼저 **지난 속성을 끈다.** 불(.lit)만 끄고 움직임(.beat)을 안 끄면, 보스를
  // 바꿨는데 이전 속성 카드가 남은 횟수만큼 계속 뛰어 «어느 쪽이지»가 된다.
  for (const on of document.querySelectorAll("#pool .nk.lit, #pool .nk.beat")) {
    on.classList.remove("lit", "beat");
  }
  litElem = want;                       // 다음 보스를 꽂을 때까지 켜 둔다
  const hits = document.querySelectorAll(`#pool .nk[data-elem="${want}"]`);
  hits.forEach((card, k) => {
    card.classList.add("lit");
    // 화면 왼쪽에 있는 것부터 켜져야 «훑고 지나간다»가 된다. 목록 순서가 곧
    // 왼→오→다음 줄이라 인덱스만으로 파도가 만들어진다. 너무 늘어지지 않게 자른다.
    card.style.setProperty("--hit-d", `${Math.min(k * 26, 900)}ms`);
    // 가만히 빛나기만 하면 목록에 묻힌다 — **5초쯤 계속 튀고 번쩍인다.**
    // 파도(지연)로 시작해 저마다 몇 번 뛰고 멎는다. 멎은 뒤엔 .lit가 남아
    // 다음 보스를 꽂을 때까지 «여기가 그 속성»을 계속 말한다.
    replay(card, "beat");
  });
}

/** 니케를 칸에 놓은 순간 — 그 칸만 짧게 «쾅». */
function slamSlot(deckIdx, idx) {
  if (!fxOn()) return;
  const row = $("#bench-rows")?.children[deckIdx];
  // 칸이 아니라 **칸을 감싼 상자**에 건다 — .u-slot은 overflow:hidden이라 충격파가
  // 칸 밖으로 못 퍼진다. 상자에 걸면 파장이 이웃 칸 위로 번져 «쾅»이 산다.
  const cell = row?.querySelectorAll(".u-cell")[idx];
  replay(cell, "slam");
  replay(row, "nudge");
  puff(cell);
}

/** 떨어진 자리에서 이는 먼지. 입자 수·방향이 매번 달라야 «찍어낸 효과»로 안 보인다.
 *  DOM으로 만들고 끝나면 지운다 — 애니메이션이 끝난 노드를 남겨 두면 줄마다 쌓인다. */
function puff(cell) {
  if (!fxOn()) return;
  if (!cell || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  cell.querySelector(".dust")?.remove();
  const box = el("div", "dust");
  for (let k = 0; k < 9; k++) {
    const bit = el("i");
    const dir = k < 5 ? -1 : 1;                       // 좌우로 갈라 퍼진다
    const x = dir * (14 + Math.random() * 46);
    const y = -(6 + Math.random() * 26);
    bit.style.setProperty("--dx", `${x.toFixed(1)}px`);
    bit.style.setProperty("--dy", `${y.toFixed(1)}px`);
    bit.style.setProperty("--sz", `${(3 + Math.random() * 5).toFixed(1)}px`);
    bit.style.setProperty("--d", `${(Math.random() * 90).toFixed(0)}ms`);
    box.append(bit);
  }
  cell.append(box);
  setTimeout(() => box.remove(), 900);
}

/** 두 줄의 **보스만** 맞바꾼다(편성은 제자리). 줄에 꽂힌 보스를 끌어 옮길 때 쓴다. */
function uSwapBoss(i, j) {
  if (i === j || i < 0 || j < 0 || i >= UNION_DECKS || j >= UNION_DECKS) return;
  uSnap(T("{v}·{v1}번 줄 보스 맞바꾸기", { v: i + 1, v1: j + 1 }));
  const a = uDeck(i), b = uDeck(j);
  const t = a.weak; a.weak = b.weak; b.weak = t;
  const pk = seasonPicks();
  pk[i] = a.weak; pk[j] = b.weak;     // 기억도 함께 맞바꾼다
  bossPick = null;
  saveAll();
  renderBench();
  slamRow(j, b.weak);
}

/** 줄에 보스를 꽂는다. **중복을 허용한다** — 같은 보스를 여러 덱으로 쳐도 되고
 *  (횟수만 쓴다), 남의 줄 것을 뺏어 오면 그 줄이 빈 채로 튕겨 다닌다.
 *  줄끼리 자리를 바꾸는 것은 보스 카드를 끌었을 때(uSwapBoss)만이다. */
function uSetBoss(deckIdx, code) {
  // 드롭 짐은 **아무 문자열이나 올 수 있다.** 브라우저 기본 이미지 끌기가 끼어들면
  // 이미지 주소가 그대로 실려 오고, 그걸 그냥 넣으면 보스 코드 자리에 URL이 앉아
  // 카드 이름으로 튀어나온다(실측). 아는 다섯 속성만 받는다.
  if (!UNION_CODES.includes(code)) return;
  uSnap(T("{v}번 줄 보스 바꾸기", { v: deckIdx + 1 }));
  uDeck(deckIdx).weak = code;
  seasonPicks()[deckIdx] = code;      // 이 회차에 이렇게 골랐다고 기억해 둔다
  bossPick = null;
  saveAll();
  renderBench();
  slamRow(deckIdx, code);
}

/** 한 줄의 속성 셈. 보스를 안 골랐으면 null(따질 것이 없다).
 *
 *  경고 기준은 «우월이 모자란가»가 아니라 **«넣은 것 중 틀린 쪽이 절반을 넘었나»**다
 *  (3명 넣었으면 2명, 5명 다 넣었으면 3명부터). 빈 줄과 반쯤 짠 줄이 저절로 빠진다 —
 *  한 명 넣자마자 붉어지면 시작하기도 전에 셋 다 경고가 뜨고, 그러면 경고가 뜻을
 *  잃는다. 넣은 것의 절반을 넘겨 엉뚱하면 그때는 «이 줄은 이 보스용이 아니다»가 사실이다. */
function counterCount(d) {
  const want = COUNTER_OF[uWeak(d)];
  if (!want) return null;
  let n = 0, wrong = 0;
  for (const name of d.names) {
    if (!name) continue;
    if (byName.get(name)?.element === want) n += 1;
    else wrong += 1;
  }
  return { want, n, wrong, ok: n >= UNION_COUNTER_MIN, bad: wrong * 2 > n + wrong };
}

/** 니케 하나의 개별 설정(큐브·레벨·컨트롤)이 들어갈 덱. 솔로는 «지금 고른 덱»
 *  하나뿐이지만 유니온은 세 줄 중 **그 니케가 들어 있는 줄**이다. 이걸 안 갈라
 *  두면 유니온에서 건 컨트롤이 솔로 덱에 쓰인다(실측). */
function ctrlDeck(name) {
  if (modeNow() !== "union") return deckOf(state.settings.deck);
  for (let i = 0; i < UNION_DECKS; i++) if (uDeck(i).names.includes(name)) return uDeck(i);
  return uDeck(0);
}

/** 지금 보고 있는 편성 칸을 다시 그린다 — 모드마다 그리는 화면이 다르다. */
const refreshSlots = () => (modeNow() === "union" ? renderBench() : renderSlots());

/** 컨트롤 패널이 지금 펼쳐 놓은 니케. 유니온은 워크벤치가 따로 기억한다. */
const ctrlName = () => (modeNow() === "union" ? uCtrlOpen : ctrlOpen);

/** 프리셋·기록도 모드별로 나눠 든다 — 5덱짜리 솔로 프리셋을 3줄 유니온에 끼우면
 *  뜻이 어긋난다. 목록만 갈라 두면 화면은 그대로 쓰면서 서로 섞이지 않는다. */
const presetsNow = () => (modeNow() === "union" ? (U().presets ||= []) : state.presets);
const recordsNow = () => (modeNow() === "union" ? (U().records ||= []) : state.records);

/** 프리셋·기록 목록 통째 쓰기 (필터·자르기 결과를 되돌려 넣을 때). */
function setPresets(v) { if (modeNow() === "union") U().presets = v; else state.presets = v; }
function setRecords(v) { if (modeNow() === "union") U().records = v; else state.records = v; }

/** 지금 화면이 쓰는 전투 조건 상자. 솔로는 state.battle, 유니온은 state.union.battle. */
// 레이드 설정 패널이 지금 **어느 줄**을 보고 있나. 유니온은 설정이 세 벌이라
// 패널 하나를 줄마다 갈아 끼워 쓴다(복제하면 입력칸이 세 벌이 되어 상태가 어긋난다).
let uBattleRow = 0;
// 패널이 지금 펼쳐져 있나. DOM의 hidden만 보고 판단하면, 줄을 다시 그리는 사이
// 패널이 잠시 자리를 비켜 있어 «닫힌 것»으로 오해한다.
let uBattleOpen = false;

/** 지금 화면이 편집 중인 레이드 설정. 유니온은 **고른 줄**의 것이다. */
const battleNow = () => (modeNow() === "union" ? uDeck(uBattleRow).battle : state.battle);

/** 그 덱이 **계산에 쓸** 레이드 설정. 화면이 무엇을 보고 있든 덱 자기 것을 쓴다. */
const battleFor = (d) => (modeNow() === "union" ? (d?.battle || battleNow()) : state.battle);

/** 그 덱이 상대할 **적 코드**. 유니온은 줄에 꽂힌 보스의 속성이 곧 적 코드다
 *  (솔로는 «데려갈 속성»을 고르므로 WEAK_TO_ENEMY로 뒤집어야 한다). */
const enemyCodeFor = (d) => (modeNow() === "union" ? uWeak(d) : enemyCode());
/** 전투 시간 쓰기 — 지금 모드의 상자에 넣는다. */
function setDuration(v) {
  if (modeNow() === "union") U().duration = v; else state.settings.duration = v;
}
/** 지금 화면이 쓰는 전투 시간. */
const durationNow = () => (modeNow() === "union" ? (U().duration ?? 180)
                                                : state.settings.duration);
const uDeck = (i) => (U().decks[i] ||= newDeck());

/** 지금 모드의 덱 수·덱. 계산과 결과 화면이 이 둘만 보면 모드를 안 따져도 된다. */
const deckCountNow = () => (modeNow() === "union" ? UNION_DECKS : DECK_COUNT);
const deckAt = (i) => (modeNow() === "union" ? uDeck(i) : deckOf(i));
const modeNow = () => (unionOn() && state.settings.mode === "union" ? "union" : "solo");

/** 모드 전환 연출 — 누른 자리에서 충격파가 판 끝까지 퍼지고 판이 한 번 관통된다.
 *  연출은 결과를 기다리게 하지 않는다: 화면은 이미 바뀐 뒤에 얹힌다. */
function playWarp(m) {
  if (!fxOn()) return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const stage = document.querySelector(".stage");
  const head = document.querySelector(".stage-head");
  if (!stage) return;
  const btn = document.getElementById(m === "union" ? "mode-union" : "mode-solo");
  const layer = el("div", "stage-warp");
  if (btn) {
    const b = btn.getBoundingClientRect(), s2 = stage.getBoundingClientRect();
    const x = b.left + b.width / 2 - s2.left, y = b.top + b.height / 2 - s2.top;
    layer.style.setProperty("--wx", `${x}px`);
    layer.style.setProperty("--wy", `${y}px`);
    // 원점에서 **가장 먼 모서리**까지 — 파장이 판 끝을 지나서 사라지게 한다
    const far = Math.max(
      Math.hypot(x, y), Math.hypot(s2.width - x, y),
      Math.hypot(x, s2.height - y), Math.hypot(s2.width - x, s2.height - y));
    layer.style.setProperty("--wr", `${Math.ceil(far)}px`);
  }
  stage.append(layer);
  stage.classList.add("is-warping");
  head?.classList.add("is-swap");
  btn?.classList.add("just-on");
  window.setTimeout(() => {
    layer.remove();
    stage.classList.remove("is-warping");
    head?.classList.remove("is-swap");
    btn?.classList.remove("just-on");
    // CSS의 가장 긴 애니메이션(shock 1000ms)보다 넉넉히 뒤에 치운다 — 먼저 걷으면
    // 파장이 판 끝에 닿기도 전에 잘린다.
  }, 1100);
}

/** 모드 전환. 솔로의 데이터·DOM은 건드리지 않는다 — 화면만 갈아 끼운다. */
function setMode(m, { save: doSave = true, warp = true } = {}) {
  if (m === modeNow()) return;
  state.settings.mode = m;
  // 배치모드는 솔로 5덱 전용이다 — 유니온은 세 줄이 이미 한 화면에 있다
  if (m === "union" && fastMode) setFastMode(false);
  renderMode();
  buildBattle();                 // 레이드 설정 입력칸을 그 모드의 상자로 다시 채운다
  if (doSave) saveAll();
  renderAll();
  // 모드를 바꾸면 **편성으로 돌아온다.** 프리셋·결과·기록은 모드마다 내용이 통째로
  // 갈리는 화면이라, 보던 자리에 그대로 서 있으면 목록이 조용히 바뀐 것처럼 보인다.
  // 모드를 바꾼 사람이 다음에 할 일도 대개 편성이다.
  document.querySelector('.tab[data-tab="deck"]')?.click();
  if (warp) playWarp(m);
}

// 되돌리기 — 유니온 편성은 한 번 실수로 빼면 다시 짜기가 성가시다. 바꾸기 **직전**의
// 세 줄을 통째로 찍어 두고, 누르면 그 순간으로 되돌린다. 계산 결과는 이름으로
// 찾으므로(fingerprint) 되돌리면 옛 결과가 그대로 다시 붙는다.
const UNDO_MAX = 40;
let uUndo = [];

/** 바꾸기 직전을 찍는다. 무엇을 한 것인지도 함께 남겨 버튼이 말해 줄 수 있게 한다. */
function uSnap(label, at = null) {
  if (modeNow() !== "union") return;
  // `at`은 «그 자리에서 되돌릴 수 있는 일»의 좌표다. 니케를 뺐을 때만 채운다 —
  // 빈 칸에 되돌리기 단추를 띄워, 실수로 뺀 자리에서 바로 되돌릴 수 있게 한다.
  uUndo.push({ label, at, decks: JSON.parse(JSON.stringify(U().decks)) });
  if (uUndo.length > UNDO_MAX) uUndo.shift();
}

/** 그 칸이 «방금 뺀 자리»인가 — 맞으면 빈 칸에 되돌리기 단추가 뜬다. */
function undoSpotAt(deckIdx, idx) {
  const top = uUndo[uUndo.length - 1];
  return top?.at && top.at.deckIdx === deckIdx && top.at.idx === idx ? top : null;
}

/** 마지막 한 번을 되돌린다. */
function uUndoLast() {
  const last = uUndo.pop();
  if (!last) return;
  U().decks = last.decks.map((d) => ({ ...d, names: [...d.names] }));
  picked = null; bossPick = null;
  saveAll();
  renderAll();
  flashStatus(T("되돌렸습니다 — {label}", { label: last.label }));
}

// 지금 어느 줄을 끌고 있나 — 드래그 중에는 dataTransfer를 못 읽으므로 따로 든다.
let deckDragFrom = null;
// 줄에 꽂힌 보스를 끌 때, 그 끌기가 **어느 줄에 놓였는지**. 놓인 데가 없으면
// 「밖으로 던진 것」이라 그 줄을 비운다 — 니케 칸에서 끌어내는 것과 같은 손버릇이다.
// dragend는 drop 뒤에 오므로 이 깃발로 갈린다.
let bossDropped = false;

// 걸린 보스 카드를 덮는 사선 줄 수와 간격(px). 상자는 카드의 3배짜리 **정사각**이고
// (실측 684×684), 줄이 그 세로를 끝까지 메워야 어느 모서리도 안 빈다 — 684/13 ≈ 53.
// 34줄로는 442px까지만 닿아 한쪽 귀퉁이가 비어 보였다.
const HATCH_BARS = 54;
const HATCH_GAP = 13;

// 방금 꽂은 보스가 **무엇에 약한가**. 아래 목록에서 그 속성 카드를 계속 켜 둔다 —
// 몇 초 만에 꺼지면 «어느 카드였지»를 다시 찾아야 한다. 다음 보스를 꽂을 때까지
// 남고, 그때 새 속성으로 갈린다.
let litElem = null;

// 직전에 «걸려» 있던 줄. renderBench()가 매번 줄을 통째로 새로 그리므로, 이걸
// 기억해 두지 않으면 이미 걸려 있던 줄까지 사선이 다시 그어진다(실측: 다른 줄에
// 보스를 꽂았을 뿐인데 세 줄이 같이 그어졌다). **새로 걸린 줄만** 긋는다.
let uShortWas = new Set();
let unionHideWired = false;
function wireUnionHide() {
  if (unionHideWired) return;
  const eye = $("#union-hide");
  if (!eye) return;
  unionHideWired = true;
  eye.onclick = () => {
    state.settings.unionNameHidden = state.settings.unionNameHidden === false;
    saveAll();
    renderUnionBar();
  };
}

function renderMode() {
  const sw = $("#mode-sw");
  if (!sw) return;
  sw.hidden = !unionOn();                 // 만드는 중 — 로컬에서만 보인다
  const m = modeNow();
  sw.classList.toggle("at-union", m === "union");
  for (const b of sw.querySelectorAll(".mode-btn")) {
    b.classList.toggle("on", b.dataset.mode === m);
  }
  const pill = $("#mode-pill");
  if (pill) pill.textContent = m === "union" ? "UNION RAID" : "SOLO RAID";
  // 테마 스코프 — tokens.css의 `:root[data-mode="union"]` 블록이 여기에 걸린다
  document.documentElement.setAttribute("data-mode", m);
  // 두 화면은 자리를 나눠 쓴다. 솔로 쪽 DOM은 **감추기만** 하고 내용은 안 건드린다 —
  // 돌아오면 있던 그대로여야 한다.
  const union = m === "union";
  if (!union) litElem = null;
  const squad = $("#squad-wrap"), tabs = $("#deck-tabs");
  if (squad) squad.hidden = union;
  if (tabs) tabs.hidden = union || fastMode;
  // 배치모드는 «5덱 25칸을 빠르게 채우는» 화면이라 유니온에는 쓸 자리가 없다
  const fastWrap = document.querySelector(".fast-toggle-wrap");
  if (fastWrap) fastWrap.hidden = union;
  // 상단 메뉴도 콘텐츠를 탄다. 솔로 전용 도구는 유니온에서 내린다 —
  //   · 솔레덱 훔쳐오기 : 솔로레이드 기록에서 덱을 긁어오는 기능이다
  //   · 프리셋·기록    : 솔로 5덱 기준으로 저장된 것들이라 유니온에 끼면 뜻이 어긋난다
  // «솔레덱 훔쳐오기»만 내린다 — 솔로레이드 기록에서 덱을 긁어오는 기능이라
  // 유니온에는 대상 자체가 없다. 프리셋·기록은 유니온에도 필요하므로 그대로 두고,
  // 목록만 모드별로 갈라 둔다(presetsNow·recordsNow).
  const steal = document.querySelector("#tab-steal");
  if (steal) steal.hidden = union;
  // 「캡처에서 솔레 기록 만들기」는 **솔로레이드 스쿼드 목록 캡처**를 읽는 기능이다.
  // 유니온에는 그런 화면이 없으므로 내린다 — 눌러 봐야 읽을 것이 없다.
  const shotOpen = document.querySelector("#shot-open");
  if (shotOpen) shotOpen.hidden = union;
  // 「덱 비우기」·「프리셋 저장」·「덱 계산」은 «지금 고른 덱» 하나를 뜻한다. 유니온에는
  // 그런 것이 없어서 **어느 줄인지 말하지 않는 버튼**이 된다 — 내리고, 같은 일은
  // 줄 손잡이에서 줄 번호를 달고 한다(N번 줄 계산·줄 비우기).
  for (const sel of ["#deck-clear", "#preset-save-single", "#deck-calc"]) {
    const b = document.querySelector(sel);
    if (b) b.hidden = union;
  }
  // 묶음 저장이 유니온에서는 유일한 저장이다 — 무엇을 담는지 이름으로 말한다
  const bundle = document.querySelector("#preset-save-bundle");
  if (bundle) bundle.textContent = union ? T("프리셋 묶음 저장") : T("묶음 저장");
  const clearAll = document.querySelector("#deck-clear-all");
  if (clearAll) clearAll.textContent = union ? T("전부 비우기") : T("전체 비우기");
  if (union) {
    const drop = document.querySelector("#shot-drop");
    if (drop) drop.hidden = true;
  }
  // 레이드 설정은 유니온에서 **줄마다 따로** 잡아야 한다(보스가 셋이다). 한 벌짜리
  // 공통 패널을 그대로 두면 세 줄에 같은 값이 걸려 뜻이 어긋나므로, 개별 UI를
  // 붙이기 전까지는 내려 둔다.
  const btWrap = document.querySelector("#bt-toggle")?.closest(".fwrap");
  if (btWrap) btWrap.hidden = union;
  // 설정 패널은 **한 벌뿐**이라 모드에 따라 자리를 옮겨 다닌다. 솔로로 돌아올 때
  // 유니온 줄 밑에 두고 오면, 솔로의 «레이드 설정»을 눌러도 아무것도 안 열린다.
  const bp = $("#btpanel");
  if (bp && !union && btWrap && bp.parentElement !== btWrap) {
    btWrap.append(bp);
    bp.hidden = true;
    uBattleOpen = false;
  }

  // 덱 툴바(비우기·프리셋·계산)·컨트롤 패널·계산 처리는 원래 솔로 편성 상자 안에
  // 있다. 유니온에서는 그 상자가 통째로 숨으므로 **옮겨 심는다** — 복제하면
  // «전체 계산» 같은 버튼이 두 벌이 되어 상태가 어긋난다.
  const host = union ? $("#union-bench")?.parentElement : $("#squad-wrap .squad");
  const foot = document.querySelector(".squad-foot");
  const engRow = document.querySelector(".engine-row");
  const ctrlPanel = document.querySelector("#ctrl-panel");
  if (host && foot) {
    if (union) {
      // 워크벤치 바로 아래로
      // 컨트롤은 모달(#ctrl-sheet)이 데려간다 — 여기서 벤치 밑에 심으면 줄이 벌어진다
      $("#union-bench").after(foot, engRow);
    } else if (foot.parentElement !== host) {
      host.append(ctrlPanel, foot, engRow);
    }
  }
  const ub = $("#union-bar"), sw2 = $("#solo-weak");
  if (ub) ub.hidden = m !== "union";
  // 레벨·유니온명은 스펙 옆에 따로 서 있다(유니온 바 밖) — 모드가 직접 켜고 끈다.
  // 유니온명은 «이름이 있을 때만» 뜨므로, 켜는 판단은 renderUnionBar에 맡기고
  // 여기서는 끄기만 한다.
  const lv2 = $("#union-lv");
  if (lv2) lv2.hidden = m !== "union";
  const nameWrap = $("#union-name-wrap");
  if (nameWrap && m !== "union") nameWrap.hidden = true;
  if (sw2) sw2.hidden = m === "union";
  if (m === "union") { wireUnionHide(); renderUnionBar(); }
  // 필터 바는 DOM을 함께 쓰고 **상태만 갈린다**(curFilter). 모드가 바뀌면 지금
  // 상자의 값으로 다시 맞춰 준다 — 안 그러면 솔로에서 건 칩이 유니온 화면에
  // 그대로 떠 있어 목록과 표시가 어긋난다.
  if (!inCoop) {
    const q = $("#q");
    if (q) q.value = curFilter().q;
    buildFilters();
  }
}

/** 지금 스펙의 유니온 이름. 블라링크에서 받아 온 계정 정보에 실려 온다
 *  (`_account.union` — `scraper/profile_fetch.fetch_union`). 없으면 null. */
const unionName = () => activeRec()?.fetched?._account?.union?.name || null;

/** 유니온 상단 — 유니온명과 레벨 계기. 보스는 워크벤치의 각 줄이 들고 있다. */
function renderUnionBar() {
  const wrap = $("#union-name-wrap"), nm = $("#union-name");
  const name = unionName();
  if (wrap && nm) {
    wrap.hidden = !name;
    if (name) nm.textContent = name;
    // 가림은 **이 브라우저에만** 남는다. 스샷 찍을 때만 켜는 스위치라 계정·서버로
    // 넘길 값이 아니다.
    // **기본은 가림**이다 — 유니온명은 스샷에 딸려 나가면 곤란한 사람이 있고,
    // 한 번 새어 나간 것은 되돌릴 수 없다. 보고 싶으면 누르면 된다(그 선택은
    // 이 브라우저에만 남는다).
    const hidden = state.settings.unionNameHidden !== false;
    wrap.classList.toggle("masked", hidden);
    const eye = $("#union-hide");
    if (eye) {
      eye.textContent = hidden ? "◌" : "◉";
      eye.setAttribute("aria-pressed", String(hidden));
      eye.title = hidden ? T("유니온명 다시 보기") : T("유니온명 가리기");
    }
  }
  // 회차 고르개 — 고르면 보스 다섯의 «안에 든 것»이 통째로 바뀐다(속성 배정도).
  // 줄에 꽂아 둔 속성(weak)은 그대로 두므로, 회차만 바꾸면 «같은 자리에 이번 회차
  // 보스»가 들어온다.
  const ss = $("#union-season");
  if (ss && document.activeElement !== ss) {
    const cur = unionSeason();
    if (ss.options.length !== UNION_SEASONS.length + 1) {
      ss.textContent = "";
      // 최신 회차가 위로 — 대개 이번 것을 본다. 커스텀은 맨 아래(직접 짜는 자리다).
      for (const se of [...UNION_SEASONS].reverse()) {
        const o = el("option", null, `${se.label} · ${se.start.slice(2).replace(/-/g, ".")}`);
        o.value = String(se.id);
        ss.append(o);
      }
      // 보스를 직접 짜 넣는 화면이 아직 없다 — 목록에 자리는 잡아 두되 «준비중»으로
      // 잠가 둔다. 고를 수 있게 열어 두면 빈 판만 나와 «고장 났나»가 된다.
      const co = el("option", null, "유니온 커스텀 설정 (준비중)");
      co.value = CUSTOM_SEASON;
      co.disabled = true;
      ss.append(co);
    }
    ss.value = String(cur.id);
    ss.onchange = () => {
      // 회차 id는 숫자지만 커스텀만 문자열이다 — 무턱대고 Number()로 바꾸면 NaN이 된다
      U().season = ss.value === CUSTOM_SEASON ? CUSTOM_SEASON : Number(ss.value);
      applySeasonPicks();             // 고른 적 없는 회차면 세 줄이 빈 채로 선다
      saveAll();
      renderAll();
    };
  }

  const lv = $("#union-level");
  if (lv && document.activeElement !== lv) {
    lv.value = state.settings.unionLevel ?? "";
    const auto = unionLevel();
    lv.placeholder = auto ? String(auto) : "—";
  }
}

/** 유니온 워크벤치 — 왼쪽 보스 5(속성 고정), 오른쪽 덱 3줄.
 *  **솔로와 데이터·DOM을 공유하지 않는다** — 자기 저장소(state.union)만 읽고,
 *  자기 칸(renderUnionSlots)만 그린다. */
function renderBench() {
  const on = modeNow() === "union";
  const bench = $("#union-bench");
  if (bench) bench.hidden = !on;
  if (!on) return;

  // 보스 풀 — 속성 다섯 고정. 회차마다 안의 보스만 바뀐다.
  const pool = $("#boss-pool");
  pool.textContent = "";
  for (const code of UNION_CODES) {
    pool.append(bossCard(code, { pool: true }));
  }

  // 덱 세 줄 — [보스] + [니케 5칸]
  const rows = $("#bench-rows");
  rows.textContent = "";
  for (let i = 0; i < UNION_DECKS; i++) {
    const d = uDeck(i);
    const code = uWeak(d);
    const row = el("div", "bench-row");
    row.style.setProperty("--code-c", CODE_VAR[code] || "var(--color-stage-line)");

    const take = (c) => uSetBoss(i, c);
    row.addEventListener("dragover", (e) => {
      e.preventDefault();
      // 덱을 끌고 있으면 «여기와 바뀐다»를, 보스를 끌고 있으면 «여기 꽂힌다»를 말한다
      row.classList.add(deckDragFrom != null && deckDragFrom !== i ? "swap" : "over");
    });
    row.addEventListener("dragleave", () => row.classList.remove("over", "swap"));
    row.addEventListener("drop", (e) => {
      e.preventDefault(); row.classList.remove("over", "swap");
      const payload = e.dataTransfer.getData("text/plain");
      if (payload.startsWith("deck:")) uSwapDecks(Number(payload.slice(5)), i);
      else if (payload.startsWith("boss:")) { bossDropped = true; uSwapBoss(Number(payload.slice(5)), i); }
      else take(payload);
    });
    if (bossPick) {
      row.classList.add("armed");
      // 풀에서 보스를 «고른» 상태면 줄 아무 데나 눌러도 꽂힌다 — 끌기가 안 되는
      // 환경(터치·트랙패드)에서도 같은 일을 할 수 있어야 한다. 니케 칸을 누른
      // 경우는 그쪽 핸들러가 먼저 먹으므로 여기까지 오지 않는다.
      row.onclick = (e) => { if (!e.target.closest(".u-slot, .row-side")) take(bossPick); };
    } else {
      row.onclick = null;
    }

    const target = bossCard(code, { deckIdx: i, onTake: take });
    const cells = el("div", "bench-slots");
    renderUnionSlots(cells, i);

    // 줄 왼쪽 손잡이 — 편성을 통째로 위/아래로 옮기는 단추와, **꽉 찬 뒤에도**
    // 남아 있는 우월 속성 경고. 빈 칸 힌트는 다 채우면 사라지므로, 「5명 다 넣었는데
    // 우월이 둘뿐」인 상태를 말해 줄 자리가 따로 있어야 한다.
    // 줄 손잡이 — **끌어서** 편성을 통째로 다른 줄로 옮긴다. 보스는 줄에 남는다
    // (「이 보스는 그대로 두고 편성만 다른 줄로」가 하고 싶은 일이다).
    // 줄 손잡이 — 단추가 아니라 **왼쪽 긴 영역 전체**가 잡히는 자리다. 조준할
    // 것 없이 그 줄 옆을 잡아 끌면 편성이 통째로 따라온다. 보스는 줄에 남는다.
    // 끌 수 있는 자리는 **양옆의 빈 영역**이다. 줄 전체를 draggable로 두면 니케 한 명을
    // 집으려 해도 줄이 통째로 끌려온다(실측) — 안쪽은 저마다 할 일이 있는 자리다.
    const grabL = el("div", "row-grab");
    const grabR = el("div", "row-grab");
    const side = el("div", "row-side");
    side.title = T("{v}번 편성을 끌어 다른 줄과 맞바꿉니다 (보스는 그대로)", { v: i + 1 });
    side.draggable = true;
    const onGrabStart = (e) => {
      e.dataTransfer.setData("text/plain", `deck:${i}`);
      e.dataTransfer.effectAllowed = "move";
      // 끌고 다니는 그림은 **줄과 정확히 같은 크기의 복제본**으로 찍는다.
      // 줄 자체를 넘기면 붉은 해치처럼 카드 밖으로 뻗는 자식 때문에 스냅샷 원점이
      // 줄보다 위에서 시작해, 해치가 있는 줄만 한 칸쯤 아래로 밀려 잡혔다(실측).
      // contain: paint로는 안 잡혔다 — 넘치는 것을 아예 떼어 낸 복제본이 확실하다.
      const box = row.getBoundingClientRect();
      const shot = row.cloneNode(true);
      shot.querySelectorAll(".boss-hatch, .dust").forEach((n) => n.remove());
      shot.classList.remove("lifted", "swap", "over");
      shot.style.cssText = `position:fixed; left:-20000px; top:0; margin:0;`
        + `width:${box.width}px; height:${box.height}px; overflow:hidden;`
        + `background:var(--color-stage-2); pointer-events:none;`;
      document.body.append(shot);
      e.dataTransfer.setDragImage(shot, e.clientX - box.left, e.clientY - box.top);
      setTimeout(() => shot.remove(), 0);      // 스냅샷은 이미 찍혔다
      side.classList.add("dragging");
      // 줄 **전체가 들린다** — 손잡이만 흐려지면 「판이 움직인다」가 안 읽힌다.
      deckDragFrom = i;
      row.classList.add("lifted");
      $("#bench-rows")?.classList.add("shuffling");
    };
    const onGrabEnd = () => {
      side.classList.remove("dragging");
      deckDragFrom = null;
      row.classList.remove("lifted");
      $("#bench-rows")?.classList.remove("shuffling");
      for (const r of $("#bench-rows")?.children || []) r.classList.remove("swap");
    };
    for (const g of [grabL, grabR]) {
      g.draggable = true;
      g.title = T("{v}번 편성을 끌어 다른 줄과 맞바꿉니다 (보스는 그대로)", { v: i + 1 });
      g.addEventListener("dragstart", onGrabStart);
      g.addEventListener("dragend", onGrabEnd);
    }
    side.append(el("span", "row-grip", "⠿"));

    // 줄마다 **제 레이드 설정**과 **제 계산 버튼**을 든다. 세 줄이 서로 다른 보스를
    // 치므로 설정도 계산도 줄 단위여야 한다 — 위쪽에 공용 버튼 하나만 두면
    // 「지금 어느 줄 얘기지」가 매번 생긴다.
    const raid = el("button", "row-btn row-raid");
    raid.type = "button";
    // 톱니만 두면 무엇을 여는 버튼인지 안 읽힌다 — 글자를 함께 적는다
    raid.append(el("i", null, "⚙"), el("span", null, "레이드 설정"));
    raid.title = T("{v}번 줄의 레이드 설정 — 방어력·코어·적정거리·버스트", { v: i + 1 });
    raid.setAttribute("aria-label", T("{v}번 줄 레이드 설정", { v: i + 1 }));
    if (battleChanged(uDeck(i).battle)) raid.classList.add("has");
    raid.onclick = (e) => { e.stopPropagation(); openRowBattle(i); };
    side.append(raid);

    const calc = el("button", "row-btn row-calc");
    calc.type = "button";
    const rr = resultOf(d);
    calc.disabled = !isFull(d) || !!d.calcState;
    calc.dataset.state = d.calcState === "run" ? "loading" : "";
    // 버튼이 **줄 안에** 있으므로 몇 번 줄인지는 자리가 이미 말한다 — 글자에까지
    // 「1번 줄」을 넣으면 읽을 것만 는다. 설명이 필요한 곳은 툴팁이다.
    calc.textContent = d.calcState === "run" ? T("계산 중…") : rr ? T("재계산") : T("계산");
    calc.title = isFull(d) ? T("{v}번 줄만 계산합니다", { v: i + 1 }) : T("5명을 다 채워야 계산할 수 있습니다");
    calc.onclick = (e) => { e.stopPropagation(); calcDecks([i], true); };
    side.append(calc);

    // 그 줄만 프리셋으로. 유니온 프리셋은 보스와 레이드 설정까지 담으므로
    // 「이 보스에 이 편성」 한 줄만 따로 두고 쓰는 일이 실제로 잦다.
    const save = el("button", "row-btn row-save", "프리셋 저장");
    save.type = "button";
    save.disabled = !d.names.some(Boolean);
    save.title = T("{v}번 줄만 프리셋으로 저장합니다 (보스·레이드 설정 포함)", { v: i + 1 });
    save.onclick = (e) => {
      e.stopPropagation();
      uBattleRow = i;                 // currentPreset("single")이 이 줄을 담는다
      openPresetSave("single");
    };
    side.append(save);

    const wipe = el("button", "row-btn row-wipe", "비우기");
    wipe.type = "button";
    wipe.disabled = !d.names.some(Boolean);
    wipe.title = T("{v}번 줄의 니케를 모두 뺍니다 (보스는 그대로)", { v: i + 1 });
    wipe.onclick = (e) => {
      e.stopPropagation();
      uSnap(T("{v}번 줄 비우기", { v: i + 1 }));
      d.names = Array(SLOTS).fill(null);
      d.control = {};
      saveAll(); renderAll();
    };
    side.append(wipe);

    // 그 줄의 결과 — 계산하면 여기에 바로 뜬다. 결과 탭까지 안 가도 된다.
    const out = el("span", "row-total");
    // 숫자 앞에 **그 줄을 치는 속성**을 적는다 — 「풍압 89.98억」처럼 읽혀야
    // 어느 조건에서 나온 딜인지가 숫자와 함께 온다.
    const want = COUNTER_OF[uWeak(d)];
    if (want && !d.error) {
      const tag = el("span", "row-total-el", want);
      tag.style.setProperty("--code-c", CODE_VAR[want] || "var(--color-stage-line)");
      out.append(tag);
    }
    out.append(el("b", null,
      d.error ? T("오류") : rr ? `${I18N.dmg(rr.total)}` : isFull(d) ? T("미계산") : "—"));
    if (d.error) { out.classList.add("err"); out.title = d.error; }
    side.append(out);

    row.append(grabL, side, target, cells, grabR);
    rows.append(row);
  }
  // 세 줄 합계 — 유니온에서 실제로 궁금한 숫자는 줄별 딜이 아니라 **오늘의 총딜**이다.
  const sumVal = $("#bench-sum-val"), sumNote = $("#bench-sum-note");
  if (sumVal) {
    let sum = 0, done = 0, full = 0;
    for (let k = 0; k < UNION_DECKS; k++) {
      const dk = uDeck(k);
      if (isFull(dk)) full += 1;
      const r = resultOf(dk);
      if (r) { sum += r.total; done += 1; }
    }
    sumVal.textContent = done ? `${I18N.dmg(sum)}` : "—";
    // 다 됐을 때는 **아무 말도 안 한다.** 숫자가 곧 답이고, 옆에 «모두 계산됨»을
    // 붙여 봐야 읽을 것만 는다. 말을 거는 건 뭔가 빠졌을 때뿐이다.
    sumNote.textContent = done === UNION_DECKS ? ""
      : done ? T("{v}줄이 아직 계산 전입니다", { v: UNION_DECKS - done })
      : full ? "" : T("다섯 명씩 채우면 계산할 수 있습니다");
    sumVal.classList.toggle("partial", done > 0 && done < UNION_DECKS);
  }

  // 상단 바(유니온명·레벨)도 여기서 함께 맞춘다. 모드 전환 때만 그리면 스펙을
  // 다시 받아 온 뒤에도 옛 값이 그대로 남는다(실측: 유니온명이 안 떴다).
  wireUnionHide();
  renderUnionBar();
}

/** 보스 카드 — 랩처 그림이 주인공이다. 지금은 속성 아이콘이 자리를 지키고,
 *  나중에 회차별 랩처 아트를 `.boss-art`에 끼우면 그대로 들어간다.
 *  풀(왼쪽)은 끌 수 있고, 줄(오른쪽)에 꽂힌 것은 눌러서 다음 속성으로 돈다. */
function bossCard(code, { pool = false, deckIdx = null, onTake = null } = {}) {
  const box = el("div", "boss" + (pool ? " boss-pick" : " boss-set"));
  box.style.setProperty("--code-c", CODE_VAR[code] || "var(--color-stage-line)");
  if (!code) box.classList.add("empty");
  // 줄 번호는 안 적는다 — 세 줄이 보스 그림·속성색으로 이미 갈리고, 유니온에는
  // 「몇 번 덱」이라는 뜻이 따로 없다(출격 세 번일 뿐이다). 번호를 달면 읽을 것만 는다.
  // 우월 속성이 모자란 줄은 보스 카드에 **붉은 사선**이 그어진다. 빈 칸 힌트는
  // 다 채우면 사라지므로, 「5명 다 넣었는데 우월이 둘뿐」인 상태를 말할 자리가
  // 따로 있어야 한다 — 그 자리는 «틀린 대상»인 보스 카드다.
  if (deckIdx != null && code) {
    const cc = counterCount(uDeck(deckIdx));
    if (cc && cc.bad) {
      box.classList.add("boss-short");
      const fresh = !uShortWas.has(deckIdx);
      if (fresh) box.classList.add("wipe");                     // 이번에 새로 걸렸다
      uShortWas.add(deckIdx);
      // 줄무늬를 **막대 하나씩** 만든다. 반복 그라디언트 한 장이면 «덮개가 미끄러지는»
      // 느낌뿐이라, 줄이 저마다 그어지게 하려면 요소가 따로 있어야 한다.
      // 회전한 상자 안에 가로 막대를 쌓아 두면 화면에서는 대각선 줄이 된다.
      const hatch = el("i", "boss-hatch");
      for (let k = 0; k < HATCH_BARS; k++) {
        const bar = el("b");
        bar.style.top = `${k * HATCH_GAP}px`;
        // **한 줄씩** 그어진다 — 시차를 넉넉히 벌려 앞 줄이 거의 다 그어진 뒤
        // 다음 줄이 시작한다. 각 줄은 제 오른쪽 위 끝에서 아래로 자란다
        // (transform-origin: 100% 50%). 손으로 «////»를 긋는 순서 그대로다.
        if (fresh) bar.style.animationDelay = `${140 + k * 34}ms`;
        hatch.append(bar);
      }
      box.append(hatch);
      box.style.setProperty("--want-c", CODE_VAR[cc.want] || "var(--color-stage-line)");
      // 숫자는 카드에 안 적는다 — 세 줄에 셋이 떠 있으면 시끄럽다. 몇 명 모자란지는
      // 툴팁이 답하고, 화면은 «걸렸다/아니다»만 말한다. 툴팁 본문은 아래에서
      // 카드 설명과 함께 붙인다(여기서 title을 쓰면 그쪽이 덮어쓴다).
      box.dataset.warn = T("{code} 보스는 {want}에 약합니다 — ", { code, want: cc.want })
        + T("엉뚱한 속성이 {wrong}명입니다 ({want} {n}명, ", { wrong: cc.wrong, want: cc.want, n: cc.n })
        + T("{UNION_COUNTER_MIN}명 이상 권장)", { UNION_COUNTER_MIN });
    } else {
      uShortWas.delete(deckIdx);      // 풀렸다 — 다음에 다시 걸리면 그때 다시 긋는다
    }
  }
  const art = el("div", "boss-art");
  // 이번 회차에 그 속성으로 나오는 랩처. 회차가 바뀌면 그림도 이름도 같이 바뀐다.
  const b = code ? bossOf(code) : null;
  if (b) {
    const im = el("img", "boss-img");
    im.src = `image/boss/${b.art}.webp`;
    im.alt = "";
    im.loading = "lazy";
    art.append(im);
  } else {
    art.append(el("span", "u-plus", "+"));
  }
  box.append(art);
  // 속성 아이콘은 «무엇에 약한가»라 그림 위가 아니라 **카드 왼쪽 어깨**에 둔다 —
  // 랩처 그림을 가리지 않으면서 한눈에 갈린다.
  const f = ELEMENT_ICON[code];
  if (f) {
    const badge = el("img", "boss-code");
    badge.src = `image/icon/${f}`;
    badge.alt = code;
    badge.title = T("{code} 보스", { code });
    box.append(badge);
  }
  // 오른쪽 어깨에는 **약점 속성**을 배지로만 얹는다 — 「이 보스를 치는 속성」이
  // 편성을 짤 때 실제로 필요한 정보라, 보스 속성과 나란히 보여야 고르면서 헷갈리지
  // 않는다. 글자는 안 붙인다(카드 셋에 셋이 뜨면 시끄럽다).
  const want = COUNTER_OF[code];
  const wf = want && ELEMENT_ICON[want];
  if (wf) {
    const wb = el("img", "boss-want");
    wb.src = `image/icon/${wf}`;
    wb.alt = want;
    wb.title = T("{code} 보스는 {want}에 약합니다", { code, want });
    box.append(wb);
  }
  box.append(el("span", "boss-name", b ? b.name : (code || T("보스"))));
  if (pool) {
    box.draggable = true;
    box.title = T("{v}{code} 약점 — 덱 줄로 끌어다 놓으세요", { v: b ? T(b.name) + " · " : "", code });
    box.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", code);
      e.dataTransfer.effectAllowed = "copy";
      box.classList.add("dragging");
    });
    box.addEventListener("dragend", () => box.classList.remove("dragging"));
    box.onclick = () => { bossPick = bossPick === code ? null : code; renderBench(); };
    if (bossPick === code) box.classList.add("armed");
  } else {
    box.title = box.dataset.warn
      || T("{v}번 덱이 칠 보스 — 다른 줄로 끌면 서로 맞바꿉니다", { v: deckIdx + 1 });
    // 비우는 길 — 꽂기만 되고 뺄 수가 없었다. 니케 칸의 ✕와 같은 자리·같은 손버릇이다.
    if (code) {
      const x = el("button", "slot-x boss-x", "✕");
      x.type = "button";
      x.title = T("{v}번 줄 보스 비우기", { v: deckIdx + 1 });
      x.onclick = (e) => {
        e.stopPropagation();
        uSnap(T("{v}번 줄 보스 비우기", { v: deckIdx + 1 }));
        uDeck(deckIdx).weak = null;
        seasonPicks()[deckIdx] = null;   // 이 회차에 «안 골랐다»로 기억한다
        saveAll();
        renderBench();
      };
      box.append(x);
    }
    // 줄에 꽂힌 보스도 **끌 수 있다.** 풀에서 새로 꽂는 것과 같은 규약을 쓰되,
    // 어느 줄에서 왔는지를 함께 실어 정확히 그 줄과 맞바꾼다.
    box.draggable = true;
    box.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", `boss:${deckIdx}`);
      e.dataTransfer.effectAllowed = "move";
      box.classList.add("dragging");
      bossDropped = false;
    });
    box.addEventListener("dragend", () => {
      box.classList.remove("dragging");
      if (bossDropped) return;                  // 다른 줄에 놓였다 — 맞바꿈이 처리했다
      // 줄 밖으로 던졌다 = 비우기
      uSnap(T("{v}번 줄 보스 비우기", { v: deckIdx + 1 }));
      uDeck(deckIdx).weak = null;
      seasonPicks()[deckIdx] = null;
      saveAll();
      renderBench();
    });
    // 그냥 누르면 **아무 일도 안 일어난다.** 눌러서 속성이 한 칸씩 도는 것은
    // 「고른 적도 없는데 지멋대로 바뀐다」로 읽힌다 — 바꾸는 길은 왼쪽에서 고르거나
    // 끌어다 놓는 것, 둘뿐이어야 한다.
    box.onclick = () => { if (bossPick) onTake?.(bossPick); };
  }
  return box;
}

/** 유니온 덱 한 줄의 5칸. 솔로의 renderSlots와 **별개 함수**다 — 줄에 맞춘 크기,
 *  자기 저장소, 자기 드래그 규약을 쓴다. */
function renderUnionSlots(wrap, deckIdx) {
  const d = uDeck(deckIdx);
  wrap.textContent = "";
  d.names.forEach((name, idx) => {
    const slot = el("div", "u-slot" + (name ? " has" : ""));
    slot.dataset.udeck = String(deckIdx);
    slot.dataset.idx = String(idx);
    // 뺐든 바꿨든, 방금 손댄 자리라면 **그 칸에서** 되돌릴 수 있게 한다
    const spot = undoSpotAt(deckIdx, idx);
    if (spot) {
      slot.classList.add("has-undo");
      const back = el("button", "u-undo", "↩");
      back.type = "button";
      back.title = T("{label} — 되돌리기", { label: spot.label });
      back.onclick = (e) => { e.stopPropagation(); uUndoLast(); };
      slot.append(back);
    }
    if (name) {
      const c = card(name, { inSlot: true });
      // 유니온 칸에서는 **액자를 속성색으로** 든다. 이 화면에서 줄마다 따지는 것은
      // 등급이 아니라 «이 줄 보스에 우월한가»라서, 카드 다섯 장의 테두리만 훑어도
      // 속성이 맞는지 보여야 한다.
      const elem = byName.get(name)?.element;
      if (elem && CODE_VAR[elem]) c.style.setProperty("--frame", CODE_VAR[elem]);
      slot.append(c);
      // 칸에서 칸으로 끌어 옮긴다 — 로스터에서 끄는 것과 같은 손버릇이어야 한다
      slot.addEventListener("pointerdown",
        (e) => startDrag(e, name, { union: true, deckIdx, idx }));
      const x = el("button", "slot-x", "✕");
      x.type = "button"; x.title = T("슬롯 비우기");
      x.onclick = (e) => {
        e.stopPropagation();
        uSnap(T("{name} 빼기", { name }), { deckIdx, idx });
        d.names[idx] = null; saveAll(); renderBench();
      };
      slot.append(x);
    } else {
      // 빈 칸이 **무엇을 기다리는지** 스스로 말한다. 우월 속성이 아직 모자란 줄이면
      // 그 속성 아이콘을 옅게 깔아 둔다 — 떠 있는 경고 배지를 하나 더 얹는 대신,
      // 이미 «채워야 할 자리»인 곳이 답을 들고 있는 편이 손이 먼저 간다.
      const cc = counterCount(d);
      const f = cc && !cc.ok ? ELEMENT_ICON[cc.want] : null;
      if (f) {
        const hint = el("div", "u-want");
        hint.style.setProperty("--want-c", CODE_VAR[cc.want] || "var(--color-stage-line)");
        const im = el("img"); im.src = `image/icon/${f}`; im.alt = "";
        hint.append(im, el("span", "u-plus", "+"));
        hint.title = T("{v} 보스는 {want}에 약합니다 — ", { v: uWeak(d), want: cc.want })
          + T("{want} {UNION_COUNTER_MIN}명 이상 권장 (지금 {n}명)", { want: cc.want, UNION_COUNTER_MIN, n: cc.n });
        slot.append(hint);
      } else {
        slot.append(el("span", "u-plus", "+"));
      }
    }
    slot.onclick = () => {
      // 집어 든 카드가 있으면 그걸 놓는다. 없으면 **찾아서 꽂는 시트**를 연다 —
      // 빈 칸을 눌렀는데 아무 일도 안 일어나면 무엇을 해야 할지 알 수 없다.
      if (picked) {
        uSnap(T("{picked} 배치", { picked }));
        d.names[idx] = picked; picked = null; setStatus("");
        saveAll(); renderBench();
        slamSlot(deckIdx, idx);
        return;
      }
      if (!d.names[idx]) openPick(deckIdx, idx);
    };
    // 카드 아래 3줄 — 큐브 종류·레벨·컨트롤. 솔로와 같은 구성이어야 같은 손버릇으로
    // 쓸 수 있다. 다만 저장소는 유니온 것을 본다.
    const cell = el("div", "u-cell");
    cell.append(slot, cubeCell(d, idx));
    const more = el("button", "slot-more" + (uCtrlOpen === name ? " on" : ""));
    more.type = "button";
    if (name) {
      const on = Object.keys(d.control?.[name] || {}).length;
      more.append(el("span", null, on ? T("컨트롤 {on}", { on }) : T("컨트롤")));
      more.append(el("i", null, "▾"));
      if (on) more.classList.add("has");
      more.title = T("{name} 컨트롤 설정", { name });
      more.onclick = (e) => {
        e.stopPropagation();
        if (uCtrlOpen === name) { closeUnionCtrl(); return; }
        openUnionCtrl(name);
      };
    } else {
      more.classList.add("slot-more-gap");
      more.setAttribute("aria-hidden", "true");
      more.append(el("span", null, "컨트롤"));
    }
    cell.append(more);
    wrap.append(cell);
  });
}

// 클릭으로 고른 보스(끌기 대안). 고른 상태에서 줄을 누르면 그 줄에 들어간다.
let bossPick = null;
let uCtrlOpen = null;   // 유니온에서 컨트롤을 펼친 니케

function wireUnion() {
  const lv = $("#union-level");
  if (lv) lv.onchange = () => {
    const v = Number(lv.value);
    state.settings.unionLevel = (Number.isFinite(v) && v > 0) ? Math.round(v) : null;
    lv.value = state.settings.unionLevel ?? "";
    renderUnionBar(); saveAll(); renderAll();
  };
}

/** 계산 처리 고르개를 «지금 보이는 화면»으로 옮겨 심는다. DOM을 복제하지 않는다 —
 *  두 벌을 두면 서버/브라우저 «켜짐» 표시가 서로 어긋난다. */
function moveEngineRow(toFast) {
  const eng = $(".engine");
  const host = toFast ? $("#fast-engine") : $(".engine-row");
  if (eng && host && eng.parentElement !== host) host.append(eng);
}

/** 니케 한 명 얼굴. 순위 표에서 이름만 있으면 누가 누군지 훑기 어렵다. */
function faceOne(name) {
  const th = el("span", "cmp-art");
  const rec = byName.get(name);
  if (rec?.img) {
    const im = el("img");
    im.src = artSrc(rec, name);
    im.alt = ""; im.loading = "lazy"; im.decoding = "async"; im.draggable = false;
    th.append(im);
  }
  return th;
}

// 캐릭터 하나당 값 하나인 육성 경고 — 스킬 레벨·애장품 단계·미육성. 쉼표로 나열한
// 문장 대신 초상화 카드로 보여준다(버프 대상과 같은 결 — 유저 피드백).
const GF_GROUPS = [
  ["low_skill", T("스킬 레벨 낮음")],
  ["low_favorite", T("애장품 단계 낮음")],
  ["ungrown", T("미육성 (프로필에 없음)")],
];

/** 카드 한 칸의 아래 값 표시. 그룹마다 재는 값이 다르다. */
function gfCardValue(group, item) {
  if (group === "low_skill") {
    const lv = item.levels || {};
    return `${lv["1"] ?? "-"}/${lv["2"] ?? "-"}/${lv["3"] ?? "-"}`;
  }
  if (group === "low_favorite") return T("{stage}단계", { stage: item.stage });
  return T("미육성");
}

function renderGrowthFlags(gf) {
  const box = $("#deck-growth-flags");
  if (!box) return;
  box.textContent = "";
  if (!gf) { box.hidden = true; return; }
  const groups = GF_GROUPS
    .map(([key, label]) => [key, label, gf[key] || []])
    .filter(([, , items]) => items.length);
  if (!groups.length) { box.hidden = true; return; }
  box.hidden = false;
  for (const [key, label, items] of groups) {
    const grp = el("div", "gf-group");
    grp.append(el("span", "gf-group-label", label));
    const cards = el("div", "gf-cards");
    for (const item of items) {
      const card = el("div", "gf-card");
      card.append(faceOne(item.name));
      card.append(el("span", "gf-card-nm", item.name));
      card.append(el("span", "gf-card-v", gfCardValue(key, item)));
      cards.append(card);
    }
    grp.append(cards);
    box.append(grp);
  }
}

const OL_STAT_LABEL = {
  crit_rate: T("크리티컬 확률"), crit_dmg: T("크리티컬 대미지"), atk_pct: T("공격력"),
  atk_dmg_pct: T("공격 대미지"), charge_dmg_pct: T("차지 대미지"),
  charge_speed_pct: T("차지 속도"), max_ammo_pct: T("최대 장탄"), accuracy_pct: T("명중률"),
  charge_speed_caster_based_pct: T("차지 속도 (시전자 기준)"),
  atk_caster_based_pct: T("공격력 (시전자 기준)"), atk_flat: T("공격력(고정)"),
};

function openTopAtk(title, cases) {
  const dlg = $("#topatk-sheet");
  const body = $("#topatk-body");
  if (!dlg || !body) return;
  $("#topatk-t").textContent = title;
  body.textContent = "";

  // `textContent`라 마크다운이 글자로 나온다 — 강조는 요소로 만든다
  const low = cases.every((c) => c.kind === "low");
  const lead = el("p", "prose prose-sm", low
    ? T("「최종 공격력이 가장 «낮은» 기본 버스트 3단계 아군 N기에게」 거는 버프입니다. 대상은 ")
    : T("「자신을 제외한 최종 공격력이 가장 높은 아군 N기에게」 거는 버프입니다. 대상은 "));
  lead.append(el("b", null, "버프가 걸리는 그 순간의 최종 공격력"));
  lead.append(el("span", null,
    T("으로 정해집니다 — 소지 공격력이 아니라, 그때까지 걸린 버프(자기 버스트 자버프 포함)를")
    + T(" 다 더한 값입니다.")));
  body.append(lead);

  // 사이클에 못 붙은 것만 있으면 «왜»를 말해 준다. 「사이클 밖 · 3버 없음」만 적어 두면
  // 화면이 무슨 말을 하는지 알 수가 없다 — 실제로 이 자리에서 막혔다.
  const names0 = deckOf(state.settings.deck).names.filter(Boolean);
  const st0 = burstStages(names0);
  if (!cases.some((c) => c.cycles && c.cycles.length)) {
    const why = el("p", "share-pick-note warn");
    why.textContent = st0.ok
      ? T("이 계산에서는 풀버스트가 열리지 않아 사이클에 묶이지 않았습니다.")
      : T("{v} 버스트가 없어 **풀버스트가 열리지", { v: st0.missing.map((x) => x + T("단계")).join("·") })
        + T(" 않습니다.** 아래는 미란다 버스트만 발동한 결과이고, 풀버스트 시작에 걸리는")
        + T(" 버프(웨이크업!의 1발 크리티컬 확률)는 발동하지 않았습니다.");
    why.textContent = why.textContent.replace(/\*\*/g, "");
    body.append(why);
  }

  // 버프별로 묶는다 — 같은 버프의 사이클별 차이를 나란히 봐야 읽힌다
  const byBuff = new Map();
  for (const c of cases) {
    if (!byBuff.has(c.buff)) byBuff.set(c.buff, []);
    byBuff.get(c.buff).push(c);
  }

  for (const [buff, list] of byBuff) {
    const blk = el("div", "ta-buff");
    const h = el("div", "ta-buff-h");
    h.append(el("b", null, `${list[0].caster} 「${buff}」`));
    h.append(el("span", "ta-stat",
      `${OL_STAT_LABEL[list[0].stat] || list[0].stat || T("효과")}`
      + T(" · {v} {v1}기", { v: low ? T("하위") : T("상위"), v1: list[0].slots })));
    blk.append(h);

    for (const c of list) {
      const cs = el("div", "ta-case" + (!low && c.dealer_got === false ? " miss" : ""));
      const ch = el("div", "ta-case-h");
      ch.append(el("span", "ta-cyc",
        c.cycles.length ? T("사이클 {v}", { v: c.cycles.join("·") }) : T("풀버스트 밖")));
      if (c.dealer && low) {
        // 최저공 버프는 «그 사이클의 3버»가 받아야 하는 것이 아니다 — 3버 중 최저가
        // 받는다. 여기에 ✔/✘를 붙이면 정상 동작이 실패처럼 읽힌다.
        ch.append(el("span", "ta-dealer", T("그 사이클 3버: {dealer}", { dealer: c.dealer })));
      } else if (c.dealer) {
        ch.append(el("span", "ta-dealer", T("3버 {dealer}", { dealer: c.dealer })));
        ch.append(el("span", "ta-mark" + (c.dealer_got ? " ok" : " miss"),
          c.dealer_got ? T("✔ 3버가 받음") : T("✘ 3버가 못 받음")));
      } else {
        ch.append(el("span", "ta-dealer",
          T("풀버스트가 없어 «그 사이클의 3버»를 가릴 수 없습니다")));
      }
      cs.append(ch);

      for (const e of c.ranking) {
        const row = el("div", "ta-row" + (e.got ? " got" : ""));
        row.append(faceOne(e.name));
        row.append(el("span", "ta-nm", e.name));
        const v = el("span", "ta-atk", e.atk.toLocaleString("ko-KR"));
        v.title = T("소지 공격력 {v}", { v: e.base.toLocaleString("ko-KR") });
        row.append(v);
        if (e.got) {
          row.append(el("span", "ta-need got", "받음"));
        } else if (e.tie) {
          row.append(el("span", "ta-need tie", "동점 — 순서로 밀림"));
        } else if (e.need != null) {
          // 최저공은 «내려야» 받는다 — 부호를 뒤집어 적지 않으면 정반대로 읽힌다
          row.append(el("span", "ta-need", low
            ? T("공증 −{v}%p 내려야", { v: e.need.toFixed(1) })
            : T("공증 +{v}%p 필요", { v: e.need.toFixed(1) })));
        } else {
          row.append(el("span", "ta-need", ""));
        }
        cs.append(row);
      }
      blk.append(cs);
    }
    body.append(blk);
  }

  body.append(el("p", "prose prose-sm", low
    ? T("«공증 −N%p 내려야»는 오버로드 공격력 증가 기준입니다 —")
      + T(" (내 최종 공격력 − 커트라인) ÷ 내 소지 공격력.")
    : T("«공증 +N%p 필요»는 오버로드 공격력 증가 기준입니다 —")
      + T(" (커트라인 최종 공격력 − 내 최종 공격력) ÷ 내 소지 공격력.")));

  $("#topatk-x").onclick = () => dlg.close();
  $("#topatk-close").onclick = () => dlg.close();
  if (!dlg.open) dlg.showModal();
}

// ── 프리셋 ──────────────────────────────────────────────────────────────
// 기록(records)과 **다른 물건이다.** 기록은 «그때 그 스펙으로 계산한 결과»의 스냅샷이라
// 스펙이 바뀌면 낡는다. 프리셋은 편성과 운용만 담아 스펙과 무관하게 계속 유효하다.
// 그래서 결과(total·chars)를 **일부러 담지 않는다** — 담으면 「저장된 수치」가 지금 내
// 수치인지 매번 의심해야 한다.
//
// 두 종류를 **한 배열에** 담는다. 목록·삭제·파일 입출력이 전부 같은 코드를 타고,
// 다른 점은 `kind`와 `decks`의 길이뿐이다:
//   single — 덱 하나(5인 조합). 「이 조합」을 모아 두는 용도
//   bundle — 여러 덱을 한 이름으로. 「26년 8월 작열 솔레」처럼 그 주의 편성 전체
// 묶음은 5덱일 필요가 없다 — 저장할 때 **빈 덱은 버린다**.

const PRESET_KINDS = { single: T("단일"), bundle: T("묶음") };

/** 지금 편성에서 프리셋 한 장을 만든다.
 *
 *  **담는 것은 니케 이름뿐이다.** 컨트롤(운용)도, 조건(약점 코드·전투 시간·레이드 설정)도
 *  넣지 않는다 — 프리셋은 «이 조합»이고, 운용과 조건은 그때그때 화면에서 정하는 것이다.
 *  담아 두면 꺼낼 때마다 지금 보고 있는 설정이 조용히 갈린다. */
function currentPreset(name, kind) {
  const union = modeNow() === "union";
  // 유니온에는 «지금 고른 덱»이 없다 — 세 줄이 한 화면에 다 있다. 「덱 하나만」은
  // 마지막으로 손댄 줄(레이드 설정을 연 줄)을 뜻하게 한다.
  const cur = union ? uBattleRow : state.settings.deck;
  const idx = kind === "single"
    ? [cur]
    : [...Array(deckCountNow()).keys()].filter((i) => deckAt(i).names.some(Boolean));
  return {
    id: uid(),
    name,
    kind,
    mode: union ? "union" : "solo",
    at: new Date().toISOString(),
    // 유니온은 편성만으로는 되살릴 수 없다 — **어느 보스를 어떤 조건으로 쳤는지**가
    // 곧 그 편성의 뜻이다. 보스 속성과 그 줄의 레이드 설정을 함께 담는다.
    decks: idx.map((i) => {
      const d = deckAt(i);
      const out = { names: [...d.names] };
      if (union) {
        out.weak = d.weak || null;
        out.battle = d.battle ? JSON.parse(JSON.stringify(d.battle)) : null;
      }
      return out;
    }),
  };
}

/** 니케 얼굴 띠.
 *
 *  이름 줄만으로는 «어떤 조합인지»가 한눈에 안 들어온다 — 목록에서 고르는 자리에는
 *  얼굴이 있어야 한다. 다만 초상화는 256×512(1:2)라 그대로 넣으면 목록이 세로로
 *  길어지므로, 기록 탭이 쓰는 얼굴 크롭(`object-position: center 16%`)으로 자른다.
 *  이름은 아래에 작게 붙이고 전체 이름은 `title`에 둔다 — 38px에서는 세 글자면
 *  잘리지만, 얼굴과 함께 보면 그걸로 충분히 알아본다. */
function faceStrip(names, opts = {}) {
  const wrap = el("div", "face-strip");
  for (const n of names.slice(0, SLOTS)) {
    const cell = el("span", "face" + (n ? "" : " empty"));
    cell.title = n || T("빈 자리");
    const rec = n ? byName.get(n) : null;
    if (rec?.img) {
      const im = el("img");
      im.src = artSrc(rec, n);
      im.alt = ""; im.loading = "lazy"; im.decoding = "async"; im.draggable = false;
      cell.append(im);
    } else {
      // 로스터에 없는 니케(내 스펙 밖·미출시)는 그림이 없다 — 빈 칸으로 두지 않고 표시한다
      cell.append(el("span", "face-none", n ? "?" : ""));
    }
    if (opts.labels !== false) cell.append(el("span", "face-nm", n || ""));
    wrap.append(cell);
  }
  return wrap;
}

const presetHeads = (p) => (p.decks || []).reduce((n, d) => n + d.names.filter(Boolean).length, 0);
const presetIsSingle = (p) => (p.kind || (p.decks?.length === 1 ? "single" : "bundle")) === "single";

/** 저장 이름의 기본값.
 *
 *  묶음은 **언제·무엇을 위한 편성인지**가 이름의 전부다(「26년 8월 작열 솔레」).
 *  단일은 조합을 알아볼 수 있어야 하니 대표 니케를 쓴다. */
function autoPresetName(kind) {
  const union = modeNow() === "union";
  // 유니온은 «약점 코드» 하나로 묶이지 않는다 — 줄마다 보스가 다르다. 회차 이름이
  // 그 편성이 무엇을 위한 것인지를 가장 잘 말해 준다.
  const code = union ? unionSeason().label : (state.settings.code || T("속성없음"));
  if (kind === "bundle") {
    const d = new Date();
    const what = union ? T("유니온") : T("솔레");
    return T("{v}년 {v1}월 {code} {what}", { v: String(d.getFullYear()).slice(2), v1: d.getMonth() + 1, code, what });
  }
  const cur = union ? uBattleRow : state.settings.deck;
  const names = deckAt(cur).names.filter(Boolean);
  const head = union ? T("{v} 줄", { v: uWeak(uDeck(cur)) || code }) : code;
  if (!names.length) return T("{head} 빈 덱", { head });
  return names.length > 1 ? T("{head} · {v} 외 {v1}명", { head, v: names[0], v1: names.length - 1 }) : `${head} · ${names[0]}`;
}

// ── 저장 시트 ───────────────────────────────────────────────────────────

function openPresetSave(kind) {
  const dlg = $("#preset-save-sheet");
  const body = $("#preset-save-body");
  const go = $("#preset-save-go");
  if (!dlg || !body || !go) return;

  const cur0 = modeNow() === "union" ? uBattleRow : state.settings.deck;
  const filled = kind === "single"
    ? (deckAt(cur0).names.some(Boolean) ? [cur0] : [])
    : [...Array(deckCountNow()).keys()].filter((i) => deckAt(i).names.some(Boolean));
  if (!filled.length) {
    // **탭을 옮기지 않는다.** 저장할 게 없다는 말을 들으려고 다른 화면으로 끌려갈 이유가
    // 없다 — 사용자는 편성을 채우려고 여기 있다.
    flashStatus(kind === "single"
      ? T("지금 덱이 비어 있습니다 — 먼저 니케를 배치하세요.")
      : T("저장할 편성이 없습니다 — 먼저 니케를 배치하세요."));
    return;
  }

  $("#preset-save-t").textContent = modeNow() === "union"
    ? (kind === "single" ? T("프리셋 저장 — {v}번 줄", { v: cur0 + 1 }) : T("프리셋 묶음 저장 — 세 줄"))
    : (kind === "single" ? T("프리셋 저장 (단일)") : T("묶음 저장"));
  body.textContent = "";

  const row = el("div", "preset-name-row");
  row.append(el("span", "field-label", "이름"));
  const inp = el("input", "preset-name-in");
  inp.type = "text";
  inp.maxLength = PRESET_NAME_MAX;
  inp.autocomplete = "off";
  inp.value = autoPresetName(kind);
  inp.setAttribute("aria-label", T("프리셋 이름"));
  row.append(inp);
  body.append(row);

  // `textContent`라 마크다운이 그대로 글자로 나온다 — 강조는 요소로 만든다
  // **deckAt**이다. deckOf(솔로 덱)로 읽으면 유니온에서 저장을 열었을 때 미리보기에
  // 솔로 1~3덱이 뜬다 — 저장되는 내용(currentPreset)과 화면이 어긋난다(실측).
  const union = modeNow() === "union";
  const heads = filled.reduce((n, i) => n + deckAt(i).names.filter(Boolean).length, 0);
  const unit = union ? T("줄") : T("덱");
  const note = el("p", "prose prose-sm", T("담기는 것: {length}{unit} {heads}명 — ", { length: filled.length, unit, heads }));
  if (union) {
    note.append(el("b", null, "편성과 보스·레이드 설정"));
    note.append(el("span", null, "을 담습니다. 컨트롤·계산 결과는 담지 않습니다."));
  } else {
    note.append(el("b", null, "편성만"));
    note.append(el("span", null, " 담습니다. 컨트롤·전투 조건·계산 결과는 담지 않습니다."));
  }
  body.append(note);

  const list = el("div", "preset-lines");
  for (const i of filled) {
    const names = deckAt(i).names;
    const line = el("div", "preset-line");
    line.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
    if (union) {
      const w = uWeak(uDeck(i));
      line.append(el("span", "preset-boss", w ? (bossOf(w)?.name || w) : T("보스 없음")));
    }
    line.append(faceStrip(names));
    const n = names.filter(Boolean).length;
    if (n < SLOTS) line.append(el("span", "prof-meta", `${n}/5`));
    list.append(line);
  }
  body.append(list);

  const dup = el("p", "share-pick-note warn");
  dup.hidden = true;
  body.append(dup);
  const syncDup = () => {
    const nm = inp.value.trim();
    const hit = presetsNow().find((x) => x.name === nm);
    dup.hidden = !hit;
    if (hit) {
      dup.textContent = T("같은 이름의 {v} 프리셋이 있습니다", { v: PRESET_KINDS[hit.kind] || "" })
        + T(" — «{v}»으로 저장합니다. 덮어쓰지 않습니다.", { v: uniquePresetName(nm) });
    }
    go.disabled = !nm;
  };
  inp.oninput = syncDup;
  syncDup();

  const close = () => dlg.close();
  $("#preset-save-x").onclick = close;
  $("#preset-save-cancel").onclick = close;
  const commit = () => {
    const name = inp.value.trim().slice(0, PRESET_NAME_MAX);
    if (!name) return;
    savePreset(name, kind);
    close();
  };
  go.onclick = commit;
  inp.onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();               // 엔터가 폼을 제출해 화면이 새로 뜨지 않게
    commit();
  };
  if (!dlg.open) dlg.showModal();
  inp.focus();
  inp.select();
}

/** 같은 이름이 있으면 «이름 (1)»·«이름 (2)»로 비켜 준다 — 윈도우가 파일을 겹칠 때처럼.
 *
 *  덮어쓰기는 되돌릴 수 없다. 이름을 재활용하려는 것인지, 그냥 같은 이름이 떠오른
 *  것인지 저장 단추 하나로는 갈라낼 수 없으므로 **잃는 쪽을 고르지 않는다.**
 *  진짜로 바꿔 치우려면 프리셋 탭에서 지우고 저장하면 된다. */
function uniquePresetName(base) {
  const taken = new Set(presetsNow().map((x) => x.name));
  if (!taken.has(base)) return base;
  for (let i = 1; i < 1000; i++) {
    const cand = `${base} (${i})`;
    if (!taken.has(cand)) return cand;
  }
  return `${base} (${uid()})`;
}

function savePreset(want, kind) {
  if (presetsNow().length >= PRESET_MAX) {
    // 이건 «프리셋 탭에서 지워야» 해결되는 일이라 그쪽으로 안내한다
    presetMsg(T("프리셋은 {PRESET_MAX}개까지 저장합니다 — 쓰지 않는 것을 먼저 지우세요.", { PRESET_MAX }), "err");
    flashStatus(T("프리셋이 {PRESET_MAX}개로 찼습니다 — «프리셋» 탭에서 지우세요.", { PRESET_MAX }));
    return;
  }
  const name = uniquePresetName(want);
  const next = currentPreset(name, kind);
  presetsNow().unshift(next);
  saveAll();
  renderPresets();
  presetMsg(T("«{name}»에 저장했습니다", { name })
            + (name === want ? "" : T(" — «{want}»이 이미 있어 이름을 비켰습니다", { want }))
            + T(" — {v} · {length}덱 {v1}명.", { v: PRESET_KINDS[kind], length: next.decks.length, v1: presetHeads(next) }), "ok");
  flashStatus(T("프리셋 «{name}» 저장 — «프리셋» 탭에 있습니다.", { name }));
}

// ── 가져오기 시트 ───────────────────────────────────────────────────────
// 공유 페이지의 «전부 가져오기»와 같은 문제를 푼다: **되돌릴 수 없는 조작이라 미리
// 보여 준다.** 다른 점은 프리셋은 5덱이 아닐 수 있어서 «어느 덱으로»를 짝지어야 하는
// 것이다 — 그래서 행마다 대상 덱 고르개를 둔다.

function openPresetLoad(p, opts = {}) {
  const sink = opts.sink || presetMsg;
  const dlg = $("#preset-load-sheet");
  const body = $("#preset-load-body");
  const go = $("#preset-load-go");
  if (!dlg || !body || !go) return;

  const decks = (p.decks || []).filter((d) => d.names.some(Boolean));
  if (!decks.length) { sink(T("불러올 편성이 없습니다."), "err"); return; }

  // 기본 짝: 앞에서부터 1덱·2덱·… 단일은 «지금 보고 있는 덱»이 기본이다.
  const pick = decks.map((_, i) => (decks.length === 1 ? state.settings.deck : i));
  const on = decks.map(() => true);

  $("#preset-load-t").textContent = T("«{name}» 가져오기", { name: p.name });

  const paint = () => {
    body.textContent = "";
    body.append(el("p", "prose prose-sm",
      T("고른 덱이 내 덱을 덮습니다. 들어가는 것은 편성뿐이라 컨트롤은 «전부 자동»이 됩니다.")
      + (p.cond
        ? T(" 약점 코드·전투 시간은 이 기록의 값({v} · {duration}초)으로 되돌립니다.", { v: p.cond.code || T("속성없음"), duration: p.cond.duration })
        : T(" 약점 코드·전투 조건은 지금 화면의 값을 그대로 씁니다."))));

    const rows = el("div", "share-pairs");
    decks.forEach((d, i) => {
      // **행 전체가 누르는 자리다.** 왼쪽 체크만 반응하면 어디를 눌러야 하는지 매번
      // 겨냥해야 한다. 대상 고르개(select)만 예외로 두어 클릭이 새어 올라오지 않게 한다.
      const row = el("div", "share-pair pick" + (on[i] ? " on" : ""));
      row.setAttribute("role", "button");
      row.setAttribute("aria-pressed", String(on[i]));
      row.tabIndex = 0;
      const toggle = () => {
        on[i] = !on[i];
        if (on[i]) dedupeTargets(pick, on, i);      // 켜면서 자리가 겹칠 수 있다
        paint();
      };
      row.onclick = toggle;
      row.onkeydown = (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        toggle();
      };

      row.append(el("span", "share-pair-ck", on[i] ? "✓" : ""));
      row.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));

      const mid = el("span", "share-pair-mid");
      mid.append(faceStrip(d.names));
      const mine = (state.decks[pick[i]]?.names || []).filter(Boolean);
      mid.append(el("span", "share-pair-dst" + (on[i] ? " on" : ""),
        on[i] ? T("지금 {v}덱: {v1}", { v: pick[i] + 1, v1: mine.length ? mine.join(" · ") : T("빈 덱") })
              : T("가져오지 않습니다")));
      row.append(mid);

      const sel = el("select", "preset-target");
      for (let t = 0; t < deckCountNow(); t++) {
        const o = el("option", null, modeNow() === "union" ? T("내 {v}번 줄", { v: t + 1 }) : T("내 {v}덱", { v: t + 1 }));
        o.value = String(t);
        sel.append(o);
      }
      sel.value = String(pick[i]);
      sel.disabled = !on[i];
      sel.setAttribute("aria-label", T("{v}번 편성을 넣을 덱", { v: i + 1 }));
      sel.onchange = () => {
        pick[i] = Number(sel.value);
        dedupeTargets(pick, on, i);                 // 그 덱을 쓰던 행은 빈 자리로 밀린다
        paint();
      };
      // 고르개를 누른 것이 «행 끄기»로 읽히면 안 된다
      for (const ev of ["click", "keydown", "pointerdown"]) {
        sel.addEventListener(ev, (e) => e.stopPropagation());
      }
      row.append(sel);

      rows.append(row);
    });
    body.append(rows);

    // 미리보기 — 무엇이 비워지고 무엇이 빈 자리로 남는가
    const sel = decks.map((d, i) => ({ d, t: pick[i] })).filter((_, i) => on[i]);
    const notes = el("div", "share-sheet-notes");

    const seen = new Map();
    for (const { t } of sel) seen.set(t, (seen.get(t) || 0) + 1);
    // `dedupeTargets`가 고를 때마다 풀어 주므로 평소에는 걸리지 않는다 — 안전망이다
    const clash = [...seen].filter(([, c]) => c > 1).map(([t]) => T("{v}덱", { v: t + 1 }));
    if (clash.length) {
      notes.append(el("p", "share-pick-note warn",
        T("{v}에 두 개가 겹칩니다 — 서로 다른 덱을 고르세요.", { v: clash.join(" · ") })));
    }

    // 밀려나는 편성이 어디로 가는지 — 고른 조합에 따라 달라지므로 매번 다시 센다
    const plan = planDisplaced(sel.map(({ t }) => t));
    if (plan.shifted.length) {
      notes.append(el("p", "share-pick-note",
        T("지금 그 덱에 있는 편성은 {v}", { v: plan.shifted.map((x) => T("{v}덱→{v1}덱", { v: x.from + 1, v1: x.to + 1 })).join(" · ") })
        + T("으로 옮깁니다.")));
    }
    if (plan.lost.length) {
      notes.append(el("p", "share-pick-note warn",
        T("빈 덱이 없어 {v}의 편성은 사라집니다.", { v: plan.lost.map((t) => T("{v}덱", { v: t + 1 })).join(" · ") })));
    }

    const names = sel.flatMap(({ d }) => d.names.filter(Boolean));
    const missing = [...new Set(names.filter((n) => !haveChar(n)))];
    const want = new Set(names.filter(haveChar));
    const targets = new Set(sel.map(({ t }) => t));
    const emptied = new Map();
    for (let i = 0; i < deckCountNow(); i++) {
      if (targets.has(i)) continue;
      for (const nm of (deckAt(i)?.names || [])) {
        if (!nm || !want.has(nm)) continue;
        if (!emptied.has(i)) emptied.set(i, []);
        emptied.get(i).push(nm);
      }
    }
    if (emptied.size) {
      const where = [...emptied.entries()].sort((a, b) => a[0] - b[0])
        .map(([d, ns]) => T("{v}덱에서 {v1}", { v: d + 1, v1: briefNames([...new Set(ns)]) })).join(", ");
      notes.append(el("p", "share-pick-note warn",
        T("덱 간 중복이라 {where}{v} 비웁니다.", { where, v: eul(where) })));
    }
    if (missing.length) {
      notes.append(el("p", "share-pick-note",
        T("내 스펙에 없는 {length}명은 빈 자리로 들어갑니다 — {v}.", { length: missing.length, v: briefNames(missing) })));
    }
    if (!sel.length) notes.append(el("p", "share-pick-note warn", "가져올 덱을 하나 이상 고르세요."));
    body.append(notes);

    go.disabled = !sel.length || clash.length > 0;
    go.textContent = sel.length > 1 ? T("{length}덱 가져오기", { length: sel.length }) : T("가져오기");
  };
  paint();

  const close = () => dlg.close();
  $("#preset-load-x").onclick = close;
  $("#preset-load-cancel").onclick = close;
  go.onclick = () => {
    const entries = decks.map((d, i) => ({ names: d.names, target: pick[i],
                                          weak: d.weak || null, battle: d.battle || null }))
      .filter((_, i) => on[i]);
    if (!entries.length) return;
    close();
    const res = importMapped(entries, p.cond ? { cond: p.cond } : {});
    const kind = res.missing.length || res.moved.length || res.dup?.length ? "warn" : "ok";
    const where = entries.map((e) => T("{v}덱", { v: e.target + 1 })).join(" · ");
    sink(T("«{name}» → {where}에 {v}", { name: p.name, where, v: importReport(res) }), kind);
    flashStatus(T("«{name}» → {where}. 수치는 다시 계산해야 합니다.", { name: p.name, where }));
    document.querySelector('.tab[data-tab="deck"]')?.click();
  };
  if (!dlg.open) dlg.showModal();
}

// ── 목록 ────────────────────────────────────────────────────────────────

function renderPresets() {
  const cnt = $("#preset-count");
  if (cnt) cnt.textContent = `${presetsNow().length} / ${PRESET_MAX}`;

  const fwrap = $("#preset-filter");
  if (fwrap) {
    fwrap.textContent = "";
    const counts = {
      all: presetsNow().length,
      single: presetsNow().filter(presetIsSingle).length,
      bundle: presetsNow().filter((p) => !presetIsSingle(p)).length,
    };
    for (const [k, label] of [["all", T("전체")], ["single", T("단일")], ["bundle", T("묶음")]]) {
      const b = el("button", "chip" + (presetFilter === k ? " on" : ""), `${label} ${counts[k]}`);
      b.type = "button";
      b.onclick = () => { presetFilter = k; renderPresets(); };
      fwrap.append(b);
    }
  }

  const wrap = $("#preset-list");
  if (!wrap) return;
  wrap.textContent = "";
  const list = presetsNow().filter((p) => presetFilter === "all"
    || (presetFilter === "single") === presetIsSingle(p));
  if (!list.length) {
    wrap.append(el("p", "prose prose-sm", presetsNow().length
      ? T("이 종류에는 저장된 프리셋이 없습니다.")
      : T("저장된 프리셋이 없습니다. 편성 탭에서 «프리셋 저장»(덱 하나) 또는")
        + T(" «묶음 저장»(여러 덱)을 누르세요.")));
    return;
  }

  for (const p of list) {
    const single = presetIsSingle(p);
    const box = el("div", "prof");
    const top = el("div", "prof-top");
    top.append(el("span", "preset-kind" + (single ? " single" : " bundle"),
      single ? T("단일") : T("묶음")));
    top.append(el("b", "prof-name", p.name));
    top.append(el("span", "prof-meta",
      `${when(p.at)} · ${single ? T("{v}명", { v: presetHeads(p) }) : T("{length}덱 {v}명", { length: p.decks.length, v: presetHeads(p) })}`));

    const acts = el("div", "prof-acts");
    acts.append(mkBtn(T("불러오기"), "btn-primary", () => openPresetLoad(p)));
    acts.append(mkBtn(T("이름 변경"), "btn-ghost", () => {
      askRename(box, T("프리셋 이름"), p.name, PRESET_NAME_MAX, (v) => {
        p.name = v;
        saveAll(); renderPresets();
      });
    }));
    acts.append(mkBtn(T("내보내기"), "btn-ghost",
      () => downloadJson({ presets: [p] }, T("니케프리셋-{name}", { name: p.name }))));
    acts.append(mkBtn(T("삭제"), "btn-ghost", () => {
      askInline(box, T("«{name}» 프리셋을 지웁니다.", { name: p.name }), T("지우기"), () => {
        setPresets(presetsNow().filter((x) => x.id !== p.id));
        saveAll(); renderPresets();
        presetMsg(T("«{name}»을 지웠습니다.", { name: p.name }), "ok");
      });
    }));
    top.append(acts);
    box.append(top);

    const lines = el("div", "preset-lines");
    p.decks.forEach((d, i) => {
      if (!d.names.some(Boolean)) return;
      const line = el("div", "preset-line");
      if (!single) line.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
      line.append(faceStrip(d.names));
      lines.append(line);
    });
    box.append(lines);
    wrap.append(box);
  }
}

// ── 파일 입출력 ─────────────────────────────────────────────────────────
// 내보낸 파일은 **프리셋만** 담는다. 계정 이름·스펙 지문이 들어갈 자리가 없다
// (프리셋 자체가 편성과 조건뿐이다).

function exportAllPresets() {
  if (!presetsNow().length) { presetMsg(T("내보낼 프리셋이 없습니다."), "err"); return; }
  downloadJson({ presets: presetsNow() }, T("니케프리셋-전체-{v}개", { v: presetsNow().length }));
  presetMsg(T("{v}개를 파일로 내보냈습니다.", { v: presetsNow().length }), "ok");
}

/** 프리셋 한 건이 쓸 만한 모양인가. 파일에서 온 것은 믿지 않는다. */
function cleanPreset(x) {
  if (!x || typeof x !== "object") return null;
  const decks = Array.isArray(x.decks) ? x.decks : null;
  if (!decks || !decks.length) return null;
  const out = [];
  for (const d of decks.slice(0, DECK_COUNT)) {
    const names = Array.isArray(d?.names) ? d.names.slice(0, SLOTS)
      .map((n) => (typeof n === "string" && n.length <= 40 ? n : null)) : null;
    if (!names || !names.some(Boolean)) continue;
    while (names.length < SLOTS) names.push(null);
    out.push({ names });               // 편성만 — 컨트롤·조건은 애초에 받지 않는다
  }
  if (!out.length) return null;
  const kind = x.kind === "single" || out.length === 1 ? "single" : "bundle";
  return {
    id: uid(),
    name: String(x.name || T("가져온 프리셋")).slice(0, PRESET_NAME_MAX),
    kind,
    at: typeof x.at === "string" ? x.at : new Date().toISOString(),
    decks: kind === "single" ? out.slice(0, 1) : out,
  };
}

/** 파일에서 프리셋을 받는다. **이름이 겹치면 덮지 않고 번호를 붙인다** —
 *  남이 준 파일이 내가 쓰던 프리셋을 조용히 지우면 안 된다. */
function importPresets(arr) {
  const taken = new Set(presetsNow().map((p) => p.name));
  let added = 0, skipped = 0, full = false;
  for (const raw of arr) {
    const p = cleanPreset(raw);
    if (!p) { skipped++; continue; }
    if (presetsNow().length + added >= PRESET_MAX) { full = true; break; }
    let nm = p.name, k = 2;
    while (taken.has(nm)) nm = `${p.name} (${k++})`;
    taken.add(nm);
    presetsNow().unshift({ ...p, name: nm });
    added++;
  }
  saveAll(); renderPresets();
  const parts = [T("{added}개를 가져왔습니다.", { added })];
  if (skipped) parts.push(T("{skipped}개는 모양이 아니라 건너뜁니다.", { skipped }));
  if (full) parts.push(T("{PRESET_MAX}개가 차서 나머지는 넣지 않았습니다.", { PRESET_MAX }));
  presetMsg(parts.join(" "), skipped || full ? "warn" : "ok");
  return added;
}

async function importPresetFiles(files) {
  const all = [];
  for (const f of files) {
    try {
      const data = JSON.parse(await f.text());
      if (Array.isArray(data?.presets)) all.push(...data.presets);
      else if (Array.isArray(data)) all.push(...data);
      else if (data?.decks) all.push(data);
      else throw new Error(T("프리셋 파일이 아닙니다"));
    } catch (e) {
      presetMsg(`${f.name}: ${String(e.message || e)}`, "err");
      return;
    }
  }
  if (!all.length) { presetMsg(T("파일에 프리셋이 없습니다."), "err"); return; }
  importPresets(all);
}

// ── 기록 ────────────────────────────────────────────────────────────────

/** 이 니케를 지금 편성에 올릴 수 있나.
 *
 *  스펙이 있으면 그 스펙에 있는지, 고정 스펙이면 로스터에 있는지 본다. 기록·프리셋·
 *  공유를 불러올 때 **같은 판정**을 써야 한다 — 한 곳만 느슨하면 계산 단계에서
 *  «스킬 미파싱»으로 터진다. */
function haveChar(n) {
  if (!n) return false;
  return activeRec() ? !!charSpec(n) : byName.has(n);
}

/** 계산이 끝난 덱만 모아 기록 모양으로 만든다. 기록 저장과 공유가 함께 쓴다. */
function collectDecks() {
  const decks = [];
  let total = 0;
  for (let i = 0; i < deckCountNow(); i++) {
    const d = deckAt(i);
    const r = resultOf(d);
    if (!r) continue;
    const one = { names: [...d.names], total: r.total, chars: r.chars,
                  detail: r.detail || null, notes: r.notes || "" };
    // 유니온은 «어느 보스에 이 편성»까지가 한 벌이다
    if (modeNow() === "union" && uWeak(d)) one.weak = uWeak(d);
    decks.push(one);
    total += r.total;
  }
  return { decks, total, mode: modeNow() };
}

/* ── 캡처에서 솔레 기록 만들기 ─────────────────────────────────────────────
 * 스쿼드 목록 캡처를 넣으면 25칸의 니케를 알아내 기록으로 남긴다.
 * 자르기는 브라우저(`squadshot.js`), 판독은 서버(`web/squad_ocr.py`)가 한다 —
 * 대조군 서명표를 내보내지 않기 위해서고, 덕분에 캡처 원본도 서버에 안 올라간다.
 *
 * **자동판독을 100% 믿게 만들지 않는다.** 실측 74/75인데, 틀린 한 칸도 후보
 * 안에는 있었다(75/75). 그래서 칸마다 후보를 보여 주고 고칠 수 있게 한다.
 */
let shotState = null;              // {cells, boxes, shot, align, rows, cols, locked}
let shotBusy = false;

function shotMsg(text, kind) {
  const n = $("#shot-msg");
  if (!n) return;
  n.textContent = text || "";
  n.className = "acct-msg" + (kind ? " " + kind : "");
}

/** 캡처 상자를 연다·닫는다. `want`를 주면 그 상태로 맞춘다(지름길이 쓴다). */
function shotToggleDrop(want) {
  const drop = $("#shot-drop"), btn = $("#shot-open");
  if (!drop) return;
  if (!HEALTH.ocr) {
    recMsg(T("캡처 판독은 서버가 필요합니다 — 지금 서버에 연결할 수 없습니다."), "err");
    return;
  }
  drop.hidden = want == null ? !drop.hidden : !want;
  btn?.classList.toggle("on", !drop.hidden);
  btn?.setAttribute("aria-expanded", String(!drop.hidden));
  if (!drop.hidden) {
    shotMsg("");
    drop.scrollIntoView({ block: "center", behavior: "smooth" });
    drop.focus({ preventScroll: true });
  }
}

function shotWire() {
  const drop = $("#shot-drop");
  if (!drop) return;
  $("#shot-open").onclick = () => shotToggleDrop();
  $("#shot-pick").onclick = () => $("#shot-file").click();
  $("#shot-guide").onclick = () => $("#shot-guide-sheet").showModal();
  $("#shot-guide-x").onclick = () => $("#shot-guide-sheet").close();
  $("#shot-file").onchange = (e) => {
    const f = e.target.files?.[0];
    if (f) shotHandle(f);
    e.target.value = "";
  };
  for (const ev of ["dragenter", "dragover"]) {
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) {
    drop.addEventListener(ev, () => drop.classList.remove("over"));
  }
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("image/"));
    if (f) shotHandle(f);
    else shotMsg(T("그림 파일이 아닙니다."), "err");
  });
  // 붙여넣기는 **상자에 초점이 있을 때만** 받는다 — 다른 입력칸에 붙여넣는 것을
  // 가로채면 안 된다.
  document.addEventListener("paste", (e) => {
    if (drop.hidden || !drop.contains(document.activeElement) && document.activeElement !== drop) return;
    const it = [...(e.clipboardData?.items || [])].find((x) => x.type.startsWith("image/"));
    if (!it) return;
    e.preventDefault();
    shotHandle(it.getAsFile());
  });
  $("#shot-x").onclick = () => $("#shot-sheet").close();
  $("#shot-find-x").onclick = () => $("#shot-find-sheet").close();
  $("#shot-find-sheet").addEventListener("close", () => { shotFindAt = -1; });
  $("#shot-save").onclick = shotSave;
}

async function shotHandle(file) {
  if (shotBusy) return;
  shotBusy = true;
  shotMsg(T("판독하는 중…"));
  try {
    const st = await shotRead(file, {});
    st.locked = {};
    shotState = st;
    shotRender();
    $("#shot-sheet").showModal();
    shotMsg("");
  } catch (e) {
    shotMsg(String(e.message || e), "err");
  } finally {
    shotBusy = false;
  }
}

/** 점수를 사람 말로. «3.2σ»는 우리끼리 쓰는 값이지 사용자에게 보일 것이 아니다.
 *
 *  1등의 등급은 **칸 뱃지와 같은 기준**을 써야 한다 — 뱃지는 «애매»인데 목록은
 *  «확정»이라고 하면 어느 쪽을 믿어야 할지 알 수 없다. 그래서 1등은 2등과의
 *  거리(`sure`)로 정하고, 나머지는 1등과의 거리로 정한다.
 */
function shotGrade(score, best, sure) {
  const gap = best - score;
  if (gap <= 0.001) return sure ? T("확정") : T("유력");
  if (gap < 0.6) return T("비슷");
  if (gap < 1.5) return T("가능");
  return T("낮음");
}

/** **확정된** 이름만 다른 칸의 후보에서 뺀다.
 *
 *  애매한 칸이 쥐고 있는 이름까지 빼면 안 된다 — 그 칸이 틀렸을 수도 있는데,
 *  정답을 아는 다른 칸의 목록에서 그 이름이 통째로 사라진다(실측: 라피 : 레드 후드가
 *  옆 칸에 «유력»으로 잡히는 바람에, 정답인 칸의 후보에서 없어졌다).
 *  같은 니케가 두 칸에 들어가는 건 **저장할 때** 막는다. */
function shotOthers(i) {
  const st = shotState;
  const used = new Set();
  st.cells.forEach((c, k) => {
    if (k === i || !c.pick) return;
    if (c.sure || st.locked[k]) used.add(c.pick);
  });
  return used;
}

/** 지금 어느 칸이든 쓰고 있는 이름 — 검색 모달에서 «다른 칸에 있음»을 알려 줄 때 쓴다.
 *  후보를 **빼는** 데는 쓰지 않는다. */
function shotTaken(i) {
  const used = new Set();
  shotState.cells.forEach((c, k) => { if (k !== i && c.pick) used.add(c.pick); });
  return used;
}

function shotRender() {
  const wrap = $("#shot-grid");
  wrap.textContent = "";
  const st = shotState;
  const nc = st.cols;
  const weak = st.cells.filter((c) => !c.sure).length;
  $("#shot-summary").classList.remove("warn");
  $("#shot-summary").textContent =
    T("{rows}개 스쿼드 × {nc}명을 읽었습니다. ", { rows: st.rows, nc })
    + (weak ? T("{weak}칸이 «애매»입니다 — 눌러서 후보 중에 고르세요.", { weak })
            : T("모두 «확정»으로 읽혔습니다. 그래도 한 번 훑어봐 주세요."));
  st.cells.forEach((c, i) => {
    if (i % nc === 0) {
      // 스쿼드 머리글에 **총딜 입력칸**을 함께 둔다. 판독값이 채워져 있고 고칠 수
      // 있다 — 숫자 판독은 90%라 사람이 한 칸 고치는 길이 반드시 있어야 한다.
      const r = Math.floor(i / nc);
      const hd = el("div", "shot-row-hd");
      hd.append(el("b", null, `SQUAD ${r + 1}`));
      const inp = el("input", "shot-power");
      inp.type = "text";
      inp.inputMode = "numeric";
      inp.placeholder = T("총딜 (숫자만)");
      const v = st.powers?.[r];
      inp.value = v ? String(v) : "";        // 쉼표를 넣지 않는다 — 고칠 때 걸린다
      // 판독이 흔들린 줄은 표시해 둔다. 얼굴 딱지와 달리 이 신호는 실측으로
      // 오답을 정확히 집어낸다(오답 2/2, 헛표시 0).
      const sureP = st.powerSure?.[r];
      if (v && sureP === false) inp.classList.add("shaky");
      if (!v) inp.classList.add("weak");
      inp.oninput = () => {
        const n = Number(String(inp.value).replace(/[^0-9]/g, ""));
        st.powers = st.powers || [];
        st.powers[r] = n || 0;
        inp.classList.toggle("weak", !n);
      };
      hd.append(inp);
      // 억 단위로도 읽어 준다 — 55억인지 550억인지 자릿수를 눈으로 세지 않게
      const eokEl = el("span", "shot-eok", v ? `${I18N.dmg(v)}` : "");
      hd.append(eokEl);
      inp.addEventListener("input", () => {
        const n = Number(String(inp.value).replace(/[^0-9]/g, ""));
        eokEl.textContent = n ? `${I18N.dmg(n)}` : "";
      });
      // 판독한 숫자 그림을 그대로 보여 준다 — 읽은 값과 눈으로 대조해야 고칠 수 있다
      const th = st.powerThumbs?.[r];
      if (th) {
        const im = el("img", "shot-power-img");
        im.src = th;
        im.alt = "";
        im.title = T("판독한 숫자 영역");
        hd.append(im);
      }
      wrap.append(hd);
    }
    const cell = el("div", "shot-cell" + (c.sure ? "" : " weak")
      + (st.locked[i] ? " locked" : ""));
    const img = el("img", "shot-thumb");
    img.src = shotThumb(st, i, 64);
    img.alt = "";
    cell.append(img);
    const tag = st.locked[i] ? T("내가 고침") : (c.sure ? T("확정") : T("애매"));
    cell.append(el("span", "shot-tag" + (st.locked[i] ? " fixed" : c.sure ? " sure" : " weak"),
                   tag));
    const used = shotOthers(i);
    const opts = [];
    const best = c.candidates.length ? c.candidates[0].score : 0;
    for (const cand of c.candidates) {
      if (used.has(cand.name) && cand.name !== c.pick) continue;   // 확정된 것만 뺀다
      opts.push([cand.name, `${cand.name} · ${shotGrade(cand.score, best, c.sure)}`]);
    }
    if (!opts.some(([v]) => v === c.pick)) opts.unshift([c.pick, c.pick]);
    const sel = selectEl(opts, c.pick, (v) => shotFix(i, v));
    sel.className = "shot-pick";
    cell.append(sel);
    // 후보는 «닮은 순 몇 개»다. 크게 빗나가면 정답이 목록에 아예 없고, 전역 배정이
    // 한 니케를 한 번만 쓰므로 다른 칸이 가져간 이름도 빠진다. 그때 손으로 못 넣으면
    // 기록을 통째로 버려야 한다 — 그래서 **전체 명단 검색**을 붙인다.
    // 확정 칸에는 안 붙인다(대부분 맞다). 애매하거나 이미 고친 칸에만.
    if (!c.sure || st.locked[i]) {
      const fb = el("button", "btn btn-ghost shot-find-btn", "이름으로 찾기");
      fb.type = "button";
      fb.onclick = () => shotFindOpen(i);
      cell.append(fb);
    }
    wrap.append(cell);
  });
}

/** 어느 칸을 고치는 중인가. 검색 모달이 닫히면 -1로 돌아간다. */
let shotFindAt = -1;

/** 이름 대조용 — 공백·쉼표·괄호·콜론을 지운다. «라피:레드 후드»를 «라피레드후드»로
 *  두면 «레드후드»·«라피」 어느 쪽으로 쳐도 걸린다. */
const shotNorm = (t) => String(t).toLowerCase().replace(/[\s:·,()\[\]{}–—-]/g, "");

/** 후보에 아예 없는 니케를 **전체 명단에서 찾아** 넣는다. */
function shotFindOpen(i) {
  shotFindAt = i;
  const dlg = $("#shot-find-sheet");
  const q = $("#shot-find-q");
  const st = shotState;
  const r = Math.floor(i / st.cols) + 1, c = (i % st.cols) + 1;
  $("#shot-find-note").textContent =
    T("SQUAD {r}의 {c}번째 칸 — 지금은 «{v}»입니다.", { r, c, v: st.cells[i].pick || T("없음") })
    + T(" 이미 다른 칸이 쓰는 이름을 고르면 그쪽이 다시 배정됩니다.");
  q.value = "";
  q.oninput = shotFindRender;
  shotFindRender();
  if (!dlg.open) dlg.showModal();
  q.focus();
}

function shotFindRender() {
  const box = $("#shot-find-list");
  box.textContent = "";
  const raw = $("#shot-find-q").value.trim();
  const key = shotNorm(raw);
  const used = shotFindAt >= 0 ? shotTaken(shotFindAt) : new Set();
  const list = ROSTER.filter((r) => !key || shotNorm(r.name).includes(key))
    .sort((x, y) => x.name.localeCompare(y.name, "ko"));
  if (!list.length) {
    box.append(el("p", "share-pick-note warn", "그런 이름이 없습니다."));
    return;
  }
  for (const rec of list.slice(0, 200)) {
    const b = el("button", "shot-find-item" + (used.has(rec.name) ? " used" : ""));
    b.type = "button";
    if (rec.img) {
      const im = el("img");
      im.src = artSrc(rec, rec.name);
      im.alt = "";
      im.loading = "lazy";
      im.decoding = "async";
      im.draggable = false;
      b.append(im);
    }
    b.append(el("span", "shot-find-nm", rec.name));
    if (used.has(rec.name)) b.append(el("i", "shot-find-used", "다른 칸에 있음"));
    b.onclick = () => {
      const at = shotFindAt;
      $("#shot-find-sheet").close();
      if (at >= 0) shotFix(at, rec.name);
    };
    box.append(b);
  }
  if (list.length > 200) {
    box.append(el("p", "share-pick-note", T("{length}명 중 200명만 보입니다 — 더 치세요.", { length: list.length })));
  }
}

async function shotFix(i, name) {
  const st = shotState;
  st.locked[i] = name;
  shotMsg("");
  $("#shot-summary").textContent = T("다시 배정하는 중…");
  try {
    st.cells = await shotRelock(st, st.locked);
    shotRender();
  } catch (e) {
    $("#shot-summary").textContent = String(e.message || e);
  }
}

function shotSave() {
  const st = shotState;
  if (!st) return;
  const nc = st.cols;
  // 같은 니케가 두 칸에 들어간 기록은 **저장하지 않는다.** 솔로레이드에서 한 니케는
  // 한 덱에만 들어가므로 중복은 반드시 오답이다. 검색으로 아무나 넣을 수 있게 된
  // 이상, 저장 문턱에서 막지 않으면 틀린 기록이 그대로 남는다.
  {
    const seen = new Map();
    const dup = [];
    st.cells.forEach((c, i) => {
      if (!c.pick) return;
      if (seen.has(c.pick)) dup.push([c.pick, seen.get(c.pick), i]);
      else seen.set(c.pick, i);
    });
    const empty = st.cells.filter((c) => !c.pick).length;
    if (dup.length || empty) {
      const at = (i) => `SQUAD ${Math.floor(i / nc) + 1}-${(i % nc) + 1}`;
      const sm = $("#shot-summary");
      sm.textContent =
        (dup.length
          ? T("저장하지 않았습니다 — 같은 니케가 두 칸에 있습니다: {v}.", { v: dup.map(([n, a, b]) =>
              `${n} (${at(a)} · ${at(b)})`).join(", ") })
          : T("저장하지 않았습니다 —"))
        + (empty ? T(" 그리고 {empty}칸이 비어 있습니다.", { empty }) : "")
        + T(" 고친 뒤 다시 저장하세요.");
      sm.classList.add("warn");
      return;
    }
  }
  const decks = [];
  let total = 0;
  for (let r = 0; r < st.rows; r++) {
    const names = [];
    for (let c = 0; c < nc; c++) names.push(st.cells[r * nc + c].pick || "");
    const dmg = Number(st.powers?.[r]) || 0;
    total += dmg;
    // `names`가 없으면 «편성 불러오기»와 공유가 동작하지 않는다 — 그쪽은 이름만 본다.
    // `chars`(니케별 딜)는 **캡처에서 알 수 없다.** 빈 dict로 둔다 —
    // `{이름: {}}`처럼 채우면 렌더러가 값을 숫자로 여겨 NaN이 뜬다.
    decks.push({ names, total: dmg, chars: {}, detail: null, notes: "" });
  }
  const rec = {
    id: uid(),
    at: new Date().toISOString(),
    kind: "solo-shot",
    label: T("솔레 기록 · {length}덱{v}", { length: decks.length, v: total ? ` · ${I18N.dmg(total)}` : "" }),
    name: ($("#shot-name").value || "").trim() || T("솔레 기록 {v}", { v: when(new Date().toISOString()) }),
    code: state.settings.code, duration: durationNow(),
    profileName: T("캡처 판독"), profileSig: "",
    engine: engine(), decks, total,
  };
  recordsNow().unshift(rec);
  setRecords(recordsNow().slice(0, 200));
  saveAll();
  renderRecords();
  $("#shot-sheet").close();
  $("#shot-drop").hidden = true;
  recMsg(T("캡처에서 {length}덱을 읽어 기록에 저장했습니다.", { length: decks.length }), "ok");
}

function saveRecord() {
  const { decks, total, mode } = collectDecks();
  if (!decks.length) { recMsg(T("저장할 계산 결과가 없습니다 — 먼저 계산하세요."), "err"); return; }
  const p = activeRec();
  const union = mode === "union";
  const rec = {
    id: uid(),
    at: new Date().toISOString(),
    // 유니온 기록은 **모드를 달고 다닌다** — 이미지로 뽑을 때 세로 한 줄로 그릴지가
    // 여기서 갈린다. 솔로 기록에는 이 열쇠가 없다(예전 기록도 그대로 산다).
    ...(union ? { mode: "union" } : {}),
    label: union
      ? T("{v} 유니온 · {length}줄 · {v1}", { v: unionSeason().label, length: decks.length, v1: I18N.dmg(total) })
      : T("{v} · {length}덱 · {v1}", { v: state.settings.code || T("속성없음"), length: decks.length, v1: I18N.dmg(total) }),
    code: state.settings.code, duration: durationNow(),
    profileName: p ? p.name : T("고정 스펙"), profileSig: profSig(),
    engine: engine(), decks, total,
  };
  recordsNow().unshift(rec);
  setRecords(recordsNow().slice(0, 200));
  saveAll();
  renderRecords();
  recMsg(T("기록에 저장했습니다 — {label}", { label: rec.label }), "ok");
  // 이 저장 단추는 **결과 탭**에 있는데, 방금 그 메시지는 **기록 탭 안** 요소라
  // 결과 탭에 남아 있으면 안 보인다 — 그래서 확인 겸 지름길을 모달로 띄운다.
  $("#rec-saved-msg").textContent = T("«{label}»을(를) 기록에 저장했습니다.", { label: rec.label });
  const dlg = $("#rec-saved-sheet");
  if (dlg && !dlg.open) dlg.showModal();
}

/** ISO 시각 → 사람이 읽는 표기. 오늘·어제는 시각만, 그 전은 날짜까지.
 *  `2026-08-21T06:17:56+09:00`처럼 기계가 남긴 문자열을 그대로 보여 줄 자리가 아니다. */
function when(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff === 0) return T("오늘 {t}", { t: hm });
  if (diff === 1) return T("어제 {t}", { t: hm });
  const sameYear = d.getFullYear() === new Date().getFullYear();
  if (I18N.lang === "en") {
    const md = d.toLocaleDateString("en-US", { month: "short", day: "numeric",
                                               ...(sameYear ? {} : { year: "numeric" }) });
    return `${md} ${hm}`;
  }
  const y = sameYear ? "" : T("{y}년 ", { y: d.getFullYear() });
  return `${y}${T("{m}월 {d}일", { m: d.getMonth() + 1, d: d.getDate() })} ${hm}`;
}

/** 안내 문구를 **부르는 쪽이 지정한 자리**에 쓴다. 탭마다 문구 자리가 따로 있어서,
 *  한 자리에만 쓰면 다른 탭에서 누른 결과가 안 보이는 곳에 뜬다. */
function msgAt(sel, msg, kind = "") {
  const n = $(sel);
  if (!n) return;
  n.textContent = msg;
  n.className = "acct-msg " + kind;
}
const recMsg = (msg, kind = "") => msgAt("#rec-msg", msg, kind);
const presetMsg = (msg, kind = "") => msgAt("#preset-msg", msg, kind);
const shareMsg = (msg, kind = "") => msgAt("#share-msg", msg, kind);

// 기록 종류. «시뮬»은 결과 탭에서 저장한 계산 스냅샷이고, «솔레»는 캡처에서 읽은
// 실제 기록이다. 수치의 출처가 달라서 같은 목록에 섞이면 헷갈린다.
const REC_KINDS = [["all", T("전체")], ["sim", T("시뮬 기록")], ["shot", T("솔레 기록")]];
let recKind = "all";
const recKindOf = (r) => (r.kind === "solo-shot" ? "shot" : "sim");

function renderRecKinds() {
  const bar = $("#rec-kinds");
  if (!bar) return;
  bar.textContent = "";
  const n = { all: recordsNow().length, sim: 0, shot: 0 };
  for (const r of recordsNow()) n[recKindOf(r)]++;
  for (const [key, label] of REC_KINDS) {
    const b = mkBtn(`${label} ${n[key]}`, "rec-kind" + (recKind === key ? " on" : ""),
      () => { recKind = key; renderRecords(); });
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(recKind === key));
    bar.append(b);
  }
}

function renderRecords() {
  const wrap = $("#rec-list");
  if (!wrap) return;
  renderRecKinds();
  wrap.textContent = "";
  const shown = recordsNow().filter((r) => recKind === "all" || recKindOf(r) === recKind);
  if (recordsNow().length && !shown.length) {
    wrap.append(el("p", "prose prose-sm",
      recKind === "shot"
        ? T("솔레 기록이 없습니다. 위 «캡처에서 솔레 기록 만들기»로 스쿼드 화면을 넣어 보세요.")
        : T("시뮬 기록이 없습니다. 결과 탭에서 «기록에 저장»을 누르세요.")));
    return;
  }
  if (!recordsNow().length) {
    const patWarn = el("p", "ctrl-pat-warn");
  patWarn.hidden = true;
  wrap.append(patWarn);
  wrap.append(el("p", "prose prose-sm",
      T("아직 기록이 없습니다. 결과 탭에서 «기록에 저장»을 누르세요.")));
    return;
  }
  for (const r of shown) {
    const box = el("div", "prof");
    const top = el("div", "prof-top");
    const shot = recKindOf(r) === "shot";
    top.append(el("span", "rec-badge" + (shot ? " shot" : ""), shot ? T("솔레") : T("시뮬")));
    top.append(el("b", "prof-name", r.name || r.label));
    top.append(el("span", "prof-meta", shot
      ? T("{v} · 캡처 판독 · {length}덱", { v: when(r.at), length: r.decks.length })
      : T("{v} · {duration}초 · {profileName}", { v: when(r.at), duration: r.duration, profileName: r.profileName })
        + ` · ${r.engine === "server" ? T("서버") : T("브라우저")}`));
    const acts = el("div", "prof-acts");
    acts.append(mkBtn(T("편성 불러오기"), "btn-primary", () => loadRecord(r)));
    // 공유는 **서버가 받아 줄 때만** 보인다 — 눌러 놓고 실패를 알려 주는 버튼은 두지 않는다
    const sout = el("div", "share-out");
    sout.hidden = true;
    if (HEALTH.share) {
      acts.append(mkBtn(T("공유 링크"), "btn-ghost", () => makeShare(r, sout, recMsg)));
    }
    acts.append(mkBtn(T("이미지 저장"), "btn-ghost", () => imageRecord(r)));
    acts.append(mkBtn(T("이미지 복사"), "btn-ghost", () => copyImageRecord(r)));
    acts.append(mkBtn(T("내보내기"), "btn-ghost",
      () => downloadJson(r, T("니케기록-{v}", { v: r.name || r.label }))));
    acts.append(mkBtn(T("삭제"), "btn-ghost", () => {
      askInline(box, T("«{v}» 기록을 지웁니다.", { v: r.name || r.label }), T("지우기"), () => {
        setRecords(recordsNow().filter((x) => x.id !== r.id));
        saveAll(); renderRecords();
        recMsg(T("기록을 지웠습니다."), "ok");
      });
    }));
    top.append(acts);
    box.append(top);
    box.append(sout);

    const det = el("details", "prof-names");
    det.append(el("summary", null, T("{length}덱 상세 보기", { length: r.decks.length })));
    det.append(recDetail(r));
    box.append(det);
    wrap.append(box);
  }
}

/** 딜 타임라인 — **확인용**이지 저장되는 값이 아니다(호출부 주석 참고).
 *  구간(버킷)마다 니케별 딜을 쌓아 올린 막대이고, 색은 도넛·막대·표와 **같은
 *  deckColor**를 그대로 쓴다 — 같은 니케는 이 화면 어디서 봐도 같은 색이어야 한다.
 *  풀버스트로 열린 구간은 옅은 띠로 배경에 깔고, 그 사이클을 연 순간에 세모 표를
 *  하나씩 찍는다.
 *
 *  **1·2·3버를 따로 찍지 않는다.** 실측: 셋이 몇 분의 1초 간격으로 몰려 있어서
 *  180초 축에 그대로 찍으면 겹쳐 뭉갠다(버그 리포트로 확인됨). 그래서
 *  `burst_cycles()`(계산기 쪽)가 풀버스트 하나당 1·2·3버를 미리 묶어 주고,
 *  여기서는 사이클당 세모 하나 + 툴팁에 단계별 발동자를 담는다. 풀버스트로
 *  안 이어진 버스트(`strays`)도 조용히 버리지 않고 작고 옅은 표로 남긴다. */
function timelineEl(names, timeline, burstCycles, duration) {
  const det = el("details", "rec-timeline");
  const sum = el("summary", null, "딜 타임라인");
  sum.append(el("span", "rec-timeline-hint", "확인용 · 저장 안 됨"));
  det.append(sum);

  const wrap = el("div", "tl-wrap");
  const order = (names || []).filter(Boolean);

  // 범례 — deckColor와 같은 색의 점 + 이름. 막대 색만으로 「누구인지」를 추측하게
  // 두지 않는다.
  const legend = el("div", "tl-legend");
  for (const nm of order) {
    const item = el("span", "tl-legend-item");
    const dot = el("i", "tl-legend-dot");
    dot.style.background = deckColor(names, nm);
    item.append(dot, nm);
    legend.append(item);
  }
  wrap.append(legend);

  const { bucket_sec, buckets } = timeline;
  const totals = buckets.map((b) => Object.values(b).reduce((s, v) => s + v, 0));
  const maxTotal = Math.max(1, ...totals);
  const cycles = burstCycles?.cycles || [];
  const strays = burstCycles?.strays || [];

  const plot = el("div", "tl-plot");

  // 배경 — 풀버스트로 열려 있던 구간. 막대보다 먼저 붙여 뒤에 깔리게 한다.
  for (const c of cycles) {
    const band = el("div", "tl-fb");
    band.style.left = `${clamp01(c.start / duration) * 100}%`;
    band.style.width = `${Math.max(clamp01((c.end - c.start) / duration) * 100, 0.6)}%`;
    plot.append(band);
  }

  // 막대 — 구간마다 니케별 딜을 **배치 순서로** 쌓는다(순서가 딜 크기로 매번
  // 바뀌면 «이 니케는 늘 이 자리»라는 감을 못 잡는다).
  const bars = el("div", "tl-bars");
  buckets.forEach((b, i) => {
    const col = el("div", "tl-col");
    const t0 = i * bucket_sec, t1 = Math.min(duration, (i + 1) * bucket_sec);
    const lines = [`${t0.toFixed(0)}~${t1.toFixed(0)}s`];
    for (const nm of order) {
      const v = b[nm];
      if (!v) continue;
      const seg = el("div", "tl-seg");
      seg.style.height = `${Math.max((v / maxTotal) * 100, 1.2)}%`;
      seg.style.background = deckColor(names, nm);
      col.append(seg);
      lines.push(`${T(nm)} ${I18N.dmg(v)}`);
    }
    col.title = lines.length > 1 ? lines.join("\n") : T("{v} — 딜 없음", { v: lines[0] });
    bars.append(col);
  });
  plot.append(bars);

  // 사이클 표 — 풀버스트를 연 순간 하나에 세모 하나.
  for (const c of cycles) {
    const mark = el("div", "tl-mark");
    mark.style.left = `${clamp01(c.start / duration) * 100}%`;
    const parts = ["1", "2", "3"].map((s) => c.casts[s]
      ? T("{s}버 {v}s · {v1}", { s, v: c.casts[s].t.toFixed(1), v1: T(c.casts[s].name) }) : T("{s}버 — 없음", { s }));
    mark.title = [T("풀버스트 {v}~{v1}s", { v: c.start.toFixed(1), v1: c.end.toFixed(1) }), ...parts].join("\n");
    plot.append(mark);
  }
  // 못 이어진 버스트 — 작고 옅게. 사라뜨리면 «왜 이 캐릭터가 버스트를 안 쓴 것처럼
  // 보이지»가 풀리지 않는다.
  for (const s of strays) {
    const mark = el("div", "tl-mark tl-mark-stray");
    mark.style.left = `${clamp01(s.t / duration) * 100}%`;
    mark.title = T("{v}s · {stage}버 · {name} — 풀버스트로 안 이어짐", { v: s.t.toFixed(1), stage: s.stage, name: s.name });
    plot.append(mark);
  }
  wrap.append(plot);

  const axis = el("div", "tl-axis");
  axis.append(el("span", null, "0s"));
  axis.append(el("span", null, `${Math.round(duration / 2)}s`));
  axis.append(el("span", null, `${Math.round(duration)}s`));
  wrap.append(axis);

  wrap.append(el("p", "tl-note",
    T("▲ 풀버스트가 열린 순간 (음영 = 지속 구간) · 옅은 세모 = 풀버스트로 못 이어진 버스트")));

  det.append(wrap);
  return det;
}
const clamp01 = (v) => Math.max(0, Math.min(1, v));

/** 기록 한 건의 상세 — 인게임 «전투 기록»처럼 덱마다 니케 5명의 딜을 세로로 세운다.
 *  덱 총딜(5명 전체딜)과 전체 합계를 함께 적는다 — 기여도만 보이면 «얼마나 셌나»를 놓친다. */
/** 덱별 상세. 결과 탭·기록 탭·공유 페이지가 **모두 이 렌더러 하나만** 쓴다 —
 *  같은 것을 두 곳에서 그리면 어느 쪽이 맞는지 매번 확인해야 한다.
 *
 *  `opts.deckAction(i, blk)`이 있으면 덱 블록마다 불러 준다. 공유 페이지가
 *  «이 덱 가져오기»를 그 자리에 얹는 데 쓴다. */
function recDetail(r, opts = {}) {
  const box = el("div", "rec-decks");
  r.decks.forEach((d, i) => {
    const blk = el("div", "rec-deck");

    const head = el("div", "rec-deck-h");
    head.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
    head.append(el("span", "rec-deck-sub", "5명 전체딜"));
    const tot = el("b", "rec-deck-total", `${I18N.dmg(d.total)}`);
    tot.title = Math.round(d.total).toLocaleString("ko-KR");
    head.append(tot);
    blk.append(head);

    // 전체딜 100%를 **누가 얼마나 채웠는지**. 이 자리가 답할 질문은 «누가 지배하는가»
    // 하나뿐이고, «20%와 14% 중 뭐가 큰가»는 아래 개별 막대가 정확히 답한다.
    // 그래서 띠 대신 도넛이다 — 행 옆에 세우면 세로를 더 먹지도 않는다.
    const rowsAll = charsByFormation(d.names, d.chars);
    // 캡처에서 만든 기록은 «니케별 딜»이 없다 — 합계와 편성만 안다. 그때는 도넛·막대
    // 대신 얼굴 줄만 보여 준다. 없는 수치를 0으로 그리면 있는 것처럼 읽힌다.
    if (!rowsAll.length) {
      // 덱 블록은 [도넛][행 목록] 두 칸 격자다. 얼굴 줄만 넣으면 첫 칸(도넛 자리)에
      // 갇혀 왼쪽에 세로로 쌓인다 — 한 칸짜리로 풀어 준다.
      blk.classList.add("rec-deck-faces");
      const strip = el("div", "face-strip");
      for (const nm of (d.names || []).filter(Boolean)) {
        const rec2 = byName.get(nm);
        const f = el("div", "face" + (rec2?.img ? "" : " empty"));
        if (rec2?.img) {
          const im = el("img");
          im.src = artSrc(rec2, nm);
          im.alt = "";
          f.append(im);
        } else {
          f.append(el("div", "face-none", nm.slice(0, 1)));
        }
        f.append(el("div", "face-nm", nm));
        strip.append(f);
      }
      blk.append(strip);
      box.append(blk);
      if (opts.deckAction) opts.deckAction(i, blk);
      return;
    }
    // 도넛만은 **딜 순**이다 — 목록·막대는 배치 순이지만, 동그라미는 조각을
    // 딜 크기 순으로 이어야 「누가 지배하는가」가 회전 순서로도 바로 읽힌다.
    blk.append(donutEl(rowsAll.slice().sort((a, b) => b[1] - a[1]), d.total, d.names));
    // 행들을 한 상자로 묶는다 — 그래야 덱 블록이 [도넛][행 목록] **두 칸**으로 끝난다.
    // 행을 격자에 직접 늘어놓으면 도넛이 여러 행을 걸쳐야 하고, 그 span이 암시 행을
    // 잔뜩 만들어 블록 아래에 빈 공간이 생긴다.
    const list = el("div", "rec-rows");
    blk.append(list);

    // 덱 안에서 가장 센 니케를 100%로 잡는다 — 인게임 막대도 덱 내 최대 기준이다.
    // 줄 순서 자체는 **배치 순서**다(딜 순 아님) — 편성과 대조하기 쉬우라고.
    const rows = charsByFormation(d.names, d.chars);
    const top = Math.max(1, ...rows.map(([, v]) => v));
    for (const [nm, dmg] of rows) {
      const rec2 = byName.get(nm);
      const li = el("div", "rec-ch");

      const th = el("div", "rec-ch-art");
      if (rec2?.img) {
        const im = el("img");
        im.src = artSrc(rec2, nm);
        im.alt = ""; im.loading = "lazy"; im.decoding = "async"; im.draggable = false;
        th.append(im);
      }
      li.append(th);

      const mid = el("div", "rec-ch-mid");
      const nmrow = el("div", "rec-ch-nm");
      // 도넛 조각과 **같은 색 점**. 조각에 이름을 그어 붙이면 240px 칸에서 겹치므로,
      // 색으로 잇고 이름은 이 줄에서 읽게 한다.
      const dot = el("i", "rec-ch-dot");
      dot.style.background = deckColor(d.names, nm);
      nmrow.append(dot);
      nmrow.append(el("span", "rec-ch-b", BURST_ROMAN[rec2?.burst] || "?"));
      nmrow.append(el("span", null, nm));
      mid.append(nmrow);

      const bar = el("div", "rec-ch-bar");
      const fill = el("i");
      fill.style.width = `${Math.max((dmg / top) * 100, 1.5)}%`;
      fill.style.background = deckColor(d.names, nm);   // 도넛·점과 같은 색
      bar.append(fill);
      mid.append(bar);
      li.append(mid);

      // 총딜 하나로는 «왜 이 딜인지»를 못 읽는다 — 기본공격/스킬 비중과 히트·크리를 함께.
      const dt = d.detail?.[nm];
      if (dt && dt.total) {
        // 아래 띠는 **위 딜 막대와 같은 길이** 안에서 갈린다. 전폭으로 두면 딜이 적은
        // 니케도 띠만 길어 «많이 때린 것»처럼 읽힌다.
        const seg = el("div", "rec-ch-split");
        seg.style.width = `${Math.max((dmg / top) * 100, 1.5)}%`;
        const nPct = (dt.normal / dt.total) * 100;
        const sN = el("i", "seg-normal"); sN.style.width = `${nPct}%`;
        sN.title = T("기본공격 {v} ({v1}%)", { v: I18N.dmg(dt.normal), v1: nPct.toFixed(1) });
        const sS = el("i", "seg-skill"); sS.style.width = `${100 - nPct}%`;
        sS.title = T("스킬 {v} ({v1}%)", { v: I18N.dmg(dt.skill), v1: (100 - nPct).toFixed(1) });
        seg.append(sN, sS);
        mid.append(seg);
        const sub = el("div", "rec-ch-sub");
        sub.append(el("span", null, T("기본 {v}%", { v: nPct.toFixed(0) })));
        sub.append(el("span", null, T("스킬 {v}%", { v: (100 - nPct).toFixed(0) })));
        sub.append(el("span", null, T("{v}히트", { v: dt.hits.toLocaleString("ko-KR") })));
        // 크리는 **기대 크리율**이다. 기대값 모드에서는 크리를 확률로 굴리지 않고
        // 계수에 녹이므로 `is_crit`이 늘 false다 — 대신 히트마다 실린 `crit_frac`
        // (그 히트의 크리 확률)을 평균 내면 «몇 %가 크리로 들어갔는지»가 나온다.
        // 옛 기록은 크리가 0으로 저장돼 있다 — «크리 0%»로 적으면 사실이 아니다
        if (dt.hits && dt.crit > 0) {
          sub.append(el("span", null, T("크리 {v}%", { v: ((dt.crit / dt.hits) * 100).toFixed(0) })));
        }
        mid.append(sub);
      }

      const val = el("div", "rec-ch-v");
      val.append(el("b", null, `${I18N.dmg(dmg)}`));
      val.append(el("span", null, `${((dmg / (d.total || 1)) * 100).toFixed(1)}%`));
      const dps = dmg / (r.duration || 1);
      val.append(el("span", null, T("{v}/초", { v: I18N.dmg(dps) })));
      val.title = Math.round(dmg).toLocaleString("ko-KR");
      li.append(val);

      list.append(li);
    }
    if (!rows.length) list.append(el("div", "rec-ch-none", "니케별 수치가 없는 기록입니다"));
    // 덱 노트(«고정 스펙 아님…»)는 여기 안 적는다. 편성 탭에서 이미 크게 경고하고,
    // 기록은 «그때 이 수치가 나왔다»를 보는 자리라 매 덱마다 같은 문단이 반복될 뿐이다.

    // 타임라인 — **확인용**. `d.timeline`은 결과 탭이 방금 계산한 결과에만 실려 온다
    // (collectDecks가 기록에는 안 담는다) — 그래서 기록 탭에서 이 함수를 같이 써도
    // 저장된 기록에서는 조용히 안 뜬다.
    if (d.timeline?.buckets?.length) {
      blk.append(timelineEl(d.names, d.timeline, d.burstCycles, r.duration || 180));
    }
    if (opts.deckAction) opts.deckAction(i, blk);
    box.append(blk);
  });

  const sum = el("div", "rec-sum");
  sum.append(el("span", null, T("{length}덱 전체 합계", { length: r.decks.length })));
  const sv = el("b", null, `${I18N.dmg(r.total)}`);
  sv.title = Math.round(r.total).toLocaleString("ko-KR");
  sum.append(sv);
  box.append(sum);
  return box;
}

/** 기여도 도넛. 라이브러리 없이 SVG 원 하나에 `stroke-dasharray`로 조각을 얹는다.
 *  가운데에는 덱 총딜을 적어 «무엇의 100%인지»를 도넛 자체가 말하게 한다. */
function donutEl(rows, total, names) {
  const NS = "http://www.w3.org/2000/svg";
  const R = 34, C = 2 * Math.PI * R, W = 12;
  const box = el("div", "rec-donut");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 88 88");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", T("덱 기여도 — {v}", { v: rows.map(([n, v]) =>
    `${T(n)} ${((v / (total || 1)) * 100).toFixed(0)}%`).join(", ") }));

  const ring = document.createElementNS(NS, "circle");
  ring.setAttribute("cx", "44"); ring.setAttribute("cy", "44"); ring.setAttribute("r", R);
  ring.setAttribute("fill", "none"); ring.setAttribute("stroke-width", W);
  ring.setAttribute("stroke", "var(--color-stage-3)");
  svg.append(ring);

  let acc = 0;
  for (const [nm, dmg] of rows) {
    const frac = dmg / (total || 1);
    const seg = document.createElementNS(NS, "circle");
    seg.setAttribute("cx", "44"); seg.setAttribute("cy", "44"); seg.setAttribute("r", R);
    seg.setAttribute("fill", "none"); seg.setAttribute("stroke-width", W);
    seg.setAttribute("stroke", deckColor(names, nm));
    // 조각 사이 2px 간격 — 인접한 채움이 붙으면 경계가 사라진다 (dataviz 규칙)
    const len = Math.max(0, C * frac - 2);
    seg.setAttribute("stroke-dasharray", `${len} ${C - len}`);
    seg.setAttribute("stroke-dashoffset", `${-C * acc}`);
    seg.setAttribute("transform", "rotate(-90 44 44)");
    const t = document.createElementNS(NS, "title");
    t.textContent = `${T(nm)} — ${I18N.dmg(dmg)} (${(frac * 100).toFixed(1)}%)`;
    seg.append(t);
    svg.append(seg);

    // 조각 안에 퍼센트. 좁은 조각에 글자를 넣으면 넘치므로 **8% 이상만** 적는다.
    if (frac >= 0.08) {
      const mid = (acc + frac / 2) * 2 * Math.PI - Math.PI / 2;
      const rr = R;                       // 링 한가운데
      const tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", (44 + Math.cos(mid) * rr).toFixed(1));
      tx.setAttribute("y", (44 + Math.sin(mid) * rr).toFixed(1));
      tx.setAttribute("text-anchor", "middle");
      tx.setAttribute("dominant-baseline", "central");
      tx.setAttribute("font-size", "7");
      tx.setAttribute("font-weight", "700");
      // 링 색이 밝아 흰 글자는 묻힌다 — 어두운 글자에 얇은 밝은 테를 두른다
      tx.setAttribute("fill", "#101317");
      tx.setAttribute("paint-order", "stroke");
      tx.setAttribute("stroke", "rgba(255,255,255,.55)");
      tx.setAttribute("stroke-width", "1.6");
      tx.textContent = `${(frac * 100).toFixed(0)}%`;
      svg.append(tx);
    }
    acc += frac;
  }
  box.append(svg);
  // 가운데는 «몇 명»이 아니라 **1등이 누구인지**. 인원은 옆 목록을 세면 되고,
  // 도넛이 답해야 할 질문은 «누가 지배하는가»다.
  // `rows`는 이제 배치 순서로 들어온다 — **딜 최댓값을 따로 찾아야** 1등이 맞는다
  // (rows[0]은 편성 첫 자리일 뿐 1등이 아닐 수 있다).
  const mid = el("div", "rec-donut-mid");
  mid.append(el("b", null, `${I18N.dmg(total)}`));
  const topPick = rows.reduce((a, b) => (!a || b[1] > a[1] ? b : a), null);
  if (topPick) {
    const [tn, tv] = topPick;
    const lead = el("span", "rec-donut-lead");
    lead.append(el("i", null, tn));
    lead.append(el("em", null, `${((tv / (total || 1)) * 100).toFixed(0)}%`));
    mid.append(lead);
  }
  box.append(mid);
  return box;
}

/** 기록 → 붙여넣기 좋은 평문. 표 모양을 유지하려고 폭을 맞춘다. */
function recordText(r) {
  const L = [];
  L.push(`■ ${r.name || r.label}`);
  L.push(T("   {v} · 약점 {v1} · {duration}초 · {profileName}", { v: when(r.at), v1: r.code || T("없음"), duration: r.duration, profileName: r.profileName }));
  r.decks.forEach((d, i) => {
    L.push("");
    L.push(T("[{v}] 5명 전체딜 {v1}", { v: String(i + 1).padStart(2, "0"), v1: I18N.dmg(d.total) }));
    const rows = charsByFormation(d.names, d.chars);
    const w = Math.max(4, ...rows.map(([n]) => [...n].reduce(
      (a, ch) => a + (ch.charCodeAt(0) > 127 ? 2 : 1), 0)));
    for (const [nm, dmg] of rows) {
      const pad = w - [...nm].reduce((a, ch) => a + (ch.charCodeAt(0) > 127 ? 2 : 1), 0);
      const dt = d.detail?.[nm];
      const extra = dt && dt.total
        ? T("  기본 {v}% · {v1}히트", { v: ((dt.normal / dt.total) * 100).toFixed(0), v1: dt.hits.toLocaleString("ko-KR") })
        : "";
      L.push(`  ${nm}${" ".repeat(Math.max(0, pad))}  ${I18N.dmg(dmg)}`
             + `  ${((dmg / (d.total || 1)) * 100).toFixed(1)}%${extra}`);
    }
  });
  L.push("");
  L.push(T("합계 {v} · {length}덱", { v: I18N.dmg(r.total), length: r.decks.length }));
  return L.join("\n");
}

async function copyRecord(r) {
  const text = recordText(r);
  try {
    await navigator.clipboard.writeText(text);
    recMsg(T("클립보드에 복사했습니다."), "ok");
  } catch {
    // 클립보드 권한이 없는 환경(비 HTTPS 등)에서는 조용히 실패하지 않는다
    const ta = el("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px";
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    recMsg(ok ? T("클립보드에 복사했습니다.") : T("복사에 실패했습니다 — 내보내기를 쓰세요."),
           ok ? "ok" : "err");
  }
}

// ── 기록 비교 ───────────────────────────────────────────────────────────
// 두 기록의 «같은 덱»을 짝지어 딜이 어디서 늘었는지 본다.
//
// 덱 번호는 믿을 수 없다 — 같은 편성이 이번엔 3덱에, 저번엔 1덱에 있을 수 있다.
// 그래서 **편성이 겹치는 정도**로 짝을 짓는다. 5명 중 3명 이상 같으면 같은 덱으로 본다
// (한두 명 바꿔 끼운 것은 같은 덱, 세 명 이상 바뀌면 다른 덱이라는 뜻이다).

const COMPARE_MIN_OVERLAP = 3;

const deckNames = (d) => (d.names || []).filter(Boolean);

/** 두 기록을 짝짓는다. 겹치는 수가 큰 짝부터 확정하는 그리디 —
 *  「a가 b1과 4명, b2와 3명 겹친다」면 4명 쪽이 먼저 임자를 정해야 한다. */
function compareRecords(a, b) {
  const cand = [];
  a.decks.forEach((da, ai) => {
    const sa = new Set(deckNames(da));
    b.decks.forEach((db, bi) => {
      const n = deckNames(db).filter((x) => sa.has(x)).length;
      if (n >= COMPARE_MIN_OVERLAP) cand.push({ ai, bi, n });
    });
  });
  cand.sort((x, y) => y.n - x.n);
  const usedA = new Set(), usedB = new Set(), pairs = [];
  for (const c of cand) {
    if (usedA.has(c.ai) || usedB.has(c.bi)) continue;
    usedA.add(c.ai); usedB.add(c.bi);
    pairs.push(c);
  }
  pairs.sort((x, y) => x.ai - y.ai);

  // 편성이 겹치지 않아 짝을 못 지은 덱을 **따로 늘어놓지 않는다.** 남은 것끼리
  // **딜 순으로** 맞댄다 — 남는 1등 ↔ 남는 1등, 남는 2등 ↔ 남는 2등.
  // 편성이 통째로 바뀌어도 «내 제일 센 덱이 쟤 제일 센 덱보다 높은가»는 여전히
  // 묻고 싶은 비교다. 나란히 안 붙여 놓으면 사람이 눈으로 숫자를 옮겨 적어야 한다.
  const byTot = (rec) => (x, y) => (rec.decks[y].total || 0) - (rec.decks[x].total || 0);
  const restA = a.decks.map((_, i) => i).filter((i) => !usedA.has(i)).sort(byTot(a));
  const restB = b.decks.map((_, i) => i).filter((i) => !usedB.has(i)).sort(byTot(b));
  const rank = [];
  for (let i = 0; i < Math.min(restA.length, restB.length); i++) {
    const ai = restA[i], bi = restB[i];
    const sa = new Set(deckNames(a.decks[ai]));
    const n = deckNames(b.decks[bi]).filter((x) => sa.has(x)).length;
    rank.push({ ai, bi, n, rank: i + 1 });
  }
  // 한쪽에만 남은 덱(덱 수가 다를 때)은 어쩔 수 없이 혼자 놓는다
  return { pairs, rank, onlyA: restA.slice(rank.length), onlyB: restB.slice(rank.length) };
}

/** 증감 한 칸. 부호와 색을 함께 준다 — 숫자만 있으면 늘었는지 줄었는지 훑을 수 없다. */
function deltaEl(from, to, opts = {}) {
  const d = to - from;
  const pct = from ? (d / from) * 100 : 0;
  const cls = Math.abs(d) < 1 ? "flat" : (d > 0 ? "up" : "down");
  const box = el("span", `cmp-delta ${cls}`);
  const sign = d > 0 ? "+" : (d < 0 ? "−" : "±");
  box.append(el("b", null, `${sign}${I18N.dmg(Math.abs(d))}`));
  if (!opts.noPct && from) box.append(el("span", null, `${sign}${Math.abs(pct).toFixed(1)}%`));
  box.title = `${Math.round(from).toLocaleString("ko-KR")} → ${Math.round(to).toLocaleString("ko-KR")}`;
  return box;
}

/** 짝지은 덱 하나의 니케별 증감. 두 기록에 한쪽만 있는 니케도 빠뜨리지 않는다. */
/** 이 덱이 «니케별 딜»을 아는가. 캡처에서 만든 기록은 모른다(`chars`가 비어 있다). */
const hasChars = (d) => Object.keys(d.chars || {}).length > 0;

function charRows(da, db) {
  const A = da.chars || {}, B = db.chars || {};
  const keys = [...new Set([...Object.keys(A), ...Object.keys(B)])];
  const rows = keys.map((n) => ({ n, a: A[n] ?? null, b: B[n] ?? null }));
  // 배치 순서로 늘어놓는다 — 딜 증감 순이 아니다. 같은 편성을 계정만 바꿔 비교할 때
  // (실측 사례) 딜 증감순이면 계정마다 어느 니케가 제일 늘었는지가 갈려 순서가 매번
  // 뒤바뀐다. 기준(a) 덱의 배치를 먼저 따르고, a엔 없고 b에만 있는 이름은 b 덱
  // 배치 순서로 뒤에 붙인다.
  const orderA = deckNames(da), orderB = deckNames(db);
  const rank = (n) => {
    const ia = orderA.indexOf(n);
    if (ia !== -1) return ia;
    const ib = orderB.indexOf(n);
    return ib !== -1 ? orderA.length + ib : orderA.length + orderB.length;
  };
  return rows.sort((x, y) => rank(x.n) - rank(y.n));
}

function renderCompare(body, a, b) {
  const cmp = compareRecords(a, b);
  body.textContent = "";

  // 조건이 다르면 비교 자체가 의미가 흐려진다 — 숫자를 보여 주기 전에 말한다
  const diffs = [];
  if (a.code !== b.code) diffs.push(T("약점 코드 {v} → {v1}", { v: a.code || T("없음"), v1: b.code || T("없음") }));
  if (a.duration !== b.duration) diffs.push(T("전투 시간 {duration}초 → {duration1}초", { duration: a.duration, duration1: b.duration }));
  if (a.profileName !== b.profileName) diffs.push(T("스펙 «{profileName}» → «{profileName1}»", { profileName: a.profileName, profileName1: b.profileName }));
  if (diffs.length) {
    body.append(el("p", "share-pick-note warn",
      T("조건이 다릅니다 — {v}. 늘어난 딜이 편성 덕인지 조건 탓인지 갈립니다.", { v: diffs.join(" · ") })));
  }

  // 합계
  const sum = el("div", "cmp-sum");
  sum.append(el("span", "cmp-sum-k", "합계"));
  sum.append(el("span", "cmp-sum-v", `${I18N.dmg(a.total)} → ${I18N.dmg(b.total)}`));
  sum.append(deltaEl(a.total, b.total));
  body.append(sum);

  const head = el("p", "prose prose-sm",
    T("{v} (기준) → {v1} (비교)", { v: a.name || when(a.at), v1: b.name || when(b.at) })
    + T(" · 편성으로 짝지은 덱 {length}개", { length: cmp.pairs.length })
    + (cmp.rank.length ? T(" · 나머지 {length}개는 딜 순으로 맞댐", { length: cmp.rank.length }) : "")
    + T(" · 5명 중 {COMPARE_MIN_OVERLAP}명 이상 겹치면 같은 덱으로 봅니다.", { COMPARE_MIN_OVERLAP }));
  body.append(head);

  if (!cmp.pairs.length && !cmp.rank.length) {
    body.append(el("p", "share-pick-note warn",
      T("겹치는 덱이 없습니다 — 편성이 완전히 다른 두 기록입니다.")));
  }

  for (const p of [...cmp.pairs, ...cmp.rank]) {
    const da = a.decks[p.ai], db = b.decks[p.bi];
    const blk = el("div", "cmp-deck" + (p.rank ? " byrank" : ""));

    const h = el("div", "cmp-deck-h");
    h.append(el("span", "rec-no", String(p.ai + 1).padStart(2, "0")));
    h.append(el("span", "cmp-arrow", "→"));
    h.append(el("span", "rec-no", String(p.bi + 1).padStart(2, "0")));
    h.append(el("span", "cmp-overlap",
      p.rank ? T("편성 다름 · 남는 덱 {rank}위끼리", { rank: p.rank }) : T("{n}명 같음", { n: p.n })));
    h.append(el("span", "cmp-tot", `${I18N.dmg(da.total)} → ${I18N.dmg(db.total)}`));
    h.append(deltaEl(da.total, db.total));
    blk.append(h);

    // 편성이 어떻게 바뀌었나 — 늘어난 딜의 이유가 대개 여기에 있다.
    // 이름만 적으면 누가 누군지 바로 안 떠오른다. **얼굴로**, 그리고 바뀐 사람은
    // **딜 순으로 짝지어** 보여 준다 (빠진 1등 ↔ 새로 온 1등).
    blk.append(cmpFaces(da, db));

    // 캡처 기록은 **니케별 딜을 모른다.** 그런 쪽을 0으로 두고 표를 그리면 같은
    // 니케가 «빠짐 −100%»로 찍힌다 — 편성은 그대로인데 전멸한 것처럼 보인다.
    // 아는 것만 말한다: 덱 합계와 편성.
    if (!hasChars(da) || !hasChars(db)) {
      const who = !hasChars(da) && !hasChars(db) ? T("양쪽 다")
        : (!hasChars(da) ? T("기준") : T("비교"));
      blk.append(el("p", "cmp-nochars",
        T("{who} 캡처에서 만든 기록이라 니케별 딜이 없습니다 — 덱 합계와 편성만 견줍니다.", { who })));
      body.append(blk);
      continue;
    }
    const list = el("div", "cmp-rows");
    for (const r of charRows(da, db)) {
      const li = el("div", "cmp-row");
      const th = el("div", "cmp-art");
      const rec = byName.get(r.n);
      if (rec?.img) {
        const im = el("img");
        im.src = artSrc(rec, r.n);
        im.alt = ""; im.loading = "lazy"; im.decoding = "async"; im.draggable = false;
        th.append(im);
      }
      li.append(th);
      const nm = el("div", "cmp-nm", r.n);
      if (r.a == null) nm.append(el("i", "cmp-tag in", "새로"));
      else if (r.b == null) nm.append(el("i", "cmp-tag out", "빠짐"));
      li.append(nm);
      li.append(el("div", "cmp-v", `${I18N.dmg(r.a ?? 0)} → ${I18N.dmg(r.b ?? 0)}`));
      li.append(deltaEl(r.a ?? 0, r.b ?? 0, { noPct: r.a == null }));
      list.append(li);
    }
    blk.append(list);
    body.append(blk);
  }

  for (const [side, idxs, rec, label] of [["a", cmp.onlyA, a, T("기준에만")],
                                          ["b", cmp.onlyB, b, T("비교에만")]]) {
    for (const i of idxs) {
      const d = rec.decks[i];
      const blk = el("div", "cmp-deck lone");
      const h = el("div", "cmp-deck-h");
      h.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
      h.append(el("span", "cmp-overlap", T("{label} 있는 덱 — 맞둘 상대가 없음", { label })));
      h.append(el("span", "cmp-tot", `${I18N.dmg(d.total)}`));
      blk.append(h);
      blk.append(cmpLoneFaces(d));
      body.append(blk);
    }
  }
}

/** 비교할 기록 두 개를 고른다. **먼저 고른 쪽이 기준**이다 —
 *  이 화면이 답하는 질문이 «내가 쟤보다 몇 % 높은가»라서, 기준을 사람이 정해야 한다.
 *  (시간 순으로 고정해 두면 부호를 매번 거꾸로 읽게 된다.) */
function openCompare() {
  const dlg = $("#rec-compare-sheet");
  const body = $("#rec-compare-body");
  const go = $("#rec-compare-go");
  if (!dlg || !body || !go) return;
  if (recordsNow().length < 2) {
    recMsg(T("비교하려면 기록이 둘 이상 있어야 합니다."), "err");
    return;
  }

  let pick = [];
  const paint = () => {
    $("#rec-compare-t").textContent = T("비교할 기록 고르기");
    body.textContent = "";
    body.append(el("p", "prose prose-sm",
      T("두 개를 고르세요. **먼저 고른 쪽이 기준**이 되고, 덱은 편성이")
      + T(" {COMPARE_MIN_OVERLAP}명 이상 겹치는 것끼리 짝지어 비교합니다.", { COMPARE_MIN_OVERLAP })));
    const list = el("div", "share-pairs");
    for (const r of recordsNow()) {
      const on = pick.includes(r.id);
      const row = el("div", "share-pair pick" + (on ? " on" : ""));
      row.setAttribute("role", "button");
      row.setAttribute("aria-pressed", String(on));
      row.tabIndex = 0;
      const toggle = () => {
        if (on) pick = pick.filter((x) => x !== r.id);
        else pick = [...pick, r.id].slice(-2);      // 셋째를 고르면 가장 먼저 고른 것이 빠진다
        paint();
      };
      row.onclick = toggle;
      row.onkeydown = (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        toggle();
      };
      row.append(el("span", "share-pair-ck", on ? "✓" : ""));
      const mid = el("span", "share-pair-mid");
      // 사람이 붙인 이름이 우선 — 자동 라벨(«솔레 기록 · 5덱 · …억»)만 보이면
      // 기록이 여럿일 때 구분이 안 된다.
      mid.append(el("span", "share-pair-src", r.name || r.label));
      mid.append(el("span", "share-pair-dst" + (on ? " on" : ""),
        T("{v} · {length}덱 · {profileName} · {v1} {duration}초", { v: when(r.at), length: r.decks.length, profileName: r.profileName, v1: r.code || T("속성없음"), duration: r.duration })));
      row.append(mid);
      list.append(row);
    }
    body.append(list);
    go.disabled = pick.length !== 2;
    go.textContent = pick.length === 2 ? T("비교하기") : T("{length} / 2 골랐습니다", { length: pick.length });
  };
  paint();

  const close = () => dlg.close();
  $("#rec-compare-x").onclick = close;
  $("#rec-compare-cancel").onclick = close;
  go.onclick = () => {
    // **먼저 고른 것이 기준**이다. 시간 순으로 뒤집으면 «내가 쟤보다 몇 % 높다»를
    // 보려고 고른 사람이 부호를 매번 거꾸로 읽어야 한다.
    const byId = new Map(recordsNow().map((r) => [r.id, r]));
    const two = pick.map((id) => byId.get(id)).filter(Boolean);
    if (two.length !== 2) return;
    $("#rec-compare-t").textContent =
      `«${two[0].name || two[0].label}» → «${two[1].name || two[1].label}»`;
    renderCompare(body, two[0], two[1]);
    go.hidden = true;
    $("#rec-compare-cancel").textContent = T("닫기");
  };
  go.hidden = false;
  $("#rec-compare-cancel").textContent = T("취소");
  if (!dlg.open) dlg.showModal();
}

/** 기록 → 캔버스. **직접 그린다** — html2canvas 같은 라이브러리는 오프라인·CSP에서
 *  깨지고, 이 표는 선 몇 개라 손으로 그리는 편이 확실하다.
 *
 *  덱을 **2열**로 놓아 5덱이 한 장에 다 들어가게 하고, 초상화도 같이 그린다.
 *  세로로 쭉 늘어놓으면 5덱이 세로로 길어져 한눈에 안 들어온다. */
async function recordCanvas(r) {
  const S = 2;                                   // 레티나 배율
  const PAD = 22, COL_GAP = 18, ROW = 40, HEAD = 64, FOOT = 14;
  const DONUT = 104;                             // 왼쪽 도넛 칸
  const RX = DONUT + 14;                         // 행이 시작하는 x (도넛 오른쪽)
  const RW = 440;                                // 덱 칸 전체 폭
  const COLS = r.decks.length > 1 ? 2 : 1;
  const COL_W = 440;
  const W = PAD * 2 + COL_W * COLS + COL_GAP * (COLS - 1);
  // 니케별 딜이 없으면 `names`로 높이를 잡는다. `chars`만 보면 0줄이 되어
  // 덱 칸이 텅 빈 채로 저장된다.
  const deckRows = (d) => (Object.keys(d.chars || {}).length
    || (d.names || []).filter(Boolean).length);
  const deckH = (d) => 26 + deckRows(d) * ROW + 14;
  const rowsH = [];
  for (let i = 0; i < r.decks.length; i += COLS) {
    rowsH.push(Math.max(...r.decks.slice(i, i + COLS).map(deckH)));
  }
  const H = HEAD + rowsH.reduce((x, y) => x + y, 0) + FOOT;

  const cv = el("canvas");
  cv.width = W * S; cv.height = H * S;
  const x = cv.getContext("2d");
  x.scale(S, S);
  const INK = "#eef1f6", DIM = "#9aa3b2", AMBER = "#f0a935", BG = "#14161a", LINE = "#2a2f38";

  x.fillStyle = BG; x.fillRect(0, 0, W, H);
  x.fillStyle = INK; x.font = "700 19px Pretendard, system-ui, sans-serif";
  x.fillText(r.name || r.label, PAD, 36);
  x.strokeStyle = LINE; x.beginPath(); x.moveTo(PAD, 50); x.lineTo(W - PAD, 50); x.stroke();

  // 초상화를 먼저 다 불러 둔다 — 그리는 중에 기다리면 순서가 뒤엉킨다
  // 캡처에서 만든 기록은 `chars`가 비어 있다 — 그때는 `names`가 유일한 명단이다.
  const wanted = [...new Set(r.decks.flatMap(
    (d) => (Object.keys(d.chars || {}).length ? Object.keys(d.chars)
                                              : (d.names || []).filter(Boolean))))];
  const arts = new Map();
  await Promise.all(wanted.map(async (nm) => {
    const rec = byName.get(nm);
    if (!rec?.img) return;
    try {
      const im = new Image();
      im.src = artSrc(rec, nm);
      await im.decode();
      arts.set(nm, im);
    } catch { /* 초상화가 없으면 이름만 그린다 */ }
  }));

  let rowTop = HEAD;
  r.decks.forEach((d, i) => {
    const col = COLS === 1 ? 0 : i % COLS;
    if (col === 0 && i) rowTop += rowsH[Math.floor(i / COLS) - 1];
    const ox = PAD + col * (COL_W + COL_GAP);
    let y = rowTop;

    x.fillStyle = "#4cb3ef"; x.font = "700 13px Pretendard, system-ui, sans-serif";
    x.fillText(String(i + 1).padStart(2, "0"), ox, y);
    x.fillStyle = DIM; x.font = "11px Pretendard, system-ui, sans-serif";
    x.fillText(T("5명 전체딜"), ox + 24, y);
    x.fillStyle = AMBER; x.font = "700 15px Pretendard, system-ui, sans-serif";
    x.textAlign = "right"; x.fillText(`${I18N.dmg(d.total)}`, ox + COL_W, y); x.textAlign = "left";

    // **배치 순서**로 나열한다(딜 순 아님) — 편성을 보면서 대조하려는 목적이라
    // 실제 슬롯 순서와 같아야 훑기 쉽다. 도넛 가운데 «1등» 표시만 별도로 딜
    // 최상위를 찾는다(목록 순서와 무관하게 실제 1등이어야 한다).
    const chars = d.chars || {};
    const order = (d.names || []).filter(Boolean);
    const rows = order.length
      ? order.filter((nm) => nm in chars).map((nm) => [nm, chars[nm]])
      : Object.entries(chars);
    const topRow = rows.reduce((a, b) => (!a || b[1] > a[1] ? b : a), null);

    // 캡처에서 만든 기록은 니케별 딜을 모른다 — 도넛·막대를 0으로 그리면
    // 있는 수치처럼 읽힌다. 얼굴과 이름만 늘어놓고 끝낸다.
    if (!rows.length) {
      const list = (d.names || []).filter(Boolean);
      let fy = y + 18;
      for (const nm of list) {
        const im = arts.get(nm);
        if (im) {
          const AW = 26, AH = 34;
          x.save();
          x.beginPath(); x.rect(ox, fy - 4, AW, AH); x.clip();
          const sc = Math.max(AW / im.width, AH / im.height);
          x.drawImage(im, ox + (AW - im.width * sc) / 2,
                      fy - 4 - im.height * sc * 0.10, im.width * sc, im.height * sc);
          x.restore();
        }
        x.fillStyle = INK; x.font = "13px Pretendard, system-ui, sans-serif";
        x.fillText(nm, ox + 34, fy + 18);
        fy += ROW;
      }
      return;
    }

    // 기여도 도넛 — **왼쪽 제 칸**에. 가운데에 덱 총딜과 1등을 적어 화면과 같은
    // 모습으로 만든다 (행 위에 얹으면 이름·막대와 겹친다).
    {
      const cxx = ox + DONUT / 2, cyy = y + rows.length * ROW / 2 + 4,
            rr = DONUT / 2 - 8, lw = 11;
      let acc = -Math.PI / 2;
      x.lineWidth = lw;
      x.strokeStyle = "#232830";
      x.beginPath(); x.arc(cxx, cyy, rr, 0, Math.PI * 2); x.stroke();
      // 도넛 조각만 **딜 순**이다 — 옆 목록(`rows`)은 배치 순이지만, 동그라미는
      // 딜 크기 순으로 이어야 회전 순서만 봐도 「누가 지배하는가」가 읽힌다.
      const donutRows = rows.slice().sort((a, b) => b[1] - a[1]);
      for (const [nm, dmg] of donutRows) {
        const frac = dmg / (d.total || 1);
        const gap = 0.035;                       // 조각 사이 틈 (dataviz 규칙)
        x.strokeStyle = deckColor(d.names, nm);
        x.beginPath();
        x.arc(cxx, cyy, rr, acc + gap / 2, acc + frac * Math.PI * 2 - gap / 2);
        x.stroke();
        acc += frac * Math.PI * 2;
      }
      x.lineWidth = 1;
      x.textAlign = "center";
      x.fillStyle = AMBER; x.font = "700 14px Pretendard, system-ui, sans-serif";
      x.fillText(`${I18N.dmg(d.total)}`, cxx, cyy - 2);
      if (topRow) {
        const [tn, tv] = topRow;
        x.fillStyle = INK; x.font = "9px Pretendard, system-ui, sans-serif";
        let lead = tn;
        while (x.measureText(lead).width > DONUT - 22 && lead.length > 2) lead = lead.slice(0, -1);
        if (lead !== tn) lead = lead.slice(0, -1) + "…";
        x.fillText(lead, cxx, cyy + 10);
        x.fillStyle = DIM;
        x.fillText(`${((tv / (d.total || 1)) * 100).toFixed(0)}%`, cxx, cyy + 21);
      }
      x.textAlign = "left";
    }
    const top = Math.max(1, ...rows.map(([, v]) => v));
    for (const [nm, dmg] of rows) {
      y += ROW;
      // 얼굴이 알아볼 만해야 «누구 기록인지»가 한눈에 온다 — 막대를 조금 줄이고
      // 초상화를 키운다.
      const px = ox + RX, pw = 30, ph = 36;
      const im = arts.get(nm);
      x.fillStyle = "#1c2027"; x.fillRect(px, y - 30, pw, ph);
      if (im) {
        x.save();
        x.beginPath(); x.rect(px, y - 30, pw, ph); x.clip();
        // 세로로 긴 초상화(256×512)에서 얼굴 쪽만 — 카드와 같은 잘림 위치
        const sc = pw / im.width;
        x.drawImage(im, px, y - 30, pw, im.height * sc);
        x.restore();
      }
      // 버스트 단계를 이름 앞에 — 편성을 읽을 때 «몇 버스트가 몇 명인지»가 먼저 궁금하다
      const bx0 = px + pw + 7;
      const burst = BURST_ROMAN[byName.get(nm)?.burst] || "?";
      x.fillStyle = "#2b313a"; x.fillRect(bx0, y - 23, 15, 14);
      x.fillStyle = DIM; x.font = "700 9px Pretendard, system-ui, sans-serif";
      x.textAlign = "center"; x.fillText(burst, bx0 + 7.5, y - 13); x.textAlign = "left";

      x.fillStyle = INK; x.font = "12px Pretendard, system-ui, sans-serif";
      const nameW = 100;
      let label = nm;
      while (x.measureText(label).width > nameW && label.length > 2) label = label.slice(0, -1);
      if (label !== nm) label = label.slice(0, -1) + "…";
      x.fillText(label, bx0 + 20, y - 12);
      x.fillStyle = INK; x.font = "700 12px Pretendard, system-ui, sans-serif";
      x.textAlign = "right"; x.fillText(`${I18N.dmg(dmg)}`, ox + RW - 42, y - 12);
      x.fillStyle = DIM; x.font = "11px Pretendard, system-ui, sans-serif";
      x.fillText(`${((dmg / (d.total || 1)) * 100).toFixed(1)}%`, ox + RW, y - 12);
      x.textAlign = "left";
      const bx = px + pw + 7, bw = ox + RW - 46 - bx;
      x.fillStyle = "#232830"; x.fillRect(bx, y - 5, bw, 3);
      const w = bw * (dmg / top);
      x.fillStyle = deckColor(d.names, nm);
      x.fillRect(bx, y - 5, w, 3);
      // 그 아래 얇게 평타/스킬. **딜 막대와 같은 길이 안에서** 갈린다 —
      // 전폭으로 두면 딜이 적은 니케도 띠만 길어 «많이 때린 것»처럼 읽힌다.
      const dt = d.detail?.[nm];
      if (dt && dt.total) {
        const nw = w * (dt.normal / dt.total);
        x.fillStyle = "#4cb3ef"; x.fillRect(bx, y - 1, nw, 2);
        x.fillStyle = "#c48218"; x.fillRect(bx + nw, y - 1, w - nw, 2);
      }
    }
  });

  // 맨 아래에는 아무것도 두지 않는다 — «전체 합계»는 제목 줄에 이미 있고,
  // 평타/스킬 범례도 뺐다. 얇은 두 색 띠는 봐서 알 만한 것이라, 한 줄을 더 쓰는
  // 값어치가 없었다.
  return cv;
}

const recFile = (r) => T("니케기록-{v}.png", { v: (r.name || r.label).replace(/[\/:*?"<>|]/g, "_") });

/** 유니온 기록 → 캔버스. **솔로와 별개 함수다** — 솔로는 5덱을 2열로 앉히지만
 *  유니온은 세 줄이 곧 한 출격 묶음이라 위에서 아래로 **한 줄로** 쭉 이어야
 *  「오늘 이렇게 쳤다」가 그대로 읽힌다. 줄마다 어느 보스였는지도 함께 적는다. */
async function unionRecordCanvas(r) {
  const S = 2;
  // 왼쪽에 기여도 도넛 칸을 둔다 — 솔로 기록과 같은 읽는 법이라야 두 장을 나란히
  // 놓고 봐도 눈이 안 헤맨다.
  const PAD = 22, ROW = 40, HEAD = 70, FOOT = 16, DONUT = 104, W = 560;
  const RX = PAD + DONUT + 14;
  const deckRows = (d) => (Object.keys(d.chars || {}).length
    || (d.names || []).filter(Boolean).length);
  // 도넛이 행보다 클 수 있다 — 둘 중 큰 쪽이 그 줄의 높이다
  const deckH = (d) => 30 + Math.max(deckRows(d) * ROW, DONUT) + 16;
  const H = HEAD + r.decks.reduce((a, d) => a + deckH(d), 0) + FOOT;

  const cv = el("canvas");
  cv.width = W * S; cv.height = H * S;
  const x = cv.getContext("2d");
  x.scale(S, S);
  const INK = "#eef1f6", DIM = "#9aa3b2", ROSE = "#ff8ad0", BG = "#14161a", LINE = "#2a2f38";

  x.fillStyle = BG; x.fillRect(0, 0, W, H);
  x.fillStyle = INK; x.font = "700 19px Pretendard, system-ui, sans-serif";
  x.fillText(r.name || r.label, PAD, 34);
  x.fillStyle = ROSE; x.font = "800 20px Pretendard, system-ui, sans-serif";
  x.textAlign = "right";
  x.fillText(`${I18N.dmg(r.total)}`, W - PAD, 34);
  x.textAlign = "left";
  x.fillStyle = DIM; x.font = "500 12px Pretendard, system-ui, sans-serif";
  x.fillText(T("{duration}초 · {v}", { duration: r.duration, v: r.profileName || "" }).trim(), PAD, 52);
  x.strokeStyle = LINE; x.beginPath(); x.moveTo(PAD, 60); x.lineTo(W - PAD, 60); x.stroke();

  const wanted = [...new Set(r.decks.flatMap(
    (d) => (Object.keys(d.chars || {}).length ? Object.keys(d.chars)
                                              : (d.names || []).filter(Boolean))))];
  const arts = new Map();
  await Promise.all(wanted.map(async (nm) => {
    const rec = byName.get(nm);
    if (!rec?.img) return;
    try {
      const im = new Image();
      im.src = artSrc(rec, nm);
      await im.decode();
      arts.set(nm, im);
    } catch { /* 초상화가 없으면 이름만 그린다 */ }
  }));

  let y = HEAD;
  r.decks.forEach((d, i) => {
    // 줄 머리 — 몇 번 줄, 어느 보스, 그 줄 딜
    x.fillStyle = ROSE; x.font = "800 13px Pretendard, system-ui, sans-serif";
    x.fillText(String(i + 1).padStart(2, "0"), PAD, y + 14);
    x.fillStyle = INK; x.font = "700 14px Pretendard, system-ui, sans-serif";
    const boss = d.weak ? (bossOf(d.weak)?.name || d.weak) : "";
    x.fillText(boss, PAD + 26, y + 14);
    x.textAlign = "right";
    x.fillStyle = ROSE; x.font = "800 15px Pretendard, system-ui, sans-serif";
    const dealTxt = `${I18N.dmg(d.total)}`;
    x.fillText(dealTxt, W - PAD, y + 14);
    // 숫자 앞에 «무슨 약점 줄인지»를 붙인다 — 줄 머리의 보스 이름만으로는 속성이
    // 안 읽힌다(보스 이름과 속성을 외우고 있어야 하는 그림이 된다).
    if (d.weak) {
      const dw = x.measureText(dealTxt).width;   // 15px 폰트인 지금 재야 맞다
      x.fillStyle = DIM; x.font = "600 12px Pretendard, system-ui, sans-serif";
      x.fillText(T("{weak}약점", { weak: d.weak }), W - PAD - dw - 7, y + 14);
    }
    x.textAlign = "left";
    y += 30;

    const names = Object.keys(d.chars || {}).length
      ? Object.keys(d.chars) : (d.names || []).filter(Boolean);

    // 기여도 도넛 — 솔로 기록과 **같은 읽는 법**이다. 두 장을 나란히 놓고 봐도
    // 눈이 안 헤매야 한다. 조각은 딜 순으로 돌아 「누가 지배하는가」가 회전만 봐도
    // 읽히고, 가운데에는 그 줄 총딜과 1등을 적는다.
    {
      const pairs = names.map((nm) => [nm, (d.chars || {})[nm] || 0])
                         .filter(([, v]) => v > 0);
      const cxx = PAD + DONUT / 2;
      const cyy = y + Math.max(names.length * ROW, DONUT) / 2;
      const rr = DONUT / 2 - 8;
      x.lineWidth = 11;
      x.strokeStyle = "#232830";
      x.beginPath(); x.arc(cxx, cyy, rr, 0, Math.PI * 2); x.stroke();
      let acc = -Math.PI / 2;
      for (const [nm, dmg] of pairs.slice().sort((a, b) => b[1] - a[1])) {
        const frac = dmg / (d.total || 1);
        const gap = 0.035;                        // 조각 사이 틈
        x.strokeStyle = deckColor(d.names, nm);
        x.beginPath();
        x.arc(cxx, cyy, rr, acc + gap / 2, acc + frac * Math.PI * 2 - gap / 2);
        x.stroke();
        acc += frac * Math.PI * 2;
      }
      x.lineWidth = 1;
      x.textAlign = "center";
      x.fillStyle = ROSE; x.font = "700 14px Pretendard, system-ui, sans-serif";
      x.fillText(`${I18N.dmg(d.total)}`, cxx, cyy - 2);
      const topPair = pairs.slice().sort((a, b) => b[1] - a[1])[0];
      if (topPair) {
        const [tn, tv] = topPair;
        x.fillStyle = INK; x.font = "9px Pretendard, system-ui, sans-serif";
        let lead = tn;
        while (x.measureText(lead).width > DONUT - 22 && lead.length > 2) lead = lead.slice(0, -1);
        if (lead !== tn) lead = lead.slice(0, -1) + "…";
        x.fillText(lead, cxx, cyy + 10);
        x.fillStyle = DIM;
        x.fillText(`${((tv / (d.total || 1)) * 100).toFixed(0)}%`, cxx, cyy + 21);
      }
      x.textAlign = "left";
    }

    // 막대 기준은 **그 줄 최고딜**이다 — 세 줄을 한 자로 재면 딜 낮은 줄은 죄다
    // 뭉개져서 그 안의 서열이 안 보인다. 줄마다 다시 잡아야 «이 줄에서 누가 컸나»가
    // 읽히고, 줄끼리 비교는 위의 총딜과 도넛이 맡는다.
    const top = Math.max(1, ...names.map((nm) => (d.chars || {})[nm] || 0));
    const BX = RX + 36, BW = W - PAD - 48 - BX;
    for (const nm of names) {
      const im = arts.get(nm);
      // 초상화가 없어도 받침은 깐다 — 이름 왼쪽이 들쭉날쭉하면 훑기 나쁘다
      x.fillStyle = "#1c2027"; x.fillRect(RX, y + 2, 28, 30);
      if (im) {
        x.save();
        x.beginPath(); x.rect(RX, y + 2, 28, 30); x.clip();
        // 얼굴이 오도록 위쪽을 잡는다 (초상화는 세로로 길다)
        x.drawImage(im, RX, y + 2 - 5, 28, 56);
        x.restore();
      }
      x.fillStyle = INK; x.font = "500 13px Pretendard, system-ui, sans-serif";
      let label = nm;
      while (x.measureText(label).width > BW - 6 && label.length > 2) label = label.slice(0, -1);
      if (label !== nm) label = label.slice(0, -1) + "…";
      x.fillText(label, BX, y + 17);
      const v = (d.chars || {})[nm];
      if (v != null) {
        x.textAlign = "right";
        x.fillStyle = INK; x.font = "700 13px Pretendard, system-ui, sans-serif";
        x.fillText(`${I18N.dmg(v)}`, W - PAD - 44, y + 17);
        x.fillStyle = DIM; x.font = "11px Pretendard, system-ui, sans-serif";
        x.fillText(`${((v / (d.total || 1)) * 100).toFixed(1)}%`, W - PAD, y + 17);
        x.textAlign = "left";
        // 딜 막대 — 도넛이 «비중»이면 막대는 «크기»다. 도넛 조각과 **같은 색**이라야
        // 왼쪽 동그라미와 오른쪽 목록이 눈에서 이어진다.
        x.fillStyle = "#232830"; x.fillRect(BX, y + 25, BW, 3);
        const bw = BW * (v / top);
        x.fillStyle = deckColor(d.names, nm);
        x.fillRect(BX, y + 25, bw, 3);
        // 평타/스킬은 **막대 길이 안에서** 갈린다 (솔로와 같은 규칙) — 전폭으로 두면
        // 딜이 적은 니케도 띠만 길어 «많이 때린 것»처럼 읽힌다.
        const dt = d.detail?.[nm];
        if (dt && dt.total) {
          const nw = bw * (dt.normal / dt.total);
          x.fillStyle = "#4cb3ef"; x.fillRect(BX, y + 30, nw, 2);
          x.fillStyle = "#c48218"; x.fillRect(BX + nw, y + 30, bw - nw, 2);
        }
      }
      y += ROW;
    }
    // 도넛이 행보다 크면 그 차이만큼 더 내린다 (deckH와 같은 셈이라야 겹치지 않는다)
    y += Math.max(0, DONUT - names.length * ROW);
    y += 16;
    if (i < r.decks.length - 1) {
      x.strokeStyle = LINE; x.beginPath();
      x.moveTo(PAD, y - 8); x.lineTo(W - PAD, y - 8); x.stroke();
    }
  });
  return cv;
}

/** 기록을 그릴 캔버스를 고른다 — 유니온만 제 함수로 간다. */
const recordCanvasFor = (r) =>
  (r?.mode === "union" ? unionRecordCanvas(r) : recordCanvas(r));

async function imageRecord(r) {
  const cv = await recordCanvasFor(r);
  cv.toBlob((blob) => {
    if (!blob) return recMsg(T("이미지를 만들지 못했습니다."), "err");
    const url = URL.createObjectURL(blob);
    const a2 = el("a");
    a2.href = url;
    a2.download = recFile(r);
    a2.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    recMsg(T("이미지를 저장했습니다."), "ok");
  }, "image/png");
}

/** 클립보드에 PNG로. 붙여넣기로 바로 공유할 수 있게 — 저장 → 첨부보다 한 단계 짧다. */
async function copyImageRecord(r) {
  const cv = await recordCanvasFor(r);
  const blob = await new Promise((res) => cv.toBlob(res, "image/png"));
  if (!blob) return recMsg(T("이미지를 만들지 못했습니다."), "err");
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    recMsg(T("이미지를 클립보드에 복사했습니다."), "ok");
  } catch (e) {
    // 비 HTTPS나 권한 없는 브라우저에서는 이미지 클립보드가 막힌다 — 이유를 말한다
    recMsg(T("이미지 복사가 막혔습니다 ({name}) — «이미지 저장»을 쓰세요.", { name: e.name }), "err");
  }
}

// 덱 안 다섯 자리의 색. **역할군이 아니라 «누구»를 가리킨다** — 화력형이 둘이면
// 역할군 색으로는 같은 색이 되어 도넛에서 한 덩어리로 보인다.
// 색은 덱의 **슬롯 순서**에 묶는다(딜 순위가 아니다) — 순위로 묶으면 계산할 때마다
// 색이 서로 바뀌어 «같은 색 = 같은 니케»가 깨진다.
// 검증: `dataviz/scripts/validate_palette.js … --mode dark` 6검사 전부 PASS
// (최악 인접쌍 ΔE 18.8 protan · 20.0 normal).
const DECK_COLORS = ["#c48218", "#168dd9", "#40a35c", "#a05fd0", "#d95f5f"];
const deckColor = (names, name) => {
  const i = (names || []).indexOf(name);
  return DECK_COLORS[(i < 0 ? 0 : i) % DECK_COLORS.length];
};

/** 기록의 편성을 덱으로 되살린다. **덱 구성만 가져온다** — 수치는 되살리지 않는다.
 *  그때의 스펙과 지금 스펙이 다를 수 있어 옛 수치를 지금 결과로 보여 주면 거짓이 된다.
 *  지금 스펙에 없는 니케는 빈 자리로 두고 누가 빠졌는지 말해 준다.
 *
 *  **통째로 덮지 않는다.** 예전에는 5덱을 한 번에 밀어 버려서, 짜 두던 편성을 되돌릴
 *  방법이 없었다. 프리셋과 **같은 시트**로 어느 덱을 어디에 넣을지 고르게 하고,
 *  밀려나는 편성은 빈 덱으로 옮긴다. 기록은 조건을 갖고 있으므로 그것도 되돌린다. */
function loadRecord(r) {
  openPresetLoad({
    name: r.name || r.label,
    kind: "bundle",
    decks: (r.decks || []).map((d) => ({ names: d.names })),
    cond: { code: r.code, duration: r.duration },
  }, { sink: recMsg });
}


// ── 계정 공통 설정 (콘솔 레벨) ──────────────────────────────────────────
// 콘솔(재활용 연구실)은 니케 하나가 아니라 **계정 전체**에 걸린다. 그래서 니케 시트가
// 아니라 스펙 고르개 옆 톱니에서 연다. 블라링크 조회에는 자동으로 들어오지만
// 레츠도로 CSV에는 아예 없어서, 손으로 넣을 자리가 필요하다.
// 콘솔(재활용 연구실)은 **세 갈래**다: 공통 하나 · 역할군 셋 · 기업 다섯.
// 블라 조회는 역할군·기업을 각각 dict로 주고, 손으로 넣을 때는 숫자 하나로 퉁칠 수도
// 있다. 그래서 편집기는 두 모양을 **모두** 읽고, 쓸 때는 언제나 dict로 쓴다.
const CONSOLE_DEFAULT = { common_level: 180, class_level: 100, company_level: 100 };
const CONSOLE_MAX = 500;

/** 지금 스펙의 콘솔 값 (동기화 값 위에 수정본을 얹은 것). */
function consoleNow() {
  const c = (mergedProfile()?._account || {}).console || {};
  return { ...CONSOLE_DEFAULT, ...c };
}

/** 스칼라든 dict든 «이 키의 값»을 꺼낸다. */
function conVal(v, key, fallback) {
  if (v == null) return fallback;
  if (typeof v === "number") return v;
  const n = v[key];
  return typeof n === "number" ? n : fallback;
}

/** 콘솔이 **손으로 고쳐졌는가.** 동기화 값과 다르면 톱니에 색이 든다. */
function consoleEdited() {
  const rec = activeRec();
  return !!(rec && rec.edits?._account?.console
            && Object.keys(rec.edits._account.console).length);
}

/** 한 칸을 고친다. 역할군·기업은 **dict 전체를 다시 써서** 다른 칸을 잃지 않는다. */
function setConsole(group, key, value) {
  const rec = activeRec();
  if (!rec) return;
  const now = consoleNow();
  rec.edits._account ||= {};
  const con = (rec.edits._account.console ||= {});
  if (group === "common_level") {
    con.common_level = value;
  } else {
    const keys = group === "class_level" ? CLASS_ORDER : CORP_ORDER;
    const base = now[group];
    const next = {};
    for (const k of keys) next[k] = conVal(base, k, CONSOLE_DEFAULT[group]);
    next[key] = value;
    con[group] = next;
  }
  results = {};                      // 지문이 바뀐다 — 옛 결과를 남기지 않는다
  saveAll();
  buildAcctSheet();
  syncAcctCog();
  renderAll();
}

/** 톱니에 «수정됨» 색. 콘솔을 손대면 바로 보여야 한다 — `renderAll`은 이 버튼을 안 건드린다. */
function syncAcctCog() {
  const cog = $("#acct-cog");
  if (cog) cog.classList.toggle("edited", consoleEdited());
}

function conRow(label, group, key, cur) {
  const r = el("div", "grp-row");
  r.append(el("span", "ol-part", label));
  const opts = Array.from({ length: CONSOLE_MAX + 1 }, (_, i) => [i, String(i)]);
  r.append(selectEl(opts, cur, (v) => setConsole(group, key, Number(v))));
  return r;
}

function buildAcctSheet() {
  const body = $("#acct-body");
  if (!body) return;
  body.textContent = "";
  const rec = activeRec();
  $("#acct-sheet-sub").textContent = rec
    ? `${rec.name} · ${rec.source}` + (consoleEdited() ? T(" · 수정됨") : "")
    : T("저장된 스펙이 없습니다");
  $("#acct-revert").disabled = !consoleEdited();
  if (!rec) {
    body.append(el("p", "prose prose-sm",
      T("먼저 «내 계정» 탭에서 육성 데이터를 불러오세요.")));
    return;
  }

  const now = consoleNow();
  body.append(group(T("재활용 연구실 (콘솔) — 공통"),
    [conRow(T("공통"), "common_level", null, conVal(now.common_level, null, 180))]));
  body.append(group(T("역할군별"),
    CLASS_ORDER.map((k) => conRow(k, "class_level", k, conVal(now.class_level, k, 100)))));
  body.append(group(T("기업별"),
    CORP_ORDER.map((k) => conRow(k, "company_level", k, conVal(now.company_level, k, 100)))));
  // `**…**`는 마크다운이 아니라 그냥 별표로 보인다 — 강조는 태그로 한다
  const note = el("p", "prose prose-sm");
  note.append(T("블라블라링크 조회에서는 "));
  note.append(el("b", null, "전초기지 정보를 공개"));
  note.append(T("로 둬야 자동으로 들어옵니다. 레츠도로 CSV에는 아예 없습니다. ")
    + T("손대지 않으면 기본 스펙(공통 {common_level} · ", { common_level: CONSOLE_DEFAULT.common_level })
    + T("역할군 {class_level} · 기업 {company_level})으로 ", { class_level: CONSOLE_DEFAULT.class_level, company_level: CONSOLE_DEFAULT.company_level })
    + T("계산합니다."));
  body.append(note);
}

function openAcctSheet() {
  buildAcctSheet();
  $("#acct-sheet").showModal();
}

// ── 계정 탭 ─────────────────────────────────────────────────────────────
function acct(msg, kind = "") {
  const n = $("#acct-msg");
  n.textContent = msg;
  n.className = "acct-msg " + kind;
}

async function copyInto(text, sink, okMsg, failMsg, failKind = "err") {
  try {
    await navigator.clipboard.writeText(text);
    sink(okMsg, "ok");
  } catch {
    // 클립보드 권한이 없는 환경(비 HTTPS 등)에서는 조용히 실패하지 않고 대안을 준다
    const ta = el("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px";
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand?.("copy");
    ta.remove();
    sink(ok ? okMsg : failMsg, ok ? "ok" : failKind);
  }
}
const copyText = (text, okMsg) =>
  copyInto(text, acct, okMsg, T("복사가 막혔습니다 — bookmarklet.js를 직접 여세요."));

/** 사람이 읽을 이름을 만든다. `nikke-raw-1034…` 같은 파일명을 그대로 쓰지 않는다. */
// 스펙 이름 길이 상한. 스펙 카드의 이름 줄과 상단 고르개가 이 길이까지는 안 깨진다
// (그 위로 가면 카드 머리에서 버튼들을 아래로 밀어낸다).
const NAME_MAX = 24;

/** 이름에 붙일 짧은 시각 도장 (`08-21 07:52`). */
function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 이미 쓰는 이름이면 뒤에 번호를 붙인다. */
function uniqName(base) {
  const b = String(base || "").trim().slice(0, NAME_MAX);
  if (!b) return "";
  const used = new Set(Object.values(state.profiles).map((r) => r.name));
  if (!used.has(b)) return b;
  for (let i = 2; i < 99; i++) if (!used.has(`${b} ${i}`)) return `${b} ${i}`;
  return b;
}

/** 파일명 → 프로필 기본 이름. 확장자를 떼고 겹치면 번호를 붙인다. */
function fileName(raw) {
  return uniqName(String(raw || "").replace(/\.[^.]+$/, ""));
}

function autoName() {
  const used = new Set(Object.values(state.profiles).map((r) => r.name));
  if (!used.has("내 계정")) return T("내 계정");
  for (let i = 2; i < 99; i++) if (!used.has(T("계정 {i}", { i }))) return T("계정 {i}", { i });
  return T("계정");
}

function addProfile({ profile, notices, source, name, edits }) {
  // 스펙 하나가 약 120KB다. 한도(약 5MB)를 기록·결과 캐시와 나눠 쓰므로 여기서 막는다 —
  // 다 차서 저장이 실패하면 **무엇이 안 들어갔는지도 모르게** 된다.
  if (Object.keys(state.profiles).length >= PROFILE_MAX) {
    throw new Error(T("스펙은 {PROFILE_MAX}개까지 저장합니다 — ", { PROFILE_MAX })
                    + T("«내 계정» 탭에서 쓰지 않는 스펙을 먼저 지우세요."));
  }
  const m = profile._meta || {};
  const id = uid();
  state.profiles[id] = {
    id, name: name || autoName(), openid: m.openid || "", area: m.area ?? null,
    source, fetched_at: m.fetched_at || new Date().toISOString(),
    notices: notices || [], fetched: profile,
    // 불러오기로 들어온 `_edits`만 복원한다 — 없으면 빈 수정 층에서 시작한다.
    edits: (edits && typeof edits === "object")
      ? { ...edits, chars: { ...(edits.chars || {}) } } : { chars: {} },
  };
  state.settings.profileId = id;
  results = {};                     // 스펙이 바뀌면 옛 결과는 의미가 없다
  saveAll();
  renderProfiles(); renderProfilePick(); renderAll();
  return state.profiles[id];
}

async function convertRaw(raw, name) {
  const data = await askWorker({ type: "convert", raw: JSON.stringify(raw), name });
  if (data.type === "error") throw new Error(data.error);
  return data;
}
async function convertCsv(text, name) {
  const data = await askWorker({ type: "convert_csv", text, name });
  if (data.type === "error") throw new Error(data.error);
  return data;
}

async function syncUrl() {
  const url = $("#url-in").value.trim();
  if (!url) return acct(T("URL을 넣어 주세요."), "err");
  const btn = $("#url-go");
  btn.dataset.state = "loading"; btn.disabled = true;
  acct(T("블라블라링크에서 받는 중…"));
  try {
    const raws = await fetchQueued({ url }, (m) => acct(m));
    // 이름에 **닉네임을 못 쓴다.** 남의 계정 닉네임을 주는 라우트가 조회 세션 권한으로는
    // 전부 `220000 not permission`이다 (게임 API·UGC 모두 확인).
    //
    // **openid 꼬리도 안 쓴다.** 예전에는 «블라 41757 (한국)»처럼 뒤 5자리를 붙여
    // 구분했는데, 이 이름이 상단 고르개에 늘 떠 있어서 스크린샷에 그대로 찍혀
    // 올라갔다(실사용 확인). 조회에 쓸 수 없는 조각이라 계정이 뚫리진 않지만,
    // 굳이 계정과 이어지는 숫자를 화면에 남길 이유가 없다. 지역만 쓰고, 같은
    // 지역이 여럿이면 `uniqName`이 뒤에 번호를 붙인다 — 이름은 언제든 바꿀 수 있다.
    //
    // **계정 하나에 지역(한섭·일섭 등)이 여럿 걸릴 수 있다** — raws가 그만큼 온다.
    // 첫 지역만 받으면 나머지 지역은 영영 못 본다(실측: 일섭이 메인인 계정이 한섭으로만
    // 저장되던 버그). 그래서 감지된 지역을 전부 별도 스펙으로 저장하고, 이름에 지역을
    // 붙여 구분한다.
    const names = [];
    for (const raw of raws) {
      const label = uniqName(T("블라 ({v})", { v: raw.area_label || T("글로벌") }));
      const out = await convertRaw(raw, label);
      const rec = addProfile({ ...out, source: T("블라링크"), name: label });
      names.push(T("{name}(니케 {v}종)", { name: rec.name, v: Object.keys(rec.fetched.chars).length }));
    }
    acct(names.length > 1
      ? T("이 계정에 지역이 {length}개 걸려 있어 각각 저장했습니다 — {v}.", { length: names.length, v: names.join(" · ") })
      : T("{v} 저장했습니다.", { v: names[0] }), "ok");
  } catch (e) {
    acct(String(e.message || e), "err");
  } finally {
    btn.dataset.state = ""; btn.disabled = false;
  }
}

async function importFiles(files) {
  for (const f of files) {
    try {
      const text = await f.text();
      // 레츠도로 CSV — 확장자나 헤더로 알아본다
      if (/\.csv$/i.test(f.name) || text.slice(0, 300).includes('"이름"')) {
        acct(T("{name} 변환 중…", { name: f.name }));
        // CSV에는 계정 닉네임·아이디 칸이 없다 (55칼럼 전부 니케별 값이다).
        // 그나마 사람이 알아볼 단서는 **파일명**이라 그걸 기본 이름으로 쓴다.
        // `addProfile`은 `out.name`을 보는데 변환기는 이름을 돌려주지 않는다 —
        // 따로 넘기지 않으면 파일명을 지어 놓고도 «내 계정»으로 저장된다.
        const csvName = fileName(f.name) || autoName();
        const out = await convertCsv(text, csvName);
        const rec = addProfile({ ...out, source: "letsdoro CSV", name: csvName });
        acct(T("{name} — 니케 {v}종 저장했습니다.", { name: rec.name, v: Object.keys(rec.fetched.chars).length })
             + T(" 이름은 아래 [이름] 버튼으로 바꿀 수 있습니다."), "ok");
        continue;
      }
      const data = JSON.parse(text);
      if (data.characters && data.details) {              // 북마클릿·서버 raw
        acct(T("{name} 변환 중…", { name: f.name }));
        const out = await convertRaw(data, autoName());
        const rec = addProfile({ ...out, source: data._source || "bookmarklet" });
        acct(T("{name} — 니케 {v}종 저장했습니다.", { name: rec.name, v: Object.keys(rec.fetched.chars).length })
             + T(" 이름은 아래 [이름] 버튼으로 바꿀 수 있습니다."), "ok");
      } else if (data.decks && data.total != null) {       // 내보낸 기록
        recordsNow().unshift({ ...data, id: uid() });
        saveAll(); renderRecords();
        acct(T("기록 «{v}»을 불러왔습니다 — 기록 탭에서 보세요.", { v: data.label || f.name }), "ok");
      } else if (data.chars) {                             // 내보낸 스펙
        // `_edits`는 원본이 아니라 수정 층이다 — 검증·저장 전에 떼어내
        // `edits`로 되돌린다. 안 떼면 수정본이 `fetched`로 굳는다(내보내기 주석).
        const { _edits: imported, ...base } = data;
        const v = await askWorker({ type: "validate", profile: JSON.stringify(base) });
        if (!v.ok) throw new Error(v.error);
        addProfile({ profile: base, notices: [], source: "import", edits: imported });
        acct(T("{name} 불러왔습니다.", { name: f.name }), "ok");
      } else if (Array.isArray(data.presets)) {          // 내보낸 프리셋
        // 계정 탭 드롭존에 프리셋 파일을 떨어뜨리는 일은 충분히 있을 만하다 —
        // «모르는 형식»으로 돌려보내지 않고 받아서 프리셋 탭으로 안내한다.
        const n = importPresets(data.presets);
        acct(T("프리셋 {n}개를 가져왔습니다 — «프리셋» 탭에서 보세요.", { n }), "ok");
      } else {
        throw new Error(T("모르는 형식입니다 — ")
                        + T("CSV · raw.json · 내보낸 스펙/기록/프리셋이어야 합니다."));
      }
    } catch (e) {
      acct(`${f.name}: ${String(e.message || e)}`, "err");
    }
  }
}

async function resync(rec) {
  if (!HEALTH.fetch) {
    return acct(T("이 서버는 URL 동기화를 끄고 실행됐습니다 — 북마클릿이나 CSV를 쓰세요."), "err");
  }
  if (!rec.openid) {
    return acct(T("이 스펙에는 openid가 없어 다시 싱크할 수 없습니다 (CSV·임포트 출처)."), "err");
  }
  if (rec.syncing) return;              // 두 번 눌러 두 번 조회하지 않는다
  rec.syncing = true;
  renderProfiles();
  acct(T("{name} 다시 받는 중…", { name: rec.name }));
  try {
    // area를 같이 보낸다 — 안 보내면 전체 지역을 다시 훑어서, 계정에 지역이 하나
    // 더 늘었을 때 이 스펙이 엉뚱한 지역으로 튈 수 있다. 처음 고른 지역에 고정한다.
    const raws = await fetchQueued({ openid: rec.openid, area: rec.area },
      (m) => acct(`${rec.name} — ${m}`));
    const out = await convertRaw(raws[0], rec.name);
    // 최신으로 덮되 **수정본은 남긴다** — 별 레이어라 그대로 살아남는다
    rec.fetched = out.profile;
    rec.notices = out.notices;
    // **이름은 건드리지 않는다.** 사용자가 붙인 이름을 싱크할 때마다 갈아 끼우면
    // 어느 스펙이 어느 것이었는지 알 수 없게 된다. 바뀐 건 «최종 업데이트»뿐이다.
    rec.fetched_at = out.profile?._meta?.fetched_at || new Date().toISOString();
    rec.synced_at = new Date().toISOString();
    results = {};
    saveAll(); renderProfiles(); renderAll();
    const n = Object.keys(rec.edits?.chars || {}).length;
    acct(T("최신으로 덮었습니다 ({v}).", { v: when(rec.synced_at) })
         + (n ? T(" 수정본 {n}명은 그대로 유지됩니다.", { n }) : ""), "ok");
  } catch (e) {
    acct(String(e.message || e), "err");
  } finally {
    rec.syncing = false;
    renderProfiles();
  }
}

function renderProfiles() {
  const wrap = $("#prof-list");
  wrap.textContent = "";
  const list = Object.values(state.profiles);
  if (!list.length) {
    wrap.append(el("p", "prose prose-sm",
      T("아직 저장된 스펙이 없습니다. 위에서 레츠도로 CSV를 놓거나 북마클릿으로 받아 오세요.")));
    return;
  }
  for (const rec of list) {
    const box = el("div", "prof" + (rec.id === state.settings.profileId ? " on" : ""));
    const top = el("div", "prof-top");
    top.append(el("b", "prof-name", rec.name));
    const nEdit = Object.keys(rec.edits?.chars || {}).length;
    // openid 꼬리는 적지 않는다 — 스크린샷으로 새어 나가던 자리다(위 `블라 (지역)` 주석)
    top.append(el("span", "prof-meta",
      T("{v}종", { v: Object.keys(rec.fetched?.chars || {}).length })
      + T(" · {source} · 수집 {v}", { source: rec.source, v: when(rec.fetched_at) })
      + (rec.synced_at ? T(" · 최종 갱신 {v}", { v: when(rec.synced_at) }) : "")
      + (nEdit ? T(" · 수정 {nEdit}명", { nEdit }) : "")));
    const acts = el("div", "prof-acts");
    acts.append(mkBtn(rec.id === state.settings.profileId ? T("사용 중") : T("사용"), "btn-primary",
      () => {
        state.settings.profileId = rec.id;
        saveAll(); renderProfiles(); renderProfilePick(); renderAll();
      }, rec.id === state.settings.profileId));
    // 다시 싱크는 **가능할 때만** 보인다. CSV·임포트 출처에는 openid가 없고, URL 조회를
    // 끈 서버에서는 눌러 봐야 빨간 오류만 나온다 — 못 하는 일을 버튼으로 두지 않는다.
    // 다시 싱크는 **서버 조회로 받은 스펙에만** 단다. 북마클릿으로 받은 것을 서버로
    // 다시 받으면 운영자 세션을 타는 다른 경로가 되고(비공개 계정이면 실패한다),
    // 애초에 북마클릿은 «다시 눌러서» 새로 받는 게 그쪽의 갱신 방법이다.
    if (HEALTH.fetch && rec.openid && rec.source === "블라링크") {
      const b = mkBtn(rec.syncing ? T("받는 중…") : T("다시 싱크"), "btn-ghost",
                      () => resync(rec));
      b.disabled = !!rec.syncing;
      acts.append(b);
    }
    // 내보내기는 **계정에서 받은 원본(fetched)만** `chars`에 담는다. 예전에는
    // `deepMerge(fetched, edits)`를 내보냈는데, 그러면 카드 톱니로 고친 값이 계정
    // 실측값인 것처럼 섞여 나가고 — 다시 불러오면 그게 통째로 `fetched`가 되어
    // **수정본이 원본으로 굳는다.** 실제로 이 파일로 대조하다 드레이크 우코가
    // 44.31%(수정본)로 보여 계산이 어긋난 적이 있다.
    // 수정본은 버리지 않고 `_edits`에 따로 실어 왕복(내보내기→불러오기)을 보존한다.
    acts.append(mkBtn(T("내보내기"), "btn-ghost", () => {
      const doc = { ...rec.fetched };
      if (Object.keys(rec.edits?.chars || {}).length || rec.edits?._account) {
        doc._edits = rec.edits;
      }
      downloadJson(doc, T("니케스펙-{name}", { name: rec.name }));
    }));
    acts.append(mkBtn(T("이름 변경"), "btn-ghost", () => {
      askRename(box, T("스펙 이름"), rec.name, NAME_MAX, (v) => {
        rec.name = v;
        // 사람이 직접 지은 이름은 자동 정리(아래 openid 꼬리 제거)가 건드리지 않는다
        rec.renamed = true;
        saveAll(); renderProfiles(); renderProfilePick();
      });
    }));
    acts.append(mkBtn(T("삭제"), "btn-ghost", () => {
      askInline(box, T("«{name}» 스펙을 지웁니다. 되돌릴 수 없습니다.", { name: rec.name }), T("지우기"), () => {
        delete state.profiles[rec.id];
        if (state.settings.profileId === rec.id) state.settings.profileId = "";
        saveAll(); renderProfiles(); renderProfilePick(); renderAll();
        acct(T("«{name}» 스펙을 지웠습니다.", { name: rec.name }), "ok");
      });
    }));
    top.append(acts);
    box.append(top);
    for (const n of rec.notices || []) {
      box.append(el("p", "prof-notice" + (n.level === "warn" ? " warn" : ""), n.text));
      if (n.names?.length) {
        const d = el("details", "prof-names");
        d.append(el("summary", null, T("대상 {length}종 보기", { length: n.names.length })));
        d.append(el("div", "name-chips", n.names.map(T).join(" · ")));
        box.append(d);
      }
    }
    wrap.append(box);
  }
}

function renderProfilePick() {
  syncAcctCog();
  const sel = $("#profile-pick");
  sel.textContent = "";
  const o = el("option", null, "고정 스펙");
  o.value = "";
  sel.append(o);
  for (const rec of Object.values(state.profiles)) {
    const x = el("option", null, rec.name);
    x.value = rec.id;
    sel.append(x);
  }
  sel.value = state.settings.profileId || "";
}

// ── 니케별 육성 시트 ────────────────────────────────────────────────────
let sheetName = null;

function openSheet(name) {
  const sp = charSpec(name);
  if (!sp) return;
  // 다른 설정창(육성 수정)을 여는 순간 「집어 든」 카드는 뜻을 잃는다 — 안 놓아 두면
  // 이 창을 닫고 나서도 머리글에 ««이름» — 놓을 슬롯을 누르세요»가 계속 떠 있다.
  if (picked) { picked = null; setStatus("", false); }
  sheetName = name;
  const rec = byName.get(name);
  $("#edit-title").textContent = name;
  $("#edit-sub").textContent = `${rec?.element ?? ""} · ${rec?.cls ?? ""} · ${rec?.weapon ?? ""}`
    + (isEdited(name) ? T(" · 수정됨") : "");
  const th = $("#edit-thumb");
  th.textContent = "";
  if (rec?.img) {
    const i = el("img"); i.src = artSrc(rec, name); i.alt = ""; th.append(i);
  }
  buildSheet(name, sp);
  $("#edit-revert").disabled = !isEdited(name);
  $("#edit-sheet").showModal();
}

/** 수정본에 한 값을 쓴다. `rebuild=false`면 여러 값을 연달아 쓸 때 마지막에만 다시 그린다. */
function setEdit(name, path, value, rebuild = true) {
  const rec = activeRec();
  if (!rec) return;
  rec.edits.chars ||= {};
  const e = (rec.edits.chars[name] ||= {});
  let node = e;
  for (const k of path.slice(0, -1)) node = (node[k] ||= {});
  node[path[path.length - 1]] = value;
  results = {};                      // 지문이 바뀐다 — 옛 결과를 남기지 않는다
  saveAll();
  if (rebuild) {
    buildSheet(name, charSpec(name));
    $("#edit-revert").disabled = !isEdited(name);
    renderAll();
  }
}

function revertChar(name) {
  const rec = activeRec();
  if (!rec?.edits?.chars?.[name]) return;
  delete rec.edits.chars[name];
  results = {};
  saveAll();
  buildSheet(name, charSpec(name));
  $("#edit-revert").disabled = true;
  renderAll();
}

function buildSheet(name, sp) {
  const rec = byName.get(name);
  const body = $("#edit-body");
  body.textContent = "";

  // ① 돌파 + 코강 — 인게임처럼 한 줄 11칸 (별 3 다음이 코강으로 이어진다)
  const bt = sp.breakthrough ?? 0, core = sp.core_enhancement ?? 0;
  body.append(group(T("돌파 · 코어 강화"), [stepsEl(11, (i) => ({
    label: i <= 3 ? ("★".repeat(i) || "0") : `+${i - 3}`,
    on: i <= 3 ? (core === 0 && bt === i) : core === i - 3,
    star: i <= 3,
    onclick: () => {
      setEdit(name, ["breakthrough"], Math.min(i, 3), false);
      setEdit(name, ["core_enhancement"], Math.max(0, i - 3));
    },
  }))]));

  // ② 스킬 1·2·버스트
  const sk = sp.skill_levels || {};
  const skGrp = el("div", "grp");
  skGrp.append(el("span", "grp-label", "스킬 레벨"));
  for (const s of ["1", "2", "3"]) {
    const idx = s === "3" ? 2 : Number(s) - 1;
    const info = rec?.skills?.[idx];
    const curLv = sk[s] ?? 1;
    const row = el("div", "grp-row");
    const label = el("span", "ol-part", s === "3" ? T("버스트") : T("스킬{s}", { s }));
    // 이름 + **지금 레벨**의 효과 — 라벨에 올리면 「이게 뭐 하는 스킬인지」가 바로 나온다
    label.title = [T(info?.name), skillEffectText(info, curLv)].filter(Boolean).join("\n");
    row.append(label);
    row.append(stepsEl(10, (i) => ({
      label: String(i + 1), on: curLv === i + 1,
      // 레벨 버튼 하나하나에 **그 레벨**의 효과를 미리 보여 준다 — 안 눌러도
      // 지나가며 몇 레벨이 좋을지 비교할 수 있다.
      title: [info?.name && `${T(info.name)} (Lv.${i + 1})`, skillEffectText(info, i + 1)]
        .filter(Boolean).join("\n"),
      onclick: () => setEdit(name, ["skill_levels", s], i + 1),
    })));
    skGrp.append(row);
  }
  body.append(skGrp);

  // ③ 호감도 · 소장품 · 애장품 · 큐브
  const misc = el("div", "grp");
  misc.append(el("span", "grp-label", "호감도 · 소장품 · 큐브"));
  misc.append(rowSelect(T("호감도"), Array.from({ length: 40 }, (_, i) => [i + 1, `${i + 1}`]),
    sp.affinity ?? 30, (v) => setEdit(name, ["affinity"], Number(v))));
  misc.append(rowSelect(T("소장품"), COLL_STAGES.map((s) => [s, s]),
    sp.collection_stage ?? "없음", (v) => setEdit(name, ["collection_stage"], v)));
  if (sp.favorite_stage != null) {
    misc.append(rowSelect(T("애장품"), [[0, T("없음")], [1, T("1단계")], [2, T("2단계")], [3, T("3단계")]],
      sp.favorite_stage, (v) => setEdit(name, ["favorite_stage"], Number(v))));
  }
  const cubes = MAPS?.cube_info || {};
  const cubeNames = Object.keys(cubes).filter((c) => c !== "공통").sort();
  if (cubeNames.length) {
    const curCube = sp.cube?.name ?? "렐릭 베어 큐브";
    const curLv = sp.cube?.level ?? 15;
    misc.append(rowSelect(T("큐브"), cubeNames.map((c) => [c, c]), curCube,
      (v) => setEdit(name, ["cube", "name"], v)));
    // 레벨 0 = **미장착**. 예전엔 1~15만 고를 수 있어 «큐브를 안 낀 니케»를
    // 표현할 방법이 없었고, 그래서 전원 렐릭 베어 Lv15로 계산됐다. 큐브 공통
    // 스킬(안티 코드 HC)이 우월 코드 대미지를 최대 +19.09% 주므로, 우코가 켜지는
    // 니케는 이 허수만으로 딜이 크게 부풀었다(2026-08-24 실측 대조).
    misc.append(rowSelect(T("큐브 레벨"),
      [[0, T("없음")], ...Array.from({ length: 15 }, (_, i) => [i + 1, `${i + 1}`])],
      curLv, (v) => setEdit(name, ["cube", "level"], Number(v))));
    // 이름만 두면 무슨 큐브인지 알 수 없다 — 효능을 그 레벨의 수치로 적어 준다
    misc.append(el("p", "cube-eff", cubeEffect(curCube, curLv)));
  }
  body.append(misc);

  // ④ 장비 4부위 + 오버로드 12줄
  const eq = sp.equipment || {};
  const eqGrp = el("div", "grp");
  eqGrp.append(el("span", "grp-label", "장비 · 오버로드"));
  // 4부위를 한 줄씩 쌓으면 16줄짜리 긴 목록이 된다. 부위마다 한 칸으로 묶어
  // 격자에 올리면 폭에 따라 **2열 또는 4열**로 접힌다.
  const eqGrid = el("div", "eq-grid");
  eqGrp.append(eqGrid);
  PARTS.forEach((part, pi) => {
    const cell = el("div", "eq-part");
    eqGrid.append(cell);
    const cur = eq[part] || {};
    const isCorp = cur.tier == null || cur.tier === "기업";
    const row = el("div", "grp-row");
    row.append(el("span", "ol-part", part));
    row.append(selectEl(equipOptions(rec?.corp), equipValue(cur), (v) => {
      const next = normalizeOl(charSpec(name)._ol);
      // 기업(오버로드) 장비가 아니면 오버로드 줄이 없다 — 함께 비운다. T9 기업은
      // 오버로드와 별개라 여기서도 비워야 한다(§isCorp).
      if (!v.startsWith("기업")) next[pi] = [null, null, null];
      setEdit(name, ["equipment", part], parseEquip(v, rec?.corp), false);
      setEdit(name, ["_ol"], next, false);
      setEdit(name, ["equip_skills"], deriveEquipSkills(next));
    }));
    cell.append(row);

    const ol = normalizeOl(sp._ol);
    for (let li = 0; li < 3; li++) {
      const line = ol[pi][li];
      const r = el("div", "ol-row");
      r.append(selectEl([["", T("빈 줄")], ...OL_OPTS], line?.o ?? "", (v) => {
        const next = normalizeOl(charSpec(name)._ol);
        next[pi][li] = v ? { o: v, l: line?.l ?? 15 } : null;
        setEdit(name, ["_ol"], next, false);
        setEdit(name, ["equip_skills"], deriveEquipSkills(next));
      }, !isCorp));
      r.append(selectEl(Array.from({ length: 15 }, (_, i) => [i + 1, T("{v}단계", { v: i + 1 })]),
        line?.l ?? 15, (v) => {
          const next = normalizeOl(charSpec(name)._ol);
          if (next[pi][li]) next[pi][li].l = Number(v);
          setEdit(name, ["_ol"], next, false);
          setEdit(name, ["equip_skills"], deriveEquipSkills(next));
        }, !isCorp || !line?.o));
      r.append(el("span", "ol-val", line?.o ? `${pct(line.o, line.l)}%` : ""));
      cell.append(r);
    }
  });
  body.append(eqGrp);

  // ⑤ 유도된 합산 — 계산에 실제로 들어가는 값.
  // **항목마다 한 줄**이다. 가운뎃점으로 이어 붙이면 여섯 항목이 한 덩어리로 보여
  // 어떤 수치가 무엇인지 훑을 수가 없다.
  const agg = sp.equip_skills || {};
  const rows = EQUIP_KEYS.filter((k) => {
    const v = agg[k];
    return Array.isArray(v) ? v.length : v;
  }).map((k) => {
    const v = agg[k];
    const li = el("div", "sum-row");
    li.append(el("span", "sum-k", OL_LABEL[k]));
    // 최대 장탄·차지 속도는 줄별로 따로 반올림되므로 합치지 않고 그대로 보여 준다
    li.append(el("b", "sum-v", `${Array.isArray(v) ? v.join(" + ") : v}%`));
    return li;
  });
  const box = el("div", "sum-list");
  if (rows.length) rows.forEach((r) => box.append(r));
  else box.append(el("p", "sum-row", "없음"));
  body.append(group(T("계산에 들어가는 합산"), [box]));
}

/** 큐브가 올리는 스탯 이름만. 고르는 자리에 쓰는 짧은 라벨이다 —
 *  `template`("재장전 속도 {0}% ▲")에서 수치·화살표를 떼고 남긴다.
 *  표가 없거나 문구가 비면 큐브 이름으로 돌아간다. */
function cubeStatLabel(cubeName) {
  // 아래 조건절 떼기는 한국어 문장 규칙이다 — 다른 언어는 큐브 이름으로 보인다
  if (I18N.lang !== "ko") return T(cubeName);
  const t = MAPS?.cube_info?.[cubeName]?.template;
  const s = String(t || "")
    // 조건절을 뗀다 — "전투 시작 시 재장전 속도 {0}% ▲"에서 남길 건 «재장전 속도»뿐이다.
    // 이걸 안 떼면 고르개가 좁아 전부 "전투 시작 시"로만 보인다(실측).
    .replace(/^전투 시작 시\s*/, "")
    .replace(/^\d+발 사격 시\s*/, "")
    .replace(/^착용자의[\s\S]*?때\s*/, "")
    // 수치 자리와 단위·화살표·지속시간 꼬리를 뗀다
    .replace(/\{0\}\s*(%|발|초)?/g, "")
    .replace(/\d+초\s*유지/g, "")
    .replace(/[▲▼]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return s || cubeName;
}

/** 큐브 효능 한 줄. `cube.json`의 `스킬명`·`template`·레벨별 수치로 문장을 만든다. */
function cubeEffect(cubeName, lv) {
  // 레벨 0은 미장착 — 표에 0이 없어 그대로 두면 `{0}`이 안 채워진 문장이 나온다.
  if (!Number(lv)) return T("미장착 — 큐브 효과 없음");
  const c = MAPS?.cube_info?.[cubeName];
  if (!c) return "";
  const vals = c.values?.[String(lv)];
  const v = Array.isArray(vals) ? vals[0] : vals;
  const tpl = T(String(c.template || ""));          // 현지어 템플릿(사전에 있으면)
  const txt = tpl && v != null ? tpl.replace("{0}", v) : tpl;
  // ▲는 «증가»를 뜻하는데 여기 나오는 값은 전부 증가라 구분에 쓰이지 않는다 — 잡음이다.
  return `${T(c.skill || "")} — ${txt}`.replace(/\s*[▲▼]/g, "").trim();
}

const normalizeOl = (ol) =>
  Array.from({ length: 4 }, (_, i) =>
    Array.from({ length: 3 }, (_, j) => (ol?.[i]?.[j] ? { ...ol[i][j] } : null)));
const pct = (key, lv) => {
  const t = MAPS?.skill_table?.[key];
  return t ? +(t[(lv || 1) - 1] * 100).toFixed(4) : 0;
};
/** 12줄 → 계산기 equip_skills. profile_convert._equip_skills와 같은 규칙이어야 한다. */
function deriveEquipSkills(ol) {
  const out = {};
  for (const k of EQUIP_KEYS) out[k] = PER_LINE.has(k) ? [] : 0;
  for (const part of ol) {
    for (const line of part) {
      if (!line?.o) continue;
      const v = pct(line.o, line.l);
      if (PER_LINE.has(line.o)) out[line.o].push(v);
      else out[line.o] = +(out[line.o] + v).toFixed(4);
    }
  }
  for (const k of PER_LINE) out[k].sort((a, b) => b - a);
  return out;
}

// T9 기업 장비는 제조사가 캐릭터 기업과 같아야 +30%가 붙는다(calculator/base_stat.py
// _equip_stat). 여기서는 그 매치 보너스를 받는 조합(캐릭터 자기 기업 제조사)만 고를 수
// 있게 한다 — 안 맞는 조합을 시험하고 싶으면 프로필 동기화로 실제 장비를 들여온다.
const equipOptions = (corp) => [
  ["없음", T("미장착")],
  ...Array.from({ length: 6 }, (_, i) => [T("기업{i}", { i }), T("오버로드 강화 {i}", { i })]),
  ...(corp
    ? Array.from({ length: 6 }, (_, i) => [T("T9기업{i}", { i }), T("T9 기업({corp}) 강화 {i}", { corp, i })])
    : []),
  ...Array.from({ length: 9 }, (_, i) => [`T${i + 1}`, T("일반 T{v}", { v: i + 1 })]),
];
const equipValue = (cur) =>
  cur.tier === "없음" ? T("없음")
    : cur.tier === "T9" && cur.corp ? T("T9기업{v}", { v: cur.level ?? 0 })
    : cur.tier && cur.tier !== "기업" ? cur.tier
    : T("기업{v}", { v: cur.level ?? 0 });
/** 고른 값 → 장비 한 부위.
 *
 *  **기업(오버로드)을 고를 때 `tier`를 반드시 같이 쓴다.** 수정본은 원본에 `deepMerge`로
 *  얹히므로 `{level: N}`만 쓰면 원본의 `tier: "T7"`이 그대로 남아, 고르는 순간 「일반
 *  T7」로 되돌아간 것처럼 보였다(오버로드 줄도 계속 잠겨 있었다). 계산기 쪽 규칙은
 *  `calculator/base_stat.py _equip_stat`이다 — `tier`가 없거나 «기업»이면 오버로드
 *  장비이고 그때만 `level`을 본다. T9 기업은 `tier: "T9"` + `corp` + `level`을 함께
 *  쓴다. 일반·미장착에서는 `level`이 남아 있어도 보지 않는다. */
const parseEquip = (v, corp) =>
  v === "없음" ? { tier: "없음" }
    : v.startsWith("T9기업") ? { tier: "T9", level: Number(v.slice(4)), corp, _track: "T9" }
    : v.startsWith("기업") ? { tier: "기업", level: Number(v.slice(2)) }
    : { tier: v };

// ── 전투력 계산기 ───────────────────────────────────────────────────────────
// 니케 하나의 육성 옵션을 자유로 바꿔 가며 전투력·스탯을 본다. 값은 **계정 스펙과
// 분리된 샌드박스**다 — 여기서 무엇을 만져도 스펙·덱에는 아무 영향이 없다.
// 계산은 서버(/api/cp)가 한다. 산식은 서버에만 있고 브라우저에는 결과만 온다.
let coop = null;           // 샌드박스 상태. 스펙과 무관하게 마음대로 바뀐다
let coopTimer = 0;         // 연타 흡수 — 마지막 조작 후 120ms 지나면 한 번만 보낸다
let coopSeq = 0;           // 늦게 도착한 옛 응답이 새 결과를 덮지 않게
let coopLastCp = null;     // 직전 전투력 — «+XX» 효과의 비교 기준. 캐릭터를 바꾸면 비운다

// 필터 바 DOM은 편성·전투력 계산기가 공유하지만(moveFilterBar), **상태는 안 섞는다**.
// inCoop이 지금 그 DOM이 어느 쪽에 붙어 있는지를 말해 준다 — 바 자체의 클릭 핸들러는
// 한 번만 묶이므로(붙는 곳이 바뀌어도 같은 함수가 계속 불린다), 매번 이 값으로 어느
// state를 읽고 쓸지, 어느 목록을 다시 그릴지 정한다.
let inCoop = false;
const curFilter = () =>
  (inCoop ? state.coopFilter : modeNow() === "union" ? uFilter() : state.filter);

/** 편성 탭의 필터 바를 전투력 계산기로 옮겨 붙인다(또는 되돌린다).
    복제하지 않는 이유: 두 벌이 되면 «표시»가 조용히 갈린다(칩 색깔 등). 대신 **상태**는
    처음부터 둘로 나눠 두고(state.filter / state.coopFilter) 이 함수가 옮길 때마다 바를
    지금 붙는 쪽의 상태로 다시 그린다 — 그래서 편성에서 건 필터가 전투력 계산기까지
    새어 들지 않는다. */
function moveFilterBar(toCoop) {
  inCoop = toCoop;
  const bar = document.querySelector(".filter-bar");
  if (bar) {
    const slot = toCoop ? $("#coop-filter-slot") : document.querySelector(".roster");
    if (slot && bar.parentElement !== slot) {
      if (toCoop) slot.append(bar);
      else slot.prepend(bar);
    }
  }
  // 스펙 고르개(+콘솔 톱니)도 같이 옮긴다 — 전투력 계산기도 «어느 계정으로 보나»가
  // 시작값을 정하므로 여기서 바로 바꿀 수 있어야 한다.
  const field = $("#profile-pick")?.closest(".field");
  const cog = $("#acct-cog");
  const home = document.querySelector(".stage-head");
  // 전투력 계산기에서는 **니케 검색 왼쪽**에 붙인다 — 필터 바 맨 앞이 그 자리다
  const slot2 = toCoop ? document.querySelector(".filter-bar") : home;
  if (field && slot2 && field.parentElement !== slot2) {
    if (toCoop) {
      const first = slot2.firstElementChild;
      slot2.insertBefore(field, first);
      if (cog) slot2.insertBefore(cog, first);
    } else {
      // 원래 자리는 덱 탭 줄의 «덱 번호» 앞이다
      const tabs = document.querySelector("#deck-tabs");
      home.insertBefore(field, tabs);
      if (cog) home.insertBefore(cog, tabs);
    }
  }
  // 바가 방금 어느 쪽으로 붙었든, 그 상태(curFilter())로 칩·검색어를 다시 맞춘다.
  const q = $("#q");
  if (q) q.value = curFilter().q;
  buildFilters();
}

// 레벨 상한 — 표가 1400까지 있다(실측). 게임이 늘리면 표와 이 값만 올린다.
const LV_MAX = 1400;
// 콘솔(재활용 연구실)은 게임 데이터에 상한이 없다 — 표(RecycleResearchStatTable)는
// 타입별 레벨당 증가치 한 줄뿐이고(공통 hp 450 · 역할군 hp 750/def 5 · 기업 atk 25/def 5)
// 완전히 선형이라 상한을 안 둬도 계산은 어긋나지 않는다. 그래도 오타로 말도 안 되는
// 값이 들어가지 않게 **현실적인 한계**만 둔다. UI와 서버가 같은 값을 쓴다.
const CONSOLE_MAX_LV = 1000;
// 오버로드 줄 라벨 등급 — **12단계부터** 블루, 15단계가 블랙 (유저 확인).
// 11단계는 일반이다. 게임이 바뀌면 이 두 값만 고치면 된다.
const OL_BLUE_FROM = 12;
const OL_BLACK_FROM = 15;
let coopWired = false;

function coopEnsure() {
  moveFilterBar(true);
  if (!coopWired) {
    coopWired = true;
    $("#coop-reset").onclick = () => { if (coop) coopLoad(coop.name); };
    $("#coop-back").onclick = coopBack;
    // **좌우로 끌어서** 앞·뒤 니케로 넘어간다 — 지금 목록(필터 적용분) 순서 그대로.
    const art = $("#coop-screen");
    let dragX = null;
    let dragY = null;
    art.addEventListener("pointerdown", (e) => {
      // 정보판·버튼 위에서 시작한 것은 조작이지 넘김이 아니다
      if (e.target.closest(".cp-side, .cp-rail, .cp-star, .cp-back")) return;
      // 끄는 동안 판 밖 글자까지 파랗게 긁히는 걸 막는다. pointerdown 기본동작이
      // «선택 시작»이라 그것부터 끊고, 이미 잡혀 있던 선택도 지운다.
      e.preventDefault();
      document.body.classList.add("cp-dragging");
      try { getSelection()?.removeAllRanges(); } catch { /* 선택이 없으면 그만 */ }
      dragX = e.clientX; dragY = e.clientY;
      art.classList.add("dragging");
      try { art.setPointerCapture(e.pointerId); } catch { /* 캡처 못 해도 동작한다 */ }
    });
    // 끄는 동안 일러가 손을 따라온다 — 넘어가는 중인지 눈으로 알 수 있게
    art.addEventListener("pointermove", (e) => {
      if (dragX === null) return;
      const dx = e.clientX - dragX;
      if (Math.abs(dx) < Math.abs(e.clientY - dragY)) return;   // 세로면 스크롤이다
      const a = $("#coop-art");
      a.style.transform = `translateX(${dx * 0.35}px)`;
      a.style.opacity = String(Math.max(0.45, 1 - Math.abs(dx) / 420));
    });
    art.addEventListener("dragstart", (e) => e.preventDefault());
    const dragEnd = () => {
      document.body.classList.remove("cp-dragging");
      art.classList.remove("dragging");
      const a = $("#coop-art");
      a.style.transform = "";
      a.style.opacity = "";
    };
    art.addEventListener("pointerup", (e) => {
      try { art.releasePointerCapture(e.pointerId); } catch { /* 이미 풀렸다 */ }
      dragEnd();
      if (dragX === null) return;
      const dx = e.clientX - dragX, dy = e.clientY - dragY;
      dragX = dragY = null;
      // 세로로 더 많이 움직였으면 스크롤이다 — 넘기지 않는다
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) coopStep(dx < 0 ? 1 : -1);
    });
    art.addEventListener("pointercancel", () => { dragX = dragY = null; dragEnd(); });
    $("#coop-eq-x").onclick = () => $("#coop-eq").close();
    renderCoopPool();
  }
  if (!HEALTH.cp) {
    coopMsg(T("전투력 계산기는 서버 계산이 필요합니다 — 지금 서버에 연결할 수 없어 쓸 수 없습니다."),
            "err");
  }
}

/** 아래쪽 니케 고르개. 편성 탭과 **같은 카드**를 쓴다 — 두 화면이 달라 보이면 안 된다. */
function renderCoopPool() {
  const wrap = $("#coop-pool");
  wrap.textContent = "";
  const list = filteredRoster(true, state.coopFilter);
  for (const rec of list) {
    const c = card(rec.name);
    c.onclick = () => coopLoad(rec.name);
    c.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); coopLoad(rec.name); }
    };
    wrap.append(c);
  }
}

/** 소장품·애장품 **인게임 그림** 파일명. 애장품은 캐릭터 전용이라 이름으로,
    소장품(R·SR)은 무기군 공용이라 등급+무기군으로 찾는다. 없으면 null. */
function favIconFile(name, stage) {
  const ic = MAPS?.fav_icons;
  if (!ic || !stage || stage === "없음") return null;
  const short = String(name).split(" : ")[0];
  // 애장품(캐릭터 전용) 그림은 **SR15로 둔 때만** — 단계를 바꾸면 그림도 바뀌어야 한다
  if (stage === "SR15" && ic.by_char?.[short]) return ic.by_char[short];
  const grade = /^SR/.test(stage) ? "SR" : "R";
  const weapon = byName.get(name)?.weapon;
  return ic.by_kind?.[`${grade}_${weapon}`] || null;
}

/** 인게임 장비·큐브·스킬 아이콘 (`scraper/cdn_ui_icons.py` 수집). 없으면 null. */
const uiIcon = (kind, key) => MAPS?.ui_icons?.[kind]?.[key] || null;
const iconImg2 = (file) => {
  if (!file) return null;
  const im = el("img");
  im.src = `image/icon/${file}`;
  im.alt = "";
  im.draggable = false;
  return im;
};
const iconImg = (file, cls) => {
  if (!file) return null;
  const im = el("img", cls);
  im.src = `image/ui/${file}`;
  im.alt = "";
  im.draggable = false;
  return im;
};

function coopMsg(text, level) {
  const n = $("#coop-msg");
  n.textContent = text || "";
  n.className = "acct-msg" + (level === "err" ? " err" : level === "ok" ? " ok" : "");
}

/** 계정 스펙(있으면)에서 시작값을 만든다. 없으면 기본 스펙(만렙 육성) 값. */
function coopDefaults(name) {
  const sp = charSpec(name);
  const prof = mergedProfile();
  const con = consoleNow();
  const rec = byName.get(name);
  // 우선순위 3단.
  // ① **이 캐릭터에** 직접 지정한 큐브 — 「육성 수정」 시트의 `sp.cube`.
  //    이전엔 이걸 통째로 무시해서, 캐릭터마다 따로 골라 둬도 전투력 계산기가
  //    조용히 무시하고 계정 집계로 덮어써 「캐릭터를 바꿔도 큐브가 안 바뀐다」로
  //    보였다(사용자 실측 재현).
  // ② 프로필의 «장착 중인 큐브에서 관찰된 종류별 레벨» — 계정 전체 집계라
  //    캐릭터별이 아니다(profile_convert.py `_observed_cubes`). 레벨만 보고
  //    이름은 아이콘 목록 첫 번째로 채웠던 게 예전 버그 — 그 목록은 이 계정과
  //    무관한 게임 전체 표라 항상 같은("렐릭 어설트") 큐브로 고정돼 보였다.
  //    레벨과 이름을 **같은 관찰값**에서 함께 뽑아야 짝이 맞는다.
  // ③ 그마저 없으면(정보 전무) 게임 전체 목록의 첫 큐브.
  const cubes = prof?._account?.cubes || {};
  const cubeEntries = Object.entries(cubes).sort((a, b) => b[1] - a[1]);
  const [obsCubeName, obsCubeLv] = cubeEntries[0] || [];
  // **전투력은 «지금 실제 상태»다.** 편성(딜 시뮬)은 큐브를 갈아끼우는 자원으로 보고
  // 러너 기본값(렐릭 베어 Lv15)을 일괄로 쓰지만, 이 화면은 «내 니케 전투력»이라
  // 안 꼈으면 안 낀 값이 나와야 한다. 그래서 싱크가 사실만 적어 둔 `_cube`를 본다
  // (`_` 접두라 시뮬에는 안 넘어간다 — profile_convert.py 주석).
  // 우선순위: 카드에서 직접 지정 → 인게임 실제 장착(_cube) → 관찰된 보유 → 15.
  const cubeLv = sp?.cube?.level ?? sp?._cube?.level
    ?? (cubeEntries.length ? Number(obsCubeLv) : 15);
  const sk = sp?.skill_levels || {};
  // 스펙이 없을 때의 오버로드 — 사이트 고정 스펙과 같은 합계 (부위 배치는 전투력에 무관)
  const defaultOl = [
    [{ o: "element_bonus", l: 10 }, { o: "atk_pct", l: 10 }, { o: "max_ammo_pct", l: 15 }],
    [{ o: "element_bonus", l: 10 }, { o: "atk_pct", l: 10 }, { o: "max_ammo_pct", l: 4 }],
    [{ o: "element_bonus", l: 10 }, null, null],
    [{ o: "element_bonus", l: 10 }, null, null],
  ];
  return {
    name,
    level: Number(prof?._account?.synchro_level) || 200,
    grade: sp?.breakthrough ?? 3,
    core: Math.min(7, sp?.core_enhancement ?? 0),
    affinity: sp?.affinity ?? 30,
    s1: sk["1"] ?? 10, s2: sk["2"] ?? 10, ub: sk["3"] ?? 10,
    cube_lv: cubeLv,
    cube_name: sp?.cube?.name || obsCubeName || Object.keys(MAPS?.ui_icons?.cube || {})[0] || "",
    coll_stage: sp?.collection_stage ?? "SR15",
    // 장비는 **`_eq`(원본: 단계·강화·제조사)** 를 쓴다 — 계산기용 `equipment`는
    // T1~T9의 강화·제조사를 버려서 전투력이 어긋난다 (profile_convert 참고).
    equipment: sp?._eq
      ? JSON.parse(JSON.stringify(sp._eq))
      : Object.fromEntries(PARTS.map((p) => [p, { t: 10, lv: 5, corp: null }])),
    ol: sp ? normalizeOl(sp._ol) : defaultOl,
    corp: rec?.corp || "",
    console: {
      common: conVal(con.common_level, null, 180),
      class: conVal(con.class_level, rec?.cls, 100),
      corp_lv: conVal(con.company_level, rec?.corp, 100),
    },
  };
}

/** 고르는 화면으로 되돌아간다 — 상세 층을 걷는다 (인게임 뒤로가기). */
function coopBack() {
  const dlg = $("#coop-eq");
  if (dlg?.open) dlg.close();
  $("#coop-screen").hidden = true;
  $("#coop-pick").hidden = false;
  coopMsg("");
  renderCoopPool();
  // 스크롤을 올려 주지 않으면 목록 중간이 보여 «뒤로 안 갔다»로 읽힌다
  document.querySelector('[data-panel="coop"]').scrollIntoView({ block: "start" });
}

/** 지금 목록 순서에서 앞·뒤 니케로 넘어간다 (인게임 좌우 화살표). */
function coopStep(d) {
  if (!coop) return;
  const list = filteredRoster(true, state.coopFilter);
  if (!list.length) return;
  const i = list.findIndex((r) => r.name === coop.name);
  const next = list[((i < 0 ? 0 : i + d) % list.length + list.length) % list.length];
  if (next) coopLoad(next.name);
}

function coopLoad(name, scroll = false) {
  const first = $("#coop-screen").hidden;
  coop = coopDefaults(name);
  coopLastCp = null;        // 다른 사람으로 바뀌었다 — 이번 값은 비교 대상이 없다
  coopMsg("");
  // 상세가 고르는 화면을 **대신한다** — 인게임처럼 한 번에 하나만 보인다
  $("#coop-pick").hidden = true;
  buildCoop();
  cpKick();
  // 목록에서 처음 들어올 때만 위로 올린다 — 좌우로 넘길 때마다 튀면 안 된다
  if (first || scroll) {
    document.querySelector('[data-panel="coop"]').scrollIntoView({ block: "start" });
  }
}

function cpKick() {
  clearTimeout(coopTimer);
  coopTimer = setTimeout(cpSend, 120);
}

/** JSON POST 한 번. 서버가 error를 돌려주면 그대로 예외로 올린다. */
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({ error: T("서버 응답을 읽지 못했습니다 ({status})", { status: r.status }) }));
  if (j.error) throw new Error(j.error);
  return j;
}

/** 전투력 옆에 «+XX»가 잠깐 떠올랐다 사라진다 — 옵션 하나 바꿀 때마다 바로
 *  체감되게, 인게임 스탯 강화 연출처럼. 늘어나면 파랑, 줄어들면 경고색.
 *  연달아 여러 번 바꾸면 이전 것이 사라지기 전에 새 것이 또 뜰 수 있다 —
 *  그때는 게임에서도 숫자가 겹쳐 뜨므로 자연스럽다. */
function showCpDelta(delta) {
  const numEl = $("#coop-cp");
  const host = numEl?.parentElement;
  if (!host || !delta) return;
  const tag = el("span", "cp-delta-fx" + (delta < 0 ? " down" : ""),
    `${delta > 0 ? "+" : ""}${delta.toLocaleString()}`);
  host.append(tag);
  // 숫자 자릿수가 계속 바뀌므로 고정 좌표로는 못 맞춘다 — **숫자 한가운데** 위로
  // 뜨게 실측해서 놓는다(협전 표시가 오른쪽에 바로 붙어 있어, 옆으로 붙이면 겹친다).
  const hostR = host.getBoundingClientRect(), numR = numEl.getBoundingClientRect();
  tag.style.left = `${numR.left - hostR.left + numR.width / 2}px`;
  tag.addEventListener("animationend", () => tag.remove());
}

async function cpSend() {
  if (!coop) return;
  const rec = byName.get(coop.name);
  const seq = ++coopSeq;
  try {
    const r = await fetch("/api/cp", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...coop, cls: rec.cls, weapon: rec.weapon,
        // 서버는 `console.corp`를 연구 레벨로 받는다 — 착용자 기업은 `corp`로 따로 간다
        console: { common: coop.console.common, class: coop.console.class,
                   corp: coop.console.corp_lv },
      }),
    });
    const j = await r.json();
    if (seq !== coopSeq) return;            // 그 사이 값이 또 바뀌었다 — 낡은 응답이다
    if (j.error) throw new Error(j.error);
    // 값이 바뀐 순간에만 «+XX» 효과를 띄운다. 캐릭터를 막 골랐을 때(첫 값)는
    // 비교할 이전 값이 없으니 조용히 넘어간다 — 안 그러면 고르자마자 전투력
    // 전체가 "증가"로 뜬다.
    const prevCp = coopLastCp;
    if (prevCp != null && j.cp !== prevCp) showCpDelta(j.cp - prevCp);
    coopLastCp = j.cp;
    $("#coop-cp").textContent = j.cp.toLocaleString();
    // 협전(레벨 40 고정) 전투력 — 실제 레벨과 무관하게 항상 옆에 보여 준다.
    const w40 = $("#coop-cp40-wrap");
    w40.hidden = j.cp40 == null;
    if (j.cp40 != null) $("#coop-cp40").textContent = j.cp40.toLocaleString();
    $("#coop-hp").textContent = j.hp.toLocaleString();
    $("#coop-atk").textContent = j.atk.toLocaleString();
    $("#coop-def").textContent = j.def.toLocaleString();
    coopMsg(rec.rare !== "SSR"
      ? T("R·SR 등급은 스탯 표가 SSR 기준이라 전투력이 실제보다 높게 나옵니다.") : "",
      rec.rare !== "SSR" ? "err" : "");
  } catch (e) {
    if (seq !== coopSeq) return;
    coopMsg(T("계산 실패 — {v}", { v: String(e.message || e) }), "err");
  }
}

/** 값 하나 바꾸기: 상태 수정 → 열린 탭만 다시 그림 → 재계산 예약. */
function coopSet(fn) { fn(); buildCoopPane(); cpKick(); }

const COOP_TABS = [["equip", T("장비")], ["skill", T("스킬")], ["cube", T("큐브")]];
let coopTab = "equip";

function buildCoop() {
  if (!coop) return;
  const c = coop, rec = byName.get(c.name);
  $("#coop-screen").hidden = false;

  // 전신 일러. 없으면 초상화로 물러난다 (`scraper/cdn_full.py`를 안 돌린 경우)
  const art = $("#coop-art");
  art.textContent = "";
  const img = el("img");
  img.alt = "";
  img.draggable = false;        // 없으면 브라우저 기본 이미지 드래그가 넘김을 삼킨다
  // 스킨을 입고 있으면 그 전신 일러로. **알파 경계도 그 그림 것을 써야 한다** —
  // 2048² 안에서 캐릭터가 앉은 자리가 코스튬마다 달라 기본 경계를 그대로 쓰면
  // 발이 잘리거나 붕 뜬다(`scraper/cdn_costume.py _add_bbox`).
  const cos = costumeOf(rec, c.name);
  const full = cos?.full ? `image/${cos.full}` : rec?.full ? `image/full/${rec.full}` : null;
  const fbb = cos?.full ? cos.fbb : rec?.fbb;
  img.onerror = () => { img.onerror = null; img.src = artSrc(rec, c.name); art.classList.remove("fit"); };
  img.src = full || artSrc(rec, c.name);
  art.classList.toggle("fit", !!(full && fbb));
  if (full && fbb) img.dataset.bb = fbb.join(",");
  else delete img.dataset.bb;
  art.append(img);
  fitCoopArt();
  // 판 내용(장비·스킬·큐브 목록)이 그려진 **뒤에** 한 번 더 맞춘다 — 무대 높이가
  // 판 길이를 따라가므로, 먼저 잰 값으로는 세로 위치가 위에 붙는다. 한 프레임으로도
  // 늦을 수 있어(이미지·글꼴) 판 자체의 높이 변화를 계속 지켜본다.
  settleCoopArt();
  watchCoopSide();

  // 주력 니케 = 즐겨찾기. 카드의 ★와 **같은 상태**를 쓴다
  const star = $("#coop-star");
  const isFav = state.favs.includes(c.name);
  star.classList.toggle("on", isFav);
  star.setAttribute("aria-pressed", String(isFav));
  star.title = isFav ? T("주력 니케 해제") : T("주력 니케로 지정");
  star.onclick = () => { toggleFav(c.name); buildCoop(); };

  const rare = $("#coop-rare");
  rare.textContent = rec?.rare || "";
  rare.className = `cp-rare rare-${String(rec?.rare || "").toLowerCase()}`;
  $("#coop-name").textContent = c.name;

  const lv = $("#coop-lv");
  lv.value = String(c.level);
  lv.onchange = () => {
    c.level = Math.max(1, Math.min(LV_MAX, Number(lv.value) || 1));
    lv.value = String(c.level);
    cpKick();
  };
  buildCoopGrade();
  const steps = $("#coop-lvsteps");
  steps.textContent = "";
  for (const [t, d] of [["-10", -10], ["-1", -1], ["+1", 1], ["+10", 10]]) {
    steps.append(mkBtn(t, "btn-ghost coop-step", () => {
      c.level = Math.max(1, Math.min(LV_MAX, c.level + d));
      lv.value = String(c.level);
      cpKick();
    }));
  }

  buildCoopRail();

  // 호감도 — 인게임에서 레벨 아래 «attraction RANK» 자리
  const attr = $("#coop-attr");
  attr.textContent = "";
  attr.append(el("span", "cp-attr-label", "호감도"));
  attr.append(selectEl(Array.from({ length: 40 }, (_, i) => [i + 1, `${i + 1}`]),
    c.affinity, (v) => coopSet(() => { c.affinity = Number(v); })));

  // 육각 줄 — 인게임 SQUAD 아래 자리. **4칸**: 코드 · 무기 · 역할군 콘솔 · 기업 콘솔.
  // 콘솔 두 칸은 아이콘으로 어느 쪽인지 알리고, 숫자를 눌러 바로 고친다.
  const hex = $("#coop-hex");
  hex.textContent = "";
  // 역할군·기업 아이콘은 **흰 선화**다 — 밝은 칩 위에서 사라지므로 표식을 달아
  // CSS에서 검게 뒤집는다. 속성 아이콘은 제 배경이 있어 손대지 않는다.
  const hexIcon = (file, label, mono) => {
    const h = el("span", "cp-hex");
    const im = iconImg2(file);
    if (im) { if (mono) im.classList.add("mono"); h.append(im); }
    else h.textContent = label || "";
    h.title = label || "";
    return h;
  };
  hex.append(hexIcon(ELEMENT_ICON[rec?.element], rec?.element));
  hex.append(hexIcon(null, rec?.weapon));
  for (const [key, file, label] of [
    ["common", null, T("공통 콘솔 레벨")],
    ["class", CLASS_ICON[rec?.cls], T("{v} 콘솔 레벨", { v: rec?.cls || T("역할군") })],
    ["corp_lv", CORP_ICON[rec?.corp], T("{v} 콘솔 레벨", { v: rec?.corp || T("기업") })],
  ]) {
    const box = el("label", "cp-hex cp-hex-lv");
    box.title = label;
    const im = iconImg2(file);
    if (im) { im.classList.add("mono"); box.append(im); }
    else box.append(el("span", "cp-hex-lb", "공통"));
    const inp = el("input");
    inp.type = "number"; inp.min = "0"; inp.max = String(CONSOLE_MAX_LV);
    inp.value = String(c.console[key]);
    inp.onchange = () => {
      const v = Math.max(0, Math.min(CONSOLE_MAX_LV, Number(inp.value) || 0));
      c.console[key] = v;
      inp.value = String(v);      // 잘린 값을 칸에도 보여 준다
      cpKick();
    };
    box.append(inp);
    hex.append(box);
  }

  const tabs = $("#coop-tabs");
  tabs.textContent = "";
  for (const [key, label] of COOP_TABS) {
    const b = mkBtn(label, `cp-tab${coopTab === key ? " on" : ""}`, () => {
      coopTab = key; buildCoop();
    });
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(coopTab === key));
    tabs.append(b);
  }
  buildCoopPane();
  // 어느 계정에서 왔는지는 위 고르개에 이미 떠 있다 — 여기서는 «여기 바꾼 건
  // 스펙·덱에 안 남는다»만 알리면 된다.
  $("#coop-src").textContent = charSpec(c.name)
    ? T("여기서 바꾼 것은 스펙·덱에 영향이 없습니다.")
    : T("저장된 스펙이 없어 만렙 기본값에서 시작합니다.");
}

/** 스킬 설명 한 줄 — 템플릿의 {0}·{1}…을 **그 레벨의 수치**로 채운다.
 *  레벨을 안 주면(또는 그 레벨 값이 없으면) 자리표시자를 그대로 둔다. */
function skillEffectText(info, lv) {
  if (!info?.tpl) return "";
  const vals = info.vals?.[String(lv)] || [];
  return T(info.tpl).replace(/\{(\d+)\}/g,
    (m, i) => (vals[Number(i)] !== undefined ? vals[Number(i)] : m));
}

/** 스킬 하나 — 인게임처럼 **설명과 레벨**을 함께. */
function openSkillModal(idx) {
  const c = coop, rec = byName.get(c.name);
  const key = ["s1", "s2", "ub"][idx];
  const info = rec?.skills?.[idx];
  $("#coop-eq-title").textContent = info?.name
    ? `${info.name} — ${[T("스킬1"), T("스킬2"), T("버스트")][idx]}`
    : [T("스킬1"), T("스킬2"), T("버스트")][idx];
  const ico = $("#coop-eq-ico");
  ico.textContent = "";
  const im0 = iconImg(info?.icon);
  if (im0) ico.append(im0);
  const body = $("#coop-eq-body");
  body.textContent = "";

  const lvRow = el("div", "grp");
  lvRow.append(el("span", "grp-label", "레벨"));
  lvRow.append(stepsEl(10, (i) => ({
    label: String(i + 1), on: c[key] === i + 1,
    title: skillEffectText(info, i + 1) || undefined,
    onclick: () => { c[key] = i + 1; cpKick(); buildCoopPane(); openSkillModal(idx); },
  })));
  body.append(lvRow);

  if (info?.tpl) {
    const d = el("div", "grp");
    d.append(el("span", "grp-label", "효과"));
    // 인게임 설명과 같은 읽기 — **지금 레벨의 수치**로 채운다
    const text = skillEffectText(info, c[key]);
    d.append(el("pre", "skill-text", text));
    body.append(d);
  }
  const dlg = $("#coop-eq");
  if (!dlg.open) dlg.showModal();
}

/** 돌파★·코어 — **누르면 바로 다시 그려야 한다.** buildCoop에서만 그리면 조작해도
    별이 그대로 남는다(실측 버그). */
function buildCoopGrade() {
  if (!coop) return;
  const c = coop;
  const gradeWrap = $("#coop-grade");
  gradeWrap.textContent = "";
  for (let i = 1; i <= 3; i++) {
    const b = el("button", "cp-gstar" + (c.grade >= i ? " on" : ""), "★");
    b.type = "button";
    b.title = T("{i}돌파", { i });
    // 누른 별까지만 채운다. 같은 별을 다시 누르면 한 칸 줄어든다.
    b.onclick = () => {
      c.grade = (c.grade === i && c.core === 0) ? i - 1 : i;
      if (c.grade < 3) c.core = 0;      // 코어강화는 3돌파 전제
      buildCoopGrade();
      cpKick();
    };
    gradeWrap.append(b);
  }
  // 코어강화는 **3돌파를 다 채운 뒤에만** 있는 값이라 그때만 보여 준다. 상한은 +7.
  if (c.grade >= 3) {
    const core = el("span", "cp-core" + (c.core ? " on" : ""));
    core.append(el("span", "cp-core-lb", "코어"));
    core.append(selectEl(Array.from({ length: 8 }, (_, i) => [i, i ? `+${i}` : "0"]),
      Math.min(7, c.core), (v) => { c.core = Number(v); buildCoopGrade(); cpKick(); }));
    gradeWrap.append(core);
  }

}

/** 왼쪽 레일(소장품) — **값이 바뀌면 다시 그려야 한다.** 별·색·그림이 단계를 따라간다. */
/** 전신 일러를 «그림이 실제로 있는 자리»에 맞춘다.
 *  원본은 2048² 정사각형인데 캐릭터가 앉은 위치가 제각각이다 — 아래 여백이
 *  0px인 니케도 645px인 니케도 있다(199장 실측). 그래서 예전처럼 «122% 키우고
 *  132px 내린다» 같은 한 값으로는 누구는 발이 잘리고 누구는 붕 떴다.
 *  빌드 때 잰 알파 경계(fbb)로 세로를 꽉 채우고 가로는 그림 중심을 맞춘다. */
function fitCoopArt() {
  const box = $("#coop-art");
  const img = box?.querySelector("img");
  if (!img) return;
  const bb = img.dataset.bb?.split(",").map(Number);
  if (!bb || bb.length !== 4) {                 // 경계를 모르면 CSS 기본값에 맡긴다
    img.style.cssText = "";
    return;
  }
  // 세로로 쌓을 때 상자는 **높이가 0**이다(그림이 절대배치라 안을 못 채운다) —
  // 높이를 여기서 정해 줄 참이므로 폭만 있으면 된다.
  const h = box.clientHeight, w = box.clientWidth;
  if (!w) return;
  const [x0, y0, x1, y1] = bb;
  const iw = x1 - x0, ih = y1 - y0;
  // 좁은 화면에서는 판이 **아래로** 내려간다 — 피할 것이 없다.
  const side = document.querySelector(".cp-side")?.getBoundingClientRect();
  const boxR = box.getBoundingClientRect();
  const stacked = !side || side.top >= boxR.bottom - 4;

  // 세로로 쌓아도 **그림 크기는 그대로**다 — 나란히 놓을 때와 같은 높이를 노리고,
  // 화면이 그보다 좁을 때만 폭에 맞춰 줄인다. 정보판은 그림 아래로 내려간다.
  let scale;
  const screen = box.closest(".cp-screen");
  if (stacked) {
    const target = parseFloat(getComputedStyle(box).getPropertyValue("--cp-art-h")) || h;
    scale = Math.min(target / ih, (w * 0.98) / iw);
    // 정보판이 시작하는 자리 = 그림 높이의 58%. 상체는 트여 있고 **하체는 판이
    // 덮는다** — 전신을 다 그리면서도 위쪽이 쓸데없이 길어지지 않는다.
    screen?.style.setProperty("--cp-clear", `${Math.round(ih * scale * 0.58)}px`);
  } else {
    screen?.style.removeProperty("--cp-clear");
    scale = (box.clientHeight || h) / ih;       // 나란히 놓을 때는 세로를 꽉 채운다
  }
  const size = (img.naturalWidth || 2048) * scale;
  const inkW = iw * scale;
  // 가로는 **정보판을 피해서** 앉힌다.
  //  · 판을 뺀 빈 자리가 그림보다 넓으면 → 그 빈 자리의 한가운데. 화면이 넓어질수록
  //    판 쪽으로 딸려가 오른쪽 구석에 서 있던 걸 고친 것이다(넓은 창에서 실측).
  //  · 빈 자리가 모자라면 → 판 왼쪽 끝에 붙이고 그림 폭의 18%만 판 아래로 밀어
  //    넣는다(인게임과 같은 겹침). 그래도 모자라면 왼쪽 끝까지 물러난다.
  const freeW = Math.max(0, w - side.width);
  const inkLeft = stacked
    ? (w - inkW) / 2
    : (freeW >= inkW ? (freeW - inkW) / 2
                     : Math.max(8, w - side.width + inkW * 0.18 - inkW));
  img.style.height = `${size}px`;
  img.style.width = `${size}px`;
  img.style.top = `${-y0 * scale}px`;
  img.style.left = `${inkLeft - x0 * scale}px`;

  // 세로 — 그림틀은 고정 높이(`--cp-art-h`)인데 무대는 오른쪽 판 길이를 따라
  // 더 길어진다. 틀을 top:0에 두면 니케가 위에 붙고 발밑이 휑하게 남는다
  // (실측: 무대 786px에 그림 680px → 아래 105px 공백). 남는 만큼 내려 **바닥에
  // 서게** 한다. 무대가 그림보다 짧으면 0이라 머리가 잘리지 않는다.
  // 무대 높이는 **오른쪽 판이 정한다.** `screen.clientHeight`만 보면 판 내용이
  // 아직 안 그려진 순간에 0이 잡혀 그대로 위에 붙는다(실측) — 판 실측 높이를 함께 본다.
  // 남는 공간은 **절반만** 내린다. 바닥에 딱 붙이면 이번엔 머리 위가 휑하다 —
  // 위아래로 나눠 두면 어느 쪽으로도 치우치지 않는다.
  const sh = Math.max(screen?.clientHeight || 0, side ? side.height : 0);
  box.style.top = stacked ? "" : `${Math.max(0, (sh - box.clientHeight) / 2)}px`;
}
// 판 크기는 창 폭(모바일 분기)에 따라 한 번 바뀐다 — 그때 다시 맞춘다.
// `fitCoopArt`가 상자 높이를 건드리므로 되먹임으로 도는 걸 막는다.
let fittingArt = false;
const refitArt = () => {
  if (fittingArt) return;
  fittingArt = true;
  try { fitCoopArt(); } finally { requestAnimationFrame(() => { fittingArt = false; }); }
};
if (typeof ResizeObserver === "function") {
  new ResizeObserver(refitArt).observe(document.documentElement);
}
// 무대 높이는 **오른쪽 판 길이**가 정한다. 판은 탭(장비·스킬·큐브)마다, 그리고
// 내용이 그려지면서 높이가 바뀌는데 창 크기는 그대로라 위 관찰자가 못 잡는다 —
// 판을 따로 지켜봐야 세로 가운데 정렬이 뒤늦게라도 맞는다.
/** 판이 다 자랄 때까지 몇 번 더 맞춘다.
 *
 *  관찰자(`watchCoopSide`)만으로는 부족했다 — 판이 커지는 시점이 관찰을 시작하기
 *  전이면 «크기가 변한 적 없는» 상태가 되어 콜백이 오지 않는다(실측: 세로가 계속
 *  위에 붙어 있었다). 프레임·짧은 지연 몇 번이면 글꼴·이미지까지 자리 잡는다. */
function settleCoopArt() {
  requestAnimationFrame(fitCoopArt);
  for (const ms of [120, 400]) setTimeout(fitCoopArt, ms);
}

let cpSideRO = null;
function watchCoopSide() {
  if (typeof ResizeObserver !== "function") return;
  const side = document.querySelector(".cp-side");
  if (!side) return;
  cpSideRO ||= new ResizeObserver(refitArt);
  cpSideRO.disconnect();
  cpSideRO.observe(side);
}

function buildCoopRail() {
  if (!coop) return;
  const c = coop;
  const rail = $("#coop-rail");
  rail.textContent = "";
  const railBox = el("div", "cp-item");
  railBox.append(el("span", "cp-item-label", "소장품"));
  const icon = favIconFile(c.name, c.coll_stage);
  const g = /^(SSR|SR|R)/.exec(c.coll_stage || "");
  const shot = el("div", "cp-item-art" + (icon ? "" : " empty")
    + (g ? ` grade-${g[1].toLowerCase()}` : ""));
  if (icon) {
    const im = el("img");
    im.src = `image/icon/${icon}`;
    im.alt = "";
    im.draggable = false;
    shot.append(im);
  } else {
    shot.textContent = "—";
  }
  railBox.append(shot);
  // 인게임처럼 **별로** 표기한다 — 5레벨당 별 하나(15레벨 = ★★★). 색은 등급색.
  const m = /^(SSR|SR|R)(\d*)$/.exec(c.coll_stage || "");
  if (m) {
    const grade = m[1];
    const lv = m[2] ? Number(m[2]) : 0;
    const stars = el("span", `cp-item-stars grade-${grade.toLowerCase()}`);
    for (let i = 0; i < 3; i++) {
      stars.append(el("i", "cp-star-pip" + (i < Math.floor(lv / 5) ? " on" : "")));
    }
    railBox.append(stars);
  }
  railBox.append(selectEl(COLL_STAGES.map((s) => [s, s]), c.coll_stage, (v) => {
    c.coll_stage = v;
    buildCoopRail();          // 별·색·그림이 고른 단계를 따라간다
    cpKick();
  }));
  rail.append(railBox);
}

/** 부위 하나의 장비 플랫 스탯 — 인게임 «장비 능력치» 칸.
    배율 = 1 + (제조사 일치 0.3) + 0.1×강화. 서버 엔진과 같은 규칙이다. */
function equipFlat(cls, part, cur) {
  const t = Number(cur?.t) || 0;
  const zero = { atk: 0, def: 0, hp: 0 };
  const tbl = MAPS?.equip_stats;
  if (!tbl || t < 1 || !cls) return zero;
  const lv = Math.max(0, Math.min(5, Number(cur.lv) || 0));
  const base = t >= 10 ? tbl["기업"]?.[cls]?.[part]?.["0"] : tbl["일반"]?.[`T${t}`]?.[cls]?.[part];
  if (!base) return zero;
  const mult = t >= 10 ? 1 + 0.1 * lv
    : 1 + (cur.corp && cur.corp === coop?.corp ? 0.3 : 0) + 0.1 * lv;
  return { atk: base.atk * mult, def: base.def * mult, hp: base.hp * mult };
}

/** 스킬 원 하나 — 아이콘 + 오른쪽 아래 레벨 배지. 버스트는 크게 그리고 단계를 얹는다. */
function skTile(key, idx, big) {
  const c = coop, rec = byName.get(c.name);
  const t = el("button", "sk-tile" + (big ? " big" : ""));
  t.type = "button";
  t.title = rec?.skills?.[idx]?.name || [T("스킬1"), T("스킬2"), T("버스트")][idx];
  const im = iconImg(rec?.skills?.[idx]?.icon, "sk-tile-img");
  if (im) t.append(im);
  else t.append(el("span", "sk-tile-nm", [T("스킬1"), T("스킬2"), T("버스트")][idx]));
  if (big && rec?.burst) {
    const bd = el("span", "sk-burst-badge", BURST_ROMAN[rec.burst] || String(rec.burst));
    t.append(bd);
  }
  t.append(el("span", "tile-lv", String(c[key])));
  t.onclick = () => openSkillModal(idx);
  return t;
}

function buildCoopPane() {
  const pane = $("#coop-pane");
  if (!coop) return;
  pane.textContent = "";
  const c = coop, rec = byName.get(c.name);

  if (coopTab === "equip") {
    // 인게임 장비 화면: 위에 «장비 효과 보기»(합계), 아래로 부위마다
    // 그림 · 장비 능력치(플랫) · 장비 효과(오버로드 줄). 만렙 줄은 반전 강조.
    const sum = {};
    for (const part of c.ol) {
      for (const line of part) {
        if (line?.o) sum[line.o] = +( (sum[line.o] || 0) + pct(line.o, line.l) ).toFixed(4);
      }
    }
    const keys = Object.keys(sum);
    if (keys.length) {
      const head = el("div", "ovl-sum");
      head.append(el("span", "ovl-sum-hd", "장비 효과 보기"));
      const g = el("div", "ovl-sum-grid");
      for (const k of keys) {
        const r = el("div", "ovl-sum-row");
        r.append(el("span", null, T("[{v} 증가]", { v: OL_LABEL[k] })));
        r.append(el("b", null, `${sum[k].toFixed(2)}%`));
        g.append(r);
      }
      head.append(g);
      pane.append(head);
    }

    PARTS.forEach((part, pi) => {
      const cur = c.equipment[part] || { t: 0 };
      const on = Number(cur.t) >= 1;
      const isT10 = Number(cur.t) >= 10;
      const row = el("div", "ovl-part" + (on ? "" : " empty"));

      // 아이콘 — 인게임 장비 타일처럼 왼쪽에 뱃지 둘(위: 오버로드/기업, 아래: 역할군)과
      // 왼쪽 아래 강화 수치를 얹는다.
      const art = el("span", "ovl-art" + (isT10 ? " ovl" : ""));
      const ic = on ? iconImg(uiIcon("equip", `T${cur.t}|${rec?.cls}|${part}`)) : null;
      if (ic) art.append(ic);
      if (on && (isT10 || cur.corp)) {
        const badges = el("span", "ovl-badges");
        const badge = (cls, file) => {
          const b = el("i", "ovl-bg " + cls);
          const im = iconImg2(file);
          if (im) b.append(im);
          badges.append(b);
        };
        // 오버로드(T10)는 인게임 뱃지 그림. 그 아래 단계는 같은 자리에 기업 마크.
        // (인게임엔 역할군 뱃지도 한 칸 더 붙지만, 니케마다 고정이라 뺐다.)
        if (isT10) badge("ol", "icon-overload.png");
        else if (cur.corp) badge("corp", CORP_ICON[cur.corp]);
        art.append(badges);
      }
      art.append(el("span", "ovl-art-lv",
        on ? String(Number(cur.lv) || 0).padStart(2, "0") : "—"));
      row.append(art);

      const cols = el("span", "ovl-cols");

      // 왼쪽 — 부위·단계·강화·제조사를 **여기서 바로** 고친다
      const setup = el("span", "ovl-stat");
      setup.append(el("span", "ovl-hd", part));
      const r1 = el("span", "ovl-set-row");
      r1.append(selectEl(
        [[0, T("미장착")], ...Array.from({ length: 10 }, (_, i) => [i + 1, `T${i + 1}`])],
        Number(cur.t) || 0, (v) => coopSet(() => {
          cur.t = Number(v);
          c.equipment[part] = cur;
          if (Number(v) < 10) c.ol[pi] = [null, null, null];
          if (Number(v) >= 10) cur.corp = null;
        })));
      if (on) {
        r1.append(selectEl(Array.from({ length: 6 }, (_, i) => [i, `+${i}`]),
          Number(cur.lv) || 0, (v) => coopSet(() => { cur.lv = Number(v); })));
      }
      if (on && !isT10) {
        // 제조사는 **같은 줄**에 붙인다 — 아래로 내려가면 카드가 한 칸 커진다.
        // «제조사 없음»은 길어서 줄을 넘기던 이름이라 «일반장비»로 줄였다.
        const cs = selectEl([["", T("일반장비")], ...CORP_ORDER.map((x) => [x, x])],
          cur.corp || "", (v) => coopSet(() => { cur.corp = v || null; }));
        if (!cur.corp) cs.classList.add("plain");
        r1.append(cs);
      }
      setup.append(r1);
      const fl = equipFlat(rec?.cls, part, cur);
      for (const [lab, v] of [["체력", fl.hp], ["공격", fl.atk], ["방어", fl.def]]) {
        if (!v) continue;
        const r = el("span", "ovl-stat-row");
        r.append(el("span", null, lab));
        r.append(el("b", null, Math.round(v).toLocaleString()));
        setup.append(r);
      }
      cols.append(setup);

      // 오른쪽 — 오버로드 3줄. 옵션·단계를 바로 고르고 수치는 등급색으로 보인다.
      const eff = el("span", "ovl-eff");
      eff.append(el("span", "ovl-hd", "장비 효과"));
      for (let li = 0; li < 3; li++) {
        const line = c.ol[pi][li];
        const lv = line?.l ?? 15;
        const tier = !line?.o ? "" : lv >= OL_BLACK_FROM ? " black" : lv >= OL_BLUE_FROM ? " blue" : "";
        const r = el("span", "ovl-eff-row" + tier);
        r.append(selectEl([["", T("빈 줄")], ...OL_OPTS], line?.o ?? "", (v) => coopSet(() => {
          c.ol[pi][li] = v ? { o: v, l: line?.l ?? 15 } : null;
        }), !isT10));
        const lvSel = selectEl(Array.from({ length: 15 }, (_, i) => [i + 1, `${i + 1}`]),
          lv, (v) => coopSet(() => {
            if (c.ol[pi][li]) c.ol[pi][li].l = Number(v);
          }), !isT10 || !line?.o);
        if (line?.o) olLevelHints(lvSel, line.o);
        r.append(lvSel);
        r.append(el("b", null, line?.o ? `${pct(line.o, lv).toFixed(2)}%` : ""));
        eff.append(r);
      }
      cols.append(eff);
      row.append(cols);
      pane.append(row);
    });
    return;
  }

  if (coopTab === "skill") {
    // 인게임 SKILL 상자: 왼쪽에 작은 원 둘(스킬1·2), 오른쪽에 큰 버스트 원.
    // 원마다 오른쪽 아래에 레벨 배지가 붙는다.
    const grid = el("div", "sk-grid");
    const col = el("div", "sk-col");
    ["s1", "s2"].forEach((key, i) => {
      col.append(skTile(key, i, false));
    });
    grid.append(col);
    grid.append(skTile("ub", 2, true));
    pane.append(grid);
    return;
  }

  if (coopTab === "cube") {
    // 인게임 HARMONY CUBE 상자: 큐브 카드 → 스킬 원 → 스탯 → 고르개.
    // 전부 가운데 한 줄기로 세운다 — 왼쪽 붙임과 가운데 정렬이 섞여 어수선했다.
    const wrap = el("div", "cube-pane");
    const card0 = el("div", "cube-card" + (c.cube_lv ? "" : " empty"));
    const cic = c.cube_lv ? iconImg(uiIcon("cube", c.cube_name), "cube-card-img") : null;
    if (cic) card0.append(cic);
    card0.append(el("span", "cube-card-lv", c.cube_lv ? `LV.${c.cube_lv}` : T("없음")));
    wrap.append(card0);

    // 큐브 스킬 원 — 레벨에 따라 스킬 레벨이 오른다. **없는 칸은 아예 안 그린다**
    // (인게임에도 세 번째 칸을 쓰는 큐브가 없다 — 빈 동그라미만 남아 어수선했다).
    const icons = MAPS?.ui_icons?.cube_skill?.[c.cube_name] || [];
    const skInfo = MAPS?.ui_icons?.cube_skill_info?.[c.cube_name] || [];
    const lvs = MAPS?.ui_icons?.cube_levels?.[c.cube_name] || {};
    const ring = el("div", "cube-skills");
    for (let i = 0; i < 3; i++) {
      const lv = (lvs[`level${i + 1}`] || [])[Math.max(0, c.cube_lv - 1)] || 0;
      if (!lv) continue;
      const t = el("div", "cube-sk");
      const nfo = skInfo[i];
      // 설명의 {0}·{1}은 **지금 스킬 레벨의 수치**로 채운다
      const dsc = T(nfo?.desc || "").replace(/\{(\d+)\}/g, (m, k) => {
        const arr = nfo?.vals?.[Number(k)] || [];
        return arr[Math.max(0, lv - 1)] ?? arr[arr.length - 1] ?? m;
      });
      t.title = [nfo?.name && `${T(nfo.name)} (Lv.${lv})`, dsc]
        .filter(Boolean).join(String.fromCharCode(10)) || T("스킬 Lv.{lv}", { lv });
      const im = iconImg(icons[i], "cube-sk-img");
      if (im) t.append(im);
      t.append(el("span", "tile-lv", String(lv)));
      ring.append(t);
    }
    if (ring.childElementCount) wrap.append(ring);

    const st = MAPS?.cube_stats?.[String(c.cube_lv)] || { atk: 0, def: 0, hp: 0 };
    const stats = el("div", "cube-stats");
    for (const [ico, v] of [["공격", st.atk], ["방어", st.def], ["체력", st.hp]]) {
      const r = el("div", "cube-stat");
      r.append(el("span", "cube-stat-k", ico));
      r.append(el("b", null, Number(v).toLocaleString()));
      stats.append(r);
    }
    wrap.append(stats);

    // 고르개 둘은 라벨을 오른쪽 맞춤한 2열 격자로 — 칸 폭이 제각각이면 어수선하다
    const form = el("div", "cube-form");
    const names = Object.keys(MAPS?.ui_icons?.cube || {});
    if (names.length) {
      form.append(el("span", "cube-form-k", "종류"));
      form.append(selectEl(names.map((n) => [n, n]), c.cube_name || names[0],
        (v) => coopSet(() => { c.cube_name = v; })));
    }
    form.append(el("span", "cube-form-k", "레벨"));
    form.append(selectEl(Array.from({ length: 16 }, (_, i) => [i, i ? `${i}` : T("없음")]),
      c.cube_lv, (v) => coopSet(() => { c.cube_lv = Number(v); })));
    wrap.append(form);

    // 큐브는 **종류가 전투력에 영향이 없다** — 17종 전부 스탯·계수 배열이 같다(실측)
    wrap.append(el("p", "cube-note",
      T("큐브는 종류와 무관합니다 — 전투력에는 레벨만 들어갑니다.")));
    pane.append(wrap);
  }
}

function group(label, nodes) {
  const g = el("div", "grp");
  g.append(el("span", "grp-label", label));
  for (const n of [].concat(nodes)) g.append(n);
  return g;
}
function stepsEl(n, f) {
  const wrap = el("div", "steps");
  for (let i = 0; i < n; i++) {
    const s = f(i);
    const b = el("button", "step" + (s.on ? " on" : "") + (s.star ? " star" : ""), s.label);
    b.type = "button";
    b.onclick = s.onclick;
    if (s.title) b.title = s.title;
    wrap.append(b);
  }
  return wrap;
}
/** 오버로드 단계 고르개 — **펼쳤을 때만** 단계마다 수치를 함께 보여 준다.
 *  닫힌 상자는 좁아야 오른쪽 칸(장비 효과)이 안 밀리므로, 여는 순간 라벨을 늘렸다가
 *  고르거나 빠져나오면 숫자만 남긴다. 목록 폭은 브라우저가 가장 긴 항목에 맞춘다. */
function olLevelHints(sel, key) {
  const wide = () => {
    for (const o of sel.options) o.textContent = T("{value}단계 · {v}%", { value: o.value, v: pct(key, Number(o.value)).toFixed(2) });
  };
  const narrow = () => { for (const o of sel.options) o.textContent = o.value; };
  sel.addEventListener("pointerdown", wide);   // 목록이 열리기 전에 끼어든다
  sel.addEventListener("keydown", wide);
  sel.addEventListener("change", narrow);
  sel.addEventListener("blur", narrow);
}

function selectEl(opts, value, onchange, disabled = false) {
  const s = el("select");
  for (const [v, label] of opts) {
    const o = el("option", null, label);
    o.value = String(v);
    s.append(o);
  }
  s.value = String(value);
  s.disabled = disabled;
  s.onchange = () => onchange(s.value);
  return s;
}
function rowSelect(label, opts, value, onchange) {
  const r = el("div", "grp-row");
  r.append(el("span", "ol-part", label));
  r.append(selectEl(opts, value, onchange));
  return r;
}

// ── 필터 패널 (인게임 정렬/필터 구조) ───────────────────────────────────
function buildFilters() {
  sortRow();
  // 버스트 필터도 카드와 같은 인게임 글리프를 쓴다 — 한쪽만 글자면 두 표기가 어긋난다
  multiRow("#f-burst", "burst",
           [["1", ""], ["2", ""], ["3", ""], ["A", ""]], "sq sq-burst",
           (v) => BURST_ICON[v]);
  const have = (k) => new Set(ROSTER.map((r) => r[k]));
  multiRow("#f-class", "cls",
    CLASS_ORDER.filter((v) => have("cls").has(v)).map((v) => [v, v]));
  multiRow("#f-element", "element",
    CODE_ORDER.filter((v) => have("element").has(v)).map((v) => [v, v]));
  multiRow("#f-weapon", "weapon",
    WEAPONS.filter((w) => ROSTER.some((r) => r.weapon === w)).map((v) => [v, v]));
  multiRow("#f-corp", "corp",
    CORP_ORDER.filter((v) => have("corp").has(v)).map((v) => [v, v]));
  const fi = $("#f-favitem");
  if (fi) {
    fi.classList.toggle("on", curFilter().favItem);
    fi.onclick = () => {
      const f = curFilter();
      f.favItem = !f.favItem;
      fi.classList.toggle("on", f.favItem);
      syncFilterChrome(); saveAll(); renderPools();
    };
  }
  syncFilterChrome();
}

// 숫자로 세는 정렬은 «큰 값부터»가 자연스럽고, 이름은 «가나다순»이 자연스럽다.
// 정렬을 새로 고를 때 그 방향을 기본으로 준다 (같은 칩을 다시 누르면 뒤집힌다).
const DESC_FIRST = new Set(["combat", "elem", "elematk"]);

/** 정렬은 하나만 고른다 (해제 없음). 트리거에 현재 정렬 이름이 뜬다. */
function sortRow() {
  const wrap = $("#f-sort");
  wrap.textContent = "";
  for (const [v, label] of SORTS) {
    const on = curFilter().sort === v;
    const b = el("button", `chip chip-sort${on ? " on" : ""}`
      + (on && curFilter().asc !== false ? " asc" : ""), label);
    b.type = "button";
    // 인게임과 같다: 다른 칩을 누르면 그 기준으로, **같은 칩을 다시 누르면 방향이 뒤집힌다.**
    b.onclick = () => {
      const f = curFilter();
      if (f.sort === v) f.asc = f.asc === false;
      else { f.sort = v; f.asc = !DESC_FIRST.has(v); }
      sortRow();
      syncFilterChrome();
      saveAll(); renderPools();
    };
    wrap.append(b);
  }
}

/** 다중 선택 칩 줄. 누르면 켜지고 다시 누르면 꺼진다 — 아무것도 안 켜면 필터 없음. */
function multiRow(sel, key, opts, cls = "chip", icon = null) {
  const wrap = $(sel);
  if (!wrap) return;
  wrap.textContent = "";
  for (const [v, label] of opts) {
    const b = el("button", cls + (curFilter()[key].includes(v) ? " on" : ""), label);
    b.type = "button";
    // 아이콘 훅 — 글자 대신 인게임 글리프를 넣는 줄(버스트)이 쓴다
    const file = icon && icon(v);
    if (file) {
      const im = el("img");
      im.src = `image/icon/${file}`;
      im.alt = ""; im.draggable = false;
      b.append(im);
      b.title = T("버스트 {v}", { v });
    }
    b.onclick = () => {
      const arr = curFilter()[key];
      const i = arr.indexOf(v);
      if (i === -1) arr.push(v); else arr.splice(i, 1);
      b.classList.toggle("on", arr.includes(v));
      syncFilterChrome();
      saveAll(); renderPools();
    };
    wrap.append(b);
  }
}

/** 트리거 라벨·비우기 노출·즐겨찾기 버튼 상태를 필터 상태와 맞춘다. */
function syncFilterChrome() {
  const f = curFilter();
  const label = Object.fromEntries(SORTS)[f.sort] || T("전투력");
  // 트리거는 **패널 안의 필터만** 센다. 버스트와 즐겨찾기는 위쪽 바에 제 버튼이 있고
  // 켜지면 그 버튼이 파래지므로, 트리거까지 물들이면 무엇이 걸렸는지 되레 헷갈린다.
  const n = f.cls.length + f.element.length + f.weapon.length + f.corp.length
    + (f.favItem ? 1 : 0);
  // 트리거는 인게임처럼 **항상 강조색**이다. 필터가 걸려 있으면 개수를 함께 보여 준다.
  $("#f-trig-label").textContent = n ? `${label} · ${n}` : label;
  const dir = $("#f-dir");
  if (dir) {
    dir.textContent = f.asc === false ? "▼" : "▲";
    dir.title = f.asc === false ? T("내림차순 — 눌러서 오름차순") : T("오름차순 — 눌러서 내림차순");
  }
  $("#f-toggle-wrap").classList.toggle("filtered", n > 0);
  $("#f-toggle").classList.toggle("filtered", n > 0);
  // 비우기는 **패널을 연 동안에만** 보인다. 평소에 떠 있으면 로스터 위에 떠서
  // 카드를 가리고, 무엇을 비우는 버튼인지도 문맥 없이 읽힌다.
  $("#f-clear").hidden = !n || $("#fpanel").hidden;
  const fav = $("#f-fav");
  fav.classList.toggle("on", f.favOnly);
  fav.setAttribute("aria-pressed", String(f.favOnly));
  const only = $("#f-parsed");
  if (only) only.classList.toggle("on", f.parsed);
}

// ── 컨트롤 (고급) ───────────────────────────────────────────────────────
// 계산기가 재현하는 컨트롤은 `context/CONTROL.md`가 정본이다. 여기서는 그중
// **정책으로 켜고 끄는 것**만 다룬다 (명시 시퀀스는 UI로 만들 물건이 아니다).
// 톡톡이·홀드는 차지형(SR·RL) 전용이라 그 무기군에만 줄이 뜬다.
const CHARGE_WEAPONS = new Set(["SR", "RL"]);
const TAP_RATES = [["3.0", T("3.0 (미숙련)")], ["3.3", "3.3"], ["3.6", T("3.6 (숙련)")],
                   ["3.9", "3.9"], ["4.2", T("4.2 (상한)")]];
const RELOAD_POLICIES = [["before_fb_end", T("풀버스트 종료 전")],
                         ["into_fb", T("풀버스트 안으로")]];

/** 이 덱에서 계산에 보낼 컨트롤. 슬롯에 없는 이름과 빈 설정은 버린다. */
function ctrlPayload(d) {
  const out = {};
  for (const n of d.names.filter(Boolean)) {
    const c = d.control?.[n];
    if (c && Object.keys(c).length) out[n] = c;
  }
  return Object.keys(out).length ? out : null;
}

function setCtrl(name, key, value) {
  const d = ctrlDeck(name);
  d.control ||= {};
  const c = (d.control[name] ||= {});
  if (value == null) delete c[key];
  else c[key] = value;
  if (!Object.keys(c).length) delete d.control[name];
  saveAll(); buildControl(); refreshSlots(); renderResults();
}

function ctrlToggle(name, key, on, make) {
  setCtrl(name, key, on ? make() : null);
}

// 버스트 주기를 직접 입력하는 중인 니케 — 유효한 값이 들어오기 전까지는 저장하지
// 않으므로, 칩이 켜진 상태를 저장값과 별개로 들고 있어야 한다.
let patDraft = null;

/** "1,3,5" 같은 사이클 나열 → 계산기가 받는 값. 못 읽으면 null.
 *  «3의 배수» 같은 말로 된 입력은 받지 않는다 — 형식이 하나면 헷갈릴 게 없다.
 *  배수로 굴리고 싶으면 사이클을 그대로 나열하면 된다 (3,6,9,12…). */
function parsePattern(text) {
  const t = (text || "").trim();
  const xs = t.split(/[,\s]+/).filter(Boolean);
  if (!xs.length || !xs.every((x) => /^\d+$/.test(x))) return null;
  const ns = [...new Set(xs.map(Number))].sort((a, b) => a - b);
  return ns.length <= 40 && ns.every((n) => n >= 1 && n <= 999) ? ns : null;
}

// ── 주기 겹침 검사 ─────────────────────────────────────────────────────────
// 같은 버스트 단계의 두 명이 같은 사이클을 지정하면 그 사이클엔 한 명만 나간다 —
// 지정은 «시간표»라서 겹침은 입력 실수다. 저장하기 전에 막고 어디가 겹치는지 말해 준다.
// (자동으로 걸리는 패턴과는 비교하지 않는다 — 자동은 계산기가 조건을 보고 알아서 피한다.)
const PAT_HORIZON = 40;          // every:N을 펼쳐 볼 사이클 상한

/** 프리셋 이름이면 로스터에 실린 값으로 푼다. 날값은 그대로. */
function resolvePat(name, v) {
  if (typeof v === "string" && !v.startsWith("every:")) {
    return (byName.get(name)?.patterns || {})[v] ?? null;
  }
  return v;
}

function patternCycles(v) {
  if (Array.isArray(v)) return new Set(v);
  if (typeof v === "string" && v.startsWith("every:")) {
    const n = Number(v.slice(6));
    const out = new Set();
    for (let c = n; n >= 1 && c <= PAT_HORIZON; c += n) out.add(c);
    return out;
  }
  return null;
}

/** 이 덱에서 `name`에게 `value`를 지정하면 누구와 겹치나. 없으면 null. */
function patternConflict(name, value) {
  const d = deckOf(state.settings.deck);
  const me = byName.get(name);
  const mine = patternCycles(resolvePat(name, value));
  if (!me || !mine) return null;
  for (const other of d.names.filter(Boolean)) {
    if (other === name) continue;
    const v = d.control?.[other]?.burst_pattern;
    if (v == null || v === "안 씀") continue;
    const or = byName.get(other);
    if (!or) continue;
    if (!(me.burst === or.burst || me.burst === "A" || or.burst === "A")) continue;
    const theirs = patternCycles(resolvePat(other, v));
    if (!theirs) continue;
    const hit = [...mine].filter((c) => theirs.has(c)).sort((a, b) => a - b);
    if (hit.length) return { who: other, cycles: hit.slice(0, 6) };
  }
  return null;
}

/** 같은 단계 멤버들의 선버를 끈다.

    주기는 «이 사이클엔 내가»라는 시간표고 선버는 «항상 내가 먼저»다 — 같은 단계에
    둘이 공존하면 사용자가 예측할 수 없는 순서가 나온다. 주기를 지정하는 순간
    그 단계의 선버를 풀어 버린다. */
function dropSameStageFirst(name) {
  const d = deckOf(state.settings.deck);
  const me = byName.get(name);
  if (!me) return;
  for (const n of d.names.filter(Boolean)) {
    if (n === name || !d.control?.[n]?.burst_first) continue;
    const o = byName.get(n);
    if (o && (o.burst === me.burst || o.burst === "A" || me.burst === "A")) {
      setCtrl(n, "burst_first", null);
    }
  }
}

/** 겹치면 경고를 띄우고 true. 저장은 부른 쪽이 건너뛴다. */
function patWarnIf(name, value) {
  const conflict = patternConflict(name, value);
  const w = $("#ctrl-panel .ctrl-pat-warn");
  if (w) {
    w.hidden = !conflict;
    if (conflict) {
      w.textContent = T("{who}의 주기와 겹칩니다 — ", { who: conflict.who })
        + T("{v}번째 풀버스트를 둘 다 지정했습니다.", { v: conflict.cycles.join("·") });
    }
  }
  return !!conflict;
}

/** 저장된 주기 → 입력칸 표기. parsePattern의 역방향이다. */
function patternText(v) {
  if (Array.isArray(v)) return v.join(",");
  if (typeof v === "string" && v.startsWith("every:")) return v.slice(6) + T("의 배수");
  return String(v ?? "");
}

function buildControl() {
  const wrap = $("#ctrl-panel");
  if (!wrap) return;
  const open = ctrlName();
  const d = ctrlDeck(open);
  const name = open && d.names.includes(open) ? open : null;
  wrap.hidden = !name;
  wrap.textContent = "";
  if (!name) return;

  const c = d.control?.[name] || {};
  const rec = byName.get(name);
  const charge = CHARGE_WEAPONS.has(rec?.weapon);
  // 동료 조건이 맞으면 레이어가 강제로 거는 컨트롤(예: 미란다와 있으면 미하라 엄폐컨) —
  // 체크박스를 꺼도 계산에는 반영되지 않으므로(끌 이유가 없는 컨트롤이라 레이어가 항상
  // 이긴다), 여기서는 켜진 채로 잠가서 보여준다. web/build.py `_forced_control` 참고.
  const forced = (rec?.forced_control || [])
    .filter((r) => r.with.every((n) => d.names.includes(n)));
  const forcedKey = (key) => forced.find((r) => r.key === key);

  const head = el("div", "ctrl-head");
  head.append(el("b", null, name));
  head.append(el("span", null, T("{v} · 컨트롤", { v: rec?.weapon || "" })));
  // 「전부 자동」은 **누르는 버튼이자 켜져 있는 상태**다. 아무것도 안 켰을 때
  // 회색으로 죽여 두면 «못 누른다»로 읽혀서, 지금이 자동인지 아닌지가 안 보였다.
  // 아래 칩들과 같은 언어로 — 아무것도 안 켜져 있으면 이쪽에 불이 들어온다.
  const auto = !Object.keys(c).length;
  const off = mkBtn(T("전부 자동"), `ctrl-auto${auto ? " on" : ""}`, () => {
    if (auto) return;
    delete d.control?.[name];
    saveAll(); buildControl(); refreshSlots(); renderResults();
  });
  off.setAttribute("aria-pressed", String(auto));
  off.title = auto ? T("지금 전부 자동입니다") : T("이 니케의 컨트롤을 모두 끕니다");
  head.append(off);
  wrap.append(head);

  const opts = el("div", "ctrl-opts");
  if (charge) {
    opts.append(ctrlCheck(T("톡톡이"), !!c.tap_fire, (on) =>
      ctrlToggle(name, "tap_fire", on, () => ({ rate: 3.6, release: 0.03 })),
      T("차지를 끝까지 하지 않고 짧게 눌렀다 떼기를 반복합니다 — ")
      + T("발당 대미지는 낮지만 발사 횟수가 늘어납니다 (차지형 전용)")));
    if (c.tap_fire) {
      opts.append(selectEl(TAP_RATES, String(c.tap_fire.rate ?? 3.6), (v) =>
        setCtrl(name, "tap_fire", { ...c.tap_fire, rate: Number(v) })));
    }
  }
  opts.append(ctrlCheck(T("장전컨"), !!c.reload?.policy, (on) =>
    setCtrl(name, "reload", on
      ? { ...(c.reload || {}), policy: "before_fb_end" }
      : (c.reload?.cancel_on_full ? { cancel_on_full: true } : null)),
    T("엄폐로 재장전을 유리한 버프 구간에 밀어 넣습니다 — ")
    + T("버프가 없는 시간에 장전을 끝내 두는 컨트롤입니다")));
  if (c.reload?.policy) {
    opts.append(selectEl(RELOAD_POLICIES, c.reload.policy, (v) =>
      setCtrl(name, "reload", { ...c.reload, policy: v })));
  }
  opts.append(ctrlCheck(T("탄충 취소"), !!c.reload?.cancel_on_full, (on) =>
    setCtrl(name, "reload", on
      ? { ...(c.reload || {}), cancel_on_full: true }
      : (c.reload?.policy ? { ...c.reload, cancel_on_full: undefined } : null)),
    T("재장전 중에 스킬의 탄환 충전으로 탄창이 차면 재장전을 끊고 바로 사격합니다")));
  const coverForced = forcedKey("cover");
  const coverChip = ctrlCheck(T("버스트 엄폐컨"), coverForced ? true : !!c.cover, (on) => {
    if (on && charge) setCtrl(name, "hold", null);
    ctrlToggle(name, "cover", on, () => ({ policy: "own_full_burst" }));
  },
    (coverForced ? coverForced.note + " " : "")
    + T("본인 버스트 사이클의 풀버스트 동안 엄폐해 한 발도 쏘지 않습니다 — ")
    + T("발수로 소모되는 버프를 일반 공격에 낭비하지 않으려는 컨트롤입니다")
    + (charge ? T(" (차지형은 홀드가 낫습니다 — 켜면 홀드가 꺼집니다)") : ""));
  if (coverForced) { coverChip.disabled = true; coverChip.classList.add("forced"); }
  opts.append(coverChip);
  if (charge) {
    const holdForced = forcedKey("hold");
    const holdChip = ctrlCheck(T("홀드"), holdForced ? true : !!c.hold, (on) => {
      if (on) setCtrl(name, "cover", null);
      ctrlToggle(name, "hold", on, () => ({ policy: "own_full_burst", lead: 0.5 }));
    },
      (holdForced ? holdForced.note + " " : "")
      + T("풀차지 후 떼지 않고 들고 있다가 자기 풀버스트 안에서 발사합니다 — ")
      + T("버프와 차지 배율을 센 한 방에 몰아줍니다 (차지형 전용, 엄폐컨보다 유리 — 켜면 엄폐컨이 꺼집니다)"));
    if (holdForced) { holdChip.disabled = true; holdChip.classList.add("forced"); }
    opts.append(holdChip);
  }
  // 버스트 운용도 같은 줄에 잇는다 — `.ctrl-opts`가 flex-wrap이라 칸이 모자라면
  // 알아서 내려간다.
  //
  // 선버(배치보다 먼저)와 주기(정해진 사이클에만)는 **서로 배타다** — «미룰 캐릭터를
  // 앞으로 끌어온다»는 말이 안 되므로, 한쪽을 켜면 다른 쪽이 꺼진다.
  // 주기는 톡톡이와 같은 문법: 칩을 켜야 상세(어느 주기인지) 셀렉트가 나온다.
  // 선버는 같은 버스트 단계에서 **한 명만** — 두 명이 «내가 먼저»를 켜면 결국 배치
  // 순서로 갈리는데, 그건 켠 사람에게 거짓말이 된다. 라디오처럼 동작한다:
  // 켜면 같은 단계의 다른 선버가 이쪽으로 옮겨 온다 (잠그는 것보다 손이 덜 간다).
  const sameStage = (n) => {
    const o = byName.get(n);
    return o && rec && (o.burst === rec.burst || o.burst === "A" || rec.burst === "A");
  };
  const firstHolder = d.names.filter(Boolean).find(
    (n) => n !== name && d.control?.[n]?.burst_first && sameStage(n));
  const first = ctrlCheck(T("선버"), !!c.burst_first, (on) => {
    if (on) {
      patDraft = null;
      setCtrl(name, "burst_pattern", null);
      for (const n of d.names.filter(Boolean)) {
        if (n !== name && d.control?.[n]?.burst_first && sameStage(n)) {
          setCtrl(n, "burst_first", null);
        }
      }
    }
    setCtrl(name, "burst_first", on ? true : null);
  });
  if (firstHolder) {
    // 다른 멤버가 들고 있으면 흐리게 — «기본으로는 안 켜진다»가 보이게 한다.
    // 잠그지는 않는다: 누르면 그쪽 선버가 이쪽으로 옮겨 온다.
    first.classList.add("taken");
    first.title = T("켜면 {firstHolder}의 선버가 이쪽으로 옮겨집니다 — ", { firstHolder })
      + T("같은 버스트 단계에서는 한 명만");
  } else {
    first.title = T("같은 버스트 단계에서 배치 순서와 무관하게 먼저 사용합니다");
  }
  opts.append(first);

  // 버스트 금지 — 그 니케만 버스트 후보에서 뺀다. «버스트 주기»가 «언제 쓸지»를
  // 정하는 것과 달리 이건 «아예 안 쓴다»다. 쿨이 돌아온 서브딜러가 끼어들어 원하지
  // 않는 단계를 채우는 편성(토브·솔린 덱 등)에서 쓴다. 주기와 같이 켤 이유가 없어
  // 켜면 주기를 끈다.
  opts.append(ctrlCheck(T("버스트 금지"), c.no_burst === true, (on) => {
    if (on) {
      patDraft = null;
      setCtrl(name, "burst_pattern", null);
      setCtrl(name, "burst_first", null);
    }
    setCtrl(name, "no_burst", on ? true : null);
  }, T("이 니케는 버스트를 쓰지 않습니다 — 쿨이 돌아와도 다른 니케가 대신 씁니다")));

  // 버스트 주기 — 모든 니케에 뜬다. 알려진 정석(카탈로그)이 있는 캐릭터는 그걸
  // 프리셋으로 주고, 나머지는 직접 입력한다. 켜기 전까지는 자동(조합에 따라 계산기가
  // 정함)이고, «안 씀» 같은 별도 해제 옵션은 없다 — 끄면 그게 자동이다.
  const presets = Object.keys(rec?.patterns || {});
  const patOn = c.burst_pattern !== undefined || patDraft === name;
  opts.append(ctrlCheck(T("버스트 주기"), patOn, (on) => {
    if (on) {
      setCtrl(name, "burst_first", null);
      if (presets.length && !patternConflict(name, presets[0])) {
        patDraft = null;
        dropSameStageFirst(name);
        setCtrl(name, "burst_pattern", presets[0]);
      } else if (presets.length) {
        patDraft = name; buildControl(); patWarnIf(name, presets[0]);
      }
      else { patDraft = name; buildControl(); }   // 입력이 유효해질 때까지 저장하지 않는다
    } else {
      patDraft = null;
      setCtrl(name, "burst_pattern", null);
    }
  }, T("몇 번째 풀버스트에 버스트를 쓸지 정합니다 — 끄면 자동(조합에 따라 계산기가 정함)")));
  if (patOn) {
    const manual = c.burst_pattern !== undefined && !presets.includes(c.burst_pattern);
    if (presets.length) {
      const PATS = [...presets.map((v) => [v, v]), ["직접", T("직접 입력")]];
      const cur = manual || patDraft === name ? T("직접") : c.burst_pattern;
      const sel = selectEl(PATS, cur, (v) => {
        if (v === "직접") { patDraft = name; setCtrl(name, "burst_pattern", null); return; }
        if (patWarnIf(name, v)) { sel.value = cur; return; }   // 겹침 — 되돌린다
        patDraft = null;
        dropSameStageFirst(name);
        setCtrl(name, "burst_pattern", v);
      });
      opts.append(sel);
    }
    if (!presets.length || manual || patDraft === name) {
      const inp = el("input", "ctrl-pat");
      inp.type = "text";
      inp.placeholder = T("예: 1,3,5,9 (몇 번째 풀버스트인지)");
      inp.value = manual ? patternText(c.burst_pattern) : "";
      inp.onchange = () => {
        const v = parsePattern(inp.value);
        const clash = v ? patWarnIf(name, v) : false;
        inp.classList.toggle("bad", (!v && inp.value.trim() !== "") || clash);
        if (v && !clash) {
          patDraft = null;
          dropSameStageFirst(name);
          setCtrl(name, "burst_pattern", v);
        }
      };
      opts.append(inp);
    }
  }
  wrap.append(opts);

  wrap.append(el("p", "prose prose-sm",
    T("기본은 전부 자동입니다. 실제로는 한 번에 한 명만 조작할 수 있으니, ")
    + T("여러 명을 동시에 켜면 그만큼 비현실적인 상한이 됩니다.")
    + (rec?.pattern_note ? " " + rec.pattern_note : "")
    + forced.map((r) => " " + r.note).join("")));
}

/** 켜고 끄는 칩. 브라우저 기본 체크박스는 어두운 판에서 흰 상자로 튀어 나온다 —
 *  앱이 이미 쓰는 칩 언어(`.chip` / `.on`)를 그대로 쓴다. */
function ctrlCheck(label, on, onchange, tip) {
  const b = el("button", "chip ctrl-chip" + (on ? " on" : ""), label);
  b.type = "button";
  b.setAttribute("aria-pressed", String(on));
  b.onclick = () => onchange(!on);
  if (tip) b.title = tip;
  return b;
}

// ── 전투 조건 ───────────────────────────────────────────────────────────
const BT_FIELDS = [
  ["#bt-def", "def", "int"], ["#bt-core", "core_px", "int"],
  ["#bt-parts", "has_parts", "bool"], ["#bt-partint", "part_break_interval", "num"],
  ["#bt-maxburst", "max_burst_count", "int"], ["#bt-first", "first_burst_time", "num"],
  ["#bt-switch", "burst_switch_delay", "num"], ["#bt-reenter", "burst_reenter_delay", "num"],
];

function buildBattle() {
  const durIn = $("#duration");
  if (durIn) durIn.value = String(durationNow());
  for (const [sel, key, kind] of BT_FIELDS) {
    const n = $(sel);
    if (!n) continue;
    if (kind === "bool") n.checked = !!battleNow()[key];
    else n.value = battleNow()[key];
    n.onchange = () => {
      const raw = kind === "bool" ? n.checked : Number(n.value);
      let v = raw;
      if (kind !== "bool") {
        const lo = Number(n.min || 0), hi = Number(n.max || Infinity);
        v = Math.min(hi, Math.max(lo, Number.isFinite(raw) ? raw : BATTLE_DEFAULT[key]));
        if (kind === "int") v = Math.round(v);
        n.value = v;
      }
      battleNow()[key] = v;
      syncBattleChrome(); saveAll(); renderAll();
    };
  }
  const wrap = $("#bt-range");
  wrap.textContent = "";
  for (const w of WEAPONS) {
    const b = el("button", "chip" + (battleNow().optimal_range_weapons.includes(w) ? " on" : ""), w);
    b.type = "button";
    b.onclick = () => {
      const arr = battleNow().optimal_range_weapons;
      const i = arr.indexOf(w);
      if (i === -1) arr.push(w); else arr.splice(i, 1);
      b.classList.toggle("on", arr.includes(w));
      syncBattleChrome(); saveAll(); renderAll();
    };
    wrap.append(b);
  }
  // 무기군 평타 계수 — 실전에서 탄퍼짐으로 새는 탄의 보정. 항상 6칸 전부 보여 주고
  // 기본값을 칸 옆에 적는다 (SG만 0.9, 근거는 BATTLE_DEFAULT 주석).
  const cw = $("#bt-coeff");
  if (cw) {
    cw.textContent = "";
    for (const w of WEAPONS) {
      const lab = el("label", "coeff-item");
      // 기본값은 라벨 옆에 흐리게 — 별도 줄을 쓰면 좁은 화면에서 두 줄로 꺾인다
      const name = el("span", "coeff-name", w);
      const def = el("em", "", BATTLE_DEFAULT.weapon_coeff[w].toFixed(2));
      def.title = T("기본값");
      name.append(def);
      const inp = el("input", "");
      inp.type = "number"; inp.min = "0.1"; inp.max = "1.5"; inp.step = "0.05";
      inp.inputMode = "decimal";
      inp.value = battleNow().weapon_coeff[w];
      inp.onchange = () => {
        let v = Number(inp.value);
        if (!Number.isFinite(v)) v = BATTLE_DEFAULT.weapon_coeff[w];
        v = Math.min(1.5, Math.max(0.1, v));
        inp.value = v;
        battleNow().weapon_coeff[w] = v;
        syncBattleChrome(); saveAll(); renderAll();
      };
      lab.append(name, inp);
      cw.append(lab);
    }
  }
  syncBattleChrome();
}

function syncBattleChrome() {
  const changed = battleSig() !== "def";
  // 트리거는 «무엇을 여는 버튼인지»를 말해야 한다. 값(180초)만 적으면 정체를 알 수 없다.
  $("#bt-trig-label").textContent = changed ? T("레이드 설정 *") : T("레이드 설정");
  $("#bt-toggle").classList.toggle("filtered", changed);
  $("#bt-clear").hidden = !changed || $("#btpanel").hidden;
}

function resetBattle() {
  const box = { ...BATTLE_DEFAULT, optimal_range_weapons: [],
                weapon_coeff: { ...BATTLE_DEFAULT.weapon_coeff } };
  // 유니온은 설정이 **줄마다** 따로다 — 지금 패널이 보고 있는 줄을 되돌린다.
  // 예전 공용 상자(U().battle)에 쓰면 화면이 그대로라 「눌러도 아무 일도 안 난다」가
  // 된다(실측). 그 상자는 이제 새 줄에 값을 심을 때의 씨앗으로만 남는다.
  if (modeNow() === "union") uDeck(uBattleRow).battle = box; else state.battle = box;
  // 전투 시간은 `battleNow()`이 아니라 `state.settings`에 있다 — 예전에는 여기서
  // 안 되돌려서, «기본값»을 눌러도 예전에 저장해 둔 시간(160초 등)이 그대로
  // 남았다. 레이드 설정 패널 안에 있는 입력이니 같이 되돌린다.
  if (modeNow() === "union") U().duration = 180; else state.settings.duration = 180;
  const dur = $("#duration");
  if (dur) dur.value = String(durationNow());
  buildBattle();
  saveAll(); renderAll();
}

function clearFilters() {
  const f = curFilter();
  // 패널의 «비우기»는 **패널 것만** 비운다 — 위쪽 바의 버스트·즐겨찾기는 건드리지 않는다.
  f.cls = []; f.element = []; f.weapon = []; f.corp = []; f.favItem = false;
  buildFilters();
  saveAll(); renderPools();
}

// ── 편성 공유 ───────────────────────────────────────────────────────────
// 남에게 주는 것은 **편성과 표시용 딜 수치뿐**이다. 닉네임(`profileName`)·스펙 지문
// (`profileSig`)·기본 스펙 이탈 목록(`notes`)·니케별 내역(`detail`)은 담지 않는다.
// `notes`에는 `equip_skills.charge_speed_pct: 0 → 9.26`처럼 **장비 실수치가 문장으로**
// 들어 있어서, 그대로 올리면 «편성만 공유한다»가 사실이 아니게 된다.
// 서버도 같은 화이트리스트로 다시 짓지만(`share_clean`), 애초에 브라우저를 떠나지
// 않는 것이 맞다 — 여기가 첫 번째 문이다.
//
// 받는 쪽이 가져가는 것은 **편성뿐**이다. 컨트롤(운용)도 보내지 않으므로 가져온 덱은
// «전부 자동»으로 들어간다.

function sharePayload(r) {
  const out = {
    v: SHARE_V,
    code: r.code || null,
    duration: r.duration,
    total: r.total,
    decks: r.decks.map((d) => {
      const one = { names: [...d.names], total: d.total, chars: { ...(d.chars || {}) } };
      if (d.weak) one.weak = d.weak;      // 그 줄이 친 보스
      return one;
    }),
  };
  // **어느 콘텐츠의 편성인가.** 안 실으면 받는 쪽이 지금 보고 있는 모드로 짐작해야
  // 하고, 유니온 편성이 솔로 덱에 들어가 버린다(실측). 없으면 솔로다 — 예전 링크는
  // 그대로 산다.
  //
  // `r.mode`가 없는 것은 **이 열쇠가 생기기 전에 저장된 기록**이다. 기록 목록은
  // 애초에 모드별로 갈려 있으므로(recordsNow), 지금 서 있는 모드가 곧 그 기록의
  // 모드다 — 유니온 목록에서 고른 것이 솔로 기록일 수는 없다.
  if ((r.mode || modeNow()) === "union") out.mode = "union";
  return out;
}

const shareUrl = (code) => `${location.origin}/s?c=${encodeURIComponent(code)}`;

/** 공유본을 서버에 올리고 링크 상자를 `out`에 그린다. 문구는 `sink`가 받는다. */
async function makeShare(r, out, sink) {
  if (!HEALTH.share) {
    sink(T("이 서버는 공유 저장소가 꺼져 있습니다 — 링크를 만들 수 없습니다."), "err");
    return;
  }
  if (!r.decks.length) {
    sink(T("공유할 계산 결과가 없습니다 — 먼저 계산하세요."), "err");
    return;
  }
  sink(T("공유 링크를 만드는 중…"));
  try {
    const res = await fetch("/api/share", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sharePayload(r)),
    });
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    renderShareOut(out, j.code, sink);
    // 링크는 이미 만들어져 화면에 떠 있다 — 복사가 막힌 것은 오류가 아니라 안내다
    await copyInto(shareUrl(j.code), sink,
      T("공유 링크를 복사했습니다 — 24시간 뒤 사라집니다."),
      T("링크를 만들었습니다 — 복사가 막혀 있어 아래 주소를 직접 복사하세요."), "warn");
  } catch (e) {
    sink(T("공유에 실패했습니다 — {v}", { v: String(e.message || e) }), "err");
  }
}

/** 만든 링크 상자. 주소·복사·삭제. **주소는 서버가 아니라 여기서 짓는다** —
 *  서버가 지으려면 프록시 헤더(`X-Forwarded-Host`)를 믿어야 한다. */
function renderShareOut(out, code, sink) {
  if (!out) return;
  out.hidden = false;
  out.textContent = "";
  const url = shareUrl(code);
  const row = el("div", "share-row");
  const inp = el("input", "share-url");
  inp.type = "text";
  inp.readOnly = true;
  inp.value = url;
  inp.setAttribute("aria-label", T("공유 링크"));
  inp.onclick = () => inp.select();
  row.append(inp);
  row.append(mkBtn(T("복사"), "btn-primary", () => copyInto(url, sink,
    T("공유 링크를 복사했습니다."), T("복사가 막혔습니다 — 주소를 직접 복사하세요."))));
  row.append(mkBtn(T("링크 삭제"), "btn-ghost", () => {
    askInline(out, T("이 링크를 지금 지웁니다. 받은 사람은 더 이상 열 수 없습니다."), T("지우기"),
      async () => {
    try {
      const res = await fetch("/api/unshare", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const j = await res.json();
      if (j.error) throw new Error(j.error);
      out.hidden = true;
      out.textContent = "";
      sink(j.deleted ? T("링크를 지웠습니다.") : T("이미 사라진 링크입니다."), "ok");
    } catch (e) {
      sink(T("삭제에 실패했습니다 — {v}", { v: String(e.message || e) }), "err");
    }
      });
  }));
  out.append(row);
  out.append(el("p", "prose prose-sm",
    T("이 링크에는 편성과 딜 수치만 담겨 있습니다 — 육성 스펙·계정 이름은 올라가지")
    + T(" 않습니다. 24시간이 지나면 서버에서 사라집니다.")));
}

// ── 공유된 편성 받기 ────────────────────────────────────────────────────

/** 공유 화면으로 들어간다. 탭은 이때 처음 나타난다 — 평소에는 없는 탭이다. */
function openShareTab() {
  const tab = $("#tab-share");
  if (!tab) return;
  tab.hidden = false;
  tab.click();
}

async function loadShared(code) {
  openShareTab();
  // 주소는 **곧바로** 지운다. 목적이 «새로 고치면 이 화면이 아니게»이므로 성공·실패를
  // 가리지 않는다 — 만료된 링크도 새로 고침에서 오류를 되풀이하지 않아야 한다.
  clearShareUrl();
  shareMsg(T("공유된 편성을 받는 중…"));
  try {
    const res = await fetch(`/api/share?c=${encodeURIComponent(code)}`);
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    shared = j;
    // 공유본이 **어느 콘텐츠의 편성인지** 스스로 말한다(없으면 솔로). 받는 쪽 모드가
    // 다르면 먼저 맞춰 준다 — 안 그러면 유니온 편성이 솔로 덱으로 들어간다(실측).
    const want = j.mode === "union" ? "union" : "solo";
    if (want !== modeNow() && (want !== "union" || unionOn())) {
      setMode(want, { warp: false });
      openShareTab();                    // setMode가 편성 탭으로 보낸다 — 되돌아온다
    }
    shareMsg("");
    renderShared();
  } catch (e) {
    shared = null;
    const body = $("#share-body");
    if (body) body.textContent = "";
    const cond = $("#share-cond");
    if (cond) cond.textContent = "—";
    shareMsg(String(e.message || e), "err");
  }
}

/** 들어올 이름 목록이 **내 어느 덱과 부딪치는가**.
 *
 *  대상 덱 자신은 통째로 덮어쓰므로 충돌이 아니다 — 그걸 세면 「내 02덱에 있는 앨리스를
 *  02덱으로 가져오는데 충돌」이라는 거짓 경고가 뜬다. 솔로레이드는 덱 간 중복이
 *  불가하므로, 다른 덱에 있는 같은 니케는 그 덱에서 비워야 한다. */
function shareConflicts(names, target) {
  const out = [];
  const want = new Set(names.filter(Boolean));
  state.decks.forEach((d, di) => {
    if (di === target) return;
    (d.names || []).forEach((n, si) => {
      if (n && want.has(n)) out.push({ name: n, deck: di, slot: si });
    });
  });
  return out;
}

/** 공유된 덱 하나를 내 덱 `target`에 넣는다. 공유는 운용을 담지 않으므로 컨트롤은 빈다. */
const importSharedDeck = (target, names) => importMapped([{ names, target }]);

/** 을/를. 니케 이름이 문장에 끼는 자리라 하나로 고정할 수 없다 —
 *  «앨리스을 비웠습니다»가 된다. 마지막 글자에 종성이 있으면 «을». */
/** 이/가. 같은 이유다 — «홍련 : 흑영가 받습니다»가 된다. */
function ga(word) {
  const ch = String(word ?? "").trim().slice(-1);
  const c = ch.charCodeAt(0);
  const jong = c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 !== 0;
  return jong ? "이" : "가";
}

/** 은/는. `eul`과 같은 이유로 필요하다 — «미하라 : 본딩 체인는»이 된다. */
function eun(word) {
  const ch = String(word ?? "").trim().slice(-1);
  const c = ch.charCodeAt(0);
  const jong = c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 !== 0;
  return jong ? "은" : "는";
}

function eul(word) {
  const ch = String(word ?? "").trim().slice(-1);
  const c = ch.charCodeAt(0);
  const jong = c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 !== 0;
  return jong ? "을" : "를";
}

/** 이름 목록을 짧게. 다섯 명을 다 적으면 문장이 두 줄을 넘어 정작 요점이 안 읽힌다. */
const briefNames = (ns) => (ns.length <= 3
  ? ns.map(T).join(" · ")
  : T("{v} 외 {v1}명", { v: ns.slice(0, 2).map(T).join(" · "), v1: ns.length - 2 }));

/** 주소에서 공유 코드를 뗀다. **화면을 띄우자마자 부른다.**
 *
 *  공유 화면은 «링크를 눌러 한 번 보는 화면»이다. 주소에 코드가 남아 있으면 새로
 *  고칠 때마다 이 화면으로 돌아와, 평소 화면으로 가려면 매번 탭을 다시 눌러야 한다.
 *  주소를 지우면 새로 고침이 평소 화면으로 간다 — 링크 원본은 받은 사람이 카톡·
 *  디스코드에 그대로 갖고 있으므로 잃는 것이 없다.
 *
 *  화면 자체는 지우지 않는다. 이미 받아 둔 편성이 메모리에 남아 있어서, 주소가
 *  정리된 뒤에도 덱을 하나씩 이어서 가져올 수 있다.
 *
 *  `pushState`가 아니라 `replaceState`다 — 히스토리에 항목을 더하면 뒤로 가기가
 *  «아무 일도 안 일어나는 한 번»을 먹는다. */
function clearShareUrl() {
  if (!location.search) return;
  try { history.replaceState(null, "", "/"); } catch { /* 파일 프로토콜 등 */ }
}

/** 가져온 결과를 사람 문장으로. 「몇 명 들어갔고, 어디서 비웠고, 누가 빠졌는지」 */
function importReport(res) {
  const parts = [T("{count}명을 넣었습니다.", { count: res.count })];
  if (res.shifted?.length) {
    parts.push(T("원래 있던 편성은 {v}", { v: res.shifted.map((x) => T("{v}덱→{v1}덱", { v: x.from + 1, v1: x.to + 1 })).join(" · ") })
               + T("으로 옮겼습니다."));
  }
  if (res.lost?.length) {
    parts.push(T("빈 덱이 없어 {v}의 편성은 사라졌습니다.", { v: res.lost.map((t) => T("{v}덱", { v: t + 1 })).join(" · ") }));
  }
  if (res.moved.length) {
    // 니케마다 «이름(N덱)»을 늘어놓으면 같은 덱 번호가 다섯 번 반복된다 — 덱으로 묶는다
    const byDeck = new Map();
    for (const c of res.moved) {
      if (!byDeck.has(c.deck)) byDeck.set(c.deck, []);
      byDeck.get(c.deck).push(c.name);
    }
    const where = [...byDeck.entries()].sort((a, b) => a[0] - b[0])
      .map(([d, ns]) => T("{v}덱에서 {v1}", { v: d + 1, v1: briefNames([...new Set(ns)]) }));
    const list = where.join(", ");
    parts.push(T("덱 간 중복이라 {list}{v} 비웠습니다.", { list, v: eul(list) }));
  }
  if (res.missing.length) {
    const who = [...new Set(res.missing)];
    parts.push(T("내 스펙에 없는 {length}명은 빈 자리입니다 — {v}.", { length: who.length, v: briefNames(who) }));
  }
  // 공유본 **자체가** 덱 간 중복을 갖고 있을 수 있다 — 사이트는 중복 편성도 경고만 하고
  // 저장을 허용한다. 편성 탭의 중복 경고를 보기 전에 여기서 먼저 말해 준다.
  if (res.dup?.length) {
    parts.push(T("공유된 편성에 덱 간 중복이 있습니다 — {v}.", { v: briefNames(res.dup) })
               + T(" 솔로레이드에서는 불가능한 편성이니 한쪽을 바꿔야 합니다."));
  }
  parts.push(T("수치는 내 스펙으로 다시 계산해야 합니다."));
  return parts.join(" ");
}

/** 대상 덱 고르개. 덱 블록 안에서 펼친다 — 모달로 띄우면 원본 편성이 가려진다. */
function sharePickBox(srcIdx, names, host) {
  // 열려 있던 고르개를 다시 누르면 접는다. 다만 **이미 가져온 결과 상자**는 «열린 고르개»가
  // 아니다 — 그걸 접기로 세면 다른 덱으로 한 번 더 가져올 수가 없다.
  const open = host.querySelector(".share-pick:not(.done)");
  for (const x of document.querySelectorAll(".share-pick")) x.remove();
  if (open) return;

  const box = el("div", "share-pick");
  box.append(el("p", "share-pick-h",
    T("«{v}» 편성을 내 어느 덱으로 가져올까요?", { v: String(srcIdx + 1).padStart(2, "0") })));
  const inNames = names.filter(Boolean);
  box.append(el("p", "share-pick-src", inNames.map(T).join(" · ") || T("빈 덱")));

  let target = state.settings.deck;
  const rows = el("div", "share-targets");
  const foot = el("div", "share-pick-foot");
  const go = mkBtn(T("가져오기"), "btn-amber", () => {
    const res = importSharedDeck(target, names);
    const kind = res.missing.length || res.moved.length ? "warn" : "ok";
    const text = T("«내 {v}덱»에 {v1}", { v: target + 1, v1: importReport(res) });
    // 결과는 **누른 자리에** 남긴다 — 아래쪽 덱에서 누른 사람은 화면 맨 위 문구를 못 본다
    box.textContent = "";
    box.className = `share-pick done ${kind}`;
    box.append(el("p", "share-pick-done", text));
    shareMsg(text, kind);
  });

  const paint = () => {
    rows.textContent = "";
    // **지금 모드의 덱**을 대상으로 삼는다. 유니온에서 가져왔는데 솔로 덱에 들어가면
    // 「가져왔다는데 화면엔 없다」가 된다(실측).
    for (let i = 0; i < deckCountNow(); i++) {
      const mine = (deckAt(i)?.names || []).filter(Boolean);
      const hit = shareConflicts(names, i);
      const row = el("button", "share-target" + (i === target ? " on" : ""));
      row.type = "button";
      row.setAttribute("aria-pressed", String(i === target));
      row.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
      const mid = el("span", "share-target-mid");
      mid.append(el("span", "share-target-now", mine.length ? mine.join(" · ") : T("빈 덱")));
      const note = el("span", "share-target-note" + (hit.length ? " warn" : ""));
      if (hit.length) {
        const from = [...new Set(hit.map((c) => c.deck))].sort((a, b) => a - b);
        note.textContent = T("충돌 {length}명 — {v}", { length: hit.length, v: from.map((x) => x + 1 + T("덱")).join(" · ") })
          + T("에서 비웁니다");
      } else {
        note.textContent = T("충돌 없음");
      }
      mid.append(note);
      row.append(mid);
      row.onclick = () => { target = i; paint(); go.textContent = T("{v}덱에 가져오기", { v: i + 1 }); };
      rows.append(row);
    }
  };
  paint();
  box.append(rows);

  const missing = inNames.filter((n) => !haveChar(n));
  if (missing.length) {
    box.append(el("p", "share-pick-note",
      T("내 스펙에 없는 {length}명은 빈 자리로 들어갑니다 — {v}.", { length: missing.length, v: missing.join(" · ") })));
  }
  go.textContent = T("{v}덱에 가져오기", { v: target + 1 });
  foot.append(mkBtn(T("취소"), "btn-ghost", () => box.remove()));
  foot.append(go);
  box.append(foot);
  host.append(box);
}

/** 내 스펙에 있는 것만 남긴 5칸 배열. 없는 니케는 **빈 자리**로 둔다. */
function fitNames(names, missing) {
  const kept = [];
  for (const nm of (names || []).slice(0, SLOTS)) {
    if (!nm) { kept.push(null); continue; }
    if (haveChar(nm)) kept.push(nm);
    else { kept.push(null); if (missing) missing.push(nm); }
  }
  while (kept.length < SLOTS) kept.push(null);
  return kept;
}

/** 공유된 덱 여러 개를 내 같은 번호 덱에 덮는다. `which`는 덱 번호 목록(없으면 전부). */
function importSharedAll(sh, which) {
  const all = [...Array(Math.min(sh.decks.length, deckCountNow())).keys()];
  const idx = (which && which.length ? [...which] : all).sort((a, b) => a - b);
  return importMapped(idx.map((i) => ({ names: sh.decks[i]?.names, target: i })));
}

/** **편성을 내 덱에 넣는 단 하나의 경로.** 공유·프리셋이 모두 이걸 부른다 —
 *  덱 간 중복 처리를 두 벌 두면 한쪽만 고쳐져서 조용히 갈린다.
 *
 *  `entries` = `[{names, target}]`
 *
 *  - 내 스펙에 없는 니케는 **빈 자리**로 둔다 (자리를 당기지 않는다 — 누가 비었는지 보여야 한다)
 *  - **대상 덱에 있던 편성은 버리지 않고 빈 덱으로 옮긴다.** 3덱에 넣는다고 3덱에 짜
 *    두었던 편성이 사라지면, 되돌릴 방법이 없다. 빈 덱이 없을 때만 사라진다(그때는 말해 준다)
 *  - **덮이지 않는 덱에서만** 같은 니케를 비운다. 덱을 하나씩 넣으면 앞서 넣은 덱에서
 *    다시 비우는 일이 생긴다(들어오는 편성 안에 같은 니케가 두 번 있으면)
 *  - **컨트롤은 비운다.** 공유도 프리셋도 운용을 담지 않으므로 «전부 자동»에서 시작한다
 *  - `opts.cond`가 있으면 약점 코드·전투 시간까지 되돌린다. **기록만** 이걸 쓴다 —
 *    기록은 «그 조건에서 이 수치가 나왔다»는 뜻이라 조건을 떼면 수치를 읽을 수 없다
 *
 *  결과 캐시는 따로 지우지 않아도 된다: 지문(`fingerprint`)에 `names`가 들어 있어서
 *  이름이 바뀐 덱은 자동으로 «계산 안 된 덱»이 된다. */
function importMapped(entries, opts = {}) {
  const missing = [], moved = [];
  const union = modeNow() === "union";
  const nDecks = deckCountNow();
  const incoming = new Map();          // 내 덱 번호 → 이름 5칸
  // 유니온은 «어느 보스를 어떤 조건으로» 까지가 한 편성이다 — 따로 실어 둔다.
  const extra = new Map();
  for (const e of entries || []) {
    const t = Number(e?.target);
    if (!Number.isInteger(t) || t < 0 || t >= nDecks) continue;
    incoming.set(t, fitNames(e.names, missing));
    if (union && (e.weak || e.battle)) extra.set(t, { weak: e.weak, battle: e.battle });
  }
  if (!incoming.size) return { count: 0, decks: 0, missing, moved, dup: [], shifted: [], lost: [] };

  // 밀려나는 편성을 먼저 옮긴다. **비우는 것보다 먼저** 해야 한다 — 옮긴 덱도 «덮이지
  // 않는 덱»이 되어 아래 중복 비우기의 대상이 되어야 하기 때문이다.
  const { shifted, lost } = shiftDisplaced([...incoming.keys()]);

  const want = new Set([...incoming.values()].flat().filter(Boolean));
  for (let i = 0; i < nDecks; i++) {
    if (incoming.has(i)) continue;
    const d = deckAt(i);
    d.names.forEach((nm, si) => {
      if (!nm || !want.has(nm)) return;
      moved.push({ name: nm, deck: i, slot: si });
      d.names[si] = null;
      if (d.control) delete d.control[nm];   // 덱에서 빠진 니케의 운용은 따라다니지 않는다
    });
  }

  let count = 0;
  for (const [i, names] of incoming) {
    const d = deckAt(i);
    d.names = names;
    d.control = {};
    count += names.filter(Boolean).length;
    // 보스·레이드 설정은 **있을 때만** 덮는다. 옛 프리셋(편성만 담긴 것)을 불러왔다고
    // 지금 걸어 둔 보스가 지워지면 안 된다.
    const ex = extra.get(i);
    if (ex?.weak && UNION_CODES.includes(ex.weak)) d.weak = ex.weak;
    if (ex?.battle) d.battle = JSON.parse(JSON.stringify(ex.battle));
  }

  if (opts.cond) applyCond(opts.cond.code, opts.cond.duration);

  if (!union) state.settings.deck = [...incoming.keys()].sort((a, b) => a - b)[0] ?? 0;
  else uBattleRow = [...incoming.keys()].sort((a, b) => a - b)[0] ?? 0;
  ctrlOpen = null; picked = null;
  saveAll(); renderAll();

  const seen = new Map();
  for (const nm of [...incoming.values()].flat()) if (nm) seen.set(nm, (seen.get(nm) || 0) + 1);
  const dup = [...seen].filter(([, c]) => c > 1).map(([nm]) => nm);
  return { count, decks: incoming.size, missing, moved, dup, shifted, lost };
}

/** 대상 덱에 있던 편성을 빈 덱으로 옮긴다. 반환: 옮긴 목록과 옮길 자리가 없던 덱.
 *
 *  빈 덱을 앞에서부터 쓰고, **대상으로 지정된 덱은 자리로 쓰지 않는다** — 곧 덮일
 *  자리에 옮겨 두면 옮긴 의미가 없다. 미리보기(`planDisplaced`)와 **같은 규칙**이어야
 *  한다: 화면이 「5덱으로 옮깁니다」라고 했으면 실제로 5덱에 있어야 한다. */
function shiftDisplaced(targets) {
  const set = new Set(targets);
  const free = [...Array(deckCountNow()).keys()]
    .filter((i) => !set.has(i) && !deckAt(i).names.some(Boolean));
  const shifted = [], lost = [];
  for (const t of [...set].sort((a, b) => a - b)) {
    const d = deckOf(t);
    if (!d.names.some(Boolean)) continue;
    const to = free.shift();
    if (to === undefined) { lost.push(t); continue; }
    const dst = deckOf(to);
    dst.names = [...d.names];
    dst.control = structuredClone(d.control || {});
    d.names = Array(SLOTS).fill(null);
    d.control = {};
    shifted.push({ from: t, to });
  }
  return { shifted, lost };
}

/** 대상 덱이 겹치지 않게 자리를 다시 나눈다.
 *
 *  「내 2덱」을 골랐는데 다른 행이 이미 2덱을 쓰고 있으면, **방금 고른 쪽을 살리고**
 *  그 행을 빈 자리로 밀어낸다. 「겹칩니다, 다시 고르세요」로 막으면 사용자가 순서를
 *  스스로 풀어야 한다 — 자리는 어차피 남아 있으므로 화면이 풀어 주는 게 맞다.
 *
 *  `keep`은 방금 손댄 행이다. 그 행의 선택은 건드리지 않는다.
 *  밀어낼 자리는 **비어 있는 덱을 먼저** 고른다 — 짜 둔 편성이 덮일 확률을 줄인다.
 *  (행 수는 덱 수를 넘지 않으므로 자리는 늘 남는다.) */
function dedupeTargets(pick, on, keep) {
  const order = [keep, ...pick.map((_, k) => k).filter((k) => k !== keep)];
  const used = new Set();
  for (const k of order) {
    if (!on[k]) continue;
    if (!used.has(pick[k])) { used.add(pick[k]); continue; }
    const cand = [...Array(deckCountNow()).keys()].filter((x) => !used.has(x));
    const to = cand.find((x) => !deckAt(x).names.some(Boolean)) ?? cand[0];
    if (to === undefined) continue;                 // 자리가 없다 — 경고가 대신 잡는다
    pick[k] = to;
    used.add(to);
  }
}

/** 옮김 계획을 **미리** 계산한다 (실제로 옮기지는 않는다). 시트의 미리보기가 쓴다. */
function planDisplaced(targets) {
  const set = new Set(targets);
  const free = [...Array(deckCountNow()).keys()]
    .filter((i) => !set.has(i) && !deckAt(i).names.some(Boolean));
  const shifted = [], lost = [];
  for (const t of [...set].sort((a, b) => a - b)) {
    if (!deckOf(t).names.some(Boolean)) continue;
    const to = free.shift();
    if (to === undefined) lost.push(t);
    else shifted.push({ from: t, to });
  }
  return { shifted, lost };
}

/** 전부 가져오기 시트. **되돌릴 수 없는 조작이라 미리 보여 준다** —
 *  어느 덱이 덮이고, 어느 덱에서 누가 비워지고, 누가 빠지는지. */
function openShareAllSheet(sh) {
  const dlg = $("#share-sheet");
  const body = $("#share-sheet-body");
  const go = $("#share-sheet-go");
  if (!dlg || !body || !go) return;
  const n = Math.min(sh.decks.length, deckCountNow());
  const pick = new Set([...Array(n).keys()]);          // 기본은 전부 고른 상태

  const paint = () => {
    const t = $("#share-sheet-t");
    if (t) t.textContent = T("공유된 {n}덱 가져오기", { n });
    body.textContent = "";
    body.append(el("p", "prose prose-sm",
      T("고른 덱이 내 같은 번호 덱을 덮습니다. 들어가는 것은 편성뿐이고 컨트롤은")
      + T(" «전부 자동»이 됩니다 — 수치는 편성 탭에서 다시 계산하세요.")));

    const list = el("div", "share-pairs");
    for (let i = 0; i < n; i++) {
      const on = pick.has(i);
      const src = (sh.decks[i].names || []).filter(Boolean);
      const mine = (state.decks[i]?.names || []).filter(Boolean);
      const row = el("button", "share-pair" + (on ? " on" : ""));
      row.type = "button";
      row.setAttribute("aria-pressed", String(on));
      row.append(el("span", "share-pair-ck", on ? "✓" : ""));
      row.append(el("span", "rec-no", String(i + 1).padStart(2, "0")));
      const mid = el("span", "share-pair-mid");
      mid.append(el("span", "share-pair-src", src.join(" · ") || T("빈 덱")));
      mid.append(el("span", "share-pair-dst" + (on ? " on" : ""),
        on ? T("내 {v}덱을 덮습니다 — 지금 {v1}", { v: i + 1, v1: mine.length ? mine.join(" · ") : T("빈 덱") })
           : T("가져오지 않습니다 — 내 {v}덱은 그대로", { v: i + 1 })));
      row.append(mid);
      row.onclick = () => { if (on) pick.delete(i); else pick.add(i); paint(); };
      list.append(row);
    }
    body.append(list);

    // 미리보기 — 고른 조합이 무엇을 비우고 무엇을 빈 자리로 남기는가
    const idx = [...pick].sort((a, b) => a - b);
    const names = idx.flatMap((i) => (sh.decks[i].names || []).filter(Boolean));
    const missing = [...new Set(names.filter((x) => !haveChar(x)))];
    const want = new Set(names.filter(haveChar));
    const emptied = new Map();
    for (let i = 0; i < deckCountNow(); i++) {
      if (pick.has(i)) continue;
      for (const nm of (deckAt(i)?.names || [])) {
        if (!nm || !want.has(nm)) continue;
        if (!emptied.has(i)) emptied.set(i, []);
        emptied.get(i).push(nm);
      }
    }
    const notes = el("div", "share-sheet-notes");
    if (emptied.size) {
      const where = [...emptied.entries()].sort((a, b) => a[0] - b[0])
        .map(([d, ns]) => T("{v}덱에서 {v1}", { v: d + 1, v1: briefNames([...new Set(ns)]) })).join(", ");
      notes.append(el("p", "share-pick-note warn",
        T("덱 간 중복이라 {where}{v} 비웁니다.", { where, v: eul(where) })));
    }
    if (missing.length) {
      notes.append(el("p", "share-pick-note",
        T("내 스펙에 없는 {length}명은 빈 자리로 들어갑니다 — {v}.", { length: missing.length, v: briefNames(missing) })));
    }
    if (!pick.size) {
      notes.append(el("p", "share-pick-note warn", "가져올 덱을 하나 이상 고르세요."));
    }
    body.append(notes);

    go.disabled = !pick.size;
    go.textContent = pick.size === n ? T("{n}덱 전부 가져오기", { n }) : T("{size}덱 가져오기", { size: pick.size });
  };
  paint();

  const close = () => dlg.close();
  $("#share-sheet-x").onclick = close;
  $("#share-sheet-cancel").onclick = close;
  go.onclick = () => {
    const idx = [...pick].sort((a, b) => a - b);
    if (!idx.length) return;
    close();
    const res = importSharedAll(sh, idx);
    shareMsg(T("내 {decks}덱에 {v}", { decks: res.decks, v: importReport(res) }),
             res.missing.length || res.moved.length || res.dup?.length ? "warn" : "ok");
  };
  if (!dlg.open) dlg.showModal();
}

function renderShared() {
  const sh = shared;
  const body = $("#share-body");
  const acts = $("#share-acts");
  const cond = $("#share-cond");
  if (!sh || !body) return;
  if (cond) {
    cond.textContent = T("{v} · {duration}초 · {length}덱", { v: sh.code || T("속성 없음"), duration: sh.duration, length: sh.decks.length })
      + T(" · 합계 {v}", { v: I18N.dmg(sh.total) });
  }
  if (acts) {
    acts.hidden = false;
    const all = $("#share-all");
    const n = Math.min(sh.decks.length, deckCountNow());
    if (all) {
      all.textContent = T("{n}{v} 전부 가져오기", { n, v: modeNow() === "union" ? T("줄") : T("덱") });
      all.onclick = () => openShareAllSheet(sh);
    }
  }
  body.textContent = "";
  body.append(recDetail(sh, {
    deckAction: (i, blk) => {
      const bar = el("div", "share-deck-act");
      bar.append(mkBtn(T("이 덱 가져오기"), "btn-primary",
        () => sharePickBox(i, sh.decks[i].names, blk)));
      blk.append(bar);
    },
  }));
}

/** 약점 코드 표시(아이콘·괄호 색)를 `state`에 맞춘다.
 *
 *  **고르개의 값만 바꾸면 안 된다.** 기록·프리셋·공유를 불러올 때 `#code`의 value만
 *  넣으면 아이콘과 괄호 색이 옛 속성 그대로 남는다 — 화면이 서로 다른 말을 한다. */
function syncCodeIco() {
  // 모서리 괄호도 같이 물들인다 (CSS가 `data-code`로 색을 고른다)
  $("#code")?.closest(".brackets")?.setAttribute("data-code", state.settings.code || "");
  const ico = $("#code-ico");
  if (!ico) return;
  const f = ELEMENT_ICON[state.settings.code];
  ico.hidden = !f;
  if (f) { ico.src = `image/icon/${f}`; ico.alt = state.settings.code; }
}

/** 편성 조건(약점 코드·전투 시간)을 한꺼번에 적용한다. 기록·프리셋·공유가 함께 쓴다. */
function applyCond(code, duration) {
  if (code != null) {
    if (modeNow() === "union") U().code = code; else state.settings.code = code;
    const sel = $("#code");
    if (sel) sel.value = code;
    syncCodeIco();
  }
  if (duration != null) {
    setDuration(Math.min(600, Math.max(10, Number(duration) || 180)));
    const dur = $("#duration");
    if (dur) dur.value = durationNow();
  }
}

// ── 초기화 ──────────────────────────────────────────────────────────────
function bindChrome() {
  const sel = $("#code");
  for (const c of CODES) {
    const o = el("option", null, c || T("속성 없음"));
    o.value = c;
    sel.append(o);
  }
  sel.value = state.settings.code;
  syncCodeIco();
  sel.onchange = () => {
    state.settings.code = sel.value;
    syncCodeIco(); saveAll(); renderAll();
  };

  const dur = $("#duration");
  dur.value = durationNow();
  dur.onchange = () => {
    setDuration(Math.min(600, Math.max(10, Number(dur.value) || 180)));
    dur.value = durationNow();
    syncBattleChrome(); saveAll(); renderAll();
  };

  $("#profile-pick").onchange = (e) => {
    state.settings.profileId = e.target.value;
    saveAll(); renderProfiles(); renderAll();
    // 전투력 계산기는 시작값을 스펙에서 가져온다 — 스펙이 바뀌면 다시 불러온다
    if (coop && !$("#coop-screen").hidden) coopLoad(coop.name);
  };
  $("#acct-cog").onclick = () => openAcctSheet();
  // 연출 끄기 — 누르는 즉시 반영한다. 다음 새로고침까지 기다릴 이유가 없다.
  $("#fx-toggle").onclick = () => {
    state.settings.fx = !fxOn();
    saveAll();
    applyFx();
  };
  $("#acct-revert").onclick = () => {
    const rec = activeRec();
    if (!rec?.edits?._account) return;
    delete rec.edits._account.console;
    if (!Object.keys(rec.edits._account).length) delete rec.edits._account;
    results = {};
    saveAll(); buildAcctSheet(); syncAcctCog(); renderAll();
  };
  // 고르기 시트 — 검색·필터 지우기·닫기. 목록은 renderPick()이 그린다.
  $("#ctrl-x").onclick = closeUnionCtrl;
  $("#ctrl-sheet")?.addEventListener("close", () => { if (uCtrlOpen) closeUnionCtrl(); });
  $("#ctrl-sheet")?.addEventListener("click", (e) => {
    if (e.target === $("#ctrl-sheet")) closeUnionCtrl();
  });
  $("#raid-x").onclick = closeRowBattle;
  $("#raid-sheet")?.addEventListener("close", () => {
    // ESC로 닫아도 패널은 제자리로 돌아가야 한다
    if (uBattleOpen) closeRowBattle();
  });
  $("#raid-sheet")?.addEventListener("click", (e) => {
    if (e.target === $("#raid-sheet")) closeRowBattle();
  });
  $("#pick-x").onclick = closePick;
  $("#pick-sheet")?.addEventListener("close", () => { pickAt = null; });
  // 바깥(백드롭)을 눌러도 닫힌다 — dialog는 그 자리도 자기 자신으로 잡힌다
  $("#pick-sheet")?.addEventListener("click", (e) => {
    if (e.target === $("#pick-sheet")) closePick();
  });
  $("#pick-q").oninput = (e) => { pickFilter().q = e.target.value; saveAll(); renderPick(); };
  $("#pick-clear").onclick = () => {
    const f = pickFilter();
    f.q = ""; f.burst = []; f.element = [];
    saveAll(); renderPick();
  };
  $("#deck-calc").onclick = () => calcDecks([state.settings.deck], true);
  const calcAll = (e) => calcDecks([...Array(deckCountNow()).keys()],
                                   e.currentTarget.dataset.force === "1");
  $("#deck-calc-all").onclick = calcAll;
  $("#deck-goto-result").onclick = () => document.querySelector('.tab[data-tab="result"]')?.click();
  $("#res-calc").onclick = calcAll;
  $("#fast-calc-all").onclick = calcAll;
  $("#fast-toggle").onclick = () => setFastMode(!fastMode);
  for (const b of document.querySelectorAll(".mode-btn")) {
    b.onclick = () => setMode(b.dataset.mode);
  }
  wireUnion();
  $("#whatsnew-x").onclick = () => $("#whatsnew-sheet").close();
  $("#whatsnew-ok").onclick = () => $("#whatsnew-sheet").close();
  // ✕(또는 ESC)로 닫으면 **아무것도 기록하지 않는다** — 다음에 또 뜬다.
  // 공지는 「봤다」를 자동으로 가정하면 안 되는 내용이라서다.
  $("#notice-x").onclick = () => $("#notice-sheet").close();
  // «다시 보지 않기»는 누르는 즉시 기록하고 닫는다(체크 후 또 닫게 하지 않는다).
  $("#notice-dismiss").onclick = () => {
    save(LS.notice, NOTICE_ID);
    $("#notice-sheet").close();
  };
  $("#rec-save").onclick = saveRecord;
  $("#rec-saved-x").onclick = () => $("#rec-saved-sheet").close();
  $("#rec-saved-stay").onclick = () => $("#rec-saved-sheet").close();
  $("#rec-saved-go").onclick = () => {
    $("#rec-saved-sheet").close();
    document.querySelector('.tab[data-tab="log"]')?.click();
  };
  // 내보내기가 있으면 불러오기도 있어야 한다 — 파일 드롭 경로는 이미 기록을 알아보므로
  // 같은 처리기(`importFiles`)에 파일만 넘긴다.
  $("#rec-import").onclick = () => $("#rec-file").click();
  $("#rec-file").onchange = (e) => {
    const fs = [...(e.target.files || [])];
    e.target.value = "";
    if (fs.length) importFiles(fs);
  };
  $("#deck-clear").onclick = () => {
    deckOf(state.settings.deck).names = Array(SLOTS).fill(null);
    saveAll(); renderAll();
  };
  $("#deck-clear-all").onclick = () => {
    // 발판 바로 아래에 묻는다 — 무엇이 비워지는지(슬롯 25칸)가 위에 그대로 보인다
    const heads = [...Array(DECK_COUNT).keys()]
      .reduce((n, i) => n + deckOf(i).names.filter(Boolean).length, 0);
    if (!heads) return;
    askInline($("#deck-ask"), T("5덱 {heads}명을 전부 비웁니다.", { heads }), T("비우기"), () => {
      for (let i = 0; i < DECK_COUNT; i++) deckOf(i).names = Array(SLOTS).fill(null);
      saveAll(); renderAll();
    });
  };

  for (const b of document.querySelectorAll(".eng")) {
    b.onclick = () => { state.settings.engine = b.dataset.eng; saveAll(); renderEngine(); };
  }

  $("#q").oninput = (e) => { curFilter().q = e.target.value; renderPools(); };

  // 정렬·필터 패널은 **누를 때만** 뜬다. 로스터 위에 떠서 자리를 밀지 않는다.
  const fp = $("#fpanel"), ft = $("#f-toggle");
  const showPanel = (on) => {
    fp.hidden = !on;
    ft.setAttribute("aria-expanded", String(on));
    syncFilterChrome();          // «비우기»가 패널을 따라 나타났다 사라진다
  };
  showPanel(false);
  // 방향 버튼은 패널을 열지 않는다 — 정렬만 뒤집는다
  $("#f-dir").onclick = (e) => {
    e.stopPropagation();
    const f = curFilter();
    f.asc = f.asc === false;
    sortRow(); syncFilterChrome(); saveAll(); renderPools();
  };
  ft.onclick = (e) => {
    e.stopPropagation();
    const willOpen = fp.hidden;
    $("#btpanel").hidden = true;                 // 둘이 겹쳐 뜨면 서로를 가린다
    $("#bt-toggle").setAttribute("aria-expanded", "false");
    showPanel(willOpen);
  };
  // 바깥을 누르거나 Esc면 닫는다
  document.addEventListener("pointerdown", (e) => {
    if (!fp.hidden && !e.target.closest(".fwrap")) showPanel(false);
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") showPanel(false); });

  $("#f-clear").onclick = (e) => { e.stopPropagation(); clearFilters(); };

  // 전투 조건 패널 — 필터와 같은 방식으로 누를 때만 뜬다
  const bp = $("#btpanel"), bt = $("#bt-toggle");
  const showBattle = (on) => {
    bp.hidden = !on;
    bt.setAttribute("aria-expanded", String(on));
    syncBattleChrome();
  };
  showBattle(false);
  bt.onclick = (e) => {
    e.stopPropagation();
    const willOpen = bp.hidden;
    $("#fpanel").hidden = true;
    $("#f-toggle").setAttribute("aria-expanded", "false");
    showBattle(willOpen);
  };
  document.addEventListener("pointerdown", (e) => {
    // 유니온에서는 패널이 줄 밑으로 옮겨 가 .fwrap 바깥에 산다 — 패널 자신과
    // 줄의 «레이드» 버튼도 «바깥»으로 치면 열자마자 닫힌다.
    if (!bp.hidden && !e.target.closest(".fwrap, #btpanel, .row-raid")) showBattle(false);
  });
  // 패널이 화면 가운데 뜨므로 «기본값»도 패널 안에 있어야 한다 — 트리거 옆에 남으면
  // 패널과 떨어진 자리에서 홀로 떠 무엇을 되돌리는 버튼인지 알 수 없다.
  bp.prepend($("#bt-clear"));
  $("#bt-clear").onclick = (e) => { e.stopPropagation(); resetBattle(); };
  $("#f-fav").onclick = () => {
    const f = curFilter();
    f.favOnly = !f.favOnly;
    syncFilterChrome(); saveAll(); renderPools();
  };
  $("#f-parsed").onclick = () => {
    const f = curFilter();
    f.parsed = !f.parsed;
    syncFilterChrome(); saveAll(); renderPools();
  };

  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => {
      for (const t of document.querySelectorAll(".tab")) {
        const on = t === tab;
        t.classList.toggle("on", on);
        t.setAttribute("aria-selected", String(on));
      }
      for (const p of document.querySelectorAll(".panel")) {
        p.hidden = p.dataset.panel !== tab.dataset.tab;
      }
      // 필터 바는 «전투력 계산기냐 아니냐»로만 갈린다 — 다른 탭 조건을 사이에 끼우면
      // else가 그쪽에 붙어 버려, coopEnsure()가 옮겨 놓은 바를 바로 되돌린다(실측:
      // 피드백 탭을 넣으면서 이 else를 뺏겨 전투력 계산기에서 필터가 사라졌고,
      // inCoop이 false로 남아 편성 쪽 「계산 가능」 필터가 목록까지 잘랐다).
      if (tab.dataset.tab === "coop") coopEnsure();
      else moveFilterBar(false);
      if (tab.dataset.tab === "feedback") fbLoad();
      if (tab.dataset.tab === "deck") markOverflow();
      else if (picked) {
        // 카드를 «집어 든» 채로 다른 탭으로 나가면, 그 상태를 알리던 머리글의
        // 「«이름» — 놓을 슬롯을 누르세요」 배지가 **탭을 넘어서도 남는다**
        // (실측: 전역 상태 표시라 다른 화면을 보는 동안에도 계속 떠 있었다).
        // 편성 화면을 벗어나는 순간 집어 든 것 자체가 뜻을 잃으므로 놓아 준다.
        picked = null;
        setStatus("", false);
      }
      if (tab.dataset.tab === "log") {
        // **탭을 직접 눌러 들어올 때는 필터를 늘 «전체»로 되돌린다.**
        // 「솔레덱 훔쳐오기」가 이 탭으로 옮기면서 recKind를 "shot"으로 바꿔
        // 두는데, 그건 그 버튼 하나만의 의도다 — 리셋 없이 두면 그 뒤로
        // 이 탭에 다시 들어올 때마다 계속 "솔레 기록"만 보이고 시뮬 기록이
        // 통째로 안 보여서 «이전 기록이 사라졌다»처럼 보인다(실측: 재현됨).
        // 순서상 이 리셋이 먼저 돌고, 훔쳐오기 자신의 `recKind = "shot"`이
        // 그 뒤에 한 번 더 실행돼 원하는 필터로 남는다 — 그 버튼은 그대로 동작한다.
        recKind = "all";
        renderRecords();
      }
    };
  }

  // «솔레덱 훔쳐오기» — 기록 탭으로 옮기고 캡처 상자까지 열어 준다.
  // 두 번 누르게 하지 않는 것이 이 단추의 존재 이유다.
  $("#tab-steal").onclick = () => {
    document.querySelector('.tab[data-tab="log"]')?.click();
    recKind = "shot";          // «솔레 기록»만 보이게 — 지금 만들려는 것이 그것이다
    renderRecords();
    shotToggleDrop(true);
  };

  $("#url-go").onclick = syncUrl;
  const drop = $("#drop"), fin = $("#file-in");
  drop.onclick = () => fin.click();
  drop.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fin.click(); }
  };
  fin.onchange = () => { if (fin.files.length) importFiles([...fin.files]); fin.value = ""; };
  for (const ev of ["dragenter", "dragover"]) {
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) {
    drop.addEventListener(ev, () => drop.classList.remove("over"));
  }
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) importFiles([...e.dataTransfer.files]);
  });

  $("#edit-revert").onclick = () => { if (sheetName) revertChar(sheetName); };

  // ── 프리셋 ──
  $("#preset-save-single").onclick = () => openPresetSave("single");
  $("#preset-save-bundle").onclick = () => openPresetSave("bundle");
  $("#rec-compare").onclick = openCompare;
  $("#preset-export-all").onclick = exportAllPresets;
  const pfin = $("#preset-file");
  $("#preset-import").onclick = () => pfin.click();
  pfin.onchange = () => {
    if (pfin.files.length) importPresetFiles([...pfin.files]);
    pfin.value = "";
  };

  // ── 공유 링크 만들기 (결과 탭) ──
  $("#res-share").onclick = () => {
    const { decks, total, mode } = collectDecks();
    const sink = (m, k) => msgAt("#res-share-msg", m, k);
    makeShare({ code: state.settings.code, duration: durationNow(), decks, total, mode },
              $("#res-share-out"), sink);
  };

  addEventListener("resize", markOverflow);
}

// ── 새 소식 ─────────────────────────────────────────────────────────────
// 중요한 변경이 생기면 다시 온 사람에게 팝업으로 알린다. 배열 순서 = 시간순
// (오래된 게 위) — 새 항목은 맨 끝에 추가한다. `v`는 그 항목까지의 누적
// 버전표라 저장된 값과 정확히 일치하는 항목**부터** 안 보여 준다(그 뒤가 새 것).
const CHANGELOG = [
  { v: "2026-08-23-fastmode", items: [
    "배치모드 — 5덱 25칸을 한 화면에서 빠르게 채우는 전용 화면을 추가했습니다.",
    "니케 얼굴을 인게임과 같은 정사각 카드로 보여줍니다.",
    "덱 순서를 드래그로 바꿀 수 있습니다(배치모드 줄 번호·01~05 탭 모두).",
  ] },
];

// ── 공지 ─────────────────────────────────────────────────────────────────
// 새 소식과 달리 **쿠키가 아니라 localStorage에 "다시 보지 않기"를 체크했을
// 때만** 기록한다. 그냥 닫으면(X·확인·ESC 전부 포함) 다음 방문에 또 뜬다 —
// 공지는 "봤다"를 자동으로 가정하면 안 되는 내용이라서다. id를 바꾸면
// 예전에 다시 보지 않기를 눌렀던 사람에게도 새로 뜬다.
// 공지는 **날짜별로 쌓는다.** 새 날짜를 맨 앞에 추가하고, 오래된 항목은 그 날짜
// 블록째 지우면 된다. `NOTICE_ID`는 «다시 보지 않기»를 무효화하는 기준이라 새
// 날짜를 넣을 때마다 함께 올린다 — 그래야 이미 닫아 둔 사람에게도 새로 뜬다.
const NOTICE_ID = "2026-08-25b";
const NOTICE_TITLE = T("업데이트 안내");
const NOTICES = [
  { date: "2026-08-25", items: [
    "**유니온 레이드 베타를 엽니다** — 위쪽에서 «유니온 레이드»로 바꾸면 세 줄을 " +
      "한 번에 짜고 계산할 수 있습니다. 회차별 보스를 골라 두면 줄마다 약점이 " +
      "따라오고, 레이드 설정도 줄별로 따로 줍니다.",
    "아직 **준비 중이라 계산 용도로만** 써 주세요 — 보스별 세부 설정 등 남은 것이 " +
      "있고, 화면과 값이 더 바뀔 수 있습니다.",
    "«내 계정» 탭에서 **다시 싱크**를 한 번 눌러 주세요 — 유니온 정보가 그때 " +
      "들어옵니다. 전에 싱크해 둔 스펙에는 없던 것이라, 누르기 전에는 상단 " +
      "유니온 자리가 비어 보입니다.",
    "**피드백을 많이 주세요.** 어색한 곳, 안 되는 곳, 있었으면 하는 것 무엇이든 " +
      "«피드백» 탭에 적어 주시면 반영합니다. 문제가 있어도 그쪽으로 알려 주세요.",
    "",
    "연출이 어지러우면 **⚙ 콘솔 옆의 ✦를 눌러 애니메이션을 끌 수 있습니다** — " +
      "끄면 튀는 것 없이 조용히 결과만 바뀝니다. 솔로·유니온 공용이라 한 번만 " +
      "꺼 두면 됩니다.",
  ] },
  { date: "2026-08-24", items: [
    "「투사체 폭발 대미지 ▲」는 **원래 무기가 RL인 니케만** 받습니다 — 무기 변경으로 " +
      "RL이 된 사격(나유타 등)에는 적용되지 않도록 고쳤습니다. 실측 대조로 확인했습니다.",
    "**차지 배율 계산을 수정했습니다** — 무기의 풀차지 배율과 「차지 대미지 ▲」 버프는 " +
      "곱이 아니라 **가산**(%p 합)입니다. 인게임 차징 표기·솔로 레이드 전투 기록 대조로 " +
      "확인했으며, **차지 대미지 버프를 받는 SR·RL**일수록 총딜이 내려갑니다 — 실측 " +
      "편성 예: 프리카 −20% · 민트 −14% · 스노우 화이트 : 헤비암즈 −13% · " +
      "디젤 : 윈터 스위츠 −3% · 네온 : 비전 아이 −2% (버프·오버로드 구성에 따라 다름).",
    "**큐브 버그를 수정했습니다** — 편성 화면에 보이는 큐브가 항상 계산에 쓰입니다. " +
      "이전에는 큐브 칸을 건드리지 않은 니케가 프로필의 장착 상태(미장착 포함)로 " +
      "계산되는 경우가 있었습니다.",
    "",
    "니케 그림이 **지금 입고 있는 코스튬(스킨)**으로 나옵니다 — 블라블라링크에서 " +
      "장착 중인 코스튬을 함께 받아와 편성 카드·배치 모드 얼굴·전투력 계산기 " +
      "전신 일러까지 그대로 바뀝니다. 외형뿐이라 **계산에는 아무 영향이 없습니다**.",
    "",
    "레이드 설정에 **무기군 평타 계수**를 추가했습니다 — 실전에서 탄퍼짐으로 빗나가는 " +
      "탄을 보정합니다. 이번 솔로 레이드 실측(전투 기록 대조) 기준 기본값은 " +
      "**SG 0.90 · SMG 0.80**, 나머지는 1.00이며 언제든 조절할 수 있습니다.",
    "계수는 **평타에만** 적용됩니다 — 스킬·버스트 대미지, 변신 모드(나유타 나래신장 " +
      "등)의 공격은 조준 판정이라 보정하지 않습니다. 기본값 상태에서는 SG·SMG가 " +
      "포함된 편성의 총딜이 이전보다 낮아집니다.",
    "",
    "편성 화면에서 **큐브를 칸마다 따로** 지정할 수 있습니다 — 카드 아래에서 종류와 " +
      "레벨을 고르면 그 자리에 적용되고, 니케를 옮기거나 덱 순서를 바꿔도 함께 " +
      "따라갑니다.",
    "큐브 레벨에 **미장착**을 추가했습니다 — 큐브를 끼지 않은 상태 그대로도 " +
      "계산할 수 있습니다.",
    "«전투력 계산기»는 블라블라링크에서 받아온 **실제 장착 중인 큐브**를 그대로 " +
      "보여줍니다. 편성 계산과 달리 지금 상태 그대로입니다.",
    "",
    "컨트롤에 **버스트 금지**를 추가했습니다 — 쿨이 돌아와도 그 니케는 버스트를 " +
      "쓰지 않아, 원하지 않는 니케가 끼어드는 것을 막을 수 있습니다.",
    "",
    "«내 계정» 탭에서 **다시 싱크**를 한 번 눌러 주세요 — 큐브 장착 정보와 " +
      "코스튬(스킨) 정보가 이번 갱신부터 들어옵니다.",
  ] },
  { date: "2026-08-23", items: [
    "재장전 딜레이를 인게임 실측값(CDN) 기반으로 정교화했습니다 — 탄 소진 후 재장전 " +
      "시작·복귀에 실제 딜레이(대부분 0.2초)가 반영되어 총딜이 소폭 낮아질 수 있습니다.",
    "클립식 SG·RL(누아르·드레이크·바이퍼·네온·페퍼·슈가·메이든·프로덕트 23·" +
      "소다 : 트윙클링 바니·센티·루마니·아니스·자칼·트리나)에 '원클립 재장전' " +
      "컨트롤을 추가했습니다 — 한 클립만 채우고 사격으로 복귀합니다.",
    "차지 무기(SR·RL)에도 재장전 시작 지연(신규 +0.2초)이 반영되어, 해당 무기 비중이 " +
      "큰 편성은 총딜이 더 크게(실측 -10~17%대 사례 있음) 줄어들 수 있습니다.",
    "아니스 : 스타·리버렐리오·네온 : 비전 아이는 위 신규 시작 지연(+0.2초)이 " +
      "똑같이 붙는 대신, 수동으로 걸려 있던 재장전 복귀 지연이 0.5초 → 0.2초로 " +
      "줄어듭니다(-0.3초). 둘을 더하면 순 -0.1초 — 0.3초 이득이 아니라 소폭 " +
      "빨라지는 정도입니다.",
  ] },
];

/** `**굵게**`만 해석해 조각으로 만든다. 공지 문장에서 무엇이 바뀌었는지 짚어 주는
 *  용도라 그 이상은 필요 없다 — `innerHTML`을 쓰지 않으려고 직접 자른다. */
function boldParts(text) {
  const out = [];
  for (const [i, part] of String(text).split("**").entries()) {
    if (!part) continue;
    out.push(i % 2 ? el("b", null, part) : document.createTextNode(part));
  }
  return out;
}

// ── 피드백 게시판 ──────────────────────────────────────────────────────
// 서버가 있어야 도는 기능이다 — 정적 서빙(로컬 미리보기)에서는 목록이 비고
// 전송이 실패하는데, 그 사실을 문구로 말해 준다. 목록은 30개씩 끊어 내려받는다.
const FB_PAGE = 30;
let fbOldest = null;   // 마지막으로 받은 글의 ts — «더 보기»의 기준점

// 이 브라우저가 쓴 비공개 글의 {id: 비밀번호} — 본인 기기에서는 비번 재입력 없이
// 자동으로 펼쳐 보인다. 서버는 누가 썼는지 모른다(익명 유지).
const fbMine = () => load(LS.fbMine, {});

function fbFill(div, it) {
  div.textContent = "";
  const when = new Date(it.ts * 1000).toLocaleDateString("ko-KR",
    { month: "numeric", day: "numeric" });
  div.append(el("div", "fb-meta",
    `${it.private ? "🔒 " : ""}${it.nick} · ${when}`));
  const body = el("div", "fb-body"); body.textContent = it.body; div.append(body);
  if (it.reply) {
    const r = el("div", "fb-reply"); r.textContent = it.reply;
    div.append(r);
  }
}

async function fbUnlock(div, it, pw, note) {
  try {
    const r = await fetch("/api/board/view", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: it.id, pw }),
    });
    if (!r.ok) throw new Error((await r.json()).error || T("열람 실패"));
    fbFill(div, await r.json());
    return true;
  } catch (err) {
    if (note) note.textContent = String(err.message);
    return false;
  }
}

function fbItem(it) {
  const div = el("div", "fb-item");
  if (!it.private) { fbFill(div, it); return div; }
  // 비공개 글 — 껍데기 + 열람 버튼. 내 브라우저가 기억하는 비번이 있으면 자동 열람
  const when = new Date(it.ts * 1000).toLocaleDateString("ko-KR",
    { month: "numeric", day: "numeric" });
  div.append(el("div", "fb-meta", T("🔒 비공개 · {nick} · {when}", { nick: it.nick, when })
    + (it.has_reply ? T(" · 답변 있음") : "")));
  const note = el("span", "fb-note", "");
  const btn = el("button", "btn btn-ghost", "비밀번호로 보기");
  btn.type = "button";
  btn.onclick = async () => {
    const pw = prompt(T("이 글을 쓸 때 정한 비밀번호"));
    if (pw) await fbUnlock(div, it, pw, note);
  };
  div.append(btn, note);
  const mine = fbMine()[it.id];
  if (mine) fbUnlock(div, it, mine, null);
  return div;
}

async function fbLoad(more = false) {
  const box = $("#fb-list");
  try {
    const q = more && fbOldest ? `?before=${fbOldest}&n=${FB_PAGE}` : `?n=${FB_PAGE}`;
    const r = await fetch("/api/board" + q);
    if (!r.ok) throw new Error();
    const items = (await r.json()).items || [];
    if (!more) box.textContent = "";
    if (!more && !items.length) {
      box.append(el("p", "fb-note", "아직 글이 없습니다 — 첫 제보를 남겨 주세요."));
    }
    for (const it of items) box.append(fbItem(it));
    if (items.length) fbOldest = items[items.length - 1].ts;
    // 한 페이지를 꽉 채워 왔으면 더 있을 가능성이 있다 — 버튼을 계속 보여 준다
    $("#fb-more").hidden = items.length < FB_PAGE;
  } catch {
    if (!more) {
      box.textContent = "";
      box.append(el("p", "fb-note",
        T("목록을 불러오지 못했습니다 — 서버가 꺼져 있으면 피드백도 쉽니다.")));
    }
  }
}

function wireFeedback() {
  const form = $("#fb-form");
  if (!form) return;
  $("#fb-more").onclick = () => fbLoad(true);
  $("#fb-private").onchange = () => { $("#fb-pw").hidden = !$("#fb-private").checked; };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const note = $("#fb-note");
    const body = $("#fb-body").value.trim();
    if (body.length < 2) { note.textContent = T("내용을 2자 이상 적어 주세요."); return; }
    note.textContent = T("보내는 중…");
    try {
      const r = await fetch("/api/board", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nick: $("#fb-nick").value,
                               body, web: $("#fb-web").value,
                               private: $("#fb-private").checked,
                               pw: $("#fb-pw").value }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || T("전송 실패"));
      if ($("#fb-private").checked && j.id) {
        // 이 브라우저에서는 비번 없이 자기 글·답변을 보게 기억해 둔다
        const m = fbMine(); m[j.id] = $("#fb-pw").value; save(LS.fbMine, m);
      }
      note.textContent = T("등록됐습니다. 감사합니다!");
      $("#fb-body").value = "";
      fbLoad();
    } catch (err) {
      note.textContent = String(err.message || T("전송 실패 — 잠시 후 다시 시도해 주세요."));
    }
  };
}

/** 공지 팝업. 체크박스로 「다시 보지 않기」를 고른 채 닫아야만 기록한다 —
 *  그냥 닫으면 다음 방문에 또 뜬다. 날짜 블록을 최신순으로 모두 보여 준다. */
function checkNotice() {
  if (load(LS.notice, null) === NOTICE_ID) return;
  const box = $("#notice-list");
  box.textContent = "";
  for (const sec of NOTICES) {
    box.append(el("h4", "notice-date", sec.date));
    // 빈 문자열은 그룹 구분자 — 영향도가 다른 항목 사이를 살짝 띄운다
    // (ul을 나누면 .steps-list의 기본 아래 여백이 간격이 된다)
    let ul = null;
    for (const item of sec.items) {
      if (item === "") { ul = null; continue; }
      if (!ul) { ul = el("ul", "steps-list"); box.append(ul); }
      const li = el("li");
      li.append(...boldParts(item));
      ul.append(li);
    }
  }
  const dlg = $("#notice-sheet");
  if (dlg && !dlg.open) dlg.showModal();
}

/** 새 소식 팝업. **처음 오는 사람은 조용히 최신 버전만 기록하고 넘어간다** —
 *  겪어 본 적 없는 «이전»과 비교하는 변경 목록은 그 사람에게는 의미가 없다. */
function checkWhatsNew() {
  const latest = CHANGELOG.at(-1).v;
  const seen = load(LS.whatsNew, null);
  if (seen === null) { save(LS.whatsNew, latest); return; }
  if (seen === latest) return;
  const at = CHANGELOG.findIndex((c) => c.v === seen);
  // 모르는 버전표(옛 형식·손상)면 무엇을 놓쳤는지 알 길이 없다 — 최신 것만 보여준다.
  const unseen = at === -1 ? CHANGELOG.slice(-1) : CHANGELOG.slice(at + 1);
  if (!unseen.length) { save(LS.whatsNew, latest); return; }
  const list = $("#whatsnew-list");
  list.textContent = "";
  for (const item of unseen.flatMap((c) => c.items)) list.append(el("li", null, item));
  save(LS.whatsNew, latest);
  const dlg = $("#whatsnew-sheet");
  if (dlg && !dlg.open) dlg.showModal();
}

async function boot() {
  // 사전이 먼저다 — 아래 render들이 만드는 글자가 전부 `T()`를 지난다.
  await I18N.ready;
  I18N.apply(document.body);             // index.html의 정적 글자
  I18N.mountPicker($("#lang-pick"));
  delete document.documentElement.dataset.i18n;   // 사전이 입혀졌다 — 본문을 연다
  const saved = load(LS.settings, {});
  // 저장된 필터가 기본값을 덮는다 — «계산 가능만»을 기본 켜짐으로 바꿨을 때
  // 이미 false로 저장해 둔 사람은 계속 꺼진 채로 보였다. 판번호로 1회만 바로잡는다.
  const FILTER_V = 4;
  const savedFilter = saved._filter || {};
  if (saved._filterV !== FILTER_V) {
    // v2까지는 단일 선택("all" 또는 값 하나)이었다. 배열 모델로 옮기고
    // «계산 가능만»은 기본값(켜짐)을 다시 쓰게 한다.
    delete savedFilter.parsed;
    for (const k of ["burst", "cls", "element", "weapon"]) {
      const v = savedFilter[k];
      savedFilter[k] = Array.isArray(v) ? v : (v && v !== "all" ? [v] : []);
    }
    // v3까지 있던 정렬(등급·한계돌파·호감도)을 골라 뒀다면 갈 곳이 없다 — 기본으로 되돌린다
    if (savedFilter.sort && !SORTS.some(([k]) => k === savedFilter.sort)) {
      delete savedFilter.sort;
    }
    // v3까지 숫자 정렬은 비교기 자체가 내림차순이라 `asc`의 뜻이 반대였다.
    // 저장된 방향을 그대로 쓰면 ▼인데 작은 값이 위로 온다 — 기본으로 되돌린다.
    delete savedFilter.asc;
    saved._filterV = FILTER_V;
  }
  state.settings._filterV = FILTER_V;
  Object.assign(state.filter, savedFilter);
  state.filter.q = "";                       // 검색어는 세션마다 비운다
  // 전투력 계산기 필터는 별도 판번호 없이 그대로 복원한다 — v4 이전 이관 대상이던
  // 옛 필드(단일 선택 등)가 애초에 존재한 적이 없어 마이그레이션이 필요 없다.
  Object.assign(state.coopFilter, saved._coopFilter || {});
  state.coopFilter.q = "";
  state.favs = saved._favs || [];
  delete saved._filter; delete saved._coopFilter; delete saved._favs;
  // 유니온 상자는 settings에 섞지 않는다 — 꺼내서 자기 자리에 둔다
  state.union = saved._union || null;
  delete saved._union;
  Object.assign(state.settings, saved);
  // 연출 스위치는 화면이 그려지기 전에 새겨야 한다 — 나중에 켜면 첫 화면만 한 번
  // 튀고 꺼진다(끈 사람에게는 그 한 번이 제일 거슬린다).
  applyFx();
  state.decks = load(LS.decks, []);
  // 큐브칸은 나중에 생긴 필드다 — 예전에 저장된 덱에는 없으므로 여기서 채운다.
  // 길이가 어긋난 채로 두면 `place()`의 자리 교환이 조용히 어긋난다.
  for (const d of state.decks) {
    if (!d) continue;
    d.cubes = Array.from({ length: SLOTS }, (_, i) => d.cubes?.[i] ?? null);
  }
  results = load(LS.results, {});
  state.profiles = load(LS.profiles, {});
  // `syncing`은 **조회하는 동안만** 참인 임시 깃발인데 저장까지 따라 들어간다.
  // 조회 중에 새로고침하거나 탭을 닫으면 참인 채로 남아, 그 스펙이 영영
  // 「받는 중…」으로 보이고 다시 누를 수도 없다(그 자리에서 return한다).
  // 불러올 때 무조건 내린다 — 페이지가 새로 뜬 시점에 진행 중인 조회는 없다.
  for (const rec of Object.values(state.profiles)) delete rec.syncing;
  // 예전 형식(«블라 41757 (한국)») 이름에서 openid 꼬리를 떼어 준다 — 그 숫자가
  // 스크린샷으로 새어 나가던 자리다. 이름은 표시용이라 지워도 잃는 정보가 없다.
  //
  // **사람이 직접 지은 이름은 건드리지 않는다**(`renamed`). 그 표식이 없던 시절에
  // 저장된 것은 구분할 방법이 없는데, 이 꼴(«블라»+숫자+괄호)로 직접 지었다면
  // 그건 자기 openid 꼬리를 손으로 적은 것이라 어차피 지우는 편이 맞다.
  for (const rec of Object.values(state.profiles)) {
    if (rec?.renamed || typeof rec?.name !== "string") continue;
    const cleaned = rec.name.replace(/^블라\s+\d{4,8}\s*\(/, "블라 (");
    if (cleaned !== rec.name) rec.name = cleaned;
  }
  state.records = load(LS.records, []);
  state.presets = load(LS.presets, []);
  state.battle = { ...BATTLE_DEFAULT, ...(saved._battle || {}) };
  state.battle.optimal_range_weapons = Array.isArray(state.battle.optimal_range_weapons)
    ? battleNow().optimal_range_weapons : [];
  // 계수 옵션이 생기기 전 저장분은 weapon_coeff가 없다 — 기본값으로 채운다
  // 모드 복원은 여기서 **판정하지 않는다** — `HEALTH`가 비동기로 오므로 이 시점에는
  // 유니온이 켜졌는지 알 수 없다(성급히 판정하면 새로고침마다 솔로로 떨어진다).
  // 판정은 health를 받은 뒤 `applyHealth()`가 한다.
  state.battle.weapon_coeff = {
    ...BATTLE_DEFAULT.weapon_coeff,
    ...(state.battle.weapon_coeff && typeof state.battle.weapon_coeff === "object"
        ? battleNow().weapon_coeff : {}),
  };
  delete saved._battle;
  for (let i = 0; i < DECK_COUNT; i++) {
    const d = deckOf(i);
    d.calcState = null; d.error = null;
    d.names = (d.names || []).slice(0, SLOTS);
    while (d.names.length < SLOTS) d.names.push(null);
  }
  state.settings.deck = Math.min(DECK_COUNT - 1, Math.max(0, state.settings.deck || 0));

  const [roster, maps, health] = await Promise.all([
    fetch("roster.json").then((r) => r.json()),
    fetch("profile_maps.json").then((r) => r.json()).catch(() => null),
    fetch("/api/health").then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  ROSTER = roster.chars;
  for (const r of ROSTER) byName.set(r.name, r);
  TOP_ATK_CASTERS = new Set(roster.top_atk_casters || []);
  TOP_ATK_BUFFS = roster.top_atk_buffs || {};
  SELF_BURST_ATK = roster.self_burst_atk || {};
  DEALER_ATK_FLAT = roster.dealer_atk_flat || {};
  SELF_FB_ATK = roster.self_fb_atk || {};
  LOW_ATK_CASTERS = new Set(roster.low_atk_casters || []);
  LOW_ATK_BUFFS = roster.low_atk_buffs || {};
  ADJ_CASTERS = new Set(roster.adjacent_casters || []);
  ADJ_BUFFS = roster.adjacent_buffs || {};
  CDR_CASTERS = new Set(roster.cdr_casters || []);
  MAPS = maps;
  if (health) HEALTH = health;
  // 이제야 유니온 가용 여부를 안다. 꺼져 있으면(상용) 저장된 모드가 union이어도
  // 솔로로 내린다 — 만드는 중인 화면이 상용에서 열리면 안 된다.
  if (state.settings.mode === "union" && !unionOn()) state.settings.mode = "solo";
  renderMode();
  buildBattle();

  $("#sync-url").hidden = !HEALTH.fetch;
  renderEngine();

  // 북마클릿 — 소스를 그대로 읽어 javascript: URL로 만든다.
  // 끌어 놓기가 막히는 환경이 흔해서 복사 경로를 둘 더 준다.
  fetch("bookmarklet.js").then((r) => r.text()).then((src) => {
    const href = "javascript:" + encodeURIComponent(src);
    $("#bm-link").href = href;
    $("#bm-copy").onclick = () => copyText(href,
      T("북마클릿 주소를 복사했습니다 — 북마크를 만들어 URL 칸에 붙여넣으세요."));
    $("#bm-copy-raw").onclick = () => copyText(src,
      T("콘솔용 코드를 복사했습니다 — blablalink.com 탭에서 F12 → Console에 붙이고 Enter."));
  }).catch(() => { $("#bm-link").removeAttribute("href"); });

  buildFilters();
  buildBattle();
  bindChrome();
  renderProfilePick();
  renderProfiles();
  renderRecords();
  renderPresets();
  // 배치모드를 켜 둔 채로 새로고침했으면 그대로 열린다 — ROSTER가 막 채워진
  // 뒤(위)라 여기서 해야 renderAll()의 로스터 격자가 온전히 그려진다.
  fastMode = !!state.settings.fastMode;
  applyFastModeDom(fastMode);
  renderAll();

  // 공유 저장소가 없는 서버에서는 만들 수 없다 — 버튼을 감춘다
  $("#res-share").hidden = !HEALTH.share;

  // `/s?c=<코드>`로 들어왔나. **경로가 아니라 질의문이다** — `index.html`의 자산 링크가
  // 전부 상대경로라서 `/s/<코드>`로 서빙하면 `/s/app.js`를 찾아 전부 404가 된다.
  shotWire();
  const code = new URLSearchParams(location.search).get("c");
  if (code) loadShared(code);
  wireFeedback();
  renderMode();
  moveEngineRow(fastMode);
  checkNotice();
  checkWhatsNew();
}

/** 덱 두 개의 편성을 «딜 순으로 세워 위아래로» 맞대어 놓는다.
 *
 *  1등끼리, 2등끼리 나란히 붙여 놓으면 «누가 누구 자리를 대신했는가»가 한눈에 읽힌다.
 *  같은 사람이면 위아래가 같은 얼굴이고, 바뀐 자리만 색으로 드러난다.
 *  캡처에서 만든 기록처럼 니케별 딜이 없으면 편성 순서를 그대로 쓴다.
 */
function cmpFaces(da, db) {
  const dmg = (d, n) => Number((d.chars || {})[n]) || 0;
  const rank = (d) => deckNames(d).slice().sort((p, q) => dmg(d, q) - dmg(d, p));
  /** 딜을 모르는 쪽은 **상대 순서를 따라간다.**
   *  둘 다 딜 순으로 세우는 게 원칙이지만, 한쪽이 캡처 기록이면 그쪽엔 세울 기준이
   *  없다. 그때 각자 편성 순서대로 두면 같은 다섯 명인데도 위아래가 어긋나
   *  «누가 누구 자리인지»가 안 보인다. 같은 사람을 세로로 맞추는 게 낫다. */
  const follow = (d, order) => {
    const mine = deckNames(d);
    const set = new Set(mine);
    const head = order.filter((n) => set.has(n));
    return [...head, ...mine.filter((n) => !head.includes(n))];
  };
  let A, B;
  if (hasChars(da)) {
    A = rank(da);
    B = hasChars(db) ? rank(db) : follow(db, A);
  } else if (hasChars(db)) {
    B = rank(db);
    A = follow(da, B);
  } else {
    A = deckNames(da).slice();
    B = follow(db, A);
  }
  const sa = new Set(A), sb = new Set(B);
  const n = Math.max(A.length, B.length);

  const grid = el("div", "cmp-rank");
  grid.style.gridTemplateColumns = `auto repeat(${n}, minmax(0, 1fr))`;
  const line = (list, other, key, cls, ranked) => {
    grid.append(el("span", "cmp-rank-k", key));
    for (let i = 0; i < n; i++) {
      const nm = list[i];
      const cell = el("div", "cmp-rcell");
      if (!nm) { grid.append(cell); continue; }
      const kept = other.has(nm);
      const f = el("div", "cmp-face " + (kept ? "" : cls));
      // 딜을 모르는 쪽에 «위»를 붙이면 없는 순위를 지어내는 것이다
      f.title = `${ranked ? T("{v}위", { v: i + 1 }) : T("{v}번째", { v: i + 1 })} · ${nm}`
        + (kept ? "" : cls === "out" ? T(" (빠짐)") : T(" (새로)"));
      const rec = byName.get(nm);
      if (rec?.img) {
        const im = el("img");
        im.src = artSrc(rec, nm);
        im.alt = nm;
        im.loading = "lazy";
        im.decoding = "async";
        im.draggable = false;
        f.append(im);
      } else {
        f.append(el("span", "cmp-face-none", nm.slice(0, 1)));
      }
      cell.append(f);
      grid.append(cell);
    }
  };
  line(A, sb, T("기준"), "out", hasChars(da));
  line(B, sa, T("비교"), "in", hasChars(db));
  return grid;
}

/** 짝 없는 덱 — 딜 순으로 한 줄. 비교 화면의 다른 줄과 같은 크기로 맞춘다. */
function cmpLoneFaces(d) {
  const dmg = (n) => Number((d.chars || {})[n]) || 0;
  const names = deckNames(d).slice().sort((p, q) => dmg(q) - dmg(p));
  const grid = el("div", "cmp-rank");
  grid.style.gridTemplateColumns = `auto repeat(${names.length}, minmax(0, 1fr))`;
  grid.append(el("span", "cmp-rank-k", "편성"));
  for (const nm of names) {
    const cell = el("div", "cmp-rcell");
    const f = el("div", "cmp-face");
    f.title = nm;
    const rec = byName.get(nm);
    if (rec?.img) {
      const im = el("img");
      im.src = artSrc(rec, nm);
      im.alt = nm;
      im.loading = "lazy";
      im.decoding = "async";
      im.draggable = false;
      f.append(im);
    } else {
      f.append(el("span", "cmp-face-none", nm.slice(0, 1)));
    }
    cell.append(f);
    grid.append(cell);
  }
  return grid;
}

boot();
