# MG 예열(warm-up) 동작 시나리오

> 캐릭터별 시나리오가 아니라 **MG 무기군 전체**에 적용되는 발사 메카닉 시나리오.
> `/bug-fix`로 작성 (2026-05-28). 예열 잔존·점진 냉각 검증용.

## 메카닉 요약

MG는 사격을 지속할수록 발사 속도가 `fire_rate_min`(3/s) → `fire_rate_max`(60/s)로 상승한다 (예열).

- **예열 진행**: 발사 1회당 `warmup_shots += 1`, `warmup_bullets`(47발)에서 최대. 발사속도 = `min + (max-min) × min(warmup_shots, 47) / 47`. (구현: [calculator/timeline.py](../../calculator/timeline.py) `_current_fire_rate`)
- **예열 냉각**: 사격이 *실제로 멈춘* 구간(재장전·기절·딜레이)만큼 시간 비례로 식는다. 냉각률 `cool_rate = warmup_bullets / cooldown_time = 47 / 1.1 ≈ 42.7발/s`. 정상 연사의 inter-shot 간격은 냉각 대상이 아니다. (구현: `_cool_warmup`)
- **재장전은 예열을 리셋하지 않는다.** 재장전 동안의 미사격이 idle로 계산되어 그만큼만 식는다. → 즉시 재장전(풀버스트 +100% 재장전 속도) 시 예열 완전 보존, 빠른 재장전(버스트 간) 시 부분 냉각.

> 수치(`fire_rate_min` 3/s, 선형 곡선, `cooldown_time` 1.1s)는 모두 **미검증 추정값** → `context/DATA_VERIFY.md` §"MG 예열 곡선".

## 검증 스쿼드

`[리틀 머메이드(SMG,B1), 크라운(MG,B2), 라피 : 레드 후드(MG,B3), 루드밀라 : 윈터 오너(MG,B3), 프리바티(AR,B3)]`

세 MG = 크라운·라피:레드후드·루드밀라:윈터오너. 풀버스트 중 재장전 속도 +100%로 즉시 재장전, 버스트 간에는 부분 버프로 ~0.65s 재장전.

## 체크리스트

`context/test.py`(TARGET=라피:레드후드, 위 스쿼드)로 시뮬 후 MG 일반사격 시각의 inter-shot 간격으로 fire_rate 측정.

- [ ] **Cold start**: 게임 시작 첫 발사 fire_rate ≈ 3/s, 이후 상승. (예열 0에서 출발)
- [ ] **풀버스트 즉시 재장전 직후**: fire_rate ≈ 60/s 유지 (예열 보존 — 재장전으로 리셋 안 됨).
- [ ] **버스트 간 빠른 재장전(~0.65s) 직후**: 3/s < fire_rate < 60/s (부분 냉각, cold보다 빠르게 재예열).
- [ ] **장시간(> cooldown_time 1.1s) 완전 미사격 후**: 예열 0 복귀.

## 비고

- `warmup_shots`는 부분 냉각을 위해 float.
- 냉각은 사격 재개 직전 `_cool_warmup`에서 한 번 적용(`last_fire_t` 기준 idle). idle ≤ 현재 예열 수준 inter-shot × 1.5면 "예약된 연사 대기"로 보아 냉각하지 않는다.
- 회귀: 이 수정으로 재장전하는 MG 포함 스쿼드(스쿼드1 등)의 MG 딜이 상승 → 기준값 갱신 필요(2026-05-28 갱신).
