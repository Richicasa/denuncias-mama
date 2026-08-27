import asyncio
import datetime
import io
import os
import re
import sys
import time
from PIL import Image
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from playwright.async_api import async_playwright
from text_cleaner import limpiar_y_corregir_sector

try:
    import pytesseract
except ImportError:
    pytesseract = None

user_states = {}

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

async def solve_ocr_linux(image_bytes: bytes) -> str:
    if not pytesseract:
        return ""
    try:
        img_orig = Image.open(io.BytesIO(image_bytes)).convert("L")
        scaled = img_orig.resize((img_orig.width * 4, img_orig.height * 4), Image.Resampling.LANCZOS)
        for th in [175, 160, 190, 150, 200, 140]:
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
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
            for _ in range(15):
                c_el = await page.query_selector("#imgCaptchaId")
                if not c_el:
                    break
                c_bytes = await c_el.screenshot()
                code = await solve_ocr_linux(c_bytes)
                if len(code) == 6:
                    valid_captcha_code = code
                    break
                await page.reload()
                await page.wait_for_load_state("networkidle")
                
            if not valid_captcha_code:
                await browser.close()
                return False, "No se pudo leer un captcha nítido de la Judicatura. Por favor intenta de nuevo.", None
                
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
                }, None
            else:
                return False, "El servidor de la Judicatura tardó en emitir el PDF.", None
                
        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error al procesar: {str(e)}", None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **¡Hola! Soy tu Asistente para Denuncias Judiciales de Extravío.**\n\n"
        "Puedo generar tu denuncia oficial del Consejo de la Judicatura y enviarte el PDF listo para imprimir.\n\n"
        "📝 **Para comenzar, envíame:**\n"
        "• El número de **Cédula** (10 dígitos)\n"
        "• El **Sector o Lugar** del extravío (ej: *El Recreo*, *Quitumbe*, *Centro Histórico*)\n\n"
        "💡 *Ejemplo:* `1708927502 Sector El Recreo`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    cedula_match = re.search(r'\b\d{10}\b', text)
    
    if cedula_match:
        cedula = cedula_match.group(0)
        sector_candidate = text.replace(cedula, "").strip()
        sector_candidate = re.sub(r'^(en el|en|sector|el|la)\s+', '', sector_candidate, flags=re.IGNORECASE).strip()
        
        if sector_candidate and len(sector_candidate) > 2:
            await procesar_y_responder(update, cedula, sector_candidate)
        else:
            user_states[user_id] = {"cedula": cedula}
            await update.message.reply_text(
                f"✅ Cédula recibida: `{cedula}`\n\n"
                "📍 ¿En qué **sector o lugar** ocurrió el extravío?\n"
                "*(Ejemplo: El Recreo, Chillogallo, La Mariscal, Centro Histórico...)*",
                parse_mode="Markdown"
            )
        return
        
    if user_id in user_states and "cedula" in user_states[user_id]:
        cedula = user_states[user_id]["cedula"]
        sector = text
        del user_states[user_id]
        await procesar_y_responder(update, cedula, sector)
        return
        
    await update.message.reply_text(
        "Por favor indícame el número de cédula (10 dígitos).\n"
        "Ejemplo: `1708927502 Sector El Recreo`",
        parse_mode="Markdown"
    )

async def procesar_y_responder(update: Update, cedula: str, sector: str):
    msg_espera = await update.message.reply_text(
        f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"📍 Sector: `{sector}`\n\n"
        f"*Esto toma aproximadamente 13 segundos...*",
        parse_mode="Markdown"
    )
    
    t0 = time.time()
    success, result, _ = await procesar_denuncia_judicial(cedula, sector)
    elapsed = time.time() - t0
    
    if success:
        pdf_bytes = result["pdf_bytes"]
        nombre = result["nombre"]
        sector_fmt = result["sector"]
        
        caption = (
            f"✅ **¡DENUNCIA GENERADA CON ÉXITO!** ({elapsed:.1f}s)\n\n"
            f"👤 **Nombre:** {nombre}\n"
            f"🆔 **Cédula:** `{cedula}`\n"
            f"📍 **Lugar:** {sector_fmt}\n\n"
            f"📄 *Documento oficial emitido por el Consejo de la Judicatura (válido para trámites legales y Registro Civil).* "
        )
        
        pdf_file = io.BytesIO(pdf_bytes)
        pdf_file.name = f"Denuncia_{cedula}.pdf"
        
        await update.message.reply_document(
            document=InputFile(pdf_file, filename=f"Denuncia_{cedula}.pdf"),
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await msg_espera.delete()
        except Exception:
            pass
    else:
        error_msg = result if isinstance(result, str) else "Error al generar la denuncia."
        await update.message.reply_text(
            f"❌ **No se pudo completar la emisión:**\n{error_msg}\n\n"
            f"Por favor reenvía los datos para reintentar.",
            parse_mode="Markdown"
        )

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    
    if not token:
        token_file = os.path.join(os.path.dirname(__file__), "telegram_token.txt")
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                
    if not token:
        print("ERROR: No se encontró el token de Telegram.")
        return

    print("====================================================================", flush=True)
    print("           🤖 BOT DE TELEGRAM DE DENUNCIAS INICIADO (LINUX)", flush=True)
    print("====================================================================", flush=True)
    print("El bot está escuchando mensajes en tiempo real 24/7.", flush=True)
    print("Tu mamá puede abrir Telegram y enviar la cédula para recibir el PDF.", flush=True)
    print("====================================================================", flush=True)
    
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
