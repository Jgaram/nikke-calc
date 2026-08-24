/* 다국어 — 한국어 원문을 키로 쓰는 사전 하나.
 *
 *  `T("덱 비우기")`처럼 **한국어 문장을 그대로 키로** 쓴다. 키를 따로 짓지 않아
 *  옮길 때 문장만 감싸면 되고, 번역이 없으면 한국어가 그대로 보인다(깨지지 않는다).
 *  니케 이름·스킬 설명(`game.<lang>.json`, scraper/cdn_locale.py)도 같은 사전에
 *  섞인다 — 화면에 찍히는 글자는 UI든 게임 데이터든 «한국어 원문 → 현지어» 하나로
 *  끝나야 `el()` 한 군데에서 다 바뀐다.
 *
 *  이 파일은 app.js **앞에서** 돈다. 언어를 동기로 정하고, 사전(`i18n/<lang>.js`)도
 *  파서를 막는 스크립트로 바로 뒤에 끼워 app.js가 뜰 때는 이미 있다. 한국어는 사전이
 *  없다 — 원문이 곧 답이다.
 */
(() => {
  const ASSET_V = "dev";                 // build.py가 지문으로 바꾼다
  const KEY = "nikke.lang.v1";
  const LANGS = [["ko", "한국어"], ["en", "English"], ["ja", "日本語"], ["zh", "繁體中文"]];
  const CODES = LANGS.map(([c]) => c);

  /** 브라우저 언어 → 지원 언어. 한·일·중이면 그것, 나머지는 영어다. */
  function guess() {
    for (const l of navigator.languages || [navigator.language || ""]) {
      const p = String(l).toLowerCase().slice(0, 2);
      if (p === "ko" || p === "ja" || p === "zh") return p;
      if (p === "en") return "en";
    }
    return "en";
  }
  let lang = "ko";
  try {
    const saved = localStorage.getItem(KEY);
    lang = CODES.includes(saved) ? saved : guess();
  } catch { lang = guess(); }

  const html = document.documentElement;
  html.lang = lang === "zh" ? "zh-Hant" : lang;
  html.dataset.lang = lang;
  // 한국어가 아니면 사전이 올 때까지 본문을 감춘다 — 한국어가 먼저 보였다가 바뀌는
  // 깜빡임이 «번역이 덜 됐다»로 읽힌다. 사전이 못 오면(네트워크) 그냥 연다.
  if (lang !== "ko") html.dataset.i18n = "pending";   // app.js boot()가 apply 뒤에 지운다

  const DICT = new Map();
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];

  /** 번역. `{name}` 자리는 `params`로 채운다 — 한국어일 때도 채운다. */
  function T(s, params) {
    if (typeof s !== "string") return s;
    let v = DICT.get(s);
    if (v == null || v === "") v = s;
    if (params) v = v.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
    return v;
  }
  T.has = (s) => DICT.has(s);

  /** 딜 단위. 한·일·중은 억(億), 영어는 B/M — «89.98억»을 «8.998B»로 읽는 사람들이다. */
  function dmg(n) {
    const v = Number(n) || 0;
    if (lang === "en") {
      const a = Math.abs(v);
      if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
      if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
      return `${(v / 1e3).toFixed(0)}K`;
    }
    const unit = lang === "ko" ? "억" : "億";
    return `${(v / 1e8).toFixed(2)}${unit}`;
  }

  /** 문서 안의 정적 글자를 바꾼다. 텍스트 노드는 앞뒤 공백을 남기고 가운데만 바꾼다. */
  function apply(root = document.body) {
    if (lang === "ko" || !root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        const p = n.parentNode;
        if (!p || p.nodeName === "SCRIPT" || p.nodeName === "STYLE") return NodeFilter.FILTER_REJECT;
        return /\S/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    const nodes = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
    for (const n of nodes) {
      const raw = n.nodeValue;
      const key = raw.trim().replace(/\s+/g, " ");
      const v = DICT.get(key);
      if (v) n.nodeValue = raw.replace(raw.trim(), v);
    }
    for (const a of ATTRS) {
      for (const e of root.querySelectorAll(`[${a}]`)) {
        const v = DICT.get(e.getAttribute(a));
        if (v) e.setAttribute(a, v);
      }
    }
  }

  /** 아직 한국어로 남은 글자 — 번역 빠진 곳을 찾는 검사용. 콘솔에서 `I18N.audit()`. */
  function audit(root = document.body) {
    const out = [];
    const ko = /[가-힣]/;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!ko.test(n.nodeValue)) continue;
      const p = n.parentElement;
      if (!p || p.closest("script,style")) continue;
      if (p.closest("[hidden]") || p.offsetParent === null && !p.closest("dialog[open]")) continue;
      const path = p.id ? `#${p.id}` : `${p.tagName.toLowerCase()}.${String(p.className).split(" ")[0]}`;
      out.push({ text: n.nodeValue.trim(), at: path });
    }
    for (const a of ATTRS) {
      for (const e of root.querySelectorAll(`[${a}]`)) {
        const v = e.getAttribute(a);
        if (ko.test(v)) out.push({ text: v, at: `${a}@${e.id || e.tagName.toLowerCase()}` });
      }
    }
    return out;
  }

  function setLang(l) {
    if (!CODES.includes(l) || l === lang) return;
    try { localStorage.setItem(KEY, l); } catch { /* 저장 못 해도 이번 방문은 바뀐다 */ }
    // 화면 전체가 다른 글자로 다시 그려져야 한다 — 새로고침이 가장 정직하다.
    location.reload();
  }

  /** 맨 아래 언어 고르개. 지금 언어는 눌린 채로 둔다. */
  function mountPicker(box) {
    if (!box) return;
    box.textContent = "";
    for (const [code, label] of LANGS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lang-btn";
      b.textContent = label;
      b.lang = code === "zh" ? "zh-Hant" : code;
      b.setAttribute("aria-pressed", String(code === lang));
      b.onclick = () => setLang(code);
      box.append(b);
    }
  }

  /** 사전 주입 — `dist/i18n/<lang>.js`(build.py가 JSON을 굽는다)가 부른다. */
  function load(obj) {
    for (const [k, v] of Object.entries(obj || {})) if (v) DICT.set(k, v);
  }
  // fetch가 아니라 **파서를 막는 스크립트**로 받는다. app.js는 최상위 상수에서도
  // `T()`를 부르는데, 그 시점에 사전이 없으면 그 상수는 영영 한국어다. 같은 출처의
  // 작은 파일이라 파싱을 잠깐 세우는 값이 그 버그보다 싸다.
  if (lang !== "ko") {
    document.write(`<script src="i18n/${lang}.js?v=${ASSET_V}"><\/script>`);
  }
  const ready = Promise.resolve();

  window.T = T;
  window.I18N = { lang, LANGS, ready, load, apply, audit, setLang, mountPicker, dmg, dict: DICT };
})();
