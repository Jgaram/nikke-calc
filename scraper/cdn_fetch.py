#!/usr/bin/env python3
"""
cdn_fetch.py
blablalink CDN에서 캐릭터 데이터 직접 수집 → nikke_scraped.json

브라우저를 쓰지 않는다. CDN 경로가 평문 경로에서 결정되므로(`cdn_path.py`)
전체 캐릭터를 수 초 만에 받는다.

Run:
  python scraper/cdn_fetch.py            # 전량 수집 + 이미지 + parse_nikke
  python scraper/cdn_fetch.py --check    # 수집 후 기존 파일과 diff만 출력 (쓰기 없음)
  python scraper/cdn_fetch.py --ids 601,602
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

import cdn_path
from parse_nikke import run as parse_nikke

ROOT = Path(__file__).parent.parent
IMAGE_DIR = ROOT / "image"
JSON_PATH = Path(__file__).parent / "nikke_scraped.json"

LOCALE = "ko"
CONCURRENCY = 16

ID_MAP_PATH = "/character/character_id_map.json"
ROLEDATA_PATH = "/roledata/{rid}-v2-{locale}.json"
# 256x512 썸네일. 기존 image/ 규격과 동일하다.
PORTRAIT_PATH = "/character/mi/mi_c{rid:03d}_00_s.webp"

# 애장품(favorite item). SSR 17개만 스킬을 바꾼다.
FAVORITE_RARE_MAP_PATH = "/equip/favorite_rare_map.json"
FAVORITE_PATH = "/equip/{locale}/favorite_{fid}.json"
# icon_resource_id "si_favoriteitem_c072_00" → resource_id 72
ICON_RID_RE = re.compile(r"c(\d+)_")

ELEMENT_MAP = {
    "Fire": "작열", "Water": "수냉", "Wind": "풍압",
    "Electronic": "전격", "Iron": "철갑",
}
CLASS_MAP = {"Attacker": "화력형", "Supporter": "지원형", "Defender": "방어형"}
CORP_MAP = {
    "ELYSION": "엘리시온", "MISSILIS": "미실리스", "TETRA": "테트라",
    "PILGRIM": "필그림", "ABNORMAL": "어브노말",
}
BURST_MAP = {"Step1": "1", "Step2": "2", "Step3": "3", "AllStep": "A"}
RARITY_RANK = {"SSR": 3, "SR": 2, "R": 1}

# 사이트가 <span>으로 렌더링하는 마크업. innerText 기준으로는 사라진다.
# 설명문에 literal로 등장하는 `<Step 1 에서 사용 시 : ...>` 같은 텍스트는 건드리면 안 되므로
# 알려진 태그만 정확히 지운다.
TAG_RE = re.compile(r"</?(?:color|word_group)(?:=[^>]*)?>")


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text).replace("\xa0", " ")


def safe_filename(name: str) -> str:
    """캐릭터명 → 이미지 파일명(확장자 제외).

    Windows에서 금지된 문자를 기존 image/ 파일 규칙대로 '_'로 치환한다.
    예: 'D : 킬러 와이프' → 'D _ 킬러 와이프'
    """
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name


def js_number(value) -> str:
    """프론트엔드의 `String(Number(v)/100)`과 같은 문자열을 만든다."""
    x = value / 100
    return str(int(x)) if x == int(x) else f"{x:.10g}"


def build_template(levels: dict[str, str]) -> dict:
    """레벨별 텍스트에서 template + values 구조 생성.

    레벨 간 변하는 숫자만 {0}, {1}... 로 바뀌고 고정 숫자는 리터럴로 남는다.
    """
    texts = [levels[str(i)] for i in range(1, len(levels) + 1) if str(i) in levels]
    if not texts:
        return {"template": "", "values": {}}

    number_pattern = re.compile(r'\d+\.?\d*')
    all_numbers = [number_pattern.findall(t) for t in texts]

    if len(all_numbers) > 1:
        changing = [
            i for i in range(len(all_numbers[0]))
            if any(all_numbers[j][i] != all_numbers[0][i] for j in range(1, len(all_numbers))
                   if i < len(all_numbers[j]))
        ]
    else:
        changing = list(range(len(all_numbers[0]))) if all_numbers else []

    base_nums = all_numbers[0]
    changing_set = set(changing)
    ph_idx = 0
    result_parts = []
    search_start = 0
    template_src = texts[0]
    for num_pos, num in enumerate(base_nums):
        m = number_pattern.search(template_src, search_start)
        if m is None:
            break
        if num_pos in changing_set:
            result_parts.append(template_src[search_start:m.start()])
            result_parts.append(f"{{{ph_idx}}}")
            ph_idx += 1
        else:
            result_parts.append(template_src[search_start:m.end()])
        search_start = m.end()
    result_parts.append(template_src[search_start:])
    template = "".join(result_parts)

    values = {}
    for lv, text in levels.items():
        nums = number_pattern.findall(text)
        values[lv] = [nums[pos] for pos in changing if pos < len(nums)]

    return {"template": template, "values": values}


def render_skill(detail: dict) -> dict:
    """스킬 상세 → {쿨타임, template, values}.

    description_localkey의 {description_value_NN}에 레벨별 값을 끼워 넣어
    레벨 1~10 텍스트를 만든 뒤 template 구조로 압축한다.
    """
    desc = strip_tags(detail.get("description_localkey") or "")
    value_list = detail.get("description_value_list") or []

    level_values = []
    for entry in value_list:
        level_values.append((entry or {}).get("description_value") or [])
    level_count = max((len(v) for v in level_values), default=0) or 1

    levels = {}
    for lv in range(1, level_count + 1):
        text = desc
        for idx, values in enumerate(level_values, start=1):
            if not values:
                continue
            token = f"{{description_value_{idx:02d}}}"
            if token in text:
                text = text.replace(token, values[min(lv, len(values)) - 1])
        levels[str(lv)] = "\n".join(line.rstrip() for line in text.strip().split("\n"))

    cooltimes = detail.get("skill_cooltime_list") or []
    cooltime = f"{cooltimes[0] / 100:.1f} s" if cooltimes else None

    return {"쿨타임": cooltime, **build_template(levels)}


def render_weapon_skill(shot: dict) -> str:
    text = strip_tags(shot.get("description_localkey") or "")
    for key in re.findall(r"\{(\w+)\}", text):
        if key in shot:
            text = text.replace(f"{{{key}}}", js_number(shot[key]))
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def adapt(role: dict) -> tuple[str, dict]:
    """roledata JSON → nikke_scraped.json 엔트리."""
    name = role["name_localkey"]
    shot = role.get("shot_detail") or {}
    weapon_desc = shot.get("description_localkey") or ""

    element = (role.get("element_details") or [{}])[0].get("element")
    for label, value, table in (
        ("속성", element, ELEMENT_MAP),
        ("클래스", role.get("class"), CLASS_MAP),
        ("기업", role.get("corporation"), CORP_MAP),
        ("버스트 단계", role.get("use_burst_skill"), BURST_MAP),
    ):
        if value not in table:
            print(f"  [WARN] {name}: 미지의 {label} 값 {value!r}", file=sys.stderr)

    skills = {}
    for key in ("skill1_detail", "skill2_detail", "ulti_skill_detail"):
        detail = role.get(key)
        if not detail:
            continue
        skills[detail.get("name_localkey", key)] = render_skill(detail)

    return name, {
        "id": role["resource_id"],
        "레어도": role.get("original_rare", ""),
        "속성": ELEMENT_MAP.get(element, element or ""),
        "클래스": CLASS_MAP.get(role.get("class"), role.get("class") or ""),
        "기업": CORP_MAP.get(role.get("corporation"), role.get("corporation") or ""),
        "버스트 단계": BURST_MAP.get(role.get("use_burst_skill"), ""),
        "무기상세": {
            "무기유형": shot.get("weapon_type", ""),
            "최대 장탄 수": str(shot.get("max_ammo", 0)),
            "재장전 시간": f"{shot.get('reload_time', 0) / 100:.2f}s",
            "조작 타입": "차지형" if "{charge_time}" in weapon_desc else "일반형",
            # 발사 메카닉. CDN 원값 그대로 둔다(rpm·개수). 초당 발수 환산은 parse_nikke.py.
            # 펠릿·총구는 곱해서 1회 발사 히트 수가 된다(예: 츠바이 5펠릿 × 2총구 = 10).
            "연사(rpm)": shot.get("rate_of_fire", 0),
            "연사최대(rpm)": shot.get("end_rate_of_fire", 0),
            "연사증가(rpm/발)": shot.get("rate_of_fire_change_pershot", 0),
            "펠릿": shot.get("shot_count", 1),
            "총구": shot.get("muzzle_count", 1),
            "무기스킬": render_weapon_skill(shot),
        },
        "스킬": skills,
        "post_fire_delay": 0,
        "post_reload_delay": 0,
    }


async def fetch_json(client: httpx.AsyncClient, path: str):
    r = await client.get(cdn_path.url(path))
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))


async def collect(ids: list[int] | None) -> dict:
    async with httpx.AsyncClient(timeout=30, http2=False) as client:
        if ids is None:
            id_map = await fetch_json(client, ID_MAP_PATH)
            ids = sorted({r["resource_id"] for r in id_map})
            print(f"캐릭터 후보 {len(ids)}개")

        limit = asyncio.Semaphore(CONCURRENCY)
        results: dict[str, dict] = {}
        missing: list[int] = []

        async def one(rid: int):
            async with limit:
                try:
                    role = await fetch_json(
                        client, ROLEDATA_PATH.format(rid=rid, locale=LOCALE)
                    )
                except httpx.HTTPStatusError:
                    missing.append(rid)
                    return
                name, entry = adapt(role)
                # 게임 내 동명이인(예: SSR 사쿠라 vs 신규 SR 사쿠라)이 존재한다.
                # 이름을 키로 쓰므로 높은 등급을 우선 보존하고 나머지는 버린다.
                prev = results.get(name)
                if prev is not None:
                    keep, drop = (prev, entry) if RARITY_RANK.get(prev["레어도"], 0) \
                        >= RARITY_RANK.get(entry["레어도"], 0) else (entry, prev)
                    print(f"  [WARN] 이름 충돌 {name!r}: "
                          f"id={keep['id']}({keep['레어도']}) 유지, "
                          f"id={drop['id']}({drop['레어도']}) 버림", file=sys.stderr)
                    results[name] = keep
                    return
                results[name] = entry

        await asyncio.gather(*(one(rid) for rid in ids))

        # 애장품(17명만) 부착. results에 있는 캐릭터에만.
        favorites = await fetch_favorites(client)
        for entry in results.values():
            fav = favorites.get(entry["id"])
            if fav:
                entry["애장품"] = fav

    if missing:
        print(f"roledata 없음 {len(missing)}개: {sorted(missing)}")
    return dict(sorted(results.items(), key=lambda kv: kv[1]["id"]))


def adapt_favorite(fav: dict) -> tuple[int, dict] | None:
    """favorite_{id}.json → (resource_id, 애장품 엔트리).

    단계별로 3개 스킬 중 하나씩 교체된다(`skill_change_slot`). 배열 순서가
    곧 애장품 단계(1/2/3)다. 콜렉션 스킬은 캐릭터 특성이 아니라 제외한다.
    """
    m = ICON_RID_RE.search(fav.get("icon_resource_id", ""))
    if not m:
        return None
    rid = int(m.group(1))

    stages = []
    for i, item in enumerate(fav.get("favoriteitem_skill_group_data") or [], start=1):
        info = item.get("info", item)
        skill = render_skill(info)  # 쿨타임/template/values
        stages.append({
            "단계": i,
            "교체슬롯": item.get("skill_change_slot"),
            "스킬명": info.get("name_localkey", ""),
            "template": skill["template"],
            "values": skill["values"],
        })
    return rid, {"아이템명": fav.get("name_localkey", ""), "단계별": stages}


async def fetch_favorites(client: httpx.AsyncClient) -> dict[int, dict]:
    """SSR 애장품 전량 수집 → {resource_id: 애장품 엔트리}."""
    try:
        rare_map = await fetch_json(client, FAVORITE_RARE_MAP_PATH)
    except httpx.HTTPStatusError:
        print("애장품: favorite_rare_map 없음, 건너뜀", file=sys.stderr)
        return {}

    fav_ids = rare_map.get("SSR", [])
    result: dict[int, dict] = {}
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(fid: int):
        async with limit:
            try:
                fav = await fetch_json(
                    client, FAVORITE_PATH.format(locale=LOCALE, fid=fid)
                )
            except httpx.HTTPStatusError:
                return
            adapted = adapt_favorite(fav)
            if adapted:
                result[adapted[0]] = adapted[1]

    await asyncio.gather(*(one(fid) for fid in fav_ids))
    print(f"애장품: {len(result)}명 수집")
    return result


async def download_images(results: dict, force: bool = False) -> None:
    IMAGE_DIR.mkdir(exist_ok=True)
    targets = []
    for name, entry in results.items():
        path = IMAGE_DIR / f"{safe_filename(name)}.webp"
        if force or not path.exists():
            targets.append((entry["id"], path))
    if not targets:
        print("이미지: 모두 존재")
        return

    limit = asyncio.Semaphore(CONCURRENCY)
    saved, failed = 0, []

    async with httpx.AsyncClient(timeout=30) as client:
        async def one(rid: int, path: Path):
            nonlocal saved
            async with limit:
                r = await client.get(cdn_path.url(PORTRAIT_PATH.format(rid=rid)))
                if r.status_code != 200:
                    failed.append(path.stem)
                    return
                path.write_bytes(r.content)
                saved += 1

        await asyncio.gather(*(one(rid, path) for rid, path in targets))

    print(f"이미지: {saved}개 저장" + (f", 실패 {failed}" if failed else ""))


def report_diff(new: dict, old_path: Path, partial: bool = False) -> None:
    """수집 결과를 기존 파일과 비교해 신규/변경/삭제를 출력.

    partial=True(--ids 부분 수집)이면 가져온 캐릭터만 비교한다. 나머지는
    수집 대상이 아니므로 "삭제"로 오인하지 않는다.
    """
    if not old_path.exists():
        # 아래 print들은 cp949 콘솔로 나간다. em dash 같은 비-cp949 문자를 쓰면
        # UnicodeEncodeError로 죽으므로 ASCII 구분자만 쓴다.
        print("기존 nikke_scraped.json 없음 - 전량 신규")
        return
    old = json.loads(old_path.read_text(encoding="utf-8"))

    added = [n for n in new if n not in old]
    removed = [] if partial else [n for n in old if n not in new]
    changed = []
    for name in new:
        if name in old and new[name] != old[name]:
            fields = [k for k in set(new[name]) | set(old[name])
                      if new[name].get(k) != old[name].get(k)]
            changed.append((name, sorted(fields)))

    print(f"\n신규 {len(added)} / 변경 {len(changed)}"
          + ("" if partial else f" / 삭제 {len(removed)}"))
    if added:
        print("  신규:", ", ".join(added))
    if removed:
        print("  삭제:", ", ".join(removed))
    for name, fields in changed:
        print(f"  변경: {name} : {', '.join(fields)}")


def parse_ids(raw: str) -> list[int]:
    """--ids 인자 파싱. 숫자 resource_id만 받는다.

    이름→id를 값싸게 조회할 인덱스가 CDN에 없다(완전한 이름 소스는 roledata
    전량뿐). 그러니 이름을 넣었으면 크래시 대신, 이름·id 없이도 되는 전량 수집을
    안내한다.
    """
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in tokens if not t.isdigit()]
    if bad:
        sys.exit(
            f"[cdn_fetch] --ids 는 숫자 resource_id만 받는다 (받은 값: {', '.join(bad)}).\n"
            f"  이름만 안다면 인자 없이 전량 수집하라 (수 초):  python scraper/cdn_fetch.py\n"
            f"  무엇이 바뀌는지 먼저 보려면:                    python scraper/cdn_fetch.py --check"
        )
    return [int(t) for t in tokens]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="쓰지 않고 diff만 출력")
    ap.add_argument("--ids", help="쉼표 구분 resource_id(숫자)만 수집. 이름만 알면 인자 없이 전량")
    ap.add_argument("--force-images", action="store_true", help="이미지 전부 다시 받기")
    args = ap.parse_args()

    ids = parse_ids(args.ids) if args.ids else None
    results = asyncio.run(collect(ids))
    print(f"수집 완료 {len(results)}명")

    partial = ids is not None

    if args.check:
        report_diff(results, JSON_PATH, partial=partial)
        print("\n--check 모드: 파일을 쓰지 않았다")
        return

    report_diff(results, JSON_PATH, partial=partial)
    if partial and JSON_PATH.exists():
        merged = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        merged.update(results)
        results = merged
    JSON_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{JSON_PATH.name} 저장")

    asyncio.run(download_images(results, force=args.force_images))
    parse_nikke(results)


if __name__ == "__main__":
    main()
