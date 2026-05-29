"""명중률 → 탄착군 → 코어히트율 분포 모델 추정 (Claude 전용 분석 도구).

커뮤니티 글의 실험적 코어히트율로부터 '탄착군 내부 탄 분포 모델'을 역추정 가능한지
검토한다. 세 모델(균일 원판 / 2D 가우시안 / 일반 거듭제곱)을 동일 3개 앵커에 피팅해
RMSE로 비교하고, 글쓴이의 '명중 1%당 효율' 미분형 데이터를 교차검증으로 병기한다.

실행: python -m context.accuracy_analysis

주의: 외부 데이터·계산기 모듈에 의존하지 않는 독립 스크립트. 모든 입력은 아래 상수.
"""

import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 깨짐 방지

# ── 입력 데이터 (글에서 추출, 동일 타겟 "블스/중거리/팔" → 코어 반경 r_c 공통 상수) ──

# 코어 히트 보너스 C (factor③ 가산분).
# damage.py _factor3(정정 후): (core_dmg_mult − 100)/100 → 200% 무기는 +1.0 (코어=2배).
# 비코어 baseline factor③ B(무버프 평타 = 1.0)와 함께 기대 데미지 ∝ (B + P·C).
CORE_BONUS = 1.0
# 글쓴이 측정 맥락의 비코어 baseline factor③. 순수 평타·무버프면 1.0.
# (풀버스트 +0.5, 적정거리 +0.3, 평균크리 +~0.075 등이 섞이면 더 큼)
BASELINE_B = 1.0

# 탄착군 직경 공식: D(px) = -slope * 명중% + base
PX_FORMULA = {
    "SG":  {"slope": 2.18, "base": 240.0},
    "AR":  {"slope": 0.69, "base": 76.0},
    "SMG": {"slope": 1.00, "base": 110.0},
}


def diameter(weapon: str, acc_pct: float) -> float:
    f = PX_FORMULA[weapon]
    return f["base"] - f["slope"] * acc_pct


# 직접 앵커 (무기, 명중%, 탄착군 직경 px, 실측 코어히트율) — 피팅 대상
# 명중%가 None인 항목은 px가 직접 주어진 경우(AR 2명중 오버로드).
ANCHORS = [
    ("SG", 75.6, 75.0, 0.40),
    ("SG", 85.0, 55.0, 0.90),
    ("AR", None, 60.0, 0.80),
]

# 미분형 "명중 1%당 댐증 효율"(pp) — 교차검증용만. (무기, 명중%, 효율 pp/1%명중)
# 전제: 1% 코어히트율 = 1% 평타댐증 → 효율 ≈ dP_core/d명중.
EFFICIENCY = [
    ("SG", 75.6, 0.462),
    ("SG", 85.0, 1.000),
    ("AR", 23.0, 1.730),
    ("AR", 37.0, 1.480),
    ("SMG", 60.0, 1.330),
    # (110% 특이점은 D≈0 코어보스 한정 → 제외)
]


# ── 분포 모델: 코어히트 확률 P(D, params). R=D/2, 코어 반경 r_c. 조준=코어 중심 가정 ──

def p_uniform(D, r_c):
    """균일 원판: P = min(1, (r_c/R)^2)."""
    R = D / 2.0
    return np.minimum(1.0, (r_c / R) ** 2)


def p_gaussian(D, c):
    """2D 가우시안(Rayleigh): P = 1 - exp(-c/D^2).
    c는 결합 상수(= 2*k^2*r_c^2, σ=D/(2k)). 레벨 피팅에서 r_c와 k는 비식별 → c만 추정."""
    return 1.0 - np.exp(-c / (D ** 2))


def p_power(D, r_c, n):
    """일반 거듭제곱: P = min(1, (r_c/R)^n). n=2면 균일과 동일."""
    R = D / 2.0
    return np.minimum(1.0, (r_c / R) ** n)


# ── 피팅 (그리드 탐색, scipy 미사용) ──

def rmse(pred, obs):
    return float(np.sqrt(np.mean((np.asarray(pred) - np.asarray(obs)) ** 2)))


def fit_models(anchors):
    Ds = np.array([a[2] for a in anchors])
    obs = np.array([a[3] for a in anchors])
    R = Ds / 2.0  # (3,)

    results = {}

    # 1) 균일: r_c 1D 그리드 (벡터화)
    grid = np.arange(1.0, 60.0, 0.05)[:, None]                 # (G,1)
    pred = np.minimum(1.0, (grid / R) ** 2)                    # (G,3)
    errs = np.sqrt(np.mean((pred - obs) ** 2, axis=1))         # (G,)
    i = int(np.argmin(errs))
    r_best = float(grid[i, 0])
    results["uniform"] = {
        "params": {"r_c": r_best}, "rmse": float(errs[i]),
        "pred": p_uniform(Ds, r_best),
    }

    # 2) 가우시안: 결합상수 c 1D 그리드
    cgrid = np.arange(50.0, 30000.0, 5.0)[:, None]             # (C,1)
    pred = 1.0 - np.exp(-cgrid / (Ds ** 2))                    # (C,3)
    errs = np.sqrt(np.mean((pred - obs) ** 2, axis=1))
    i = int(np.argmin(errs))
    c_best = float(cgrid[i, 0])
    results["gaussian"] = {
        "params": {"c": c_best,
                   "r_c(k=2)": float(np.sqrt(c_best / 8.0)),
                   "r_c(k=3)": float(np.sqrt(c_best / 18.0))},
        "rmse": float(errs[i]), "pred": p_gaussian(Ds, c_best),
    }

    # 3) 거듭제곱: (r_c, n) 2D 그리드 (벡터화)
    rg = np.arange(1.0, 60.0, 0.25)
    ng = np.arange(0.5, 6.0, 0.05)
    ratio = rg[:, None, None] / R[None, None, :]              # (Rn,1,3)
    pred = np.minimum(1.0, ratio ** ng[None, :, None])        # (Rn,N,3)
    errs = np.sqrt(np.mean((pred - obs) ** 2, axis=2))        # (Rn,N)
    ir, jn = np.unravel_index(int(np.argmin(errs)), errs.shape)
    results["power"] = {
        "params": {"r_c": float(rg[ir]), "n": float(ng[jn])},
        "rmse": float(errs[ir, jn]),
        "pred": p_power(Ds, float(rg[ir]), float(ng[jn])),
    }

    return results, Ds, obs


# ── 출력 ──

def fmt_params(p):
    return ", ".join(f"{k}={v:.3f}" for k, v in p.items())


def main():
    print("=" * 72)
    print("명중률 → 탄착군 → 코어히트율 분포 모델 추정")
    print("=" * 72)

    print("\n[직접 앵커] (동일 타겟 → 코어 반경 r_c 공통)")
    print(f"  {'무기':<5}{'명중%':>7}{'D(px)':>8}{'실측코어히트':>12}")
    for w, acc, D, hr in ANCHORS:
        accs = f"{acc:.1f}" if acc is not None else "(2OL)"
        print(f"  {w:<5}{accs:>7}{D:>8.1f}{hr*100:>11.1f}%")

    results, Ds, obs = fit_models(ANCHORS)

    print("\n[모델 피팅 결과] (RMSE 오름차순 = 데이터와 일관적인 순)")
    ranked = sorted(results.items(), key=lambda kv: kv[1]["rmse"])
    for name, r in ranked:
        print(f"\n  · {name:<9} RMSE={r['rmse']:.4f}  | {fmt_params(r['params'])}")
        print(f"    {'D(px)':>8}{'실측':>9}{'예측':>9}{'잔차pp':>9}")
        for D, o, p in zip(Ds, obs, r["pred"]):
            print(f"    {D:>8.1f}{o*100:>8.1f}%{p*100:>8.1f}%{(p-o)*100:>+8.1f}")

    print("\n[모델 순위]")
    for i, (name, r) in enumerate(ranked, 1):
        print(f"  {i}. {name:<9} RMSE={r['rmse']:.4f}")

    def model_p(name, r, D):
        if name == "uniform":
            return p_uniform(D, r["params"]["r_c"])
        if name == "gaussian":
            return p_gaussian(D, r["params"]["c"])
        return p_power(D, r["params"]["r_c"], r["params"]["n"])

    def dP_dD(name, r, D):
        """수치미분 dP/dD (모델별, 음수)."""
        h = 1e-3
        return (model_p(name, r, D + h) - model_p(name, r, D - h)) / (2 * h)

    # ── 교차검증 1: 글쓴이 '1% 코어히트=1% 댐증' 가정 그대로 (dP/d명중) ──
    print("\n" + "=" * 72)
    print("[교차검증 1] 글쓴이 가정(1%코어히트=1%댐증) 하의 dP/d명중 (피팅 미사용)")
    print("=" * 72)
    print("  dP/d명중 = dP/dD * (-slope_px).  단위: pp / 1%명중")
    header = f"  {'무기':<5}{'명중%':>7}{'D(px)':>8}{'글쓴이eff':>10}"
    for name, _ in ranked:
        header += f"{name[:6]:>9}"
    print(header)
    for w, acc, eff in EFFICIENCY:
        D = diameter(w, acc)
        slope_px = PX_FORMULA[w]["slope"]
        line = f"  {w:<5}{acc:>7.1f}{D:>8.1f}{eff:>9.3f} "
        for name, r in ranked:
            pred_eff = dP_dD(name, r, D) * (-slope_px) * 100.0  # pp/1%명중
            line += f"{pred_eff:>9.3f}"
        print(line)

    # ── 교차검증 2: 데미지 효율 예측 (정정된 코어 공식 반영) ──
    # damage.py(정정): 코어 히트 factor③ += (core_dmg_mult−100)/100 → 200% 무기 C=1.0.
    # 한 발 데미지 ∝ factor③ ∝ (B + P·C).  B=비코어 baseline factor③.
    #   글쓴이 '명중 1%당 댐증 효율'(%) = 100·d(ln damage)/d명중
    #                                    = 100·C·g/(B + P·C),  g=dP/d명중=(-slope)·dP/dD
    # B=1,C=1이면 = 100·g/(1+P).  → '1%코어히트=1%댐증'은 P→0 극한의 선형근사.
    # 모델로 예측한 효율을 글쓴이 값과 비교하고, 비(글쓴이/모델)가 점마다
    # 일정하면 단일 스케일(불완전에임 비율·평타비중 등)로 설명되는 것.
    C, B = CORE_BONUS, BASELINE_B
    print("\n" + "=" * 72)
    print(f"[교차검증 2] 데미지 효율 예측  (정정 공식: C={C:.1f}, baseline B={B:.1f})")
    print("=" * 72)
    print("  예측효율(%/1%명중) = 100·C·g/(B+P·C),  g=dP/d명중.  비 = 글쓴이/모델")
    for name, r in ranked:
        print(f"\n  · 모델={name}")
        print(f"    {'무기':<5}{'명중%':>7}{'D(px)':>8}{'P':>7}"
              f"{'글쓴이':>9}{'예측':>9}{'비':>8}")
        for w, acc, eff in EFFICIENCY:
            D = diameter(w, acc)
            slope_px = PX_FORMULA[w]["slope"]
            g = (-slope_px) * dP_dD(name, r, D)          # dP/d명중 (양수)
            P = float(model_p(name, r, D))
            pred = 100.0 * C * g / (B + P * C)
            ratio = eff / pred if pred > 1e-9 else float("inf")
            rstr = f"{ratio:>8.2f}" if np.isfinite(ratio) else f"{'∞':>8}"
            print(f"    {w:<5}{acc:>7.1f}{D:>8.1f}{P:>7.2f}"
                  f"{eff:>9.3f}{pred:>9.3f}{rstr}")
    print("\n  (비가 SG 내에서 일정하면 형태는 정합·크기만 단일 상수로 어긋남;")
    print("   D가 작아 P=1로 포화한 점은 모델 기울기 0 → 예측 0, 비=∞ = 단일 r_c와 모순)")

    # ── 민감도: 코어히트율 ±perturbation에 따른 모델 순위 안정성 ──
    print("\n" + "=" * 72)
    print("[민감도] 실측 코어히트율 ±perturbation 시 1위 모델·균일r_c 변화")
    print("=" * 72)
    rng = np.random.default_rng(0)
    base_hr = np.array([a[3] for a in ANCHORS])
    counts = {k: 0 for k in results}
    rcs = []
    N = 2000
    for _ in range(N):
        pert = base_hr + rng.uniform(-0.075, 0.075, size=base_hr.shape)
        pert = np.clip(pert, 0.01, 0.999)
        anc = [(ANCHORS[i][0], ANCHORS[i][1], ANCHORS[i][2], pert[i])
               for i in range(len(ANCHORS))]
        res, _, _ = fit_models(anc)
        winner = min(res.items(), key=lambda kv: kv[1]["rmse"])[0]
        counts[winner] += 1
        rcs.append(res["uniform"]["params"]["r_c"])
    print(f"  실측 ±7.5pp 균등노이즈 {N}회 재피팅:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<9} 1위 비율 {v/N*100:5.1f}%")
    print(f"  균일 모델 r_c: 평균 {np.mean(rcs):.1f}px, "
          f"5~95%ile {np.percentile(rcs,5):.1f}~{np.percentile(rcs,95):.1f}px")

    # ── 한계 명시 ──
    print("\n" + "=" * 72)
    print("[한계]")
    print("=" * 72)
    print("  · 직접 앵커 3점·눈대중 라운드값 → 모델 판별은 잠정적.")
    print("  · 가우시안은 레벨 피팅에서 r_c와 k가 비식별(결합상수 c만 추정).")
    print("  · 정정 공식(C=1.0, 코어=2배)으로 데미지 효율을 예측하면:")
    print("    SG는 모델 형태가 글쓴이 효율과 맞고 비(글쓴이/모델)가 거의 일정 →")
    print("    단일 스케일(불완전에임 비율·평타비중 등)만 곱하면 설명됨.")
    print("    그러나 AR/SMG의 작은 D(≈50px)는 단일 r_c≈26px상 코어 완전포함(P=1)이라")
    print("    모델 예측 0인데 글쓴이는 효율>0 → 무기별 유효코어/에임 차이 내포 추정.")
    print("    → 효율은 참고용, 피팅 제약 아님(직접 앵커 3점만 신뢰).")
    print("  · 결론은 '현재 데이터가 어느 모델을 더 지지하는가'이지 게임 구현의 증명 아님.")


if __name__ == "__main__":
    main()
