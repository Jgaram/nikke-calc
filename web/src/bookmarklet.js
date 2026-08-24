// 블라링크 육성 데이터 수집 북마클릿 — **본인 계정 전용**.
//
// blablalink.com 탭에서 실행되므로 api.blablalink.com 호출이 사이트 자신의 허용 오리진과
// 같다(우리 사이트 오리진에서는 CORS로 막힌다 — OPTIONS가 405이고 ACAO 헤더가 없다).
// 세션 쿠키는 credentials:"include"로 자동 전송되며 **브라우저를 떠나지 않는다.**
// 나가는 것은 조회 결과 JSON뿐이고, 그것도 사용자가 직접 파일로 받아 옮긴다.
//
// 조회 대상은 로그인한 본인으로 고정한다. 타인 openid를 받지 않는다 —
// 남의 프로필을 긁는 도구가 되지 않게 하는 가장 확실한 방법이다.
//
// game_* 쿠키는 전부 HttpOnly라 document.cookie로 못 읽는다(실측 2026-08-21). 읽을 필요도 없다:
//   내 openid ← ugc/proxy/standalonesite/User/GetUserInfoNew  data.info.intl_openid
//   area_id   ← game/proxy/Game/GetSavedRoleInfo              data.role_info.area_id
// 덕분에 area 후보 탐색(최대 7회 낭비 요청)이 필요 없다.
//
// 실측 비용(199종 계정): 요청 7회 · 수신 345KB · 3.1초.
//
// 이 파일은 사이트가 텍스트로 읽어 `javascript:` 북마클릿 URL로 만든다 (app.js).
// 그래서 최상위는 즉시 실행 함수 하나여야 하고, 값을 반환하면 안 된다(void로 감싼다).

void (async () => {
  const BASE = "https://api.blablalink.com/api/";
  const COMMON = { game_id: "29080", area_id: "global", source: "pc_web",
                   intl_game_id: "29080", language: "ko", env: "prod" };

  if (!location.hostname.endsWith("blablalink.com")) {
    alert("blablalink.com 탭에서 실행해야 합니다.\n로그인한 상태로 blablalink.com을 열고 다시 눌러 주세요.");
    return;
  }

  // 진행 표시 — 3초 동안 아무 반응이 없으면 눌린 줄 모른다
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;z-index:2147483647;right:16px;bottom:16px;padding:14px 18px;" +
    "background:#15161a;color:#e9eaee;font:14px/1.5 system-ui,sans-serif;border-radius:10px;" +
    "box-shadow:0 6px 24px rgba(0,0,0,.45);max-width:320px;white-space:pre-line";
  document.body.appendChild(box);
  const say = (t) => { box.textContent = t; };
  const done = (t, ms) => { say(t); setTimeout(() => box.remove(), ms); };

  const post = async (path, body) => {
    const r = await fetch(BASE + path, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Channel-Type": "2",
        "X-Language": "ko",
        "X-Common-Params": JSON.stringify(COMMON),
      },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  };

  // "29080-1234…" 또는 "1234…" → "1234…"
  const strip = (v) => {
    const m = String(v == null ? "" : v).match(/(\d{6,})\s*$/);
    return m ? m[1] : null;
  };

  try {
    say("니케 데이터를 받는 중…\n(1/4) 계정 확인");
    const me = await post("ugc/proxy/standalonesite/User/GetUserInfoNew", {});
    if (me.code !== 0) throw new Error("계정 확인 실패 (" + me.code + " " + me.msg + ")");
    const openid = strip(((me.data || {}).info || {}).intl_openid);
    if (!openid) throw new Error("로그인 상태가 아닙니다. blablalink에 로그인하고 다시 눌러 주세요.");

    const role = await post("game/proxy/Game/GetSavedRoleInfo", {});
    const area = parseInt((((role.data || {}).role_info || {}).area_id) || "0", 10) || null;
    if (!area) throw new Error("게임 계정이 연동돼 있지 않습니다 (area_id 없음).");

    say("니케 데이터를 받는 중…\n(2/4) 보유 니케");
    const rc = await post("game/proxy/Game/GetUserCharacters",
                          { intl_open_id: openid, nikke_area_id: area });
    if (rc.code !== 0 || !(rc.data || {}).characters) {
      throw new Error("보유 니케를 받지 못했습니다 (" + rc.code + " " + rc.msg + ")");
    }
    const characters = rc.data.characters;

    const codes = characters.map((c) => c.name_code);
    const details = [], stateEffects = [];
    for (let i = 0; i < codes.length; i += 60) {   // 상세는 60개씩 배치
      say("니케 데이터를 받는 중…\n(3/4) 육성 상세 " +
          Math.min(i + 60, codes.length) + "/" + codes.length);
      const rd = await post("game/proxy/Game/GetUserCharacterDetails",
        { intl_open_id: openid, nikke_area_id: area, name_codes: codes.slice(i, i + 60) });
      if (rd.code !== 0) throw new Error("육성 상세 실패 (" + rd.code + " " + rd.msg + ")");
      details.push(...(rd.data.character_details || []));
      stateEffects.push(...(rd.data.state_effects || []));
    }

    say("니케 데이터를 받는 중…\n(4/4) 전초기지");
    const ro = await post("game/proxy/Game/GetUserProfileOutpostInfo",
                          { intl_open_id: openid, nikke_area_id: area });

    const raw = {
      openid, area, characters, details,
      state_effects: stateEffects,
      outpost: (ro.data || {}).outpost_info || null,
      _source: "bookmarklet",
      _collected_at: new Date().toISOString(),
    };

    // 파일로 떨어뜨린다. 사용자가 이 파일을 계산기 사이트에 드롭한다.
    const blob = new Blob([JSON.stringify(raw)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "nikke-raw-" + openid + ".json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);

    done("완료 — 니케 " + details.length + "종\n" +
         "받은 파일을 계산기 사이트에 드롭하세요.", 12000);
  } catch (e) {
    done("실패: " + (e && e.message ? e.message : e), 15000);
  }
})();
