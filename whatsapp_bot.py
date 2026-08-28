import asyncio
import datetime
import io
import os
import re
import shutil
import sys
import time
from PIL import Image
from playwright.async_api import async_playwright
from text_cleaner import limpiar_y_corregir_sector

try:
    import winocr
except ImportError:
    winocr = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "whatsapp_profile")
os.makedirs(PROFILE_DIR, exist_ok=True)
TEMP_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(TEMP_DIR, exist_ok=True)
ARTIFACT_DIR = r"C:\Users\richi\.gemini\antigravity\brain\663695ea-4cff-4d48-beba-3b7a6920f31a"

processed_messages = set()

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

async def solve_winocr_strict(image_bytes: bytes) -> str:
    if winocr:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            scaled = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
            res = await winocr.recognize_pil(scaled, lang="es")
            cleaned = re.sub(r'[^A-Za-z0-9]', '', res.text.strip())
            if len(cleaned) == 6:
                return cleaned
        except Exception:
            pass
            
    if pytesseract:
        try:
            img_orig = Image.open(io.BytesIO(image_bytes)).convert("L")
            scaled = img_orig.resize((img_orig.width * 4, img_orig.height * 4), Image.Resampling.LANCZOS)
            for th in [175, 160, 190, 150, 200]:
                bin_img = scaled.point(lambda p: 0 if p > th else 255)
                text = pytesseract.image_to_string(
                    bin_img,
                    config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
                ).strip()
                cleaned = re.sub(r'[^A-Za-z0-9]', '', text)
                if len(cleaned) == 6:
                    return cleaned
        except Exception:
            pass
            
    return ""

async def procesar_denuncia_judicial(cedula: str, raw_sector: str):
    sector_info = limpiar_y_corregir_sector(raw_sector)
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    sector_limpio = sector_info["sector_limpio"]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
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
                if "application/pdf" in ct or "pdf" in ct:
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
        
        try:
            await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
            await page.wait_for_load_state("networkidle")
            
            # Pre-validar captcha
            valid_captcha_code = ""
            for _ in range(12):
                c_el = await page.query_selector("#imgCaptchaId")
                if not c_el:
                    break
                c_bytes = await c_el.screenshot()
                code = await solve_winocr_strict(c_bytes)
                if len(code) == 6:
                    valid_captcha_code = code
                    break
                await page.reload()
                await page.wait_for_load_state("networkidle")
                
            if not valid_captcha_code:
                await browser.close()
                return False, "No se pudo leer el captcha de la Judicatura", None
                
            # 1. Cédula
            await page.fill("#numeroIdentificacion", cedula)
            await page.locator("#numeroIdentificacion").blur()
            await page.wait_for_timeout(1800)
            
            nombre = await page.input_value("#nombreCompleto")
            if not nombre:
                nombre = "CIUDADANO REGISTRADO"
                
            # 2. Domicilio
            await page.select_option("#provinciaDomicilio", value="17")
            await page.wait_for_timeout(800)
            await page.select_option("#cantonDomicilio", label="QUITO")
            await page.fill("#direccionDomicilio", dir_domicilio)
            
            # 3. Extravío
            await page.select_option("#provinciaExtravio", value="17")
            await page.wait_for_timeout(800)
            await page.select_option("#cantonExtravio", label="QUITO")
            await page.fill("#direccionCircunstancia", dir_circunstancia)
            
            # 4. Fecha hábil
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
            
            # 5. Agregar documento
            await page.locator('input[value="+ Agregar un nuevo documento"]').click(force=True)
            await page.wait_for_timeout(800)
            
            await page.evaluate(f"""
                if (window.RichFaces && RichFaces.$('frmPopups:createPane')) {{
                    RichFaces.$('frmPopups:createPane').show();
                }}
                const sel = document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect');
                if (sel) {{
                    sel.value = '7';
                    sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                const num = document.getElementById('frmPopups:numeroNew');
                if (num) {{
                    num.value = '{cedula}';
                    num.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    num.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
                const desc = document.getElementById('frmPopups:descripcionNew');
                if (desc) {{
                    desc.value = 'CÉDULA DE IDENTIDAD';
                    desc.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    desc.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
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
            
            # 6. Enviar Captcha
            await page.fill("#captchaTxt", valid_captcha_code)
            await page.evaluate("""
                const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
                if (btn) btn.click();
            """)
            await page.wait_for_timeout(2500)
            
            # 7. Confirmar "Si"
            await page.evaluate("""
                if (window.si) {
                    window.si();
                } else {
                    const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
                    if (btn) btn.click();
                }
            """)
            
            # 8. Clic reactivo en 'Ver formulario'
            await page.evaluate("""
                new Promise((resolve) => {
                    let attempts = 0;
                    const interval = setInterval(() => {
                        attempts++;
                        const btn = document.querySelector('input[value="Ver formulario"]');
                        if (btn) {
                            clearInterval(interval);
                            btn.click();
                            resolve(true);
                        } else if (attempts >= 40) {
                            clearInterval(interval);
                            resolve(false);
                        }
                    }, 250);
                });
            """)
            
            # 9. Esperar PDF oficial
            for _ in range(18):
                if captured_pdf and captured_pdf.startswith(b"%PDF"):
                    break
                await asyncio.sleep(1)
                
            await browser.close()
            
            if captured_pdf and captured_pdf.startswith(b"%PDF") and len(captured_pdf) > 20000:
                return True, {
                    "nombre": nombre,
                    "cedula": cedula,
                    "sector": sector_limpio,
                    "pdf_bytes": captured_pdf
                }
            else:
                return False, "No se capturó el flujo PDF binario de la Judicatura"
                
        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, str(e)

async def enviar_mensaje_texto(page, texto):
    try:
        input_box = page.locator('footer div[contenteditable="true"]').first
        await input_box.click()
        for line in texto.split('\n'):
            await page.keyboard.type(line)
            await page.keyboard.press('Shift+Enter')
        await page.keyboard.press('Enter')
        await asyncio.sleep(1)
    except Exception as e:
        print(f"Error enviando mensaje: {e}", flush=True)

async def enviar_archivo_pdf(page, pdf_path, caption_text=""):
    try:
        attach_btn = page.locator('div[title="Adjuntar"], button[title="Adjuntar"], span[data-icon="plus"], span[data-icon="attach-menu-plus"]').first
        await attach_btn.click()
        await asyncio.sleep(1)
        
        file_input = page.locator('input[type="file"][accept*="*"]').first
        await file_input.set_input_files(pdf_path)
        await asyncio.sleep(2)
        
        if caption_text:
            caption_input = page.locator('div[contenteditable="true"][data-tab]').first
            if await caption_input.is_visible():
                await caption_input.fill(caption_text)
                await asyncio.sleep(0.5)
                
        send_btn = page.locator('span[data-icon="send"], div[aria-label="Enviar"]').first
        if await send_btn.is_visible():
            await send_btn.click()
        else:
            await page.keyboard.press('Enter')
            
        await asyncio.sleep(2)
        print(f"[WhatsApp] Archivo PDF enviado exitosamente: {pdf_path}", flush=True)
    except Exception as e:
        print(f"Error enviando archivo PDF: {e}", flush=True)

async def main():
    print("====================================================================", flush=True)
    print("          📲 ASISTENTE DE DENUNCIAS PARA WHATSAPP EN VIVO", flush=True)
    print("====================================================================", flush=True)
    print("Iniciando WhatsApp Web...", flush=True)
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        await page.goto("https://web.whatsapp.com")
        print("Cargando página de WhatsApp Web...", flush=True)
        
        # Esperar hasta que aparezca el código QR o el chat list
        logged_in = False
        qr_saved = False
        
        for _ in range(120):
            # Comprobar si ya está logueado
            is_ready = await page.evaluate("""
                (function() {
                    const chatList = document.querySelector('#pane-side') || document.querySelector('div[aria-label="Lista de chats"]');
                    const searchBox = document.querySelector('div[contenteditable="true"]');
                    return (chatList !== null || searchBox !== null);
                })()
            """)
            if is_ready:
                logged_in = True
                print("¡Sesión de WhatsApp detectada y activa!", flush=True)
                break
                
            # Si no está logueado, capturar el código QR
            if not qr_saved:
                qr_el = await page.query_selector('canvas, div[data-ref]')
                if qr_el:
                    qr_path = os.path.join(os.path.dirname(__file__), "qr_whatsapp.png")
                    await qr_el.screenshot(path=qr_path)
                    
                    artifact_qr = os.path.join(ARTIFACT_DIR, "qr_whatsapp.png")
                    try:
                        shutil.copy(qr_path, artifact_qr)
                    except Exception:
                        pass
                        
                    print(f"QR_GENERATED: {qr_path}", flush=True)
                    qr_saved = True
                    
            await asyncio.sleep(2)
            
        if not logged_in:
            print("Tiempo de espera agotado. Ejecuta de nuevo cuando desees escanear.", flush=True)
            return
            
        print("\n" + "=" * 68, flush=True)
        print("  🎉 ¡WHATSAPP VINCULADO CON ÉXITO!", flush=True)
        print("=" * 68, flush=True)
        print("  El bot está escuchando mensajes en tiempo real.", flush=True)
        print("  Cualquier mensaje con cédula y sector recibirá el PDF oficial.", flush=True)
        print("=" * 68 + "\n", flush=True)
        
        while True:
            try:
                unread_chats = await page.query_selector_all('span[aria-label*="no leído"], span[aria-label*="unread"], span[data-testid="icon-unread-count"]')
                
                for unread in unread_chats:
                    try:
                        await unread.click()
                        await asyncio.sleep(1.5)
                        
                        last_msg_text = await page.evaluate("""
                            (function() {
                                const msgs = document.querySelectorAll('div.message-in');
                                if (msgs.length === 0) return '';
                                const last = msgs[msgs.length - 1];
                                const textEl = last.querySelector('span.selectable-text') || last;
                                return textEl.innerText || '';
                            })()
                        """)
                        
                        msg_clean = last_msg_text.strip()
                        msg_hash = f"{msg_clean}_{int(time.time() // 60)}"
                        
                        if msg_clean and msg_hash not in processed_messages:
                            processed_messages.add(msg_hash)
                            
                            cedula_match = re.search(r'\b\d{10}\b', msg_clean)
                            if cedula_match:
                                cedula = cedula_match.group(0)
                                sector_raw = msg_clean.replace(cedula, "").strip()
                                sector_raw = re.sub(r'^(en el|en|sector|el|la)\s+', '', sector_raw, flags=re.IGNORECASE).strip()
                                
                                if not sector_raw or len(sector_raw) < 2:
                                    sector_raw = "Sector Centro"
                                    
                                print(f"[WhatsApp Mensaje Recibido] Cédula: {cedula} | Sector: {sector_raw}", flush=True)
                                
                                await enviar_mensaje_texto(
                                    page,
                                    f"⏳ *Generando denuncia oficial ante el Consejo de la Judicatura...*\n🆔 Cédula: {cedula}\n📍 Sector: {sector_raw}\n\n_Esto toma aproximadamente 13 segundos..._"
                                )
                                
                                t0 = time.time()
                                success, result = await procesar_denuncia_judicial(cedula, sector_raw)
                                elapsed = time.time() - t0
                                
                                if success:
                                    pdf_bytes = result["pdf_bytes"]
                                    nombre = result["nombre"]
                                    sector_fmt = result["sector"]
                                    
                                    pdf_file_path = os.path.join(TEMP_DIR, f"Denuncia_{cedula}.pdf")
                                    with open(pdf_file_path, "wb") as f:
                                        f.write(pdf_bytes)
                                        
                                    caption = (
                                        f"✅ *¡DENUNCIA GENERADA CON ÉXITO!* ({elapsed:.1f}s)\n\n"
                                        f"👤 *Nombre:* {nombre}\n"
                                        f"🆔 *Cédula:* {cedula}\n"
                                        f"📍 *Lugar:* {sector_fmt}\n\n"
                                        f"📄 _Documento oficial emitido por el Consejo de la Judicatura (válido para trámites legales y Registro Civil)._"
                                    )
                                    
                                    await enviar_archivo_pdf(page, pdf_file_path, caption_text=caption)
                                else:
                                    await enviar_mensaje_texto(
                                        page,
                                        f"❌ *No se pudo generar la denuncia:*\n{result}\n\nPor favor reenvía los datos para reintentar."
                                    )
                    except Exception as e:
                        print(f"Error procesando chat: {e}", flush=True)
                        
                await asyncio.sleep(2)
            except Exception as e:
                print(f"Loop error: {e}", flush=True)
                await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
