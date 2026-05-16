"""42바이트 placeholder 이미지를 실제 이미지로 교체하는 스크립트.

nikke_scraped.json의 id 필드를 이용해 blablalink.com에서 썸네일을 재다운로드한다.
Playwright 로그인 세션이 필요하므로 extract_session.py로 먼저 세션을 추출해둬야 한다.
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
IMAGE_DIR = ROOT / "image"
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"
COOKIES_FILE = ROOT / "scraper" / "cookies.json"
LS_FILE      = ROOT / "scraper" / "ls_data.json"
BASE_URL = "https://www.blablalink.com/shiftyspad/nikke"

PLACEHOLDER_SIZE = 100  # 이 크기 이하면 재다운로드 대상


def get_targets() -> list[tuple[str, int]]:
    """재다운로드가 필요한 (캐릭터명, id) 목록 반환."""
    data = json.loads(SCRAPED.read_text(encoding="utf-8"))
    targets = []
    for name, info in data.items():
        safe = name.replace("/", "_")
        path = IMAGE_DIR / f"{safe}.webp"
        size = path.stat().st_size if path.exists() else 0
        if size <= PLACEHOLDER_SIZE:
            targets.append((name, info["id"]))
    return targets


async def fetch_image_url(page, nikke_id: int) -> str | None:
    url = f"{BASE_URL}?from=list&nikke={nikke_id}"
    await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector(".nikkes-weapon-res-left", timeout=5000)
    except Exception:
        return None

    return await page.evaluate("""
        () => {
            const imgs = Array.from(document.querySelectorAll('img.w-full'));
            const thumb = imgs.find(img =>
                img.src.includes('sg-tools-cdn') && img.naturalWidth === 256
            );
            return thumb ? thumb.src : null;
        }
    """)


async def main():
    targets = get_targets()
    if not targets:
        print("재다운로드 대상 없음.")
        return

    print(f"재다운로드 대상: {len(targets)}개")
    for name, nid in targets:
        print(f"  [{nid}] {name}")

    # 세션 주입 (extract_session.py가 생성한 cookies.json / ls_data.json 사용)
    cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8")) if COOKIES_FILE.exists() else []
    ls_data = json.loads(LS_FILE.read_text(encoding="utf-8"))      if LS_FILE.exists()      else {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()

        if ls_data:
            await page.goto(BASE_URL, wait_until="domcontentloaded")
            for key, val in ls_data.items():
                await page.evaluate(f"localStorage.setItem({json.dumps(key)}, {json.dumps(val)})")

        ok, fail = 0, 0
        async with httpx.AsyncClient(timeout=15) as client:
            for name, nikke_id in targets:
                img_url = await fetch_image_url(page, nikke_id)
                if not img_url:
                    print(f"  ✗ [{nikke_id}] {name} — 이미지 URL 없음")
                    fail += 1
                    continue

                try:
                    r = await client.get(img_url)
                    if len(r.content) <= PLACEHOLDER_SIZE:
                        print(f"  ✗ [{nikke_id}] {name} — 응답도 placeholder ({len(r.content)}B)")
                        fail += 1
                        continue

                    safe = name.replace("/", "_")
                    path = IMAGE_DIR / f"{safe}.webp"
                    path.write_bytes(r.content)
                    print(f"  ✓ [{nikke_id}] {name} — {len(r.content):,}B 저장")
                    ok += 1
                except Exception as e:
                    print(f"  ✗ [{nikke_id}] {name} — 다운로드 오류: {e}")
                    fail += 1

        await browser.close()

    print(f"\n완료: 성공 {ok}개 / 실패 {fail}개")


if __name__ == "__main__":
    asyncio.run(main())
