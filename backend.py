import asyncio
import base64
import datetime
import io
import os
import re
import time
import uuid
from typing import Dict
from fastapi import FastAPI, HTTPException, Request
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
    print("🚀 Motor de navegación para denuncias judiciales iniciado y listo.")

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
    if today.weekday() == 0:    # Lunes -> Viernes (-3 días)
        delta = 3
    elif today.weekday() == 6:  # Domingo -> Viernes (-2 días)
        delta = 2
    elif today.weekday() == 5:  # Sábado -> Viernes (-1 día)
        delta = 1
    else:                       # Martes a Viernes -> Ayer (-1 día)
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
    
    sector_info = limpiar_y_corregir_sector(raw_sector)
    sector_limpio = sector_info["sector_limpio"]
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    
    session_id = str(uuid.uuid4())
    
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
                        print(f"[Captura PDF] Archivo PDF oficial recibido ({len(body)} bytes)")
                await route.fulfill(response=response)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
            
        await page.route("**/formulario.jsf*", route_interceptor)
        
        try:
            print(f"Cargando portal judicial para cédula {cedula}...")
            await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
            await page.wait_for_load_state("networkidle")
            
            # 1. Cédula y consulta a Registro Civil
            await page.fill("#numeroIdentificacion", cedula)
            await page.locator("#numeroIdentificacion").blur()
            await page.wait_for_timeout(1800)
            
            nombre = await page.input_value("#nombreCompleto")
            if not nombre:
                nombre = "CIUDADANO REGISTRADO"
                
            # 2. Domicilio (Pichincha -> Quito)
            await page.select_option("#provinciaDomicilio", value="17")
            try:
                await page.wait_for_function("document.querySelectorAll('#cantonDomicilio option').length > 1", timeout=8000)
            except Exception:
                await page.wait_for_timeout(1500)
            await page.select_option("#cantonDomicilio", label="QUITO")
            await page.fill("#direccionDomicilio", dir_domicilio)
            
            # 3. Extravío (Pichincha -> Quito)
            await page.select_option("#provinciaExtravio", value="17")
            try:
                await page.wait_for_function("document.querySelectorAll('#cantonExtravio option').length > 1", timeout=8000)
            except Exception:
                await page.wait_for_timeout(1500)
            await page.select_option("#cantonExtravio", label="QUITO")
            await page.fill("#direccionCircunstancia", dir_circunstancia)
            
            # 4. Fecha hábil anterior
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
            
            # 5. Agregar documento Cédula a la tabla oficial de la Judicatura
            print("Registrando documento extraviado en la tabla oficial...")
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
            
            # Limpieza de sombra
            await page.evaluate("""
                if (window.RichFaces && RichFaces.$('frmPopups:createPane')) {
                    RichFaces.$('frmPopups:createPane').hide();
                }
                const shade = document.getElementById('frmPopups:createPane_shade');
                if (shade) shade.remove();
            """)
            await page.wait_for_timeout(400)
            
            # 6. Intentar resolver el Captcha automáticamente con reintentos limpios
            max_attempts = 10
            solved_successfully = False
            last_captcha_b64 = ""
            
            for attempt in range(max_attempts):
                captcha_el = await page.query_selector("#imgCaptchaId")
                if not captcha_el:
                    break
                    
                captcha_bytes = await captcha_el.screenshot()
                last_captcha_b64 = base64.b64encode(captcha_bytes).decode("utf-8")
                
                auto_code = await solve_captcha_image(captcha_bytes)
                if not auto_code or len(auto_code) != 6:
                    print(f"[Intento {attempt+1}] Captcha dudoso o incompleto, refrescando...")
                    await page.evaluate("""
                        const ref = document.querySelector('img[src*="refresh"]') || document.querySelector('a[id*="j_idt"]');
                        if (ref) ref.click();
                        else document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();
                    """)
                    await page.wait_for_timeout(1200)
                    continue
                    
                print(f"[Intento {attempt+1}] Enviando código OCR: '{auto_code}'")
                await page.fill("#captchaTxt", auto_code)
                
                await page.evaluate("""
                    const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
                    if (btn) btn.click();
                """)
                await page.wait_for_timeout(2500)
                
                confirm_modal_visible = await page.evaluate("""
                    (function() {
                        const modal = document.getElementById('frmPopups:confirmForm');
                        const container = document.getElementById('frmPopups:confirmForm_container');
                        if (modal && modal.style.visibility !== 'hidden' && modal.style.display !== 'none') return true;
                        if (container && container.style.visibility !== 'hidden' && container.style.display !== 'none') return true;
                        return false;
                    })()
                """)
                
                if confirm_modal_visible:
                    print(f"[Intento {attempt+1}] ¡Captcha verificado exitosamente!")
                    solved_successfully = True
                    break
                else:
                    print(f"[Intento {attempt+1}] Captcha no aceptado, refrescando...")
                    await page.fill("#captchaTxt", "")
                    await page.evaluate("""
                        const ref = document.querySelector('img[src*="refresh"]') || document.querySelector('a[id*="j_idt"]');
                        if (ref) ref.click();
                        else document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();
                    """)
                    await page.wait_for_timeout(1200)
                    
            # 7. Confirmar en la Judicatura y descargar el PDF REAL
            if solved_successfully:
                print("Confirmando denuncia de forma real en la Judicatura (clic en Si)...")
                await page.evaluate("""
                    if (window.si) {
                        window.si();
                    } else {
                        const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
                        if (btn) btn.click();
                    }
                """)
                
                print("Esperando reactivamente el montaje de 'Ver formulario'...")
                # Sondeo reactivo cada 250ms que hace clic el instante exacto en que monta el botón
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
                
                # Esperar pasivamente la llegada del PDF real
                for _ in range(18):
                    if captured_real_pdf and captured_real_pdf.startswith(b"%PDF"):
                        break
                    await asyncio.sleep(1)
                
                if not captured_real_pdf or not captured_real_pdf.startswith(b"%PDF"):
                    raise HTTPException(
                        status_code=502,
                        detail="El servidor de la Judicatura no entregó el PDF a tiempo. Por favor reintente en unos segundos."
                    )
                    
                pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{cedula}_{uuid.uuid4().hex[:6]}.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(captured_real_pdf)
                    
                print(f"[Exito] Archivo PDF autentico guardado: {pdf_path} ({len(captured_real_pdf)} bytes)")
                
                sessions[session_id] = {
                    "pdf_path": pdf_path,
                    "cedula": cedula,
                    "nombre": nombre,
                    "sector_limpio": sector_limpio,
                    "dir_domicilio": dir_domicilio,
                    "dir_circunstancia": dir_circunstancia
                }
                await context.close()
                
                return {
                    "success": True,
                    "downloadUrl": f"/api/download-pdf/{session_id}",
                    "cedula": cedula,
                    "nombre": nombre,
                    "sectorCorregido": sector_limpio,
                    "circunstancia": dir_circunstancia
                }
                
            sessions[session_id] = {
                "context": context,
                "page": page,
                "cedula": cedula,
                "sector": sector_limpio,
                "nombre": nombre,
                "fecha": formatted_date,
                "get_pdf_ref": lambda: captured_real_pdf
            }
            
            return {
                "success": False,
                "requireManualCaptcha": True,
                "sessionId": session_id,
                "nombre": nombre,
                "fecha": formatted_date,
                "sector": sector_limpio,
                "captchaImage": f"data:image/jpeg;base64,{last_captcha_b64}"
            }
            
        except Exception as e:
            await context.close()
            print(f"Error procesando denuncia: {e}")
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/submit-captcha-manual")
async def submit_captcha_manual(req: SubmitManualCaptchaRequest):
    session = sessions.get(req.session_id)
    if not session or "page" not in session:
        raise HTTPException(status_code=404, detail="Sesión expirada.")
    
    page: Page = session["page"]
    context: BrowserContext = session["context"]
    captcha_text = req.captcha_text.strip()
    
    try:
        await page.fill("#captchaTxt", captcha_text)
        await page.evaluate("""
            const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(2500)
        
        confirm_modal_visible = await page.evaluate("""
            (function() {
                const modal = document.getElementById('frmPopups:confirmForm');
                const container = document.getElementById('frmPopups:confirmForm_container');
                if (modal && modal.style.visibility !== 'hidden' && modal.style.display !== 'none') return true;
                if (container && container.style.visibility !== 'hidden' && container.style.display !== 'none') return true;
                return false;
            })()
        """)
        
        if not confirm_modal_visible:
            captcha_el = await page.query_selector("#imgCaptchaId")
            new_b64 = ""
            if captcha_el:
                c_bytes = await captcha_el.screenshot()
                new_b64 = f"data:image/jpeg;base64,{base64.b64encode(c_bytes).decode('utf-8')}"
            return {
                "success": False,
                "error": "Código incorrecto. Intente nuevamente con el código de la imagen.",
                "newCaptcha": new_b64
            }
            
        await page.evaluate("""
            if (window.si) {
                window.si();
            } else {
                const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
                if (btn) btn.click();
            }
        """)
        
        # Sondeo reactivo también en el flujo manual
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
        
        captured_pdf = None
        for _ in range(18):
            captured_pdf = session["get_pdf_ref"]()
            if captured_pdf and captured_pdf.startswith(b"%PDF"):
                break
            await asyncio.sleep(1)
            
        if not captured_pdf or not captured_pdf.startswith(b"%PDF"):
            raise HTTPException(status_code=502, detail="El servidor de la Judicatura tardó en entregar el PDF.")
            
        pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{session['cedula']}_{uuid.uuid4().hex[:6]}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(captured_pdf)
            
        session["pdf_path"] = pdf_path
        await context.close()
        
        return {
            "success": True,
            "downloadUrl": f"/api/download-pdf/{req.session_id}",
            "cedula": session["cedula"],
            "nombre": session["nombre"]
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

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
