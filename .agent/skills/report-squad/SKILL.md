---
name: report-squad
description: 스쿼드 딜량 보고서를 만든다. 조합·버스트 운용 비교, enikk.app 실사용 기록 대조, 기존 후보에서 캐릭터 중복 없는 솔로레이드 N스쿼드 최적화, 사용자가 직접 지정한 N×5 편성의 총딜 계산을 요청할 때 사용한다. 한 캐릭터의 육성 효율은 report-growth를 사용한다.
---

# report-squad

요청을 먼저 다음 셋 중 하나로 분류한다.

| 요청 | 읽을 문서 | 실행 도구 |
|---|---|---|
| 조합·운용·조건 비교 | `references/format.md` | `scripts/report.py` |
| enikk 실사용·실측 대조 | `references/enikk.md`와 `references/format.md` 관련 절 | `scripts/enikk_spec.py`, `scripts/report.py`, `scripts/report_ref.py` |
| 캐릭터 중복 없는 솔로레이드 N덱, 또는 사용자가 지정한 N×5 편성 계산 | `references/optimize-solo-raid.md` | `scripts/optimize_solo_raid.py` |

한 캐릭터의 스킬·옵션 투자 효율이면 이 스킬을 쓰지 말고 `report-growth`로 보낸다.

## 일반 비교

1. `references/format.md`와 `context/ALIASES.md`를 읽는다.
2. 케이스 목록을 확정한다. 케이스 1건은 스쿼드·육성·버스트 운용·랩쳐 조건의 묶음이다.
3. 별칭은 정식 명칭으로 바꾼다. 표에 없는 축약어는 추측하지 않는다.
4. 랩쳐 코드가 없으면 반드시 묻고, 코어·파츠도 확인한다.
5. 육성·운용 비교에서는 비교축 밖의 변수를 모두 고정한다.
6. 확정한 케이스를 보여주고 애매한 점을 해소한 뒤 실행한다.

스펙은 `.report-work/<영문-슬러그>/spec.json`에 둔다.

```bash
python .agent/skills/report-squad/scripts/report.py \
  .report-work/<슬러그>/spec.json --jobs 8
```

같은 슬러그의 캐시가 있으면 기본 실행도 **신규·시뮬 입력이 바뀐 케이스만 계산**하고,
나머지는 기존 결과를 재사용한다. 계산기 코드·공용 스펙·파싱 데이터 또는 시드·반복 조건이
바뀌면 자동으로 전체 재계산한다. 유저가 처음부터 재계산을 요청했을 때만 `--full`을 쓴다.
표시만 고치면 `--from-cache`를 사용한다. 결과는 `reports/<슬러그>.html`, 캐시는
`.report-work/<슬러그>/result.data.json`에 생기며 `reports/index.html`도 갱신된다.

## 결과 보고

1. 생성된 HTML 경로를 알려준다.
2. 케이스별 평균 총딜 ± 표준편차를 억 단위로 짧게 적는다.
3. 기본 스펙 이탈 내용을 답변에도 옮긴다. 없으면 `1층 그대로`라고 적는다.
4. 예상 밖 순위·높은 CV·기여도 독식처럼 눈에 띄는 점만 한두 줄 덧붙인다.
5. 해석 요청이 아니면 분석을 길게 쓰지 않는다.

## 보고서 관리

```bash
python .agent/skills/report-squad/scripts/reportctl.py list
python .agent/skills/report-squad/scripts/reportctl.py reindex
python .agent/skills/report-squad/scripts/reportctl.py remove <슬러그>       # 미리보기
python .agent/skills/report-squad/scripts/reportctl.py remove <슬러그> --yes
python .agent/skills/report-squad/scripts/reportctl.py prune               # 미리보기
```

삭제는 사용자가 명시적으로 요청했을 때만 `--yes`로 실행한다. 다른 최적화 보고서가 참조하는
원본은 삭제를 거부한다. 관계를 확인하고 함께 정리할 때만 `--force`를 사용한다.

## 하지 않는 것

- 요청하지 않은 비교군을 임의로 추가하지 않는다.
- 이미지가 없어도 재다운로드하지 않는다. 파일명 규칙과 존재 여부만 보고한다.
- 계산기 코드를 고치지 않는다. 이상한 결과는 별도 버그로 보고한다.
