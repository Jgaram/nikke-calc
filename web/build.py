"""웹앱 번들 빌더.

`web/src/`의 정적 파일과 계산기 일체를 `web/dist/`로 모은다.
브라우저는 `dist/`만 보면 되고, 계산기 코드·데이터는 **복사본을 만들지 않는다** —
매 빌드마다 정본에서 다시 압축한다 (webapp-roadmap.md §4).

    python web/build.py
    python web/build.py --serve 8765     # 빌드 후 로컬 서버까지

산출물:
    dist/repo.zip          calculator/ + context/spec.py + scraper/profile_convert.py + data/
                           (Pyodide가 푼다)
    dist/roster.json       캐릭터 메타 (context/roster.py의 collect() 재사용)
    dist/profile_maps.json 육성 프로필 변환용 조회표 (CDN + 저장소 파일)
    dist/image/            초상화·아이콘 (변경분만 복사)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
DIST = ROOT / "web" / "dist"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))
from context.roster import collect  # noqa: E402  (경로 주입 후에만 import 가능)
import profile_fetch  # noqa: E402  (조회표 로더 재사용. main()은 부르지 않는다)

# Pyodide 가상 FS에 풀릴 파일들. context/spec.py는 `_ROOT`를 부모의 부모로 잡으므로
# 압축 안에서도 저장소와 같은 배치를 유지해야 data/를 찾는다.
BUNDLE_GLOBS = ("calculator/*.py", "data/*.json", "data/base_stat_tables/*.json")
# profile_convert.py는 **변환의 정본**이다. 브라우저가 JS로 다시 구현하지 않고 이걸 그대로
# import한다 — 수집 경로가 셋(CLI·북마클릿·서버)이라 사본을 만들면 반드시 어긋난다.
BUNDLE_FILES = ("context/spec.py", "scraper/profile_convert.py",
                "scraper/profile_csv.py")


def check_worker_py() -> None:
    """`worker.js`의 파이썬 블록에 **백틱이 없는지** 확인한다.

    그 파이썬은 JS 템플릿 리터럴(`const PY = `...``) 안에 들어 있다.
    독스트링에 백틱을 하나 쓰면 리터럴이 거기서 끊겨 `Unexpected identifier`로
    워커가 통째로 죽는다 — 파이썬만 보면 멀쩡해 보이고, 브라우저 계산을 켜야만
    드러나서 배포까지 나간 적이 있다. 빌드에서 잡는다.
    """
    src = (SRC / "worker.js").read_text(encoding="utf-8")
    start = src.index("const PY = ") + len("const PY = ")
    assert src[start] == chr(96), "worker.js의 PY 블록 모양이 바뀌었다"
    end = src.index(chr(96), start + 1)
    block = src[start + 1:end]
    if chr(96) in block:
        line = block[:block.index(chr(96))].count(chr(10)) + 1
        raise SystemExit(
            f"[!] worker.js 파이썬 블록 {line}번째 줄에 백틱이 있다 — 템플릿 리터럴이 "
            f"끊겨 워커가 죽는다. 백틱 대신 따옴표를 쓰거나 그냥 빼라.")


def build_zip() -> tuple[int, int]:
    """계산기 번들을 만든다. 반환: (파일 수, 압축 바이트)."""
    out = DIST / "repo.zip"
    paths: list[Path] = [ROOT / f for f in BUNDLE_FILES]
    for g in BUNDLE_GLOBS:
        paths.extend(sorted(ROOT.glob(g)))

    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit("번들 대상 없음: " + ", ".join(p.name for p in missing))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            z.write(p, p.relative_to(ROOT).as_posix())
    return len(paths), out.stat().st_size


def _rare_map() -> dict:
    """`nikke_scraped.json` → {캐릭명: R/SR/SSR}.

    인게임 카드는 등급을 **텍스트가 아니라 색**으로 알린다 — SSR 금색 · SR 보라 · R 파랑이
    카드 테두리와 하단 띠에 들어간다. 그 색을 칠하려면 등급이 필요한데 `collect()`도
    `parsed_nikke.json`도 담지 않으므로 원시 수집본에서 가져온다.
    """
    d = json.loads((ROOT / "scraper/nikke_scraped.json").read_text(encoding="utf-8"))
    return {k: v.get("레어도") for k, v in d.items()
            if isinstance(v, dict) and v.get("레어도")}


def build_roster() -> int:
    """캐릭터 메타를 JSON으로. 파싱 여부까지 담아 UI가 선택 가능 여부를 판단한다."""
    global _FULL, _RES_BY_NAME, _COSTUMES
    _FULL = _full_map()
    _COSTUMES = _costume_map()
    _maps = json.loads((DIST / "profile_maps.json").read_text(encoding="utf-8"))         if (DIST / "profile_maps.json").exists() else {}
    _RES_BY_NAME = {v: int(k) for k, v in (_maps.get("res_name") or {}).items()}
    done, todo = collect()
    rare = _rare_map()
    for r in done + todo:
        r["rare"] = rare.get(r["name"])
    chars = [_row(r, True) for r in done] + [_row(r, False) for r in todo]

    (DIST / "roster.json").write_text(
        json.dumps({"generated": date.today().isoformat(), "chars": chars,
                    **_top_atk_data()},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(chars)


def _top_atk_data() -> dict:
    """브라우저가 **시뮬 없이** 최공 대상을 가릴 수 있게, 순위를 바꾸는 값만 굽는다.

    전체 대상 버프는 모두에게 똑같이 들어가 **순위를 바꾸지 않으므로** 담지 않는다.
    남는 것은 셋뿐이다:

      `top_atk_casters`  — 최공 대상 버프를 가진 니케 (진단을 띄울지 판단)
      `top_atk_buffs`    — 그 버프의 공격력 값·대상 수 (미란다 파워 업! 등)
      `self_burst_atk`   — 자기 버스트로 자기 공격력을 올리는 값. 그 사이클의 3버만
                           받으므로 **누가 3버냐에 따라 순위가 뒤집힌다** — 이 진단의 핵심이다

    조건부(중첩·체력·명중 횟수)와 «시전자 기준»(`atk_caster_based_pct`) 버프는 넣지
    않는다. 값이 상황에 따라 달라 브라우저에서 정확히 못 센다 — 화면이 «즉시값은
    근사»라고 밝히고, 정확한 값은 계산 결과의 진단이 답한다.
    """
    skills = json.loads((ROOT / "data" / "parsed_skills.json").read_text(encoding="utf-8"))
    casters, buffs, self_atk, dealer_flat = [], {}, {}, {}
    self_fb, low_buffs, low_casters = {}, {}, []
    for name, effects in skills.items():
        for e in effects:
            t = str(e.get("target") or "")
            trg = e.get("trigger") or {}
            cond = trg.get("condition") or []
            timing = trg.get("timing") or []
            v10 = ((e.get("values") or {}).get("10"))
            if t.startswith(("allies_top_atk:", "allies_top_atk_excl:")):
                if name not in casters:
                    casters.append(name)
                if e.get("stat") == "atk_pct" and v10 and not cond:
                    # 애장품 판본이 대상 수만 다르게 여러 벌 들어온다 — 넓은 쪽을 남긴다
                    n = int(t.rsplit(":", 1)[-1] or 1)
                    cur = buffs.get(name)
                    if cur is None or n > cur["slots"]:
                        buffs[name] = {"buff": e.get("name") or "", "pct": float(v10),
                                       "slots": n, "excl": "_excl" in t,
                                       "timing": timing[0] if timing else ""}
            # ── 자기 공격력 자버프 두 갈래 ──
            # ① **자기 버스트로** 켜지는 것 → 그 사이클의 3버일 때만 (미란다 판정의 핵심)
            #    「버스트 3단계 진입 시」(아인 +70.12%)도 여기다 — 스킬3만 보면 놓친다.
            if (e.get("stat") == "atk_pct" and t == "self" and not cond and v10
                    and any(x in timing for x in ("burst_cast", "burst_enter:3"))):
                self_atk[name] = max(self_atk.get(name, 0.0), float(v10))
            # ② **풀버스트가 열리면** 켜지는 것 → 누가 3버든 매 사이클 (리버렐리오 +160%)
            if (e.get("stat") == "atk_pct" and t == "self" and not cond and v10
                    and "full_burst_start" in timing):
                self_fb[name] = max(self_fb.get(name, 0.0), float(v10))
            # ── 「최종 공격력이 가장 «낮은»」 타게팅 ──
            # 최공의 반대다. 리버렐리오가 3단계 버스트 아군 중 최저 공격력 1기에게
            # 차지 속도를 준다 — 받으려면 **공격력이 더 낮아야** 하므로 최적화 방향이 뒤집힌다.
            if t.startswith("allies_lowest_atk_burst3:"):
                if name not in low_casters:
                    low_casters.append(name)
                if v10 and not cond:
                    n = int(t.rsplit(":", 1)[-1] or 1)
                    cur = low_buffs.get(name)
                    if cur is None or n > cur["slots"]:
                        low_buffs[name] = {"buff": e.get("name") or "",
                                           "stat": e.get("stat") or "", "pct": float(v10),
                                           "slots": n,
                                           "timing": timing[0] if timing else ""}
            # 「버스트를 쓴 아군」에게 **시전자 공격력 비례**로 얹는 것 (에이다 은밀한 지원,
            # 크라운 원 포 올). 그 사이클의 3버만 크게 이득이라 순위를 뒤집는다 —
            # 실측으로 이것까지 더하면 시뮬 값과 1 이내로 맞는다.
            if (e.get("stat") == "atk_caster_based_pct" and not cond and v10
                    and t in ("allies_burst_casted_burst3", "all_allies_burst_casted")):
                dealer_flat[name] = max(dealer_flat.get(name, 0.0), float(v10))
    return {"top_atk_casters": sorted(casters),
            "top_atk_buffs": buffs,
            "self_burst_atk": self_atk,
            "self_fb_atk": self_fb,
            "dealer_atk_flat": dealer_flat,
            "low_atk_casters": sorted(low_casters),
            "low_atk_buffs": low_buffs,
            **_adjacent_data(skills),
            **_cdr_data(skills)}


def _cdr_data(skills: dict) -> dict:
    """**아군 전체**에게 버스트 쿨타임 감소를 주는 니케 — 「덱에 쿨감이 없다」
    경고에 쓴다. 자기 자신만 줄어드는 것(`target: self`)은 스쿼드 전체의 버스트
    순환에는 도움이 안 되므로 뺀다."""
    casters = set()
    for name, effects in skills.items():
        for e in effects:
            if e.get("stat") in ("burst_cooldown_reduce", "burst_cooldown") and e.get("target") == "all_allies":
                casters.add(name)
    return {"cdr_casters": sorted(casters)}


def _adjacent_data(skills: dict) -> dict:
    """루주·플로라처럼 «양옆 아군»에게 거는 버프 — 최공 대상과 달리 스탯 비교가
    필요 없다. 덱 배치 순서만 보면 누가 받는지 **항상 100% 확정**된다.

    그래서 별도 진단 패널이 아니라 **슬롯 카드에 직접 표시**한다. 실측으로 확인할
    필요가 없는(계산이 아니라 규칙인) 값이라, 예측/계산 결과 구분도 없다.
    """
    casters, buffs = [], {}
    for name, effects in skills.items():
        for e in effects:
            if not str(e.get("target") or "").startswith("allies_adjacent:"):
                continue
            if name not in casters:
                casters.append(name)
            nm = e.get("name") or ""
            lst = buffs.setdefault(name, [])
            if nm and nm not in lst:
                lst.append(nm)
    return {"adjacent_casters": sorted(casters), "adjacent_buffs": buffs}



def build_profile_maps() -> int:
    """육성 프로필 변환용 조회표를 하나로 굽는다. 반환: 표 개수.

    `profile_convert.build_profile()`이 받는 `maps`와 **같은 것**이다. CLI는 실행할 때마다
    `profile_fetch._load_maps()`로 만들고, 브라우저는 이 파일을 받아 쓴다 — 만드는 시점만
    다르고 내용은 같다.

    굽는 이유: 소장품 표는 CDN을 1+N회(등급 목록 + 아이템마다) 때린다. 방문자 브라우저가
    매번 그걸 돌 일이 아니다. 빌드가 정본에서 다시 만들므로 사본이 낡을 일도 없다
    (`repo.zip`을 매 빌드 다시 압축하는 것과 같은 원칙).

    CDN이 죽으면 **빌드를 실패시킨다.** 낡은 표로 조용히 넘어가면 장비·소장품이 통째로
    빠진 프로필이 나오는데, 그게 계산 결과로는 "육성이 덜 된 계정"과 구분되지 않는다.
    """
    maps = profile_fetch._load_maps()
    # set·tuple은 JSON에 없다. 읽는 쪽(`profile_convert._lookup`)이 문자열 키와
    # 리스트 값을 그대로 받아들이도록 되어 있다.
    maps["fav_chars"] = sorted(maps["fav_chars"])
    # 큐브 효능 — 편집기가 이름만 보여 주면 무슨 큐브인지 알 수 없다.
    # `스킬명`·`template`·레벨별 수치를 그대로 넘겨 UI가 문장을 만든다.
    cube = json.loads((ROOT / "data/base_stat_tables/cube.json").read_text(encoding="utf-8"))
    maps["cube_stats"] = cube["_stats"]
    # 장비 플랫 스탯 — 인게임 «장비 능력치»를 부위별로 보여 주는 데 쓴다.
    # repo.zip에 이미 들어가는 게임 데이터라 새로 드러나는 것은 없다.
    eq = json.loads((ROOT / "data/base_stat_tables/equipment_stats.json").read_text(encoding="utf-8"))
    maps["equip_stats"] = {k: v for k, v in eq.items() if not k.startswith("_")}
    # 전투력 계산기용 인게임 아이콘 조회표 (`scraper/cdn_ui_icons.py`). 없으면 UI가
    # 글자 타일로 물러난다 — 빌드를 세우지는 않는다.
    ui = ROOT / "data" / "ui_icons.json"
    if ui.exists():
        maps["ui_icons"] = json.loads(ui.read_text(encoding="utf-8"))
    maps["cube_names_by_name"] = {v: k for k, v in (maps.get("cube_names") or {}).items()}
    maps["cube_info"] = {
        k: {"skill": v.get("스킬명"), "template": v.get("template"),
            "values": v.get("values"), "stat": v.get("stat")}
        for k, v in cube.items() if not k.startswith("_") and isinstance(v, dict)
    }
    # 소장품·애장품 아이콘 조회표 (`python scraper/cdn_icons.py`가 만든다).
    # 없으면 카드가 색 다이아로 물러날 뿐이라 빌드를 세우지는 않는다.
    fav_icons = ROOT / "data/favorite_icons.json"
    if fav_icons.exists():
        fi = json.loads(fav_icons.read_text(encoding="utf-8"))
        maps["fav_icons"] = {k: fi[k] for k in ("by_char", "by_kind", "grade") if k in fi}
    else:
        print("  [WARN] data/favorite_icons.json 없음 — 카드가 소장품 그림 대신 색으로만 표기한다")

    (DIST / "profile_maps.json").write_text(
        json.dumps(maps, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(maps)


# 버스트 주기 카탈로그 — UI가 «이 캐릭터는 주기를 고를 수 있다»를 알아야 선택을 띄운다.
# 값(every:3 · 사이클 목록)까지 보낸다 — 겹침 검사(두 명이 같은 사이클을 지정)를
# 클라이언트가 하려면 이름만으로는 안 된다. 정본은 여전히 char_defaults.json이다.
_CHAR_DEFAULTS = json.loads((ROOT / "data" / "char_defaults.json").read_text(encoding="utf-8"))

# 스킬 설명 — 인게임처럼 «설명과 함께 레벨»을 보여 주려면 원문 템플릿과 레벨별 수치가
# 둘 다 필요하다. 정본은 수집 원본(`scraper/nikke_scraped.json`)이고 여기서 굽기만 한다.
_SCRAPED = json.loads((ROOT / "scraper" / "nikke_scraped.json").read_text(encoding="utf-8"))


def _skills(name: str) -> list | None:
    """[{name, tpl, vals}] × 3 (스킬1·스킬2·버스트 순). 없으면 None."""
    sk = (_SCRAPED.get(name) or {}).get("스킬") or {}
    out = []
    for nm, s in list(sk.items())[:3]:
        out.append({"name": nm, "tpl": s.get("template") or "",
                    "vals": s.get("values") or {}})
    return out or None


def _josa(word: str, with_final: str, without: str) -> str:
    """받침에 맞는 조사. «전담»이 / «3의 배수»가 를 가른다."""
    ch = word.rstrip("»)")[-1:]
    if ch and "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28:
        return with_final
    return without


def _pattern_note(name: str) -> str | None:
    """이 캐릭터의 «자동»이 실제로 무엇을 하는지 한 문장으로.

    컨트롤 패널의 «기본은 전부 자동입니다» 문구만 보면, 마스트를 크라운과 짜 놓고도
    주기를 손으로 켜야 하는 줄 안다. 조건은 layer(`_burst_pattern_when`)에 있으므로
    빌드가 사람 말로 구워 로스터에 싣는다 — 문구가 데이터에서 나와야 카탈로그에
    캐릭터를 더할 때 안내도 같이 따라온다.
    """
    layer = _CHAR_DEFAULTS.get(name) or {}
    default = layer.get("burst_pattern")
    if not default:
        return None
    cond = layer.get("_burst_pattern_when") or {}
    parts = []
    for key, val in cond.items():
        if key == "same_stage_cd_max":
            parts.append(f"같은 버스트 단계에 쿨 {val}초 이하인 동료가 있으면")
        elif key == "same_stage_other":
            parts.append("같은 버스트 단계에 다른 동료가 있으면")
        elif key == "with_member":
            who = "·".join(val)
            parts.append(who + _josa(who, "과", "와") + " 함께면")
        elif key == "position":
            parts.append(f"{val}번째 자리면")
    when = " ".join(parts) if parts else "이 캐릭터는"
    short = name.split(" : ")[0]
    # 자리 규칙이 있으면 «걸린다/안 걸린다»와 «어느 주기냐»를 갈라 말한다 — 붙여 쓰면
    # "1번 자리여야 걸린다"로 읽힌다 (실제로는 자리와 무관하게 걸리고, 자리는 어느
    # 시간표를 쓰는지만 바꾼다. 두 주기는 서로 다른 시간표다).
    rules = [r for r in (layer.get("_burst_pattern_rules") or [])
             if "position" in (r.get("when") or {})]
    if rules:
        swaps = " · ".join(f"{r['when']['position']}번째 자리면 «{r['use']}»로 교체"
                           for r in rules)
        return (f"{short}{_josa(short, '은', '는')} {when} 자리와 무관하게 주기가 자동으로 "
                f"걸립니다 — 기본은 «{default}», {swaps} (서로 다른 시간표입니다).")
    return (f"{short}{_josa(short, '은', '는')} {when} "
            f"«{default}»{_josa(default, '이', '가')} 자동으로 걸립니다.")


_CTRL_LABEL = {"cover": "버스트 엄폐컨", "hold": "홀드"}


def _forced_control(name: str) -> list[dict] | None:
    """동료 조건이 맞으면 레이어가 강제로 거는 컨트롤 (`_control_rules`의 `with_member`).

    체크박스로 꺼도 계산에는 반영되지 않는다 — 끌 이유가 없는 컨트롤이라 레이어가
    항상 이기게 만들어져 있다(CONTROL.md). UI가 그 사실을 숨기면 "체크 해제했는데
    왜 안 꺼지지"가 되므로, 조건이 맞는 조합에서는 패널이 미리 켜진 채로 보여 주고
    문구로 이유를 댄다. 지금은 `with_member` 조건만 굽는다 — 다른 조건(자리·쿨 등)은
    컨트롤에 쓰인 적이 없다.
    """
    layer = _CHAR_DEFAULTS.get(name) or {}
    out = []
    for rule in (layer.get("_control_rules") or []):
        members = (rule.get("when") or {}).get("with_member")
        if not members:
            continue
        who = "·".join(members)
        for key in (rule.get("control") or {}):
            label = _CTRL_LABEL.get(key)
            if not label:
                continue
            out.append({
                "with": members, "key": key,
                "note": f"{who}{_josa(who, '과', '와')} 함께면 "
                        f"{label}{_josa(label, '이', '가')} 자동으로 걸립니다 — 끌 수 없습니다.",
            })
    return out or None


# 전신 일러(`scraper/cdn_full.py`)가 있는 리소스 목록. 이름 → 파일명으로 굽는다 —
# 전투력 계산기가 인게임처럼 큰 그림을 띄우는 데 쓰고, 없으면 초상화로 물러난다.
def _full_map() -> dict:
    d = ROOT / "image" / "full"
    if not d.is_dir():
        return {}
    have = {p.stem for p in d.glob("c*.webp")}
    maps = json.loads((DIST / "profile_maps.json").read_text(encoding="utf-8"))         if (DIST / "profile_maps.json").exists() else {}
    # 알파 경계 — 그림이 실제로 있는 범위. 원본 2048² 안에서 캐릭터가 앉은 자리가
    # 제각각이라(아래 여백 0~645px) 화면이 이걸로 맞춰야 발도 머리도 안 잘린다.
    bpath = ROOT / "data" / "full_bbox.json"
    bbox = (json.loads(bpath.read_text(encoding="utf-8")).get("bbox") or {})         if bpath.exists() else {}
    out = {}
    for rid, name in (maps.get("res_name") or {}).items():
        key = f"c{int(rid):03d}"
        if key in have:
            out[name] = (f"{key}.webp", bbox.get(key))
    return out


_FULL: dict = {}


_UI_ICONS = json.loads((ROOT / "data" / "ui_icons.json").read_text(encoding="utf-8"))     if (ROOT / "data" / "ui_icons.json").exists() else {}
_RES_BY_NAME: dict = {}


def _costume_map() -> dict:
    """`data/costume_index.json` → {리소스id(str): {코스튬id(str): {cos,name,grade}}}.

    표가 없어도 빌드를 끊지 않는다 — 스킨은 외형뿐이라 없으면 기본 코스튬으로
    그려질 뿐, 계산도 화면 구조도 그대로다. (`python scraper/cdn_costume.py`로 만든다.)
    """
    f = ROOT / "data" / "costume_index.json"
    if not f.exists():
        return {}
    return (json.loads(f.read_text(encoding="utf-8")) or {}).get("costumes") or {}


def _costumes_for(rid: int | None) -> dict:
    """이 캐릭터가 가진 코스튬 → 그림 경로. **그림이 실제로 있는 것만** 담는다.

    프로필이 주는 `_costume`(=코스튬 id)을 키로 바로 찾을 수 있게 id를 키로 둔다.
    경로는 dist 기준(`image/`가 루트)이라 초상화 `img`와 같은 규칙이다.
    """
    if not rid:
        return {}
    out = {}
    for cid, c in (_COSTUMES.get(str(rid)) or {}).items():
        cos = c.get("cos")
        if not cos:
            continue
        mi = ROOT / "image" / "costume" / "mi" / f"mi_c{rid:03d}_{cos:02d}.webp"
        if not mi.exists():
            continue                      # 초상화가 없으면 바꿔 끼울 그림이 없다
        face = ROOT / "image" / "face" / f"si_c{rid:03d}_{cos:02d}.webp"
        full = ROOT / "image" / "costume" / "full" / f"c{rid:03d}_{cos:02d}.webp"
        out[cid] = {
            "name": c.get("name") or "",
            "img": f"costume/mi/mi_c{rid:03d}_{cos:02d}.webp",
            **({"face": f"face/si_c{rid:03d}_{cos:02d}.webp"} if face.exists() else {}),
            **({"full": f"costume/full/c{rid:03d}_{cos:02d}.webp",
                # 전신 알파 경계 — 코스튬마다 캐릭터가 앉은 자리가 달라서 기본
                # 코스튬의 경계(`rec.fbb`)를 그대로 쓰면 발이 잘린다.
                **({"fbb": c["fbb"]} if c.get("fbb") else {})} if full.exists() else {}),
        }
    return out


_COSTUMES: dict = {}
_NIKKE_META = json.loads((ROOT / "data" / "parsed_nikke.json").read_text(encoding="utf-8"))


def _row(rec: dict, parsed: bool) -> dict:
    img = rec["img"]
    pats = dict(((_CHAR_DEFAULTS.get(rec["name"]) or {}).get("_burst_patterns") or {}))
    pnote = _pattern_note(rec["name"])
    fctrl = _forced_control(rec["name"])
    skills = _skills(rec["name"])
    rid = _RES_BY_NAME.get(rec["name"])
    sk_icons = (_UI_ICONS.get("skill") or {}).get(str(rid)) if rid else None
    if skills and sk_icons:
        for i, sk in enumerate(skills):
            if i < len(sk_icons):
                sk["icon"] = sk_icons[i]
    # 얼굴 카드(68×68 정사각, scraper/cdn_face.py 수집) — 초상화(256×512)를 잘라
    # 만드는 대신 인게임이 스쿼드 목록에 실제로 쓰는 정사각 그림을 그대로 쓴다.
    face_file = ROOT / "image" / "face" / f"si_c{rid:03d}_00.webp" if rid else None
    face = f"face/si_c{rid:03d}_00.webp" if face_file and face_file.exists() else None
    cos_map = _costumes_for(rid)
    return {
        **({"patterns": pats} if pats else {}),
        **({"pattern_note": pnote} if pnote else {}),
        **({"forced_control": fctrl} if fctrl else {}),
        **({"full": _FULL[rec["name"]][0]} if rec["name"] in _FULL else {}),
        **({"fbb": _FULL[rec["name"]][1]}
           if _FULL.get(rec["name"], (None, None))[1] else {}),
        **({"skills": skills} if skills else {}),
        "name": rec["name"],
        "burst": rec["burst"],
        # 버스트 쿨타임. 웹이 «3버 순번이 실제로 오는가»를 판단하는 데 쓴다 —
        # 쿨 40초 · 사이클 20초면 3버 둘로 매 사이클이 덮여서 셋째는 영영 안 나간다.
        "cd": _NIKKE_META.get(rec["name"], {}).get("burst_cooldown"),
        "element": rec["element"],
        "cls": rec["cls"],
        "corp": rec["corp"],
        "weapon": rec["weapon"],
        "rare": rec.get("rare"),
        # portrait()는 "image/<파일>"을 준다. dist에서는 image/가 루트라 접두사를 뗀다.
        "img": img.split("/", 1)[1] if img else None,
        **({"face": face} if face else {}),
        # 코스튬(스킨) — 프로필의 `_costume`(장착 중인 코스튬 id)로 찾아 초상화·얼굴·
        # 전신을 갈아 끼운다. 외형뿐이라 계산에는 아무 영향이 없다.
        **({"costumes": cos_map} if cos_map else {}),
        "parsed": parsed,
    }


def stamp_assets() -> str:
    """`dist/index.html`의 css·js 링크에 빌드 지문을 붙인다. 반환: 지문.

    캐시 헤더를 신경 써도, **낡은 서버 프로세스**가 옛 헤더를 보내면 브라우저는 옛
    css를 계속 쓴다(그 화면이 "UI가 깨졌다"로 보인다). 파일 이름이 바뀌면 캐시가
    끼어들 자리가 없으므로 링크에 내용 해시를 박는다.
    """
    # **원본에서 다시 읽는다.** copy_tree는 변경분만 복사하므로, 스탬프가 박힌
    # dist/index.html을 그대로 읽으면 다음 빌드에서 옛 지문이 그대로 남는다.
    idx = DIST / "index.html"
    html = (SRC / "index.html").read_text(encoding="utf-8")
    # **원본에서 해시한다.** dist/app.js에는 지문이 주입되므로 그걸 해시하면 값이
    # 매 빌드 바뀌어(자기 자신을 먹는다) 지문이 안정되지 않는다.
    h = hashlib.sha256()
    for name in ("tokens.css", "style.css", "app.js", "squadshot.js", "worker.js"):
        f = SRC / name
        if f.exists():
            h.update(f.read_bytes())
    tag = h.hexdigest()[:8]
    for name in ("tokens.css", "style.css", "app.js", "squadshot.js"):
        html = html.replace(f'"{name}"', f'"{name}?v={tag}"')
    idx.write_text(html, encoding="utf-8")

    # app.js 안의 워커 지문도 같은 값으로 맞춘다 (원본에서 다시 읽는 이유는 위와 같다)
    app = DIST / "app.js"
    src_app = (SRC / "app.js").read_text(encoding="utf-8")
    app.write_text(src_app.replace('const ASSET_V = "dev";',
                                   f'const ASSET_V = "{tag}";', 1), encoding="utf-8")
    return tag


def copy_tree(src: Path, dst: Path, pattern: str = "*") -> int:
    """변경분만 복사한다 (초상화가 5MB대라 매 빌드 전량 복사는 낭비)."""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob(pattern)):
        if not p.is_file():
            continue
        target = dst / p.name
        if target.exists() and target.stat().st_mtime >= p.stat().st_mtime:
            continue
        shutil.copy2(p, target)
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="웹앱 번들 빌드")
    ap.add_argument("--serve", type=int, metavar="PORT", help="빌드 후 로컬 서버 실행")
    args = ap.parse_args()

    DIST.mkdir(parents=True, exist_ok=True)

    n_src = copy_tree(SRC, DIST)
    check_worker_py()
    n_zip, size = build_zip()
    n_maps = build_profile_maps()
    n_char = build_roster()
    tag = stamp_assets()
    n_img = copy_tree(ROOT / "image", DIST / "image", "*.webp")
    # 아이콘은 webp와 png가 섞여 있다 — 색이 들어간 코드 아이콘(icon-code-*.png)이
    # png라서 webp만 복사하면 dist에서 404가 난다.
    n_icon = (copy_tree(ROOT / "image" / "icon", DIST / "image" / "icon", "*.webp")
              + copy_tree(ROOT / "image" / "icon", DIST / "image" / "icon", "*.png"))
    n_full = copy_tree(ROOT / "image" / "full", DIST / "image" / "full", "*.webp")
    n_ui = copy_tree(ROOT / "image" / "ui", DIST / "image" / "ui", "*.webp")
    # 유니온 레이드 랩처 — 회차마다 다섯이 배정되고, 표(UNION_SEASONS)가 파일명을 든다
    n_boss = copy_tree(ROOT / "image" / "boss", DIST / "image" / "boss", "*.webp")
    # 얼굴 카드 전량 — 5덱 배치 모드의 정사각 카드가 쓴다. 예전엔 코스튬 00(기본)만
    # 복사했지만, 이제 프로필이 장착 중인 코스튬을 알려 주므로 스킨 얼굴도 쓴다.
    n_face = copy_tree(ROOT / "image" / "face", DIST / "image" / "face", "*.webp")
    # 스킨 그림 (`scraper/cdn_costume.py`). 전신은 `--full`로 받았을 때만 있다.
    n_cos = sum(copy_tree(ROOT / "image" / "costume" / d,
                          DIST / "image" / "costume" / d, "*.webp")
                for d in ("mi", "full") if (ROOT / "image" / "costume" / d).is_dir())

    print(f"src        {n_src}개 갱신")
    print(f"repo.zip   {n_zip}개 파일 · {size / 1048576:.2f} MB")
    print(f"roster     {n_char}명")
    print(f"assets     캐시 지문 ?v={tag}")
    print(f"maps       조회표 {n_maps}종 "
          f"({(DIST / 'profile_maps.json').stat().st_size / 1024:.0f} KB)")
    print(f"image      초상화 {n_img}개 · 아이콘 {n_icon}개 · 전신 {n_full}개 "
          f"· UI {n_ui}개 · 랩처 {n_boss}개 · 얼굴 {n_face}개 · 스킨 {n_cos}개 갱신")
    print(f"→ {DIST}")

    if args.serve:
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(DIST), **kw)

            def end_headers(self):  # 개발 중 캐시가 남으면 고친 게 안 보인다
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.serve), Handler) as httpd:
            print(f"\nhttp://localhost:{args.serve}  (같은 Wi-Fi의 폰에서도 접속 가능)")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
