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
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

CONCURRENCY_LIMIT = 3
concurrency_semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

playwright_instance = None
browser_instance: Browser = None

@app.on_event("startup")
async def startup_event():
    global playwright_instance, browser_instance
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    )
    print("🚀 Motor de navegación para denuncias judiciales iniciado y listo.", flush=True)

@app.on_event("shutdown")
async def shutdown_event():
    global playwright_instance, browser_instance
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()

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
                return False, "No se pudo leer el captcha de la Judicatura.", None
                
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
                return False, "La Judicatura no entregó el flujo binario del PDF.", None
                
        except Exception as e:
            try:
                await context.close()
            except Exception:
                pass
            return False, str(e), None

class AutoDenunciaRequest(BaseModel):
    cedula: str
    sector: str

class PreviewSectorRequest(BaseModel):
    sector: str

class SubmitManualCaptchaRequest(BaseModel):
    session_id: str
    captcha_text: str

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

@app.api_route("/api/whatsapp-webhook", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request, From: Optional[str] = Form(None), Body: Optional[str] = Form(None)):
    """Webhook universal para WhatsApp (Twilio / Meta / Green-API)"""
    cleanup_old_downloads()
    
    text_content = ""
    if Body:
        text_content = Body.strip()
    else:
        try:
            json_data = await request.json()
            # Green-API / Meta format
            text_content = (
                json_data.get("messageData", {}).get("textMessageData", {}).get("textMessage", "") or
                json_data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [{}])[0].get("text", {}).get("body", "") or
                json_data.get("body", "")
            ).strip()
        except Exception:
            pass
            
    if not text_content:
        twiml = "<Response><Message><Body>👋 ¡Hola! Por favor envíame el número de cédula y el sector para generar la denuncia oficial.\nEjemplo: 1708927502 Sector El Recreo</Body></Message></Response>"
        return Response(content=twiml, media_type="application/xml")
        
    cedula_match = re.search(r'\b\d{10}\b', text_content)
    if not cedula_match:
        twiml = "<Response><Message><Body>⚠️ Por favor incluye un número de cédula válido de 10 dígitos.\nEjemplo: 1708927502 Sector El Recreo</Body></Message></Response>"
        return Response(content=twiml, media_type="application/xml")
        
    cedula = cedula_match.group(0)
    sector_raw = text_content.replace(cedula, "").strip()
    sector_raw = re.sub(r'^(en el|en|sector|el|la)\s+', '', sector_raw, flags=re.IGNORECASE).strip()
    if not sector_raw or len(sector_raw) < 2:
        sector_raw = "Sector Centro"
        
    success, result, _ = await ejecutar_denuncia_directa(cedula, sector_raw)
    
    if success:
        session_id = result["session_id"]
        nombre = result["nombre"]
        sector_fmt = result["sector"]
        pdf_download_url = f"https://denuncias-mama.onrender.com/api/download-pdf/{session_id}"
        
        twiml = f"""<Response>
    <Message>
        <Body>✅ *¡DENUNCIA GENERADA CON ÉXITO!*
👤 *Nombre:* {nombre}
🆔 *Cédula:* {cedula}
📍 *Lugar:* {sector_fmt}

📄 *Descarga tu PDF Oficial:*
{pdf_download_url}</Body>
        <Media>{pdf_download_url}</Media>
    </Message>
</Response>"""
        return Response(content=twiml, media_type="application/xml")
    else:
        twiml = f"""<Response>
    <Message>
        <Body>❌ *No se pudo emitir la denuncia:* {result}
Por favor intenta de nuevo en unos momentos.</Body>
    </Message>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

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
