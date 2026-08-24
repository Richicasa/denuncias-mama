import asyncio
import base64
import datetime
import io
import os
import re
import uuid
from typing import Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

try:
    import pytesseract
except ImportError:
    pytesseract = None

app = FastAPI(title="Denuncias de Extravío de Documentos - 100% Automático")

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
    print("Motor de navegación iniciado exitosamente.")

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

def solve_captcha_image(image_bytes: bytes) -> str:
    """Resuelve automáticamente el captcha de texto blanco sobre fondo gris."""
    if not pytesseract:
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
        threshold = 180
        img = img.point(lambda p: 0 if p > threshold else 255)
        
        text = pytesseract.image_to_string(
            img,
            config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        ).strip()
        cleaned = re.sub(r'[^A-Za-z0-9]', '', text)
        print(f"[OCR] Captcha detectado: '{cleaned}'")
        return cleaned
    except Exception as e:
        print(f"[OCR Error] {e}")
        return ""

class AutoDenunciaRequest(BaseModel):
    cedula: str
    sector: str

class SubmitManualCaptchaRequest(BaseModel):
    session_id: str
    captcha_text: str

@app.post("/api/generar-denuncia-auto")
async def generar_denuncia_auto(req: AutoDenunciaRequest):
    cedula = req.cedula.strip()
    sector = req.sector.strip()
    
    if not cedula or len(cedula) < 10:
        raise HTTPException(status_code=400, detail="Número de cédula inválido (10 dígitos)")
    if not sector:
        raise HTTPException(status_code=400, detail="Debe indicar el sector o lugar del extravío")
        
    session_id = str(uuid.uuid4())
    context = await browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="es-EC",
        accept_downloads=True
    )
    page = await context.new_page()
    
    try:
        print(f"Iniciando trámite para cédula {cedula} en {sector}...")
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        # 1. Cédula
        await page.fill("#numeroIdentificacion", cedula)
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(2500)
        
        nombre = await page.input_value("#nombreCompleto")
        if not nombre:
            nombre = "CIUDADANO REGISTRADO"
            
        # 2. Domicilio (Pichincha -> Quito -> Sector de [Sector])
        await page.select_option("#provinciaDomicilio", value="17")
        await page.wait_for_timeout(1500)
        await page.select_option("#cantonDomicilio", value="185")
        await page.fill("#direccionDomicilio", f"Sector de {sector}")
        
        # 3. Extravío (Pichincha -> Quito -> Documento extraviado en el sector de [Sector])
        await page.select_option("#provinciaExtravio", value="17")
        await page.wait_for_timeout(1500)
        await page.select_option("#cantonExtravio", value="185")
        await page.fill("#direccionCircunstancia", f"Documento extraviado en el sector de {sector}")
        
        # 4. Fecha hábil anterior
        b_day = get_last_business_day()
        formatted_date = format_date_for_input(b_day)
        await page.evaluate(f"""
            const d = new Date({b_day.year}, {b_day.month - 1}, {b_day.day});
            if (window.RichFaces && RichFaces.$('fecha')) {{
                RichFaces.$('fecha').setValue(d);
            }}
            const input = document.getElementById('fechaInputDate');
            if (input) input.value = '{formatted_date}';
        """)
        
        # 5. Agregar documento Cédula
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
        
        # 6. Intentar resolver el Captcha automáticamente con verificación
        max_attempts = 4
        solved_successfully = False
        last_captcha_b64 = ""
        
        for attempt in range(max_attempts):
            captcha_el = await page.query_selector("#imgCaptchaId")
            if not captcha_el:
                break
                
            captcha_bytes = await captcha_el.screenshot()
            last_captcha_b64 = base64.b64encode(captcha_bytes).decode("utf-8")
            
            auto_code = solve_captcha_image(captcha_bytes)
            if not auto_code or len(auto_code) < 4:
                print(f"[Intento {attempt+1}] Captcha dudoso, refrescando imagen...")
                await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
                await page.wait_for_timeout(2000)
                continue
                
            print(f"[Intento {attempt+1}] Probando código OCR: '{auto_code}'")
            await page.fill("#captchaTxt", auto_code)
            await page.click("#j_idt170") # Aceptar
            await page.wait_for_timeout(3000)
            
            # Comprobar si apareció la ventana modal de confirmación
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
                print(f"[Intento {attempt+1}] ¡Captcha validado correctamente por la Judicatura!")
                solved_successfully = True
                break
            else:
                print(f"[Intento {attempt+1}] Captcha no válido, refrescando para reintentar...")
                await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
                await page.wait_for_timeout(2000)
                
        # 7. Si se resolvió con éxito:
        if solved_successfully:
            # Confirmar registro (clic en 'Si')
            print("Confirmando formulario en modal de la Judicatura...")
            await page.evaluate("""
                if (window.si) {
                    window.si();
                } else {
                    const siBtn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]');
                    if (siBtn) siBtn.click();
                }
            """)
            await page.wait_for_timeout(3500)
            
            # Descargar PDF directamente
            pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{cedula}_{uuid.uuid4().hex[:6]}.pdf")
            print("Descargando PDF oficial...")
            
            async with page.expect_download(timeout=45000) as download_info:
                await page.evaluate("""
                    const btn = document.querySelector('input[value="Ver formulario"]') || 
                                document.querySelector('input[name*="j_idt242"]') ||
                                document.querySelector('input[name*="j_idt252"]');
                    if (btn) {
                        btn.click();
                    } else {
                        const form = document.getElementById('frmPopups');
                        if (form) form.submit();
                    }
                """)
            download = await download_info.value
            await download.save_as(pdf_path)
            print(f"PDF guardado exitosamente: {pdf_path}")
            
            sessions[session_id] = {
                "pdf_path": pdf_path,
                "cedula": cedula,
                "nombre": nombre
            }
            await context.close()
            
            return {
                "success": True,
                "downloadUrl": f"/api/download-pdf/{session_id}",
                "cedula": cedula,
                "nombre": nombre
            }
            
        # Fallback manual en caso de que los 4 intentos automáticos no pasen
        sessions[session_id] = {
            "context": context,
            "page": page,
            "cedula": cedula,
            "sector": sector,
            "nombre": nombre,
            "fecha": formatted_date
        }
        
        return {
            "success": False,
            "requireManualCaptcha": True,
            "sessionId": session_id,
            "nombre": nombre,
            "fecha": formatted_date,
            "sector": sector,
            "captchaImage": f"data:image/jpeg;base64,{last_captcha_b64}"
        }
        
    except Exception as e:
        await context.close()
        print(f"Error procesando: {e}")
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
        await page.click("#j_idt170")
        await page.wait_for_timeout(3000)
        
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
                const siBtn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]');
                if (siBtn) siBtn.click();
            }
        """)
        await page.wait_for_timeout(3500)
        
        pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{session['cedula']}_{uuid.uuid4().hex[:6]}.pdf")
        async with page.expect_download(timeout=45000) as download_info:
            await page.evaluate("""
                const btn = document.querySelector('input[value="Ver formulario"]') || 
                            document.querySelector('input[name*="j_idt242"]') ||
                            document.querySelector('input[name*="j_idt252"]');
                if (btn) {
                    btn.click();
                } else {
                    const form = document.getElementById('frmPopups');
                    if (form) form.submit();
                }
            """)
        download = await download_info.value
        await download.save_as(pdf_path)
        
        session["pdf_path"] = pdf_path
        await context.close()
        
        return {
            "success": True,
            "downloadUrl": f"/api/download-pdf/{req.session_id}",
            "cedula": session["cedula"],
            "nombre": session["nombre"]
        }
    except Exception as e:
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

@app.get("/")
async def index():
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
