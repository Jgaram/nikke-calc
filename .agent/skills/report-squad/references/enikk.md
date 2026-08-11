# enikk 대조 보고서

## 목차

- [먼저 확인할 것](#0-먼저-확인할-것)
- [시즌 찾기](#1-시즌-찾기)
- [Teams 탭 긁기](#2-teams-탭-긁기)
- [스펙과 기준값 만들기](#3-스펙--기준값-만들기)
- [실행](#4-실행)
- [대조판 렌더](#5-대조판-렌더)
- [보고](#6-보고)

일반 비교와 달리 케이스를 사용자가 부르는 게 아니라 **enikk.app의 실사용 통계에서 뽑는다.**
결과에도 우리 시뮬 총딜과 함께 **enikk 실제 평균딜·비율**이 붙는다.

계산 자체는 `report-squad`의 러너·렌더러를 그대로 쓴다. 여기 있는 건 ① 수집 ② 조인 ③ 대조 표시뿐이다.
스펙 형식·육성 기본값·운용 조건 표기는 전부 `format.md`가 정본이다 —
**이 문서는 그걸 다시 적지 않는다.**

## 0. 먼저 확인할 것

유저 요청에서 세 가지가 정해져야 한다. 없으면 **묻는다.**

| 항목 | 예 |
|---|---|
| 어느 판 | 솔로레이드 시즌 35 / 유니온레이드 43 / 특정 약점 속성의 최근 시즌 |
| 사용 횟수 컷 | "5회 이상" · "3회 이상" — 컷마다 덱 수가 몇 배씩 달라진다 |
| 랩쳐 설정 | 코드는 사이트에서 읽히지만 **코어 유무는 안 읽힌다.** 반드시 묻는다 |

## 1. 시즌 찾기

`https://enikk.app/soloraid` 목록에서 고른다. 카드에 `SEASON n · 보스명 · 속성`이 있고
여기 **속성은 보스 자신의 속성이 아니라 약점**이다 (목록 카드 기준). 시즌 페이지 `Boss` 탭에서
`Element`(보스 속성)와 `Weakness`(약점)를 따로 확인해 **어느 쪽이 랩쳐 코드인지 확정한다.**

> 랩쳐 코드에는 **보스 속성**을 넣는다. `enemy.code=풍압`이면 작열 캐릭터에 특효가 붙는다
> (`format.md §랩쳐 설정`). 약점 속성을 코드에 넣으면 특효가 반대로 걸린다.

유저가 "작열 약점 레이드"라고 하면 **Weakness가 작열**인 시즌이다.

## 2. Teams 탭 긁기

시즌 페이지 → `Teams` 탭 → `25 per page` → `Sort by Parse Count`.
페이지마다 아래를 `browser_evaluate`로 돌리고 출력을 이어 붙인다.

```js
() => {
  const all=[...document.querySelectorAll('main *')];
  let rows=all.filter(el=>el.querySelectorAll('img[alt$="character"]').length===5);
  rows=rows.filter(el=>!rows.some(o=>o!==el&&el.contains(o)));
  const pg=document.querySelector('main').innerText.match(/Page \d+ of \d+/);
  return (pg&&pg[0])+' :: '+rows.map(r=>{
    let p=r; for(let i=0;i<6;i++){p=p.parentElement; if(p&&/\d/.test(p.innerText))break;}
    const t=p.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
    const [mx,av,n]=t.slice(-3);
    return [...r.querySelectorAll('img[alt$="character"]')]
      .map(i=>decodeURIComponent(i.src).match(/si_c(\d+)_/)[1]).join(',')
      +'='+n+'|'+mx.replace('B','')+'|'+av.replace('B','');
  }).join(' ');
}
```

한 줄이 덱 하나다 — `id,id,id,id,id=사용횟수|최고딜B|평균딜B`.
**컷 미만이 나오는 페이지까지만** 넘기면 된다(파스수 내림차순이라 그 뒤는 전부 컷 아래).
전체 덱 수를 세야 하면 마지막 페이지까지 간다.

덤프는 scratchpad에 `.txt`로 저장한다.

### 왜 id로 긁나

썸네일 URL의 `si_c{id}_`가 `scraper/nikke_scraped.json`의 `id`와 같은 체계다.
**이름 매칭은 반드시 틀린다** — 한국 서버가 영문명을 음차하지 않는 캐릭터가 있다
(`Liter`=리타, `Moran`=목단, `Rouge`=루주). 부제 구분자도 우리는 ` : `, enikk은 `: `다.

## 3. 스펙 + 기준값 만들기

```bash
python .agent/skills/report-squad/scripts/enikk_spec.py <덤프.txt> \
    --slug sr35-enikk-teams --min-uses 3 \
    --title "솔로레이드 S35 Crystal Chamber — enikk 실사용 조합 딜량" \
    --note  "enikk.app 시즌 35(작열 약점) Teams 데이터에서 3회 이상 사용된 조합 중 스킬 파싱이 끝난 것. 스쿼드 순서는 enikk 표기 그대로." \
    --code 풍압 --core-px 0 --runs 5
```

`.report-work/<슬러그>/spec.json`과 `.report-work/<슬러그>/ref.json`이 나온다.

**`parsed_skills.json`에 없는 캐릭터가 낀 덱은 자동으로 빠지고 목록이 출력된다.**
빠진 덱은 **반드시 유저에게 보고한다** — 사용 횟수가 큰 덱이 빠졌으면 유저가
`char-add`를 먼저 돌릴지 판단할 문제다. 에이전트가 임의로 등록하러 가지 않는다.

스쿼드 배열 순서는 enikk 표기 그대로 둔다(= 버스트 우선순위로 해석된다).
**임의로 재정렬하지 않는다.** 순서를 바꾸면 우리가 만든 조합이지 실사용 기록이 아니다.

## 4. 실행

```bash
python .agent/skills/report-squad/scripts/report.py \
    .report-work/<슬러그>/spec.json --jobs 8
```

덱이 수십 개라 오래 걸린다(46개 × 5회 ≈ 8분). **백그라운드로 돌린다.**

## 5. 대조판 렌더

```bash
python .agent/skills/report-squad/scripts/report_ref.py \
    .report-work/<슬러그>/result.data.json .report-work/<슬러그>/ref.json
```

최종 `reports/<슬러그>.html`을 덮어쓴다. 케이스 카드마다
`enikk 평균 5.84B · 비율 0.96` 칩이 붙는다(0.9 미만 주황, 1.1 초과 파랑).
시뮬을 다시 돌리지 않고 캐시만 읽으므로 즉시 나온다.

**`report_html.py`는 고치지 않는다.** `report_ref.py`가 `_case_card`만 감싸 갈아끼운다.
공용 보고서 형식이 바뀌면 안 되기 때문이다 — 원본 구조가 바뀌면 스크립트가 에러로 끊으니
그때 앵커를 고친다.

기준값 파일(`.report-work/<슬러그>/ref.json`)은 `label`/`unit`/`scale`/`by_squad`만 있으면 되므로
enikk이 아닌 출처에도 그대로 쓸 수 있다.

## 6. 보고

`report-squad`의 보고 규칙을 그대로 따른다 (`../SKILL.md §결과 보고`) — **기본 스펙 이탈 배너를
답변에도 옮긴다.** 그 위에 이 스킬만의 두 가지를 덧붙인다.

1. **제외된 덱** — 사용 횟수와 막은 캐릭터.
2. **비율의 분포** — 평균·CV·범위. 상관계수(Spearman)까지 내면 더 좋다.

### 비율을 해석할 때

enikk은 랭커 기록이라 육성이 우리 기본 스펙보다 좋다. 따라서 비율(우리/enikk)은
**1보다 작은 게 정상**이고, 관심사는 절대값이 아니라 **흩어짐과 계통성**이다.

- 비율이 고르면 → 순위 비교가 신뢰할 만하다.
- **특정 계열만 몰려서 낮거나 높으면 그건 육성 차이가 아니라 우리 계산기의 편향 신호다.**
  유저에게 그렇게 보고한다. 예: S35에서 미하라 : 본딩 체인 계열 5개가 전부 0.58~0.67,
  토브 계열 3개가 0.95~1.10 (전체 평균 0.82 · CV 14%).
  > 이 예시 수치는 **기본 스펙 오버로드 교체 이전**에 잰 것이다. 지금은 우리 총딜이
  > 1~5% 올라 비율도 그만큼 위로 밀린다 — 절대값을 이 예시와 직접 견주지 않는다.
  > 봐야 할 것은 여전히 흩어짐과 계통성이다.
- **비율이 1을 넘으면 특히 의심한다.** 랭커 평균을 우리 기본 스펙이 이겼다는 뜻이다.

**계산기를 여기서 고치지 않는다.** 편향을 찾으면 버그로 분리해 유저에게 보고하는 데서 멈춘다.

## 하지 않는 것

- enikk 수치를 우리 데이터 파일(`data/`)에 써넣지 않는다. 기준값은 해당 `.report-work/` 묶음에만 둔다.
- 사용 횟수 컷을 임의로 바꾸지 않는다. 덱이 너무 많으면 **줄이자고 제안하고 유저가 정한다.**
- 이름으로 캐릭터를 맞추지 않는다 (§2 참조).
