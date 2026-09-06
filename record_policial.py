import asyncio
import re
import time
from playwright.async_api import async_playwright

URL_RECORD = "https://certificados.ministeriodelinterior.gob.ec/gestorcertificados/antecedentes/"

KEYWORDS_RECORD = [
    "record", "récord", "record policial", "récord policial", "antecedentes", "certificado de antecedentes"
]

def detectar_mensaje_record(texto: str) -> bool:
    t = texto.lower()
    return any(kw in t for kw in KEYWORDS_RECORD)

def parsear_mensaje_record(texto: str) -> str | None:
    # Solo buscamos la cedula (10 digitos)
    m = re.search(r"\b(\d{10})\b", texto)
    if m:
        return m.group(1)
    return None

async def procesar_record_policial(cedula: str) -> tuple:
    """
    Retorna (success: bool, pdf_bytes_or_error: bytes|str, nombre: str|None)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security'
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC",
            accept_downloads=True
        )
        
        # Ocultar que es un bot
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        page = await context.new_page()
        
        pdf_final = None
        async def on_download(download):
            nonlocal pdf_final
            try:
                path = await download.path()
                if path:
                    with open(path, "rb") as f:
                        pdf_final = f.read()
            except Exception:
                pass
        page.on("download", on_download)
        
        try:
            await page.goto(URL_RECORD, timeout=45000)
            await page.wait_for_load_state("networkidle")
            
            # Verificar si caimos en Incapsula (WAF)
            frames = await page.locator("iframe").all()
            for f in frames:
                src = await f.get_attribute("src")
                if src and "Incapsula" in src:
                    await browser.close()
                    return False, "⚠️ El servidor del Ministerio del Interior rechazó la conexión (Incapsula Firewall). Intenta nuevamente más tarde.", None
            
            # A veces hay un modal de terminos inicial, buscamos boton Aceptar si existe
            try:
                btn_aceptar = page.locator("a, button, input[type='button']").filter(has_text=re.compile(r"Aceptar", re.IGNORECASE)).first
                if await btn_aceptar.is_visible(timeout=3000):
                    await btn_aceptar.click()
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

            # 1. Ingresar numero de documento
            # Es un input de texto. Para asegurarnos, tomamos el que este visible y no sea readonly ni de tipo oculto.
            inputs_texto = page.locator("input[type='text'], input:not([type])").filter(has=page.locator("visible=true"))
            # Lo llenamos
            if await inputs_texto.count() > 0:
                await inputs_texto.first.fill(cedula)
            else:
                # Si no encontramos por tag generico, intentamos por id comunes
                for id_guess in ["#txtCedula", "#txtDocumento", "#identificacion", "#numeroDocumento"]:
                    el = page.locator(id_guess).first
                    if await el.is_visible(timeout=1000):
                        await el.fill(cedula)
                        break

            await page.wait_for_timeout(500)
            
            # 2. Hacer clic en Siguiente
            btn_siguiente = page.locator("a, button, input[type='button'], input[type='submit']").filter(has_text=re.compile(r"Siguiente|Consultar|Buscar", re.IGNORECASE)).first
            await btn_siguiente.click()
            
            # 3. Esperar que aparezca el textarea de motivo de consulta
            # Cuando carga la persona, aparece el textarea o input para el motivo y un mensaje de error si no existe.
            await page.wait_for_timeout(4000)
            
            # Revisar si salio error (cedula incorrecta, etc)
            textos_error = ["no se encuentra", "error", "incorrecta", "no registrada"]
            body_text = await page.inner_text("body")
            body_lower = body_text.lower()
            if any(e in body_lower for e in textos_error) and "motivo de consulta" not in body_lower:
                # Si hay error y no llego al paso de motivo
                await browser.close()
                return False, "La cédula ingresada no se encuentra registrada o es inválida.", None

            # 4. Llenar motivo de consulta
            motivo_input = page.locator("textarea, input[type='text']").filter(has=page.locator("visible=true")).last
            if await motivo_input.is_visible(timeout=5000):
                await motivo_input.fill("realizar un tramite")
            else:
                await browser.close()
                return False, "No se encontró el campo 'Motivo de Consulta' en la página.", None
                
            await page.wait_for_timeout(500)
            
            # 5. Clic en el SEGUNDO boton Siguiente (o el mismo de nuevo que se refresco)
            botones_siguiente = page.locator("a, button, input[type='button'], input[type='submit']").filter(has_text=re.compile(r"Siguiente|Generar", re.IGNORECASE))
            if await botones_siguiente.count() > 1:
                await botones_siguiente.last.click()
            else:
                await botones_siguiente.first.click()
                
            await page.wait_for_timeout(4000)
            
            # 6. Clic en "Visualizar Certificado" o similar
            btn_visualizar = page.locator("a, button, input").filter(has_text=re.compile(r"Visualizar|Certificado|Imprimir|Descargar", re.IGNORECASE)).first
            
            if await btn_visualizar.is_visible(timeout=5000):
                async with page.expect_download(timeout=15000) as download_info:
                    await btn_visualizar.click()
                dl = await download_info.value
                dl_path = await dl.path()
                if dl_path:
                    with open(dl_path, "rb") as f:
                        pdf_final = f.read()
            else:
                await browser.close()
                return False, "No se encontró el botón para visualizar el certificado.", None
                
            await browser.close()
            
            # Extraer un nombre (Opcional, si no se puede mandamos generico)
            nombre = "Ciudadano"
            
            if pdf_final and len(pdf_final) > 5000:
                return "ok", pdf_final, nombre
            else:
                return False, "No se pudo obtener el PDF del récord policial.", None

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error inesperado: {str(e)}", None