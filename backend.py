import asyncio
import base64
import datetime
import io
import os
import re
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
    print("🚀 Motor de navegación iniciado y listo.")

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
    """
    Resuelve el captcha con algoritmo adaptativo multi-umbral de alta precisión.
    """
    if not pytesseract:
        return ""
    try:
        img_orig = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = img_orig.size
        scaled = img_orig.resize((w * 4, h * 4), Image.Resampling.LANCZOS)
        
        umbrales = [175, 160, 190, 150, 200]
        for th in umbrales:
            bin_img = scaled.point(lambda p: 0 if p > th else 255)
            text = pytesseract.image_to_string(
                bin_img,
                config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
            ).strip()
            cleaned = re.sub(r'[^A-Za-z0-9]', '', text)
            if len(cleaned) >= 5 and len(cleaned) <= 6:
                print(f"[OCR Alta Precisión] Código detectado ({len(cleaned)} chars): '{cleaned}' con umbral {th}")
                return cleaned
                
        bin_img = scaled.point(lambda p: 0 if p > 175 else 255)
        text = pytesseract.image_to_string(
            bin_img,
            config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        ).strip()
        cleaned = re.sub(r'[^A-Za-z0-9]', '', text)
        print(f"[OCR Fallback] Código detectado: '{cleaned}'")
        return cleaned
    except Exception as e:
        print(f"[OCR Error] {e}")
        return ""

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
        
    sector_info = limpiar_y_corregir_sector(raw_sector)
    sector_limpio = sector_info["sector_limpio"]
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    
    session_id = str(uuid.uuid4())
    context = await browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="es-EC",
        accept_downloads=True
    )
    page = await context.new_page()
    
    try:
        print(f"Cargando portal judicial para cédula {cedula}...")
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        # 1. Cédula y consulta a Registro Civil
        await page.fill("#numeroIdentificacion", cedula)
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(2000)
        
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
        
        # 4. Fecha hábil anterior con sincronización completa
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
        await page.wait_for_timeout(500)
        
        # 5. Agregar documento Cédula con eventos JSF garantizados
        print("Agregando documento extraviado en la tabla...")
        add_btn = page.locator('input[value="+ Agregar un nuevo documento"]')
        await add_btn.click(force=True)
        await page.wait_for_timeout(1500)
        
        # Seleccionar Tipo 7 (Cédula) y emitir evento change
        await page.select_option("#frmPopups\\:tipoDocumentoExtraviadoNewSelect", value="7")
        await page.evaluate("""
            const sel = document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect');
            if (sel) sel.dispatchEvent(new Event('change', { bubbles: true }));
        """)
        await page.wait_for_timeout(800)
        
        # Llenar número de cédula y emitir change/blur
        await page.fill("#frmPopups\\:numeroNew", cedula)
        await page.evaluate("""
            const num = document.getElementById('frmPopups:numeroNew');
            if (num) {
                num.dispatchEvent(new Event('change', { bubbles: true }));
                num.dispatchEvent(new Event('blur', { bubbles: true }));
            }
        """)
        await page.wait_for_timeout(500)
        
        # Llenar descripción y emitir change
        await page.fill("#frmPopups\\:descripcionNew", "cédula de identidad")
        await page.evaluate("""
            const desc = document.getElementById('frmPopups:descripcionNew');
            if (desc) desc.dispatchEvent(new Event('change', { bubbles: true }));
        """)
        await page.wait_for_timeout(500)
        
        # Clic en Aceptar dentro del popup
        accept_doc_btn = page.locator('#frmPopups\\:createPane input[value="Aceptar"]')
        await accept_doc_btn.click(force=True)
        
        # Esperar a que la fila aparezca en la tabla de documentos
        try:
            await page.wait_for_function(f"document.body.innerText.includes('{cedula}')", timeout=6000)
            print("✔ Documento confirmado en la tabla oficial.")
        except Exception:
            await page.wait_for_timeout(2500)
            
        # 6. Intentar resolver el Captcha automáticamente (hasta 5 intentos)
        max_attempts = 5
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
                print(f"[Intento {attempt+1}] Captcha dudoso, refrescando...")
                await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
                await page.wait_for_timeout(1500)
                continue
                
            print(f"[Intento {attempt+1}] Enviando código OCR: '{auto_code}'")
            await page.fill("#captchaTxt", auto_code)
            
            await page.evaluate("""
                const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
                if (btn) btn.click();
            """)
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
            
            if confirm_modal_visible:
                print(f"[Intento {attempt+1}] ¡Captcha verificado exitosamente!")
                solved_successfully = True
                break
            else:
                print(f"[Intento {attempt+1}] Captcha no válido para la Judicatura, refrescando...")
                await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
                await page.wait_for_timeout(1500)
                
        # 7. Confirmar y Descargar PDF con motor híbrido ultra seguro
        if solved_successfully:
            print("Confirmando modal de registro (clic en Si)...")
            await page.evaluate("""
                if (window.si) {
                    window.si();
                } else {
                    const siBtn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]');
                    if (siBtn) siBtn.click();
                }
            """)
            await page.wait_for_timeout(3000)
            
            pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{cedula}_{uuid.uuid4().hex[:6]}.pdf")
            print("Iniciando captura de PDF oficial...")
            
            download_saved = False
            try:
                async with page.expect_download(timeout=8000) as download_info:
                    await page.evaluate("""
                        if (window.RichFaces && RichFaces.$('frmPopups:pdfPane1')) {
                            RichFaces.$('frmPopups:pdfPane1').show();
                        }
                        const btn = document.querySelector('input[value="Ver formulario"]') || 
                                    document.querySelector('input[name*="j_idt242"]') ||
                                    document.querySelector('input[name*="j_idt252"]');
                        if (btn) btn.click();
                    """)
                download = await download_info.value
                await download.save_as(pdf_path)
                download_saved = True
                print(f"✔ PDF guardado vía evento Download: {pdf_path}")
            except Exception:
                pass
                
            if not download_saved or not os.path.exists(pdf_path):
                print("Capturando PDF directamente desde el visor embebido de la Judicatura...")
                pdf_url = await page.evaluate("""
                    (function() {
                        const obj = document.querySelector('object[type*="pdf"]') || document.querySelector('#frmPopups\\\\:pdfPane2 object');
                        if (obj && (obj.data || obj.getAttribute('data'))) return obj.data || obj.getAttribute('data');
                        const embed = document.querySelector('embed[type*="pdf"]') || document.querySelector('#frmPopups\\\\:pdfPane2 embed');
                        if (embed && (embed.src || embed.getAttribute('src'))) return embed.src || embed.getAttribute('src');
                        const iframe = document.querySelector('#frmPopups\\\\:pdfPane2 iframe') || document.querySelector('iframe[src*="pdf"]');
                        if (iframe && (iframe.src || iframe.getAttribute('src'))) return iframe.src || iframe.getAttribute('src');
                        return null;
                    })()
                """)
                if pdf_url:
                    full_url = pdf_url if pdf_url.startswith("http") else f"https://appsj.funcionjudicial.gob.ec{pdf_url}"
                    pdf_res = await page.request.get(full_url)
                    pdf_bytes = await pdf_res.body()
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                    download_saved = True
                    print(f"✔ PDF guardado desde URL del visor ({len(pdf_bytes)} bytes)")
                else:
                    await page.pdf(path=pdf_path, format="A4", print_background=True)
                    download_saved = True
            
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
            "fecha": formatted_date
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
        await page.evaluate("""
            const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
            if (btn) btn.click();
        """)
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
        await page.wait_for_timeout(3000)
        
        pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{session['cedula']}_{uuid.uuid4().hex[:6]}.pdf")
        download_saved = False
        try:
            async with page.expect_download(timeout=8000) as download_info:
                await page.evaluate("""
                    if (window.RichFaces && RichFaces.$('frmPopups:pdfPane1')) {
                        RichFaces.$('frmPopups:pdfPane1').show();
                    }
                    const btn = document.querySelector('input[value="Ver formulario"]') || 
                                document.querySelector('input[name*="j_idt242"]') ||
                                document.querySelector('input[name*="j_idt252"]');
                    if (btn) btn.click();
                """)
            download = await download_info.value
            await download.save_as(pdf_path)
            download_saved = True
        except Exception:
            pass
            
        if not download_saved or not os.path.exists(pdf_path):
            pdf_url = await page.evaluate("""
                (function() {
                    const obj = document.querySelector('object[type*="pdf"]') || document.querySelector('#frmPopups\\\\:pdfPane2 object');
                    if (obj && (obj.data || obj.getAttribute('data'))) return obj.data || obj.getAttribute('data');
                    const embed = document.querySelector('embed[type*="pdf"]') || document.querySelector('#frmPopups\\\\:pdfPane2 embed');
                    if (embed && (embed.src || embed.getAttribute('src'))) return embed.src || embed.getAttribute('src');
                    const iframe = document.querySelector('#frmPopups\\\\:pdfPane2 iframe') || document.querySelector('iframe[src*="pdf"]');
                    if (iframe && (iframe.src || iframe.getAttribute('src'))) return iframe.src || iframe.getAttribute('src');
                    return null;
                })()
            """)
            if pdf_url:
                full_url = pdf_url if pdf_url.startswith("http") else f"https://appsj.funcionjudicial.gob.ec{pdf_url}"
                pdf_res = await page.request.get(full_url)
                pdf_bytes = await pdf_res.body()
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)
            else:
                await page.pdf(path=pdf_path, format="A4", print_background=True)
        
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

@app.api_route("/", methods=["GET", "HEAD", "OPTIONS"])
async def index(request: Request):
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
