import asyncio
from playwright.async_api import async_playwright
from telegram_bot import solve_winocr_strict, get_last_business_day, format_date_for_input

async def inspect_button():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC"
        )
        page = await context.new_page()
        
        captured_pdf = None
        async def route_interceptor(route, request):
            nonlocal captured_pdf
            try:
                resp = await route.fetch()
                ct = resp.headers.get("content-type", "").lower()
                if "pdf" in ct:
                    body = await resp.body()
                    if body.startswith(b"%PDF"):
                        captured_pdf = body
                await route.fulfill(response=resp)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
        await page.route("**/formulario.jsf*", route_interceptor)
        
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf")
        await page.wait_for_load_state("networkidle")
        
        # Validar captcha
        code = ""
        for _ in range(10):
            c_el = await page.query_selector("#imgCaptchaId")
            code = await solve_winocr_strict(await c_el.screenshot())
            if len(code) == 6:
                break
            await page.reload()
            await page.wait_for_load_state("networkidle")
            
        await page.fill("#numeroIdentificacion", "1708927502")
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(1800)
        
        await page.select_option("#provinciaDomicilio", value="17")
        await page.wait_for_timeout(800)
        await page.select_option("#cantonDomicilio", label="QUITO")
        await page.fill("#direccionDomicilio", "SECTOR EL RECREO")
        
        await page.select_option("#provinciaExtravio", value="17")
        await page.wait_for_timeout(800)
        await page.select_option("#cantonExtravio", label="QUITO")
        await page.fill("#direccionCircunstancia", "EXTRAVIO EN SECTOR EL RECREO")
        
        b_day = get_last_business_day()
        formatted_date = format_date_for_input(b_day)
        await page.evaluate(f"""
            const d = new Date({b_day.year}, {b_day.month - 1}, {b_day.day});
            if (window.RichFaces && RichFaces.$('fecha')) RichFaces.$('fecha').setValue(d);
            const input = document.getElementById('fechaInputDate');
            if (input) {{ input.value = '{formatted_date}'; input.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
        """)
        await page.wait_for_timeout(400)
        
        await page.locator('input[value="+ Agregar un nuevo documento"]').click(force=True)
        await page.wait_for_timeout(800)
        await page.evaluate("""
            if (window.RichFaces && RichFaces.$('frmPopups:createPane')) RichFaces.$('frmPopups:createPane').show();
            document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect').value = '7';
            document.getElementById('frmPopups:numeroNew').value = '1708927502';
            document.getElementById('frmPopups:descripcionNew').value = 'CEDULA DE IDENTIDAD';
            const btn = document.querySelector('#frmPopups\\\\:createPane input[value="Aceptar"]') || document.getElementById('frmPopups:j_idt273');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(2000)
        await page.evaluate("""
            if (window.RichFaces && RichFaces.$('frmPopups:createPane')) RichFaces.$('frmPopups:createPane').hide();
            const shade = document.getElementById('frmPopups:createPane_shade');
            if (shade) shade.remove();
        """)
        await page.wait_for_timeout(400)
        
        await page.fill("#captchaTxt", code)
        await page.evaluate('document.getElementById("j_idt170").click()')
        await page.wait_for_timeout(2500)
        
        await page.evaluate('if (window.si) window.si();')
        await page.wait_for_timeout(2000)
        
        btn_html = await page.evaluate("""
            (() => {
                const btn = document.querySelector('input[value="Ver formulario"]');
                return btn ? btn.outerHTML : 'NO_ENCONTRADO';
            })()
        """)
        print("HTML_BOTON:", btn_html)
        
        # Probar click con locator de Playwright vs evaluate
        if await page.query_selector('input[value="Ver formulario"]'):
            print("Clickeando con locator...")
            await page.locator('input[value="Ver formulario"]').click()
            
        for _ in range(8):
            if captured_pdf:
                print(f"PDF CAPTURADO: {len(captured_pdf)} bytes")
                break
            await asyncio.sleep(1)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_button())
