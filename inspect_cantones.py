import asyncio
from playwright.async_api import async_playwright

async def inspect_cantones():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        await page.select_option("#provinciaDomicilio", value="17")
        await page.wait_for_timeout(3000)
        
        cantones = await page.eval_on_selector_all("#cantonDomicilio option", "opts => opts.map(o => ({ value: o.value, text: o.innerText }))")
        print("CANTONES DE PICHINCHA ENCONTRADOS:")
        for c in cantones:
            print(c)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_cantones())
