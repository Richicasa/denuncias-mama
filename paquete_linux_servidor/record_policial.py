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
    "record", "récord", "record policial", "récord policial", "recor", "recort",
    "recor policial", "recort policial", "antecedentes", "antecedente",
    "certificado de antecedentes", "certificado antecedentes"
]

def detectar_mensaje_record(texto: str) -> bool:
    t = texto.lower()
    return any(kw in t for kw in KEYWORDS_RECORD)

def parsear_mensaje_record(texto: str) -> str | None:
    m = re.search(r"\b(\d{10})\b", texto)
    if m:
        return m.group(1)
    return None

async def simular_click_humano(page, element) -> None:
    """
    Simula una trayectoria de mouse natural hacia el checkbox con aceleración
    y micro-pausas antes de presionar para evitar que hCaptcha detecte un bot.
    """
    try:
        cbox = await element.bounding_box()
        if cbox:
            abs_x = cbox["x"] + cbox["width"] * random.uniform(0.4, 0.6)
            abs_y = cbox["y"] + cbox["height"] * random.uniform(0.4, 0.6)
            
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
    
    await element.click(delay=120)

EXT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "nopecha_extension"))

async def procesar_record_policial(cedula: str) -> tuple:
    """
    Genera el Certificado de Antecedentes Penales (Record Policial).
    Utiliza extensión de navegador (NopeCHA) y patchright con emulación de click humano para bypass de hCaptcha/Imperva.
    Retorna: (success: bool, pdf_bytes_or_error: bytes|str, nombre: str|None)
    """
    import base64
    import tempfile
    import shutil
    
    temp_profile = tempfile.mkdtemp(prefix="rec_pol_")
    
    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        if sys.platform != "win32"
        else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    args = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-blink-features=AutomationControlled',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-infobars',
        '--disable-notifications',
        '--disable-popup-blocking',
        '--silent-debugger-extension-api',
        '--start-maximized'
    ]
    if os.path.exists(EXT_PATH):
        args.insert(0, f'--disable-extensions-except={EXT_PATH}')
        args.insert(1, f'--load-extension={EXT_PATH}')

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=temp_profile,
            headless=False,
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
            args=args,
            locale="es-EC"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # 1. Cargar portal
            print("[RECORD] 1. Cargando portal del Ministerio del Interior...")
            await page.goto(URL_RECORD, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            # Verificar si existe bloqueo previo de Imperva
            try:
                body_txt = await page.inner_text("body")
                if "Error 17" in body_txt or "Incident ID" in body_txt:
                    await context.close()
                    return False, "Bloqueo temporal de seguridad de Imperva (Error 17). Espera 5-10 minutos antes de intentar de nuevo.", None
            except Exception:
                pass

            # 2. Detectar y resolver hCaptcha (checkbox con trayectoria humana + extensión activa)
            print("[RECORD] 2. Buscando checkbox hCaptcha...")
            for _ in range(12):
                h_frame = None
                for frame in page.frames:
                    if "hcaptcha.html" in frame.url and "frame=checkbox" in frame.url:
                        h_frame = frame
                        break
                
                if h_frame:
                    cb = h_frame.locator("#checkbox")
                    if await cb.is_visible():
                        print("[RECORD] Checkbox hCaptcha detectado. Ejecutando click humano...")
                        await simular_click_humano(page, cb)
                        break
                await asyncio.sleep(1)

            # 3. Monitorear resolución (directa o interactiva con extensión) y aceptar Términos
            print("[RECORD] 3. Esperando resolución de captcha y Términos...")
            for sec in range(1, 35):
                await asyncio.sleep(1)
                
                # Descartar banner de cookies si estorba
                try:
                    cookie_btn = await page.query_selector("button:has-text('Aceptar!'), .politica-cookies button")
                    if cookie_btn and await cookie_btn.is_visible():
                        await cookie_btn.click()
                except Exception:
                    pass

                # Intentar aceptar Términos y Condiciones
                try:
                    accepted = await page.evaluate("""
                        () => {
                            const btns = Array.from(document.querySelectorAll('.ui-dialog button, button'));
                            const b = btns.find(x => x.innerText.trim().toLowerCase() === 'aceptar');
                            if (b && b.offsetParent !== null) { b.click(); return true; }
                            return false;
                        }
                    """)
                    if accepted:
                        print(f"[RECORD] Términos aceptados en {sec}s")
                        break
                except Exception:
                    pass

                # Verificar si ya se abrió el formulario
                try:
                    if await page.locator("#txtCi").is_visible():
                        break
                except Exception:
                    pass

            await page.wait_for_timeout(1000)

            # 4. Esperar formulario y llenar cédula
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

            print(f"[RECORD] 4. Llenando cédula {cedula}...")
            await page.fill("#txtCi", cedula)
            await asyncio.sleep(0.4)
            await page.click("#btnSig1")

            # 5. Esperar paso de motivo de consulta (dar tiempo al web service de Registro Civil)
            try:
                await page.wait_for_selector("#txtMotivo", state="visible", timeout=25000)
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
                return False, "El portal no devolvió datos para esta cédula (posible demora del Registro Civil).", None

            # Extraer nombre si está disponible
            nombre = None
            try:
                n_val = await page.locator("#hdName").input_value()
                if n_val and len(n_val.strip()) > 2:
                    nombre = n_val.strip()
            except Exception:
                pass

            # Llenar motivo y enviar paso 2
            print("[RECORD] 5. Llenando motivo y generando certificado...")
            await page.fill("#txtMotivo", "realizar un tramite")
            await asyncio.sleep(0.4)
            await page.click("#btnSig2")
            
            # Esperar a que el AJAX del ministerio complete y llene hdIdr
            try:
                await page.wait_for_function(
                    "() => { const el = document.getElementById('hdIdr'); return el && el.value && el.value.trim().length > 0; }",
                    timeout=15000
                )
            except Exception:
                await page.wait_for_timeout(3500)

            # 6. Generar URL directa del certificado PDF con el token de sesión
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

            # 7. Descargar PDF vía in-page fetch (utiliza la sesión autenticada del navegador)
            body = None
            try:
                pdf_b64 = await page.evaluate("""
                    async (url) => {
                        const resp = await fetch(url);
                        const blob = await resp.blob();
                        return new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result.split(',')[1]);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                    }
                """, cert_url)
                if pdf_b64:
                    body = base64.b64decode(pdf_b64)
            except Exception as e:
                print(f"[RECORD] Fallback a request.get por: {e}")
                resp = await page.request.get(cert_url)
                body = await resp.body()

            print(f"[RECORD] len={len(body) if body else 0}, is_pdf={body.startswith(b'%PDF') if body else False}")
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
        finally:
            try:
                shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception:
                pass