"""자동 버스트 선택 — 같은 단계 둘째 니케 (docs/GAMEPLAY.md §버스트 사용 순서와 배치).

    python -m unittest discover -s tests -v

1·2·3단계 모두 **스쿼드 입력 순서에서 쿨이 풀린 첫 니케**가 쓴다. 앞사람이 쿨이면 같은
단계 뒷사람이 나가고, 앞사람 쿨을 기다리지 않는다. 종전 「B1/B2는 선두만」 제한을 되살리지
않는지, 그 운용은 여전히 `operation.no_burst`로 표현되는지 본다.
"""

from __future__ import annotations

import unittest

from runner import sim

# B2가 둘: 아르카나(쿨 40초)가 앞, 크라운(쿨 20초)이 뒤. 사이클이 40초보다 짧아
# 아르카나가 쿨인 사이클이 반드시 생긴다.
SQUAD = ["리틀 머메이드", "아르카나", "크라운", "test_B3", "스노우 화이트 : 헤비암즈"]
SIM = {"duration": 60, "expected": True}


def _stage_uses(req: dict) -> list[tuple[float, str, str]]:
    """(시각, 단계, 사용자) — 본 차례 단계 사용만."""
    result = sim.execute(sim.prepare(req))
    return [(e.t, e.event.split()[0].split(":")[1], e.caster)
            for e in result.log.burst_log if e.event.startswith("stage:")]


class SameStageRotation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.uses = _stage_uses({"squad": SQUAD, "sim": SIM})
        cls.uses_no_crown = _stage_uses({"squad": SQUAD, "sim": SIM,
                                         "operation": {"no_burst": "크라운"}})

    def test_leader_first_then_second_when_leader_on_cooldown(self):
        b2 = [who for _, stage, who in self.uses if stage == "2"]
        self.assertEqual(b2[0], "아르카나", "배치 순서가 앞인 니케가 먼저 쓴다")
        self.assertIn("크라운", b2, "선두가 쿨이면 같은 단계 둘째 니케가 쓴다")
        # 아르카나는 쿨 40초라 연속 두 사이클을 못 쓴다 — 그 사이클은 크라운 몫이다
        for prev, cur in zip(b2, b2[1:]):
            self.assertFalse(prev == cur == "아르카나", b2)

    def test_stage2_does_not_wait_for_leader(self):
        """B1을 쓰고 B2가 단계 전환 딜레이(0.1초) 만에 나간다 — 선두 쿨을 기다리지 않는다."""
        b1 = [t for t, stage, _ in self.uses if stage == "1"]
        b2 = [t for t, stage, _ in self.uses if stage == "2"]
        self.assertGreaterEqual(len(b2), 4)
        for t1, t2 in zip(b1, b2):
            self.assertAlmostEqual(t2 - t1, 0.1, delta=0.02)

    def test_no_burst_keeps_old_lead_only_play(self):
        """둘째 니케를 `operation.no_burst`로 빼면 선두 쿨을 기다리는 종전 운용이 된다."""
        b2 = [who for _, stage, who in self.uses_no_crown if stage == "2"]
        self.assertEqual(set(b2), {"아르카나"})
        b1 = [t for t, stage, _ in self.uses_no_crown if stage == "1"]
        b2_t = [t for t, stage, _ in self.uses_no_crown if stage == "2"]
        self.assertTrue(any(t2 - t1 > 5.0 for t1, t2 in zip(b1, b2_t)), "아르카나 쿨을 기다린 사이클이 있다")
        self.assertLess(len(b2), sum(1 for _, stage, _ in self.uses if stage == "2"))


if __name__ == "__main__":
    unittest.main()
