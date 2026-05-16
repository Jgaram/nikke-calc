import asyncio
import json
import re
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
from parse_nikke import run as parse_nikke

IMAGE_DIR = Path(__file__).parent.parent / "image"
IMAGE_DIR.mkdir(exist_ok=True)

BASE_URL = "https://www.blablalink.com/shiftyspad/nikke"
ID_RANGE = range(0, 871)

# 속성 파일명 → 한국어 매핑
CODE_MAP = {
    "fire": "작열", "water": "수냉", "wind": "풍압",
    "electronic": "전격", "iron": "철갑"
}

HASH_MAP = {
    "014d90279e0ca7278b0b8f7e9094a8dd.webp": "SSR",
    "64ec71645699f5fce79d98cfc20a525f.webp": "SR",
    "1c5d999c42ed640c95da540af7578667.webp": "R",
    "e0ff69fe9e3cf29c55232d3590135811.webp": "지원형",
    "6c30fdc2d204905edd388fc958359ec3.webp": "방어형",
    "6337300758895c79f9afe8139500adcf.webp": "화력형",
    "4aebd6d57c8afd17d334f37130ddc6e1.webp": "엘리시온",
    "8c41a47786bc43a7db71bd7218f4989d.webp": "테트라",
    "f3d8f861af997ddc5e1ee81b715ae314.webp": "미실리스",
    "29d8e5c949e2141795446ca64a867b9b.webp": "필그림",
    "2e913befc4fc5e84f9807f8a455232dc.webp": "어브노말",
    "448005ff9513c9a8afabbece6ad2a05b.webp": "1",
    "50f0e38e5306ba4ddae04e494921216a.webp": "2",
    "3b98d581bb5776454e084075adf00c4c.webp": "3",
    "25bb6a298db42bf89464714a7fd6f159.webp": "A",
}



async def get_meta_info(page) -> dict:
    """캐릭터 메타 정보 추출 (레어도, 스쿼드, 속성, 클래스, 기업, 버스트 단계)"""
    meta = {}

    # 스쿼드 (텍스트로 직접 표시됨)
    squad_el = await page.query_selector(".w-half.text-center.text-20.text-ink-60")
    if squad_el:
        squad_text = (await squad_el.inner_text()).strip()
        if squad_text and squad_text != "레어도":
            meta["스쿼드"] = squad_text

    # 레어도 (해시 이미지)
    rarity_img = await page.query_selector("img.icon-rarity")
    if rarity_img:
        src = await rarity_img.get_attribute("src") or ""
        h = src.split("/")[-1]
        meta["레어도"] = HASH_MAP.get(h, h[:8])

    # 속성/무기/클래스/기업/버스트 (w-33 그리드)
    w33s = await page.query_selector_all(".w-33")
    for w33 in w33s:
        label_el = await w33.query_selector(".text-black.text-16.text-center")
        label = (await label_el.inner_text()).strip() if label_el else ""
        img = await w33.query_selector("img")
        if not img or not label:
            continue

        src = await img.get_attribute("src") or ""
        filename = src.split("/")[-1].replace(".png", "").replace(".webp", "")

        if label == "속성":
            raw = filename.replace("icon-code-", "")
            meta["속성"] = CODE_MAP.get(raw, raw)
        elif label in ("클래스", "기업", "버스트 단계"):
            meta[label] = HASH_MAP.get(filename + ".webp", filename[:8])

    return meta


async def inject_session(page, context):
    """localStorage + 쿠키 주입으로 로그인 상태 복원"""
    try:
        with open("ls_data.json", encoding="utf-8") as f:
            ls_data = json.load(f)
        with open("cookies.json", encoding="utf-8") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        await page.evaluate("""data => {
            for (const [k, v] of Object.entries(data)) {
                localStorage.setItem(k, v);
            }
        }""", ls_data)
        await page.reload(wait_until="domcontentloaded")
        await dismiss_cookie_popup(page)
        print("로그인 세션 주입 완료")
    except FileNotFoundError as e:
        print(f"{e.filename} 없음, 비로그인으로 진행")


async def dismiss_cookie_popup(page):
    """최초 1회 쿠키 팝업 처리"""
    try:
        btn = await page.wait_for_selector("#onetrust-accept-btn-handler", timeout=5000)
        await page.evaluate("el => el.click()", btn)
        await asyncio.sleep(0.5)
    except Exception:
        pass


async def click_skill_tab(page):
    """로그인 상태에서 장비 탭이 기본값이므로 스킬 탭 명시적 클릭"""
    tabs = await page.query_selector_all("div.bg-\\[var\\(--color-5\\)\\]")
    for tab in tabs:
        text = await tab.inner_text()
        if text.strip() == "스킬":
            await page.evaluate("el => el.click()", tab)
            await asyncio.sleep(0.3)
            break


async def close_weapon_popup(page):
    """무기 팝업 닫기 + 완전히 사라질 때까지 대기"""
    try:
        close_btn = await page.query_selector(".popup .pop-close, .popup [class*='close']")
        if close_btn:
            await page.evaluate("el => el.click()", close_btn)
        else:
            overlay = await page.query_selector(".pop-overlay, .popup-overlay, .dim")
            if overlay:
                await page.evaluate("el => el.click()", overlay)
            else:
                await page.mouse.click(10, 10)

        await page.wait_for_selector(
            ".popup:has(.nikkes-detail-weapon)",
            state="hidden",
            timeout=5000
        )
    except Exception:
        await asyncio.sleep(0.5)


async def get_weapon_info(page) -> dict:
    """무기 버튼 클릭 후 팝업에서 무기 정보 수집"""
    weapon = {}

    weapon_btn = await page.query_selector(".large.weapon-slot-item--hex")
    if not weapon_btn:
        return weapon
    await page.evaluate("el => el.click()", weapon_btn)
    await asyncio.sleep(0.5)

    popups = await page.query_selector_all(".popup")
    weapon_popup = None
    for p in popups:
        text = await p.inner_text()
        if "최대 장탄" in text:
            weapon_popup = p
            break
    if not weapon_popup:
        return weapon

    weapon_detail = await weapon_popup.query_selector(".nikkes-detail-weapon .expandable-box")
    if not weapon_detail:
        return weapon

    type_el = await weapon_detail.query_selector(".bg-gray-10.border-radius-20")
    weapon["무기유형"] = (await type_el.inner_text()).strip() if type_el else ""

    rows = await weapon_detail.query_selector_all(".flex.text-center")
    for row in rows:
        label_el = await row.query_selector(".w-60")
        value_el = await row.query_selector(".w-40")
        label = (await label_el.inner_text()).strip() if label_el else ""
        value = (await value_el.inner_text()).strip() if value_el else ""
        if label:
            weapon[label] = value

    desc_el = await weapon_detail.query_selector(".w-half.pl-5.mt-10")
    weapon["무기스킬"] = (await desc_el.inner_text()).strip() if desc_el else ""

    await close_weapon_popup(page)

    return weapon


def build_template(levels: dict[str, str]) -> dict:
    """레벨별 텍스트에서 template + values 구조 생성"""
    texts = [levels[str(i)] for i in range(1, len(levels) + 1) if str(i) in levels]
    if not texts:
        return {"template": "", "values": {}}

    # 모든 숫자(소수 포함) 추출
    number_pattern = re.compile(r'\d+\.?\d*')
    all_numbers = [number_pattern.findall(t) for t in texts]

    # 레벨 간 변하는 숫자 위치 찾기
    if len(all_numbers) > 1:
        changing = [
            i for i in range(len(all_numbers[0]))
            if any(all_numbers[j][i] != all_numbers[0][i] for j in range(1, len(all_numbers))
                   if i < len(all_numbers[j]))
        ]
    else:
        changing = list(range(len(all_numbers[0]))) if all_numbers else []

    # template 생성: 변하는 숫자만 {0}, {1}... 으로 교체
    # 같은 숫자가 여러 위치에 나타날 수 있으므로, 문자열을 앞에서부터 순회하며
    # changing 위치의 숫자를 순서대로 플레이스홀더로 교체한다.
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

    # 레벨별 values
    values = {}
    for lv, text in levels.items():
        nums = number_pattern.findall(text)
        values[lv] = [nums[pos] for pos in changing if pos < len(nums)]

    return {"template": template, "values": values}


async def get_skill_levels(page, card) -> dict:
    """카드 하나의 min~max 레벨별 스킬 설명 + 쿨타임 수집 후 template 구조로 반환"""
    levels = {}

    links = await card.query_selector_all("a")
    if len(links) < 3:
        return {"쿨타임": None, "template": "", "values": {}}

    min_btn = links[0]
    plus_btn = links[2]

    await page.evaluate("el => el.click()", min_btn)
    await asyncio.sleep(0.1)

    # 쿨타임 추출 (레벨과 무관한 고정값, ■ 앞의 숫자s 패턴)
    card_text = await card.inner_text()
    match = re.search(r'(\d+\.?\d*)\s*s(?=\s*■)', card_text)
    cooltime = match.group(0).strip() if match else None

    for _ in range(10):
        level_el = await card.query_selector(".ff-num.text-highlight-blue")
        desc_el = await card.query_selector(".nikkes-detail-weapon-skill-desc")

        level = await level_el.inner_text() if level_el else None
        desc = (await desc_el.inner_text() if desc_el else "").replace("\xa0", " ")

        if level:
            levels[level.strip()] = desc.strip()

        plus_class = await plus_btn.get_attribute("class") or ""
        if "cursor-default" in plus_class:
            break

        await page.evaluate("el => el.click()", plus_btn)
        await asyncio.sleep(0.1)

    return {"쿨타임": cooltime, **build_template(levels)}


async def scrape_character(page, nikke_id: int, skip_image: bool = False) -> tuple[str, dict] | None:
    """캐릭터 한 명의 전체 데이터 수집. skip_image=True이면 썸네일 다운로드를 건너뜀."""
    url = f"{BASE_URL}?from=list&nikke={nikke_id}"
    await page.goto(url, wait_until="domcontentloaded")

    try:
        await page.wait_for_selector(".nikkes-weapon-res-left", timeout=3000)
    except Exception:
        return None

    cards = await page.query_selector_all(".nikkes-weapon-res-left")
    if not cards:
        return None

    # 캐릭터명
    name_el = await page.query_selector(".charinfo-name span.ff-tt-extra-bold")
    char_name = (await name_el.inner_text()).strip() if name_el else f"ID_{nikke_id}"

    # 썸네일 이미지 다운로드 (최초 수집 시에만, 재스크래핑 시 skip)
    if not skip_image:
        img_url = await page.evaluate("""
            () => {
                const imgs = Array.from(document.querySelectorAll('img.w-full'));
                const thumb = imgs.find(img =>
                    img.src.includes('sg-tools-cdn') && img.naturalWidth === 256
                );
                return thumb ? thumb.src : null;
            }
        """)
        if img_url:
            safe_name = char_name.replace("/", "_")
            img_path = IMAGE_DIR / f"{safe_name}.webp"
            if not img_path.exists():
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(img_url)
                    img_path.write_bytes(r.content)
                    print(f"  [{char_name}] 이미지 저장: {img_path.name}")
                except Exception as e:
                    print(f"  [{char_name}] 이미지 다운로드 실패: {e}")

    # 메타 정보
    meta = await get_meta_info(page)

    # 무기 상세 정보
    weapon_info = await get_weapon_info(page)

    # 스킬 탭 활성화 (로그인 시 장비 탭이 기본값)
    await click_skill_tab(page)
    try:
        await page.wait_for_selector(".nikkes-weapon-res-left", state="visible", timeout=5000)
    except Exception:
        pass
    cards = await page.query_selector_all(".nikkes-weapon-res-left")

    # 스킬 데이터
    skills = {}
    for card in cards:
        name_el = await card.query_selector(".text-ink.font-bold")
        skill_name = (await name_el.inner_text()).strip().replace("\xa0", " ") if name_el else "Unknown"
        print(f"  [{char_name}] 스킬: {skill_name}")
        skills[skill_name] = await get_skill_levels(page, card)

    return char_name, {
        "id": nikke_id,
        **meta,
        "무기상세": weapon_info,
        "스킬": skills,
        "post_fire_delay": 0,
        "post_reload_delay": 0,
    }


JSON_PATH = "nikke_scraped.json"
COLLECTED_PATH = "collected_ids.json"


def load_collected() -> set:
    try:
        with open(COLLECTED_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_collected(collected: set):
    with open(COLLECTED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(collected), f)


def load_results() -> dict:
    try:
        with open(JSON_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_results(results: dict):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


async def main():
    results = load_results()
    done_names = set(results.keys())
    collected_ids = load_collected()
    if done_names:
        print(f"기존 데이터 {len(done_names)}개, skip ID {len(collected_ids)}개 로드")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 쿠키 팝업 최초 1회 처리 + 로그인 세션 주입
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await dismiss_cookie_popup(page)
        # await inject_session(page, context)  # 로그인 필요 시 주석 해제

        print(f"ID {ID_RANGE.start}~{ID_RANGE.stop - 1} 순회 시작")
        for nikke_id in ID_RANGE:
            if nikke_id in collected_ids:
                continue
            print(f"ID={nikke_id} 확인 중...")
            try:
                result = await scrape_character(page, nikke_id)
                if result is None:
                    print(f"  ID={nikke_id} 건너뜀")
                    continue
                char_name, data = result
                if nikke_id in collected_ids:
                    print(f"  [{char_name}] 이미 완료, 건너뜀")
                    continue
                results[char_name] = data
                collected_ids.add(nikke_id)
                save_collected(collected_ids)
                save_results(results)
                print(f"  [{char_name}] 완료 (누적 {len(collected_ids)}개)")
            except Exception as e:
                print(f"  ID={nikke_id} 오류: {e}")

            await asyncio.sleep(0.5)

        await browser.close()

    print(f"\n총 {len(results)}개 캐릭터 완료")
    save_results(results)
    print("nikke_scraped.json 저장 완료")
    parse_nikke(results)
    
if __name__ == "__main__":
    asyncio.run(main())