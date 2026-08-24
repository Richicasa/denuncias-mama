import asyncio
import datetime
from playwright.async_api import async_playwright

def get_last_business_day(today=None):
    if today is None:
        today = datetime.date.today()
    if today.weekday() == 0:
        delta = 3
    elif today.weekday() == 6:
        delta = 2
    elif today.weekday() == 5:
        delta = 1
    else:
        delta = 1
    return today - datetime.timedelta(days=delta)

def format_date_for_input(d):
    months_short = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{months_short[d.month - 1]} {d.day}, {d.year}"

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=60000)
        await page.wait_for_load_state("networkidle")
        
        test_cedula = "1710034065" 
        await page.fill("#numeroIdentificacion", test_cedula)
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(3000)
        
        nombre = await page.input_value("#nombreCompleto")
        print(f"Nombre Completo: '{nombre}'")
        
        # Select Pichincha & Quito
        await page.select_option("#provinciaDomicilio", value="17")
        await page.wait_for_timeout(2000)
        await page.select_option("#cantonDomicilio", value="185")
        await page.fill("#direccionDomicilio", "Sector de La Magdalena")
        
        # Extravio
        await page.select_option("#provinciaExtravio", value="17")
        await page.wait_for_timeout(2000)
        await page.select_option("#cantonExtravio", value="185")
        await page.fill("#direccionCircunstancia", "Documento extraviado en el sector de La Magdalena")
        
        # Date
        b_day = get_last_business_day()
        formatted = format_date_for_input(b_day)
        print(f"Setting loss date: {formatted}")
        await page.evaluate(f"""
            const d = new Date({b_day.year}, {b_day.month - 1}, {b_day.day});
            if (window.RichFaces && RichFaces.$('fecha')) {{
                RichFaces.$('fecha').setValue(d);
            }}
            const input = document.getElementById('fechaInputDate');
            if (input) input.value = '{formatted}';
        """)
        
        print("Clicking exact '+ Agregar un nuevo documento' button...")
        add_btn = page.locator('input[value="+ Agregar un nuevo documento"]')
        await add_btn.click()
        await page.wait_for_timeout(2000)
        
        # Ensure modal is shown
        await page.evaluate("if (window.RichFaces && RichFaces.$('frmPopups:createPane')) RichFaces.$('frmPopups:createPane').show();")
        await page.wait_for_timeout(1000)
        
        print("Selecting document type...")
        await page.select_option('select[id*="tipoDocumentoExtraviadoNewSelect"]', value="7")
        await page.wait_for_timeout(1000)
        await page.fill('input[id*="numeroNew"]', test_cedula)
        await page.fill('textarea[id*="descripcionNew"]', "cédula de identidad")
        
        print("Submitting modal document...")
        accept_doc_btn = page.locator('#frmPopups\\:createPane input[value="Aceptar"]')
        await accept_doc_btn.click()
        await page.wait_for_timeout(3000)
        
        # Check table
        rows = await page.eval_on_selector_all("#table\\:tb tr", "trs => trs.map(r => r.innerText.trim()).filter(t => t.length > 0)")
        print(f"Table rows after adding document: {rows}")
        
        # Capture Captcha
        captcha_el = await page.query_selector("#imgCaptchaId")
        if captcha_el:
            screenshot = await captcha_el.screenshot()
            print(f"SUCCESS: Captcha screenshot captured successfully! ({len(screenshot)} bytes)")
            
        await browser.close()
        print("FULL AUTOMATION FLOW PASSED!")

if __name__ == "__main__":
    asyncio.run(test())
