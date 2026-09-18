# 패턴 영상 프레임 판독

보스 스크립트의 시각은 **플레이 영상의 인게임 타이머**에서 나온다. 영상을 눈으로 훑는 대신,
내장 브라우저에서 원하는 시각으로 seek 해 **필요한 구역만 확대 합성한 한 장**을 찍어 읽는다.

전제: 내장 브라우저(`mcp__Claude_Browser__*`). 영상 재생·정지·seek는 전부 페이지 안 JS로 한다.
다운로드하지 않는다 — 브라우저에서 보는 것으로 충분하다.

## 1. 열고 화질 올리기

```js
// mcp__Claude_Browser__navigate 로 watch 페이지를 연 뒤
const p = document.querySelector('#movie_player');
p.setPlaybackQualityRange && p.setPlaybackQualityRange('hd1080', 'hd1080');
const v = document.querySelector('video');
v.muted = true; v.play();                    // 한 번 재생해야 고화질 버퍼가 붙는다
await new Promise(r => setTimeout(r, 4000)); v.pause();
JSON.stringify({ w: v.videoWidth, h: v.videoHeight });   // 1920x1080이 나와야 한다
```

기본 화질(`tiny`, 256x144)로는 타이머 숫자를 못 읽는다. **반드시 확인하고 시작한다.**

## 2. 화면에서 유튜브 UI를 걷어내기

플레이어를 그대로 키우면 상단 검색창·제목이 덮는다. 비디오 엘리먼트를 **새 컨테이너로 옮기고**
`!important` 스타일로 크기를 고정하는 편이 확실하다(유튜브가 인라인 스타일을 되돌린다).

```js
const v = document.querySelector('video');
const wrap = document.createElement('div'); wrap.id = 'cc-wrap';
wrap.style.cssText = 'position:fixed;left:0;top:0;width:800px;height:600px;background:#000;z-index:2147483647';
document.body.appendChild(wrap); wrap.appendChild(v);
const st = document.createElement('style'); st.id = 'cc-mode'; document.head.appendChild(st);
```

**스크린샷이 잡는 영역과 CSS px가 1:1이 아닐 수 있다.** 확인 방법: 알려진 자리에 빨간 상자를 띄우고
찍어 본다. 안 보이면 그만큼 잘린 것이다.

```js
const b = document.createElement('div');
b.style.cssText = 'position:absolute;left:600px;top:600px;width:100px;height:100px;background:red;z-index:5';
document.getElementById('cc-wrap').appendChild(b);
```

## 3. 합성 캔버스 — 한 장에 필요한 것만

교차 출처 영상은 캔버스에 **그릴 수는 있다**(읽지 못할 뿐이라 화면 표시는 된다). 전체 화면과 함께
타이머·스킬 설명창·자막을 확대해 붙이면 한 장으로 다 읽힌다. 좌표는 1920x1080 기준이고 영상마다
UI 위치가 다르니 첫 프레임을 보고 맞춘다.

```js
const v = document.querySelector('#cc-wrap video'), c = document.createElement('canvas');
c.id = 'cc-canvas'; document.getElementById('cc-wrap').appendChild(c);
c.width = 680 * 1.5; c.height = 512 * 1.5;
c.style.cssText = 'position:absolute;left:0;top:0;width:680px;height:512px;z-index:3;background:#000';
document.getElementById('cc-mode').textContent =
  '#cc-wrap{overflow:hidden!important} #cc-wrap video{width:40px!important;height:22px!important;opacity:0.01}';
const g = c.getContext('2d');
window.ccDraw = () => {                       // S = 1.5배로 그려 글자가 뭉개지지 않게
  const S = 1.5; g.fillStyle = '#000'; g.fillRect(0, 0, c.width, c.height);
  g.drawImage(v, 0, 0, 1920, 1080, 0, 0, 480 * S, 270 * S);          // 전체 화면
  g.drawImage(v, 1540, 0, 380, 110, 485 * S, 0, 195 * S, 56 * S);    // 오른쪽 위 = 인게임 타이머
  g.drawImage(v, 0, 0, 700, 230, 485 * S, 60 * S, 195 * S, 64 * S);  // 왼쪽 위 = 보스 스킬 설명창
  g.drawImage(v, 380, 900, 1160, 110, 0, 275 * S, 680 * S, 64 * S);  // 아래 = 자막
  g.fillStyle = '#fff'; g.font = `${16 * S}px sans-serif`;
  g.fillText('v=' + v.currentTime.toFixed(1), 490 * S, 200 * S);     // 영상 시각도 같이 적는다
};
window.ccShot = async (t) => {                // seek → 그리기 → 합성 반영까지 기다린다
  v.pause();
  await new Promise(res => { const h = () => { v.removeEventListener('seeked', h); res(); };
                             v.addEventListener('seeked', h); v.currentTime = t; setTimeout(res, 3000); });
  await new Promise(r => setTimeout(r, 300)); window.ccDraw();
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  await new Promise(r => setTimeout(r, 400)); return v.currentTime;
};
```

`ccShot`이 **그린 뒤 한 프레임 더 기다리는 게 중요하다.** 안 기다리면 스크린샷이 직전 그림을 잡아
한 칸씩 밀린 자료가 나온다(캔버스에 영상 시각을 같이 찍어 두면 바로 드러난다).

## 4. 훑기

`browser_batch`로 `ccShot(t)` → `screenshot`을 번갈아 6~8쌍씩 묶는다. 스크린샷은 `scale` 0.55~0.8이면
자막과 타이머가 읽힌다.

```
[{"name":"javascript_tool","input":{"action":"javascript_exec","text":"await window.ccShot(96)"}},
 {"name":"computer","input":{"action":"screenshot","scale":0.6}}, …]
```

- 처음엔 3초 간격으로 전체를 훑어 구간(패턴) 목록을 만든다.
- 구간 경계(패턴 시작·저지 성공·배리어 해제)만 1초 이하로 좁힌다.
- 브라우저 창이 가려지면 스크린샷이 실패한다. 그때는 `get_page_text`·`read_page`로 바꾸거나 멈춘다.

## 5. 읽은 값 적기

| 화면에서 | 스크립트로 |
|---|---|
| 카운트다운 `02:36` | `t = 180 − 156 = 24` |
| 저지 UI 남은 시간 두 프레임 | 제한시간 = 남은 시간 + (시작 시각 − 그 프레임 시각) |
| 스킬 설명창 이름 | 프리셋 `skills`의 어느 항목인지 잇는다 |
| 자막(업로더 해설) | 분기·조건. 본문 글과 어긋나면 **둘 다** note에 남긴다 |

시계는 정수로 내림해 표시된다. 같은 초에 두 사건이 보이면 순서까지는 단정하지 않는다 — `±1초`로 적는다.
