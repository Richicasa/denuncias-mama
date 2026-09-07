import asyncio
import io
import os
import random
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
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profile")

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

async def simular_click_humano(page, iframe_selector: str, element) -> None:
    """
    Simula una trayectoria de mouse natural hacia el checkbox con aceleración
    y micro-pausas antes de presionar para evitar que hCaptcha detecte un bot.
    """
    try:
        iframe_elem = await page.query_selector(iframe_selector)
        ibox = await iframe_elem.bounding_box() if iframe_elem else None
        cbox = await element.bounding_box()
        
        if ibox and cbox:
            abs_x = ibox["x"] + cbox["x"] + cbox["width"] * random.uniform(0.4, 0.6)
            abs_y = ibox["y"] + cbox["y"] + cbox["height"] * random.uniform(0.4, 0.6)
            
            start_x = random.randint(80, 250)
            start_y = random.randint(80, 250)
            await page.mouse.move(start_x, start_y)
            
            steps = 22
            for i in range(1, steps + 1):
                prog = i / steps
                ease = 1 - (1 - prog) ** 3  # cubic ease out
                curr_x = start_x + (abs_x - start_x) * ease + random.uniform(-1.5, 1.5)
                curr_y = start_y + (abs_y - start_y) * ease + random.uniform(-1.5, 1.5)
                await page.mouse.move(curr_x, curr_y)
                await asyncio.sleep(random.uniform(0.012, 0.022))
            
            await page.mouse.move(abs_x, abs_y)
            await asyncio.sleep(random.uniform(0.18, 0.35))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.12))
            await page.mouse.up()
            return
    except Exception as e:
        print(f"[RECORD] Detalle en simular_click_humano: {e}")
    
    await element.click()

async def procesar_record_policial(cedula: str) -> tuple:
    """
    Genera el Certificado de Antecedentes Penales (Record Policial).
    Utiliza perfil persistente y patchright con emulación de click humano para bypass de hCaptcha/Imperva.
    Retorna: (success: bool, pdf_bytes_or_error: bytes|str, nombre: str|None)
    """
    os.makedirs(PROFILE_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'
            ],
            locale="es-EC"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # 1. Cargar portal
            await page.goto(URL_RECORD, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            # 2. Detectar y resolver hCaptcha de Imperva con click humano
            for _ in range(8):
                h_frame = None
                for frame in page.frames:
                    if "hcaptcha.html" in frame.url and "frame=checkbox" in frame.url:
                        h_frame = frame
                        break
                
                if h_frame:
                    cb = h_frame.locator("#checkbox")
                    if await cb.is_visible():
                        print("[RECORD] Checkbox hCaptcha detectado. Ejecutando click humano...")
                        await simular_click_humano(page, "iframe[src*='frame=checkbox']", cb)
                        await page.wait_for_timeout(4000)
                        break
                await asyncio.sleep(1)

            # 3. Descartar banner de política de privacidad si estorba
            try:
                cookie_btn = await page.query_selector("button:has-text('Aceptar!'), .politica-cookies button")
                if cookie_btn and await cookie_btn.is_visible():
                    await cookie_btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

            # 4. Aceptar Términos y Condiciones
            for _ in range(6):
                accepted = await page.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('.ui-dialog button, button'));
                        const b = btns.find(x => x.innerText.trim().toLowerCase() === 'aceptar');
                        if (b) { b.click(); return true; }
                        return false;
                    }
                """)
                if accepted:
                    break
                await asyncio.sleep(1)

            await page.wait_for_timeout(1000)

            # 5. Esperar formulario y llenar cédula
            try:
                await page.wait_for_selector("#txtCi", state="visible", timeout=15000)
            except Exception:
                try:
                    await page.screenshot(path="error_record_screen.png")
                except Exception:
                    pass
                try:
                    body_txt = await page.inner_text("body")
                except Exception:
                    body_txt = ""
                await context.close()
                if "Error 17" in body_txt or "Incident ID" in body_txt:
                    return False, "Bloqueo temporal de seguridad de Imperva (Error 17). Espera 5-10 minutos antes de intentar de nuevo.", None
                return False, "No se pudo cargar el formulario del Ministerio (filtro de seguridad activo). Intenta de nuevo.", None

            await page.fill("#txtCi", cedula)
            await asyncio.sleep(0.4)
            await page.click("#btnSig1")

            # 6. Esperar paso de motivo de consulta
            try:
                await page.wait_for_selector("#txtMotivo", state="visible", timeout=15000)
            except Exception:
                body_text = await page.inner_text("body")
                for err_kw in ["no se encuentra", "no existe", "incorrecta", "error"]:
                    if err_kw in body_text.lower():
                        await context.close()
                        return False, "La cédula ingresada no se encuentra registrada o es inválida.", None
                try:
                    await page.screenshot(path="error_record_screen.png")
                except Exception:
                    pass
                await context.close()
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
            await asyncio.sleep(0.4)
            await page.click("#btnSig2")
            
            # Esperar a que el AJAX del ministerio complete y llene hdIdr
            try:
                await page.wait_for_function(
                    "() => { const el = document.getElementById('hdIdr'); return el && el.value && el.value.trim().length > 0; }",
                    timeout=10000
                )
            except Exception:
                await page.wait_for_timeout(3500)

            # 7. Generar URL directa del certificado PDF con el token de sesión
            cert_url = await page.evaluate("""
                () => {
                    const idr = document.getElementById('hdIdr') ? document.getElementById('hdIdr').value : '';
                    if (!idr) return null;
                    return 'https://certificados.ministeriodelinterior.gob.ec/gestorcertificados/antecedentes/certificado.php?code=' + btoa(idr);
                }
            """)
            print(f"[RECORD] cert_url={cert_url}")

            if not cert_url:
                await context.close()
                return False, "No se pudo generar el código del certificado.", None

            # Descargar PDF directamente con la sesión activa
            resp = await page.request.get(cert_url)
            body = await resp.body()
            print(f"[RECORD] HTTP {resp.status}, len={len(body)}, is_pdf={body.startswith(b'%PDF') if body else False}")

            await context.close()

            if body and body.startswith(b"%PDF") and len(body) > 5000:
                return True, body, nombre or "CIUDADANO REGISTRADO"
            else:
                print(f"[RECORD] Snippet: {body[:300] if body else None}")
                if body and (b"Error 17" in body or b"Incident ID" in body):
                    return False, "Bloqueo temporal de seguridad de Imperva (Error 17). Espera 5-10 minutos.", None
                return False, "El portal no entregó un archivo PDF válido.", None

        except Exception as e:
            try:
                await page.screenshot(path="error_record_screen.png")
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass
            return False, f"Error en procesamiento: {str(e)}", None