import asyncio
import time
from playwright.async_api import async_playwright
from telegram_bot import solve_winocr_strict, get_last_business_day, format_date_for_input

async def diagnose():
    print("=== INICIANDO DIAGNOSTICO PROFUNDO PASO A PASO ===")
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
        
        t0 = time.time()
        print("1. Cargando portal...")
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        print(f"   -> Portal cargado ({time.time()-t0:.2f}s)")
        
        # Prevalidar Captcha
        valid_captcha = ""
        for i in range(15):
            c_el = await page.query_selector("#imgCaptchaId")
            c_bytes = await c_el.screenshot()
            code = await solve_winocr_strict(c_bytes)
            if len(code) == 6:
                valid_captcha = code
                print(f"2. Captcha resuelto en intento #{i+1}: {code}")
                break
            await page.reload()
            await page.wait_for_load_state("networkidle")
            
        print("3. Llenando cedula y datos...")
        await page.fill("#numeroIdentificacion", "1708927502")
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(1800)
        
        nombre = await page.input_value("#nombreCompleto")
        print(f"   -> Nombre obtenido: {nombre}")
        
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
            if (window.RichFaces && RichFaces.$('fecha')) {{
                RichFaces.$('fecha').setValue(d);
            }}
            const input = document.getElementById('fechaInputDate');
            if (input) {{
                input.value = '{formatted_date}';
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            }}
        """)
        await page.wait_for_timeout(400)
        
        print("4. Agregando documento extraviado...")
        await page.locator('input[value="+ Agregar un nuevo documento"]').click(force=True)
        await page.wait_for_timeout(800)
        
        await page.evaluate("""
            if (window.RichFaces && RichFaces.$('frmPopups:createPane')) {
                RichFaces.$('frmPopups:createPane').show();
            }
            const sel = document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect');
            if (sel) { sel.value = '7'; sel.dispatchEvent(new Event('change', { bubbles: true })); }
            const num = document.getElementById('frmPopups:numeroNew');
            if (num) { num.value = '1708927502'; num.dispatchEvent(new Event('change', { bubbles: true })); }
            const desc = document.getElementById('frmPopups:descripcionNew');
            if (desc) { desc.value = 'CEDULA DE IDENTIDAD'; desc.dispatchEvent(new Event('change', { bubbles: true })); }
        """)
        await page.wait_for_timeout(600)
        
        await page.evaluate("""
            const btn = document.querySelector('#frmPopups\\\\:createPane input[value="Aceptar"]') || document.getElementById('frmPopups:j_idt273');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(2000)
        
        await page.evaluate("""
            if (window.RichFaces && RichFaces.$('frmPopups:createPane')) {
                RichFaces.$('frmPopups:createPane').hide();
            }
            const shade = document.getElementById('frmPopups:createPane_shade');
            if (shade) shade.remove();
        """)
        await page.wait_for_timeout(400)
        
        print(f"5. Enviando Captcha: {valid_captcha}...")
        await page.fill("#captchaTxt", valid_captcha)
        await page.evaluate("""
            const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(2500)
        
        msgs = await page.evaluate("""
            (() => {
                const el = document.querySelector('.rf-msgs, .rich-messages, #messages, .ui-messages');
                return el ? el.innerText : '';
            })()
        """)
        print(f"   -> Mensajes del servidor tras Aceptar: '{msgs.strip()}'")
        
        print("6. Ejecutando confirmacion Si...")
        await page.evaluate("""
            if (window.si) {
                window.si();
            } else {
                const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
                if (btn) btn.click();
            }
        """)
        
        print("7. Esperando boton Ver Formulario...")
        ver_btn_found = False
        for sec in range(20):
            await asyncio.sleep(0.5)
            ver_btn_found = await page.evaluate("""
                (() => {
                    const btn = document.querySelector('input[value="Ver formulario"]');
                    if (btn) {
                        btn.click();
                        return true;
                    }
                    return false;
                })()
            """)
            if ver_btn_found:
                print(f"   -> Boton Ver Formulario clickeado a los {sec * 0.5:.1f}s!")
                break
                
        if not ver_btn_found:
            print("   -> AVISO: Boton Ver Formulario no aparecio.")
            body_txt = await page.evaluate("document.body.innerText")
            print("   -> Contenido en pantalla:", body_txt[:300])
            
        print("8. Esperando captura del PDF...")
        for sec in range(12):
            if captured_pdf and captured_pdf.startswith(b"%PDF"):
                print(f"   -> PDF capturado con exito ({len(captured_pdf)} bytes) a los {sec}s!")
                break
            await asyncio.sleep(1)
            
        await browser.close()
        print("=== DIAGNOSTICO FINALIZADO ===")

if __name__ == "__main__":
    asyncio.run(diagnose())
