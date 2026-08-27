import asyncio
import base64
import datetime
import io
import os
import re
import time
import uuid
from typing import Dict, Optional
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from text_cleaner import limpiar_y_corregir_sector

try:
    from telegram import Update, InputFile
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters
    )
    has_telegram = True
except ImportError:
    has_telegram = False

try:
    import winocr
except ImportError:
    winocr = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

app = FastAPI(title="Denuncias de Extravío de Documentos - 100% Real")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: Dict[str, dict] = {}
user_telegram_states: Dict[int, dict] = {}
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

CONCURRENCY_LIMIT = 3
concurrency_semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

playwright_instance = None
browser_instance: Browser = None
telegram_application = None

TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8841299245:AAHqhY1cCFDqAE0V_Np89h1ORPKb3TqGBbI"
)

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

async def solve_captcha_image(image_bytes: bytes) -> str:
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

def cleanup_old_downloads():
    try:
        now = time.time()
        for f in os.listdir(DOWNLOADS_DIR):
            f_path = os.path.join(DOWNLOADS_DIR, f)
            if os.path.isfile(f_path) and (now - os.path.getmtime(f_path)) > 3600:
                try:
                    os.remove(f_path)
                except Exception:
                    pass
    except Exception:
        pass

async def ejecutar_denuncia_directa(cedula: str, raw_sector: str):
    sector_info = limpiar_y_corregir_sector(raw_sector)
    sector_limpio = sector_info["sector_limpio"]
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    
    async with concurrency_semaphore:
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC"
        )
        page = await context.new_page()
        captured_real_pdf = None
        
        async def route_interceptor(route, request):
            nonlocal captured_real_pdf
            try:
                response = await route.fetch()
                ct = response.headers.get("content-type", "").lower()
                if "application/pdf" in ct or "pdf" in ct:
                    body = await response.body()
                    if body.startswith(b"%PDF"):
                        captured_real_pdf = body
                await route.fulfill(response=response)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
                    
        await page.route("**/formulario.jsf*", route_interceptor)
        
        try:
            await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
            await page.wait_for_load_state("networkidle")
            
            # Pre-validación Captcha
            valid_code = ""
            for _ in range(12):
                c_el = await page.query_selector("#imgCaptchaId")
                if not c_el:
                    break
                c_bytes = await c_el.screenshot()
                c_text = await solve_captcha_image(c_bytes)
                if len(c_text) == 6:
                    valid_code = c_text
                    break
                await page.reload()
                await page.wait_for_load_state("networkidle")
                
            if not valid_code:
                await context.close()
                return False, "No se pudo leer un captcha nítido de la Judicatura.", None
                
            # Cédula
            await page.fill("#numeroIdentificacion", cedula)
            await page.locator("#numeroIdentificacion").blur()
            await page.wait_for_timeout(1800)
            
            nombre = await page.input_value("#nombreCompleto")
            if not nombre:
                nombre = "CIUDADANO REGISTRADO"
                
            # Domicilio
            await page.select_option("#provinciaDomicilio", value="17")
            try:
                await page.wait_for_function("document.querySelectorAll('#cantonDomicilio option').length > 1", timeout=8000)
            except Exception:
                await page.wait_for_timeout(1500)
            await page.select_option("#cantonDomicilio", label="QUITO")
            await page.fill("#direccionDomicilio", dir_domicilio)
            
            # Extravío
            await page.select_option("#provinciaExtravio", value="17")
            try:
                await page.wait_for_function("document.querySelectorAll('#cantonExtravio option').length > 1", timeout=8000)
            except Exception:
                await page.wait_for_timeout(1500)
            await page.select_option("#cantonExtravio", label="QUITO")
            await page.fill("#direccionCircunstancia", dir_circunstancia)
            
            # Fecha hábil
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
            
            # Agregar documento
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
            
            # Captcha
            await page.fill("#captchaTxt", valid_code)
            await page.evaluate("""
                const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
                if (btn) btn.click();
            """)
            await page.wait_for_timeout(2500)
            
            # Confirmar "Si"
            await page.evaluate("""
                if (window.si) {
                    window.si();
                } else {
                    const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
                    if (btn) btn.click();
                }
            """)
            
            # Clic reactivo en 'Ver formulario'
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
            
            # Esperar PDF
            for _ in range(18):
                if captured_real_pdf and captured_real_pdf.startswith(b"%PDF"):
                    break
                await asyncio.sleep(1)
                
            await context.close()
            
            if captured_real_pdf and captured_real_pdf.startswith(b"%PDF") and len(captured_real_pdf) > 20000:
                session_id = str(uuid.uuid4())
                pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{cedula}_{session_id[:6]}.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(captured_real_pdf)
                    
                sessions[session_id] = {
                    "pdf_path": pdf_path,
                    "cedula": cedula,
                    "nombre": nombre,
                    "sector_limpio": sector_limpio
                }
                
                return True, {
                    "session_id": session_id,
                    "nombre": nombre,
                    "cedula": cedula,
                    "sector": sector_limpio,
                    "pdf_bytes": captured_real_pdf
                }, None
            else:
                return False, "La Judicatura tardó en entregar el flujo PDF.", None
                
        except Exception as e:
            try:
                await context.close()
            except Exception:
                pass
            return False, str(e), None

# --- TELEGRAM BOT HANDLERS ---
async def tg_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **¡Hola! Soy tu Asistente para Denuncias Judiciales de Extravío.**\n\n"
        "Puedo generar tu denuncia oficial del Consejo de la Judicatura y enviarte el PDF listo para imprimir.\n\n"
        "📝 **Para comenzar, envíame:**\n"
        "• El número de **Cédula** (10 dígitos)\n"
        "• El **Sector o Lugar** del extravío (ej: *El Recreo*, *Quitumbe*, *Centro Histórico*)\n\n"
        "💡 *Ejemplo:* `1708927502 Sector El Recreo`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def tg_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    cedula_match = re.search(r'\b\d{10}\b', text)
    
    if cedula_match:
        cedula = cedula_match.group(0)
        sector_candidate = text.replace(cedula, "").strip()
        sector_candidate = re.sub(r'^(en el|en|sector|el|la)\s+', '', sector_candidate, flags=re.IGNORECASE).strip()
        
        if sector_candidate and len(sector_candidate) > 2:
            await tg_procesar_y_responder(update, cedula, sector_candidate)
        else:
            user_telegram_states[user_id] = {"cedula": cedula}
            await update.message.reply_text(
                f"✅ Cédula recibida: `{cedula}`\n\n"
                "📍 ¿En qué **sector o lugar** ocurrió el extravío?\n"
                "*(Ejemplo: El Recreo, Chillogallo, La Mariscal, Centro Histórico...)*",
                parse_mode="Markdown"
            )
        return
        
    if user_id in user_telegram_states and "cedula" in user_telegram_states[user_id]:
        cedula = user_telegram_states[user_id]["cedula"]
        sector = text
        del user_telegram_states[user_id]
        await tg_procesar_y_responder(update, cedula, sector)
        return
        
    await update.message.reply_text(
        "Por favor indícame el número de cédula (10 dígitos).\n"
        "Ejemplo: `1708927502 Sector El Recreo`",
        parse_mode="Markdown"
    )

async def tg_procesar_y_responder(update: Update, cedula: str, sector: str):
    msg_espera = await update.message.reply_text(
        f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"📍 Sector: `{sector}`\n\n"
        f"*Esto toma aproximadamente 13 segundos...*",
        parse_mode="Markdown"
    )
    
    t0 = time.time()
    success, result, _ = await ejecutar_denuncia_directa(cedula, sector)
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

# --- FASTAPI LIFECYCLE ---
@app.on_event("startup")
async def startup_event():
    global playwright_instance, browser_instance, telegram_application
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    )
    print("🚀 Motor de navegación para denuncias judiciales iniciado.", flush=True)
    
    if has_telegram and TELEGRAM_TOKEN:
        try:
            telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
            telegram_application.add_handler(CommandHandler("start", tg_start_command))
            telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg_handle_message))
            await telegram_application.initialize()
            await telegram_application.start()
            await telegram_application.updater.start_polling()
            print(f"🤖 Bot de Telegram conectado y activo 24/7 en la nube (@denuncias_mama_bot)", flush=True)
        except Exception as e:
            print(f"Error iniciando bot de Telegram: {e}", flush=True)

@app.on_event("shutdown")
async def shutdown_event():
    global playwright_instance, browser_instance, telegram_application
    if telegram_application:
        try:
            await telegram_application.updater.stop()
            await telegram_application.stop()
            await telegram_application.shutdown()
        except Exception:
            pass
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()

class AutoDenunciaRequest(BaseModel):
    cedula: str
    sector: str

class PreviewSectorRequest(BaseModel):
    sector: str

@app.api_route("/health", methods=["GET", "HEAD", "POST", "OPTIONS"])
@app.api_route("/ping", methods=["GET", "HEAD", "POST", "OPTIONS"])
async def ping(request: Request):
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "uptime": "active", "time": datetime.datetime.now().isoformat()}
    )

@app.post("/api/preview-sector")
async def preview_sector(req: PreviewSectorRequest):
    return limpiar_y_corregir_sector(req.sector)

@app.post("/api/generar-denuncia-auto")
async def generar_denuncia_auto(req: AutoDenunciaRequest):
    cedula = req.cedula.strip()
    raw_sector = req.sector.strip()
    
    if not cedula or len(cedula) < 10:
        raise HTTPException(status_code=400, detail="Número de cédula inválido (10 dígitos)")
    if not raw_sector:
        raise HTTPException(status_code=400, detail="Debe indicar el sector o lugar del extravío")
        
    cleanup_old_downloads()
    success, result, _ = await ejecutar_denuncia_directa(cedula, raw_sector)
    
    if success:
        return {
            "success": True,
            "downloadUrl": f"/api/download-pdf/{result['session_id']}",
            "cedula": result["cedula"],
            "nombre": result["nombre"],
            "sectorCorregido": result["sector"]
        }
    else:
        raise HTTPException(status_code=502, detail=result)

@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str):
    session = sessions.get(session_id)
    if not session or "pdf_path" not in session:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    
    file_path = session["pdf_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="El archivo PDF ha expirado.")
        
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=f"Denuncia_{session['cedula']}.pdf"
    )

@app.api_route("/", methods=["GET", "HEAD", "OPTIONS"])
async def index(request: Request):
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
