import asyncio
import json
from nikke_scraper import (
    BASE_URL, scrape_character, dismiss_cookie_popup,
    inject_session, load_results, save_results
)
from parse_nikke import run as parse_nikke
from playwright.async_api import async_playwright

TARGET_IDS = [600]

# 애장품니케목록(로그인필요): [352,101,192,170,281,32,100,150,141,142,112,30,390,80,72,550,210]

async def main():
    results = load_results()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await dismiss_cookie_popup(page)
        # await inject_session(page, context)

        for nikke_id in TARGET_IDS:
            print(f"ID={nikke_id} 수집 중...")
            try:
                result = await scrape_character(page, nikke_id)
                if result is None:
                    print(f"  ID={nikke_id} 캐릭터 없음")
                    continue
                char_name, data = result
                results[char_name] = data  # 덮어쓰기
                save_results(results)
                print(f"  [{char_name}] 완료")
            except Exception as e:
                print(f"  ID={nikke_id} 오류: {e}")
            await asyncio.sleep(0.5)

        await browser.close()

    parse_nikke(results)
    print("완료")


if __name__ == "__main__":
    asyncio.run(main())
