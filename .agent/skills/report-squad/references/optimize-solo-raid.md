# 솔로레이드 N스쿼드 최적화

기존 `report-squad` 캐시의 후보 중 캐릭터가 겹치지 않는 정확히 N개 스쿼드를 골라
평균 총딜 합이 최대인 해를 찾는다. 새 시뮬레이션은 하지 않는다.

## 시작 조건

- 같은 랩쳐·전투 시간·기본 육성으로 계산된 후보 보고서가 있어야 한다.
- 후보 캐시는 `.report-work/<원본-슬러그>/result.data.json`에 있어야 한다.
- 후보에 없는 스쿼드를 임의로 만들지 않는다.
- 기본 육성이 다른 캐시는 한 최적화에 섞지 않는다.

## 스펙

`.report-work/<최적화-슬러그>/spec.json`:

```json
{
  "title": "작열 솔로레이드 5스쿼드 총딜 최적화",
  "sources": ["../fire-solo-decks/result.data.json"],
  "target": {
    "enemy": {"code": "풍압", "core_px": 0, "has_parts": false},
    "config": {"duration": 180.0}
  },
  "require_same_defaults": true,
  "squad_count": 5,
  "top_k": 10,
  "variants": [{"name": "전체"}]
}
```

`variants`에는 `exclude_members` 같은 후보 필터를 둘 수 있다. 사용자 요청 없이 제외 조건을
추가하지 않는다.

## 실행과 보고

```bash
python .agent/skills/report-squad/scripts/optimize_solo_raid.py \
  .report-work/<최적화-슬러그>/spec.json
```

결과는 `reports/<최적화-슬러그>.html`에 생긴다. 답변에는 최적 총딜 합과 1위 해의 N개
스쿼드를 적고, 후보 원본·후보 수·제외 조건을 함께 밝힌다. 원본 보고서의 기본 스펙 이탈도
그대로 이어서 보고한다.
