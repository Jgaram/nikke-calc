import asyncio
import json
from playwright.async_api import async_playwright

TARGET_URL = "https://www.blablalink.com/shiftyspad/nikke"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(TARGET_URL)
        print("브라우저가 열렸습니다. 로그인 후 Enter를 누르세요...")
        input()

        await page.wait_for_load_state("networkidle")
        await page.goto(TARGET_URL, wait_until="networkidle")

        # localStorage 추출
        ls_data = await page.evaluate("""() => {
            return Object.fromEntries(
                Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])
            )
        }""")

        # 쿠키 추출
        cookies = await context.cookies()

        await browser.close()

    with open("ls_data.json", "w", encoding="utf-8") as f:
        json.dump(ls_data, f, ensure_ascii=False, indent=2)

    with open("cookies.json", "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

    print(f"ls_data.json: {len(ls_data)}개 키")
    print(f"cookies.json: {len(cookies)}개 쿠키")
    print("저장 완료")


if __name__ == "__main__":
    asyncio.run(main())
