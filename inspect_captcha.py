import asyncio
from playwright.async_api import async_playwright

async def inspect_captcha():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        captcha_el = await page.query_selector("#imgCaptchaId")
        if captcha_el:
            img_bytes = await captcha_el.screenshot()
            with open("sample_captcha.jpg", "wb") as f:
                f.write(img_bytes)
            print(f"Sample captcha saved: {len(img_bytes)} bytes")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_captcha())
