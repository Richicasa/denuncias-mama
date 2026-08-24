import asyncio
import base64
import datetime
import os
import uuid
from typing import Dict
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

app = FastAPI(title="Denuncias de Extravío de Documentos")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active user sessions
sessions: Dict[str, dict] = {}
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Shared Playwright instance
playwright_instance = None
browser_instance: Browser = None

@app.on_event("startup")
async def startup_event():
    global playwright_instance, browser_instance
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"]
    )
    print("Browser engine initialized successfully.")

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

class StartSessionRequest(BaseModel):
    cedula: str
    sector: str

class SubmitCaptchaRequest(BaseModel):
    session_id: str
    captcha_text: str

class RefreshCaptchaRequest(BaseModel):
    session_id: str

@app.post("/api/start-session")
async def start_session(req: StartSessionRequest):
    cedula = req.cedula.strip()
    sector = req.sector.strip()
    
    if not cedula or len(cedula) < 10:
        raise HTTPException(status_code=400, detail="Número de cédula inválido (debe tener al menos 10 dígitos)")
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
        # Load form
        await page.goto("https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf", timeout=45000)
        await page.wait_for_load_state("networkidle")
        
        # 1. Fill Cedula and trigger blur
        await page.fill("#numeroIdentificacion", cedula)
        await page.locator("#numeroIdentificacion").blur()
        await page.wait_for_timeout(2500)
        
        # Read auto-populated name
        nombre = await page.input_value("#nombreCompleto")
        if not nombre:
            nombre = "CIUDADANO REGISTRADO"
            
        # 2. Domicilio (Pichincha -> Quito -> Sector de [Sector])
        await page.select_option("#provinciaDomicilio", value="17")
        await page.wait_for_timeout(1500)
        await page.select_option("#cantonDomicilio", value="185")
        await page.fill("#direccionDomicilio", f"Sector de {sector}")
        
        # 3. Extravio (Pichincha -> Quito -> Documento extraviado en el sector de [Sector])
        await page.select_option("#provinciaExtravio", value="17")
        await page.wait_for_timeout(1500)
        await page.select_option("#cantonExtravio", value="185")
        await page.fill("#direccionCircunstancia", f"Documento extraviado en el sector de {sector}")
        
        # 4. Date calculation (previous business day)
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
        
        # 5. Add Document modal
        add_btn = page.locator('input[value="+ Agregar un nuevo documento"]')
        await add_btn.click()
        await page.wait_for_timeout(1500)
        
        # Ensure modal shown
        await page.evaluate("if (window.RichFaces && RichFaces.$('frmPopups:createPane')) RichFaces.$('frmPopups:createPane').show();")
        await page.wait_for_timeout(800)
        
        await page.select_option('select[id*="tipoDocumentoExtraviadoNewSelect"]', value="7")
        await page.wait_for_timeout(1000)
        await page.fill('input[id*="numeroNew"]', cedula)
        await page.fill('textarea[id*="descripcionNew"]', "cédula de identidad")
        
        # Submit document modal
        accept_doc_btn = page.locator('#frmPopups\\:createPane input[value="Aceptar"]')
        await accept_doc_btn.click()
        await page.wait_for_timeout(2500)
        
        # 6. Capture Captcha
        captcha_el = await page.query_selector("#imgCaptchaId")
        if not captcha_el:
            raise Exception("No se pudo cargar la imagen del Captcha del portal judicial.")
            
        captcha_bytes = await captcha_el.screenshot()
        captcha_b64 = base64.b64encode(captcha_bytes).decode("utf-8")
        
        # Save session in memory
        sessions[session_id] = {
            "context": context,
            "page": page,
            "cedula": cedula,
            "sector": sector,
            "nombre": nombre,
            "fecha": formatted_date,
            "created_at": datetime.datetime.now()
        }
        
        return {
            "success": True,
            "sessionId": session_id,
            "nombre": nombre,
            "fecha": formatted_date,
            "sector": sector,
            "captchaImage": f"data:image/jpeg;base64,{captcha_b64}"
        }
        
    except Exception as e:
        await context.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/refresh-captcha")
async def refresh_captcha(req: RefreshCaptchaRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión expirada o no encontrada. Inicie nuevamente.")
    
    page: Page = session["page"]
    try:
        # Refresh captcha click
        refresh_link = page.locator('a[onclick*="captchaRegistro.jpg"]')
        if await refresh_link.count() > 0:
            await refresh_link.click()
        else:
            await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
        
        await page.wait_for_timeout(1500)
        captcha_el = await page.query_selector("#imgCaptchaId")
        captcha_bytes = await captcha_el.screenshot()
        captcha_b64 = base64.b64encode(captcha_bytes).decode("utf-8")
        
        return {
            "success": True,
            "captchaImage": f"data:image/jpeg;base64,{captcha_b64}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error refrescando captcha: {str(e)}")

@app.post("/api/submit-captcha")
async def submit_captcha(req: SubmitCaptchaRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión expirada o no encontrada.")
    
    page: Page = session["page"]
    context: BrowserContext = session["context"]
    captcha_text = req.captcha_text.strip()
    
    try:
        # Fill captcha
        await page.fill("#captchaTxt", captcha_text)
        
        # Click Aceptar
        await page.click("#j_idt170")
        await page.wait_for_timeout(2500)
        
        # Check if error message appeared on captcha
        error_msg = await page.eval_on_selector_all(".rf-msgs, .rf-msg", "els => els.map(e => e.innerText.trim()).filter(t => t.length > 0)")
        if any("captcha" in m.lower() or "código" in m.lower() or "incorrecto" in m.lower() for m in error_msg):
            # Capture new captcha
            captcha_el = await page.query_selector("#imgCaptchaId")
            new_b64 = ""
            if captcha_el:
                c_bytes = await captcha_el.screenshot()
                new_b64 = f"data:image/jpeg;base64,{base64.b64encode(c_bytes).decode('utf-8')}"
            return {
                "success": False,
                "error": "El código Captcha no coincide. Intente nuevamente con el nuevo código.",
                "newCaptcha": new_b64
            }
            
        # Check confirmation modal
        # RichFaces.$('frmPopups:confirmForm')
        await page.wait_for_timeout(1000)
        si_btn = page.locator('#frmPopups\\:confirmForm input[value="Si"]')
        if await si_btn.count() > 0:
            await si_btn.click()
            await page.wait_for_timeout(2500)
            
        # Modal for downloading PDF
        # Click "Ver formulario" to trigger PDF download
        pdf_path = os.path.join(DOWNLOADS_DIR, f"denuncia_{session['cedula']}_{uuid.uuid4().hex[:6]}.pdf")
        
        ver_btn = page.locator('input[value="Ver formulario"]').first
        if await ver_btn.count() > 0:
            async with page.expect_download(timeout=30000) as download_info:
                await ver_btn.click()
            download = await download_info.value
            await download.save_as(pdf_path)
        else:
            # Fallback check popup
            await page.evaluate("if (window.RichFaces && RichFaces.$('frmPopups:pdfPane1')) RichFaces.$('frmPopups:pdfPane1').show();")
            await page.wait_for_timeout(1000)
            async with page.expect_download(timeout=30000) as download_info:
                await page.click('input[value="Ver formulario"]')
            download = await download_info.value
            await download.save_as(pdf_path)
            
        session["pdf_path"] = pdf_path
        
        # Cleanup browser context
        await context.close()
        
        return {
            "success": True,
            "message": "¡Denuncia generada y registrada con éxito!",
            "downloadUrl": f"/api/download-pdf/{req.session_id}",
            "cedula": session["cedula"],
            "nombre": session["nombre"]
        }
        
    except Exception as e:
        # Check if error in download or captcha
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Error procesando formulario: {str(e)}"}
        )

@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str):
    session = sessions.get(session_id)
    if not session or "pdf_path" not in session:
        raise HTTPException(status_code=404, detail="Archivo PDF no encontrado")
    
    file_path = session["pdf_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="El archivo PDF ha expirado o no existe")
        
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=f"Denuncia_Extravio_{session['cedula']}.pdf"
    )

@app.get("/")
async def index():
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=False)
