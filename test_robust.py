import asyncio
import datetime
from playwright.async_api import async_playwright
from text_cleaner import limpiar_y_corregir_sector

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
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()
        
        print("1. Cargando portal...")
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        cedula = "1710034065"
        raw_sector = "el recreo"
        sector_info = limpiar_y_corregir_sector(raw_sector)
        
        print(f"2. Ingresando cédula: {cedula}")
        await page.fill("#numeroIdentificacion", cedula)
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(2500)
        
        nombre = await page.input_value("#nombreCompleto")
        print(f"   Nombre: '{nombre}'")
        
        print("3. Seleccionando domicilio Pichincha -> Quito...")
        await page.select_option("#provinciaDomicilio", value="17")
        # Esperar a que los cantones se carguen vía AJAX
        await page.wait_for_function("document.querySelectorAll('#cantonDomicilio option').length > 1", timeout=10000)
        await page.select_option("#cantonDomicilio", value="185")
        await page.fill("#direccionDomicilio", sector_info["direccion_domicilio"])
        
        print("4. Seleccionando extravío Pichincha -> Quito...")
        await page.select_option("#provinciaExtravio", value="17")
        await page.wait_for_function("document.querySelectorAll('#cantonExtravio option').length > 1", timeout=10000)
        await page.select_option("#cantonExtravio", value="185")
        await page.fill("#direccionCircunstancia", sector_info["direccion_circunstancia"])
        
        print("5. Configurando fecha hábil...")
        b_day = get_last_business_day()
        formatted = format_date_for_input(b_day)
        await page.evaluate(f"""
            const d = new Date({b_day.year}, {b_day.month - 1}, {b_day.day});
            if (window.RichFaces && RichFaces.$('fecha')) {{
                RichFaces.$('fecha').setValue(d);
            }}
            const input = document.getElementById('fechaInputDate');
            if (input) input.value = '{formatted}';
        """)
        
        print("6. Agregando documento...")
        add_btn = page.locator('input[value="+ Agregar un nuevo documento"]')
        await add_btn.click()
        await page.wait_for_timeout(1500)
        await page.evaluate("if (window.RichFaces && RichFaces.$('frmPopups:createPane')) RichFaces.$('frmPopups:createPane').show();")
        await page.wait_for_timeout(800)
        
        await page.select_option('select[id*="tipoDocumentoExtraviadoNewSelect"]', value="7")
        await page.wait_for_timeout(1000)
        await page.fill('input[id*="numeroNew"]', cedula)
        await page.fill('textarea[id*="descripcionNew"]', "cédula de identidad")
        
        accept_doc_btn = page.locator('#frmPopups\\:createPane input[value="Aceptar"]')
        await accept_doc_btn.click()
        await page.wait_for_timeout(2500)
        
        print("7. Capturando y verificando Captcha...")
        captcha_el = await page.query_selector("#imgCaptchaId")
        if captcha_el:
            screenshot = await captcha_el.screenshot()
            print(f"   Captcha capturado con éxito ({len(screenshot)} bytes)")
            
        await browser.close()
        print("TEST COMPLETADO EXITOSAMENTE!")

if __name__ == "__main__":
    asyncio.run(test())
