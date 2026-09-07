import asyncio
import io
import os
import re
import sys
import time

# Asegurar DISPLAY en Linux para modo visual
if sys.platform != "win32" and "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":0"

# Importar o auto-instalar patchright
try:
    from patchright.async_api import async_playwright
except ImportError:
    import subprocess
    print("[RECORD] Auto-instalando patchright y dependencias anti-detección...")
    subprocess.run([sys.executable, "-m", "pip", "install", "patchright>=1.62.0", "--quiet"], check=True)
    try:
        subprocess.run([sys.executable, "-m", "patchright", "install", "chromium"], capture_output=True)
    except Exception:
        pass
    from patchright.async_api import async_playwright

URL_RECORD = "https://certificados.ministeriodelinterior.gob.ec/gestorcertificados/antecedentes/"

KEYWORDS_RECORD = [
    "record", "récord", "record policial", "récord policial", "antecedentes", "certificado de antecedentes"
]

def detectar_mensaje_record(texto: str) -> bool:
    t = texto.lower()
    return any(kw in t for kw in KEYWORDS_RECORD)

def parsear_mensaje_record(texto: str) -> str | None:
    m = re.search(r"\b(\d{10})\b", texto)
    if m:
        return m.group(1)
    return None

async def procesar_record_policial(cedula: str) -> tuple:
    """
    Genera el Certificado de Antecedentes Penales (Record Policial).
    Burlar Imperva/hCaptcha con patchright en modo visual y descarga directa.
    Retorna: (success: bool, pdf_bytes_or_error: bytes|str, nombre: str|None)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage'
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="es-EC"
        )
        page = await context.new_page()

        try:
            # 1. Cargar portal
            await page.goto(URL_RECORD, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            # 2. Detectar y resolver hCaptcha de Imperva
            for i in range(10):
                resolved = False
                for frame in page.frames:
                    if "hcaptcha.html" in frame.url and "frame=checkbox" in frame.url:
                        cb = frame.locator("#checkbox")
                        if await cb.is_visible():
                            await cb.click()
                            await page.wait_for_timeout(5000)
                            resolved = True
                            break
                if resolved:
                    break
                await asyncio.sleep(1)

            # 3. Aceptar Términos y Condiciones
            for _ in range(5):
                accepted = await page.evaluate("""
                    () => {
                        const b = Array.from(document.querySelectorAll('.ui-dialog button, button')).find(x => x.innerText.trim() === 'Aceptar');
                        if (b) { b.click(); return true; }
                        return false;
                    }
                """)
                if accepted:
                    break
                await asyncio.sleep(1)

            await page.wait_for_timeout(1000)

            # 4. Esperar formulario y llenar cédula
            try:
                await page.wait_for_selector("#txtCi", state="visible", timeout=15000)
            except Exception:
                try:
                    await page.screenshot(path="error_record_screen.png")
                except Exception:
                    pass
                await browser.close()
                return False, "No se pudo cargar el formulario del Ministerio (filtro de seguridad activo). Intenta de nuevo.", None

            await page.fill("#txtCi", cedula)
            await page.click("#btnSig1")

            # 5. Esperar paso de motivo de consulta
            try:
                await page.wait_for_selector("#txtMotivo", state="visible", timeout=15000)
            except Exception:
                body_text = await page.inner_text("body")
                for err_kw in ["no se encuentra", "no existe", "incorrecta", "error"]:
                    if err_kw in body_text.lower():
                        await browser.close()
                        return False, "La cédula ingresada no se encuentra registrada o es inválida.", None
                try:
                    await page.screenshot(path="error_record_screen.png")
                except Exception:
                    pass
                await browser.close()
                return False, "El portal no devolvió datos para esta cédula.", None

            # Extraer nombre si está disponible
            nombre = None
            try:
                n_val = await page.locator("#hdName").input_value()
                if n_val and len(n_val.strip()) > 2:
                    nombre = n_val.strip()
            except Exception:
                pass

            # Llenar motivo y enviar paso 2
            await page.fill("#txtMotivo", "realizar un tramite")
            await page.click("#btnSig2")
            await page.wait_for_timeout(2500)

            # 6. Generar URL directa del certificado PDF con el token de sesión
            cert_url = await page.evaluate("""
                () => {
                    const idr = document.getElementById('hdIdr') ? document.getElementById('hdIdr').value : '';
                    if (!idr) return null;
                    return 'https://certificados.ministeriodelinterior.gob.ec/gestorcertificados/antecedentes/certificado.php?code=' + btoa(idr);
                }
            """)

            if not cert_url:
                await browser.close()
                return False, "No se pudo generar el código del certificado.", None

            # Descargar PDF directamente con la sesión activa
            resp = await page.request.get(cert_url)
            body = await resp.body()

            await browser.close()

            if body and body.startswith(b"%PDF") and len(body) > 5000:
                return True, body, nombre or "CIUDADANO REGISTRADO"
            else:
                return False, "El portal no entregó un archivo PDF válido.", None

        except Exception as e:
            try:
                await page.screenshot(path="error_record_screen.png")
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error en procesamiento: {str(e)}", None