"""바뀐 golden snapshot의 딜량 변동을 표로 낸다. CI가 PR 요약에 붙인다.

    python .github/scripts/baseline_delta.py <기준_ref>

**통과/실패를 내지 않는다.** 그건 `context.snapshot`의 일이고, 여기서 하는 일은
"무엇이 얼마나 움직였나"를 사람이 읽을 형태로 만드는 것뿐이다.

이 표가 필요한 이유: 계산기를 고치면 baseline도 같이 고쳐 PR에 넣으므로 회귀는
**초록불로 지나간다.** 그래서 초록/빨강만 봐서는 아무것도 모른다. 리뷰가 실제로
봐야 하는 것은 *어느 스쿼드가 얼마나 움직였는가*다 — 한 캐릭터를 고쳤는데 29개
스쿼드가 전부 흔들렸다면 의도한 수정이 아니다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 윈도우 콘솔(cp949)에서도 표가 깨지지 않게. CI(리눅스)는 원래 UTF-8이지만
# 로컬에서 손으로 돌려볼 때가 있어 여기서 못박는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASELINE = "context/baseline"
NUL = chr(0)


def _changed(base: str) -> list[str]:
    """`base` 이후 손댄 baseline 경로들.

    baseline 파일명이 한글이라 `--name-only`를 그냥 쓰면 git이 8진 이스케이프로
    감싸 내보내고, 경로가 깨져 뒤따르는 `git show`가 전부 실패한다.
    `core.quotepath=false` + NUL 구분이라야 원문 그대로 온다.
    """
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false",
         "diff", "--name-only", "-z", base, "--", BASELINE],
        capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [p for p in out.split(NUL) if p]


def _total(ref: str, path: str) -> float | None:
    """`ref` 시점 스냅샷의 스쿼드 총딜. 그 시점에 없던 파일이면 None."""
    try:
        blob = subprocess.run(["git", "show", f"{ref}:{path}"],
                              capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None
    return json.loads(blob.decode("utf-8")).get("L1_numbers", {}).get("squad_total")


def main(base: str) -> int:
    changed = _changed(base)
    if not changed:
        print("## 스냅샷 변동\n\n계산 결과가 바뀐 스쿼드 없음."
              " (baseline 파일에 손댄 것이 없다)")
        return 0

    print(f"## 스냅샷 변동 — {len(changed)}개 스쿼드\n")
    print("| 스쿼드 | 이전 총딜 | 이후 총딜 | 변화 |")
    print("|---|---:|---:|---:|")
    for path in changed:
        name = Path(path).stem
        old, new = _total(base, path), _total("HEAD", path)
        if old is None:
            print(f"| {name} | — | {new:,.0f} | 신규 |")
        elif new is None:
            print(f"| {name} | {old:,.0f} | — | 삭제 |")
        else:
            pct = (new - old) / old * 100 if old else 0.0
            print(f"| {name} | {old:,.0f} | {new:,.0f} | {pct:+.3f}% |")

    total = len(list(Path(BASELINE).glob("*.json")))
    print(f"\n전체 {total}개 중 **{len(changed)}개**가 움직였다."
          " 고친 범위와 맞지 않으면 의도하지 않은 파급이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/master"))
