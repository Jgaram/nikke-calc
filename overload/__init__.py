"""오버로드 장비 옵션 뽑기 — 비용·가치·정책.

`..\\module\\`에 있던 목표 도달 DP를 들여오면서, 레벨(수치)과 딜 가치를 함께
다루도록 넓힌 패키지다. 셋으로 나뉜다.

| 모듈 | 답하는 질문 |
|---|---|
| `mechanics` | 게임 규칙이 무엇인가 (슬롯·옵션·레벨 확률, 잠금 비용) |
| `reach` | 이 옵션 조합을 맞추는 데 모듈이 몇 개 드나 |
| `value` | 이 옵션 한 줄이 우리 덱 총딜을 몇 % 올리나 |
| `policy` | 지금 잠글까, 다시 굴릴까, 그만둘까 |
| `rollout` | 그 정책대로 굴리면 실제로 모듈이 몇 개 들고 꼬리는 어떤가 |

`reach`는 `cost/`가 쓰는 "목표 도달 기대 소모량"의 정본이고, `policy`는 목표 대신
교환비 λ로 판단하는 쪽이다. 둘은 같은 게임의 다른 질문이라 코드를 합치지 않는다 —
`reach`는 레벨을 안 보는 대신 정확한 유리수를 내고, `policy`는 레벨을 보는 대신
레벨을 버킷으로 묶는다.

    from overload import Goal, expected_cost
    expected_cost(Goal(mandatory={"우코", "공"}, optional={"장탄"}))     # 45.86

    from overload import DeckContext, marginals, Values, Overload
    ctx = DeckContext(names=[...])
    m = marginals(ctx)                                   # 옵션별 한계가치
    g = Overload(Values.from_marginals(m, "리틀 머메이드"), lam=0.05)
    g.best(현재상태)                                      # 그만두기 / 효과변경 / 수치변경
"""

from .mechanics import LEVEL_P, OPTIONS, P_SHOW, SLOTS, STAT_KEY, WEIGHTS, roll_cost
from .policy import EMPTY, EffectGame, Overload, PolishGame, Values
from .reach import Goal, ReachDP, expected_cost, quantiles
from .rollout import rollout
from .value import (Build, DeckContext, Marginals, default_build, equip_skills,
                    marginals, separability_error)

__all__ = [
    "LEVEL_P", "OPTIONS", "P_SHOW", "SLOTS", "STAT_KEY", "WEIGHTS", "roll_cost",
    "Goal", "ReachDP", "expected_cost", "quantiles",
    "Build", "DeckContext", "Marginals", "default_build", "equip_skills",
    "marginals", "separability_error",
    "EMPTY", "EffectGame", "Overload", "PolishGame", "Values",
    "rollout",
]
