import asyncio
import datetime
import io
import os
import re
import subprocess
import sys
import time
from PIL import Image
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from playwright.async_api import async_playwright
from text_cleaner import limpiar_y_corregir_sector
from record_policial import detectar_mensaje_record, parsear_mensaje_record
from record_handlers import handle_message_record
from bachiller import detectar_mensaje_bachiller, parsear_mensaje_bachiller
from bachiller_handlers import handle_message_bachiller
from ant_parser import detectar_mensaje_ant, parsear_mensaje_ant, analizar_cedula_con_diagnostico
from ant_handlers import handle_message_ant

try:
    import ddddocr
    _ocr_instance = ddddocr.DdddOcr(show_ad=False)
except Exception:
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python-headless", "ddddocr>=1.4.11", "--quiet"], check=True)
        import ddddocr
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    except Exception:
        _ocr_instance = None

try:
    import winocr
except ImportError:
    winocr = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

_browser_lock = asyncio.Lock()
_global_playwright = None
_global_browser = None

MAX_RETRIES_DENUNCIA = 15
TOTAL_TIMEOUT_DENUNCIA = 300  # segundos maximos para permitir las 15 pruebas

async def reset_shared_browser():
    """Cierra limpiamente el navegador compartido y Playwright para reiniciar desde cero."""
    global _global_playwright, _global_browser
    async with _browser_lock:
        if _global_browser is not None:
            try:
                await _global_browser.close()
            except Exception:
                pass
            _global_browser = None
        if _global_playwright is not None:
            try:
                await _global_playwright.stop()
            except Exception:
                pass
            _global_playwright = None

async def get_shared_browser():
    global _global_playwright, _global_browser
    async with _browser_lock:
        browser_alive = False
        if _global_browser is not None and _global_browser.is_connected():
            browser_alive = True

        if not browser_alive:
            # Si el navegador previo falló o se desconectó, limpiar completamente
            if _global_browser is not None:
                try:
                    await _global_browser.close()
                except Exception:
                    pass
                _global_browser = None

            if _global_playwright is not None:
                try:
                    await _global_playwright.stop()
                except Exception:
                    pass
                _global_playwright = None

            _global_playwright = await async_playwright().start()
            _global_browser = await _global_playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )
        return _global_browser

# Almacenar estado de conversaciones por usuario de Telegram
user_states = {}

# Palabras clave que indican denuncia de LICENCIA
KEYWORDS_LICENCIA = [
    "licencia", "carnet de conducir", "permiso de conducir",
    "licencia de manejo", "licencia de conduccion"
]

# Tipos de licencia validos
TIPOS_LICENCIA_VALIDOS = ["A", "B", "C", "D", "E", "F", "G"]

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

async def solve_winocr_strict(image_bytes: bytes) -> str:
    """
    Resuelve el captcha del Consejo de la Judicatura a máxima velocidad:
    1. ddddocr (ultra rápido ~15ms, 98% precisión en Linux/Windows)
    2. WinOCR (Windows nativo)
    3. Pytesseract con escalado 4x Lanczos + binarización
    """
    if _ocr_instance:
        try:
            res = _ocr_instance.classification(image_bytes)
            clean = re.sub(r'[^A-Za-z0-9]', '', res).strip()
            if len(clean) == 6:
                return clean
        except Exception:
            pass

    if winocr:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            scaled = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
            res = await winocr.recognize_pil(scaled, lang="es")
            raw = res.text if hasattr(res, "text") else str(res)
            clean = re.sub(r'[^A-Za-z0-9]', '', raw).strip()
            if len(clean) == 6:
                return clean
        except Exception:
            pass

    if pytesseract:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            scaled = img.resize((img.width * 4, img.height * 4), Image.Resampling.LANCZOS)
            for th in [170, 185, 155, 200]:
                bin_img = scaled.point(lambda p: 0 if p > th else 255)
                txt = pytesseract.image_to_string(
                    bin_img,
                    config="--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                ).strip()
                clean = re.sub(r"[^A-Za-z0-9]", "", txt)
                if len(clean) == 6:
                    return clean
        except Exception:
            pass

    return ""


def parsear_mensaje(texto: str) -> dict:
    """
    Parser inteligente: extrae cedula (10 digitos), sector y tipo_licencia
    del mensaje sin importar el orden. Detecta si es denuncia de cedula o licencia.
    Diagnostica si la cedula esta mal tipeada (>10 digitos, letras, modulo 10).
    """
    resultado = {
        "cedula": None,
        "error_cedula": None,
        "sector": None,
        "tipo_licencia": None,
        "tipo_denuncia": "cedula"  # "cedula" o "licencia"
    }

    texto_lower = texto.lower()

    # 1. Detectar tipo de denuncia
    for kw in KEYWORDS_LICENCIA:
        if kw in texto_lower:
            resultado["tipo_denuncia"] = "licencia"
            break

    # 2. Extraer y diagnosticar cedula
    diag = analizar_cedula_con_diagnostico(texto)
    if diag["valida"]:
        resultado["cedula"] = diag["cedula"]
    elif diag.get("mensaje_error"):
        resultado["error_cedula"] = diag["mensaje_error"]
        resultado["cedula"] = diag.get("cedula")
    else:
        # Fallback a busqueda de 10 digitos si la hubiera
        cedula_match = re.search(r'\b(\d{10})\b', texto)
        if cedula_match:
            resultado["cedula"] = cedula_match.group(1)

    # 3. Extraer tipo de licencia (ej: "tipo A", "tipo B", "clase B", "licencia B", letra suelta al final)
    tipo_match = re.search(
        r'(?:tipo|clase|licencia)\s+([A-Fa-f])\b|'
        r'\blicencia\s+([A-Fa-f])\b|'
        r'\btipo\s+([A-Fa-f])\b|'
        r'(?<!\w)([A-Fa-f])(?!\w)(?=\s*[,.\n]|\s*$)',
        texto,
        re.IGNORECASE
    )
    if tipo_match:
        letra = next((g for g in tipo_match.groups() if g), None)
        if letra:
            resultado["tipo_licencia"] = letra.upper()
            if resultado["tipo_denuncia"] == "cedula" and letra.upper() in TIPOS_LICENCIA_VALIDOS:
                resultado["tipo_denuncia"] = "licencia"

    # 4. Extraer sector: el texto que queda despues de quitar cedula, palabras clave y tipo
    texto_sector = texto
    if resultado["cedula"]:
        texto_sector = texto_sector.replace(resultado["cedula"], "")

    # Quitar palabras clave de licencia y tipo
    texto_sector = re.sub(r'\b(licencia|carnet|permiso|conducir|manejo|conduccion|tipo|clase)\b', '', texto_sector, flags=re.IGNORECASE)
    if resultado["tipo_licencia"]:
        texto_sector = re.sub(r'\b' + resultado["tipo_licencia"] + r'\b', '', texto_sector, flags=re.IGNORECASE)

    # Quitar palabras de relleno tipicas de mensajes en lenguaje natural
    RELLENO = r'\b(perdi|perdie|perdy|mi|mis|mi|cedula|su|numero|num|es|en|el|la|los|las|de|del|y|por|hola|buenas|dias|tardes|noches|favor|gracias|necesito|quiero|quisiera|porfavor|por\s+favor)\b'
    texto_sector = re.sub(RELLENO, ' ', texto_sector, flags=re.IGNORECASE)

    # Quitar signos de puntuacion y espacios multiples
    texto_sector = re.sub(r'[,;:\-]', ' ', texto_sector)
    texto_sector = re.sub(r'\s+', ' ', texto_sector).strip()

    if texto_sector and len(texto_sector) > 2:
        resultado["sector"] = texto_sector

    return resultado


async def _ejecutar_formulario(page, context, cedula, dir_domicilio, dir_circunstancia, tipo_denuncia, tipo_licencia=None, nombre_manual=None):
    """
    Ejecuta el flujo completo del formulario en el portal de la Judicatura a velocidad Turbo (< 3 segundos).
    tipo_denuncia: "cedula" o "licencia"
    tipo_licencia: "A", "B", etc. (solo si tipo_denuncia == "licencia")
    nombre_manual: Nombre y apellido si la plataforma no lo autocompleta desde el Registro Civil.
    Retorna (captured_pdf_bytes, nombre_completo, info_estado)
    """
    captured_pdf = None

    # Bloquear fuentes e imágenes pesadas innecesarias; capturar únicamente el flujo PDF y JSF
    async def route_handler(route, request):
        nonlocal captured_pdf
        url = request.url.lower()
        rtype = request.resource_type
        if rtype in ["font", "media"] or (rtype == "image" and "captcharegistro" not in url):
            await route.abort()
            return
        if "formulario.jsf" in url or "impresionreporte" in url:
            try:
                resp = await route.fetch()
                ct = resp.headers.get("content-type", "").lower()
                if "pdf" in ct or "application/pdf" in ct or "impresionreporte" in url:
                    body = await resp.body()
                    if body.startswith(b"%PDF"):
                        captured_pdf = body
                await route.fulfill(response=resp)
                return
            except Exception:
                pass
        await route.continue_()

    await page.route("**/*", route_handler)

    # 1. Cargar formulario a máxima velocidad
    await page.goto(
        "https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf",
        timeout=25000,
        wait_until="domcontentloaded"
    )

    # 2. Llenar cédula y activar búsqueda de nombre
    await page.fill("#numeroIdentificacion", cedula)
    await page.locator("#numeroIdentificacion").blur()

    # 2.1 Comprobar si el portal muestra alerta o mensaje de rechazo de cédula
    error_portal = await page.evaluate("""
        (() => {
            const els = document.querySelectorAll('.rf-msgs-sum, .rf-msg-sum, .ui-messages-error, #msjFormulario, .ui-message-error, .alert');
            for (const el of els) {
                const txt = (el.innerText || '').trim();
                if (txt && (txt.toLowerCase().includes('cédula') || txt.toLowerCase().includes('cedula') || txt.toLowerCase().includes('identificación') || txt.toLowerCase().includes('identificacion') || txt.toLowerCase().includes('registro civil') || txt.toLowerCase().includes('no existe') || txt.toLowerCase().includes('inválid') || txt.toLowerCase().includes('incorrect'))) {
                    return txt;
                }
            }
            return null;
        })()
    """)
    if error_portal:
        return None, None, {"tipo": "ERROR_CEDULA_PLATAFORMA", "mensaje": error_portal, "cedula": cedula}

    # 3. Asignar provincias inmediatamente
    await page.select_option("#provinciaDomicilio", value="17")
    await page.select_option("#provinciaExtravio", value="17")

    # 4. Fecha hábil
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

    # 5. Cantones y direcciones
    try:
        await page.wait_for_function("() => document.querySelectorAll('#cantonDomicilio option').length > 1", timeout=2000)
    except Exception:
        pass
    await page.select_option("#cantonDomicilio", label="QUITO")
    await page.fill("#direccionDomicilio", dir_domicilio)

    try:
        await page.wait_for_function("() => document.querySelectorAll('#cantonExtravio option').length > 1", timeout=2000)
    except Exception:
        pass
    await page.select_option("#cantonExtravio", label="QUITO")
    await page.fill("#direccionCircunstancia", dir_circunstancia)

    # 5.1 Resolver el nombre del ciudadano (automático o manual si la plataforma lo pide)
    nombre = ""
    if nombre_manual:
        await page.fill("#nombreCompleto", nombre_manual.strip().upper())
        nombre = nombre_manual.strip().upper()
    else:
        # Esperar hasta 2 segundos a ver si el Registro Civil autocompleta el nombre
        for _ in range(5):
            val = (await page.input_value("#nombreCompleto") or "").strip()
            if val:
                nombre = val
                break
            await asyncio.sleep(0.4)

        # Si el campo sigue vacío, la plataforma está solicitando los nombres manualmente
        if not nombre:
            return None, None, {"tipo": "REQUIERE_NOMBRES", "cedula": cedula}

    # 6. Agregar documento
    await page.locator('input[value="+ Agregar un nuevo documento"]').click(force=True)
    await page.wait_for_timeout(200)

    if tipo_denuncia == "licencia":
        licencia_value = await page.evaluate("""
            (() => {
                const sel = document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect');
                if (!sel) return null;
                for (const opt of sel.options) {
                    if (opt.text.toLowerCase().includes('licencia') || opt.text.toLowerCase().includes('conducir')) {
                        return opt.value;
                    }
                }
                return null;
            })()
        """)
        desc_licencia = f"LICENCIA TIPO {tipo_licencia}" if tipo_licencia else "LICENCIA DE CONDUCIR"
        await page.evaluate(f"""
            if (window.RichFaces && RichFaces.$('frmPopups:createPane')) {{
                RichFaces.$('frmPopups:createPane').show();
            }}
            const sel = document.getElementById('frmPopups:tipoDocumentoExtraviadoNewSelect');
            if (sel) {{
                const licVal = {repr(licencia_value)};
                if (licVal !== null) {{
                    sel.value = licVal;
                }} else {{
                    for (const opt of sel.options) {{
                        if (opt.text.toLowerCase().includes('licencia') || opt.text.toLowerCase().includes('conducir')) {{
                            sel.value = opt.value;
                            break;
                        }}
                    }}
                }}
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
                desc.value = '{desc_licencia}';
                desc.dispatchEvent(new Event('change', {{ bubbles: true }}));
                desc.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            }}
            const btn = document.querySelector('#frmPopups\\\\:createPane input[value="Aceptar"]') || document.getElementById('frmPopups:j_idt273');
            if (btn) btn.click();
        """)
    else:
        # Cédula
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
                desc.value = 'CEDULA DE IDENTIDAD';
                desc.dispatchEvent(new Event('change', {{ bubbles: true }}));
                desc.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            }}
            const btn = document.querySelector('#frmPopups\\\\:createPane input[value="Aceptar"]') || document.getElementById('frmPopups:j_idt273');
            if (btn) btn.click();
        """)

    # Esperar confirmación de fila en la tabla judicial
    try:
        await page.wait_for_function(f"() => document.body.innerText.includes('{cedula}')", timeout=3000)
    except Exception:
        await page.wait_for_timeout(500)

    # 7. Ciclo de resolución y validación ultra-rápida del Captcha en la misma página
    c_el = await page.wait_for_selector("#imgCaptchaId", state="visible", timeout=6000)
    captcha_solved = False

    for _ in range(5):
        c_bytes = await c_el.screenshot()
        code = await solve_winocr_strict(c_bytes)

        if len(code) == 6:
            await page.fill("#captchaTxt", code)
            await page.evaluate("""
                const btn = document.querySelector('#frmIngresoFormulario input[value="Aceptar"]') || document.getElementById('j_idt170') || document.getElementById('j_idt155');
                if (btn) btn.click();
            """)

            # Comprobar si apareció el modal de confirmación en ~50ms
            try:
                modal_ok = await page.wait_for_function("""
                    () => {
                        const c = document.getElementById('frmPopups:confirmForm_container');
                        const s = document.getElementById('frmPopups:confirmForm_shade');
                        return (c && c.style.display !== 'none' && c.style.visibility !== 'hidden') || (s && s.style.display !== 'none');
                    }
                """, timeout=2500)
                if modal_ok:
                    captcha_solved = True
                    break
            except Exception:
                pass

        # Refrescar solo la imagen del captcha si no fue aceptado
        refresh_btn = await page.query_selector("a:has(img[src*='refresh']), #imgCaptchaId + a")
        if refresh_btn:
            await refresh_btn.click()
        else:
            await page.evaluate("document.getElementById('imgCaptchaId').src = '../captchaRegistro.jpg?' + Math.random();")
        await page.wait_for_timeout(350)

    if not captcha_solved:
        return None, None

    # 8. Confirmar "Si"
    await page.evaluate("""
        if (window.si) {
            window.si();
        } else {
            const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt214');
            if (btn) btn.click();
        }
    """)

    # 11. Clic reactivo en 'Ver formulario'
    await page.evaluate("""
        new Promise((resolve) => {
            let attempts = 0;
            const interval = setInterval(() => {
                attempts++;
                const buttons = Array.from(document.querySelectorAll('input[value="Ver formulario"]'));
                if (buttons.length > 0) {
                    buttons.forEach(btn => {
                        try { btn.click(); } catch(e) {}
                    });
                    clearInterval(interval);
                    resolve(true);
                } else if (attempts >= 40) {
                    clearInterval(interval);
                    resolve(false);
                }
            }, 100);
        });
    """)

    # 12. Esperar PDF oficial
    for _ in range(16):
        if captured_pdf and captured_pdf.startswith(b"%PDF"):
            break
        await asyncio.sleep(0.3)
    return captured_pdf, nombre, None


async def procesar_denuncia_judicial(cedula: str, raw_sector: str, tipo_denuncia: str = "cedula", tipo_licencia: str = None, nombre_manual: str = None):
    sector_info = limpiar_y_corregir_sector(raw_sector)
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    sector_limpio = sector_info["sector_limpio"]

    context = None
    try:
        browser = await asyncio.wait_for(get_shared_browser(), timeout=20.0)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC"
        )
        page = await context.new_page()

        captured_pdf, nombre, info_estado = await asyncio.wait_for(
            _ejecutar_formulario(
                page, context, cedula, dir_domicilio, dir_circunstancia, tipo_denuncia, tipo_licencia, nombre_manual
            ),
            timeout=45.0
        )

        if info_estado and isinstance(info_estado, dict):
            return False, info_estado, None

        if captured_pdf and captured_pdf.startswith(b"%PDF") and len(captured_pdf) > 20000:
            return True, {
                "nombre": nombre or "CIUDADANO REGISTRADO",
                "cedula": cedula,
                "sector": sector_limpio,
                "pdf_bytes": captured_pdf
            }, None
        else:
            return False, "El portal judicial no entregó el PDF.", None

    except asyncio.TimeoutError:
        return False, "Tiempo de espera agotado en el portal judicial (45s).", None
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ["target closed", "browser has been closed", "connection closed", "crash"]):
            try:
                await reset_shared_browser()
            except Exception:
                pass
        return False, f"Error: {str(e)}", None
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **¡Hola! Soy tu Asistente Multi-Trámites.**\n\n"
        "Puedo emitir documentos oficiales y enviarte el PDF listo:\n\n"
        "1️⃣ **Denuncias de Extravío (Judicatura):**\n"
        "• *Cédula:* `1708927502 Sector El Recreo`\n"
        "• *Licencia:* `1708927502 Solanda tipo B`\n\n"
        "2️⃣ **Récord Policial (Ministerio del Interior):**\n"
        "• *Ejemplo:* `record policial 1708927502`\n\n"
        "3️⃣ **Certificado de Bachiller (Ministerio de Educación):**\n"
        "• *Ejemplo:* `bachiller 1753445285`\n"
        "• *O:* `titulo de bachiller 1753445285`\n\n"
        "4️⃣ **Orden de Pago de Licencias (ANT):**\n"
        "• *Ejemplo:* `1728970128, orden de pago, tipo A, primera vez`\n"
        "• *O:* `orden de pago 1710034065 renovacion`\n\n"
        "🔄 **Comandos:**\n"
        "• `/orden_pago` — Iniciar consulta de orden de pago ANT.\n"
        "• `/actualizar` — Descarga actualizaciones de GitHub y reinicia el bot automáticamente."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Buscando actualizaciones en GitHub e instalando componentes...")
    try:
        res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True)
        salida = res.stdout.strip()
        
        # Asegurar que todas las librerías necesarias estén instaladas
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"], capture_output=True)
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)
        except Exception:
            pass
        try:
            subprocess.run([sys.executable, "-m", "patchright", "install", "chromium"], capture_output=True)
        except Exception:
            pass

        await msg.edit_text(f"🚀 ¡Actualización y componentes listos!\n\n`{salida[:200]}`\n\nReiniciando bot automáticamente...")
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit_text(f"❌ Error al actualizar: {e}")


async def orden_pago_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"flujo": "orden_pago"}
    await update.message.reply_text(
        "🚗 **Orden de Pago de Licencias ANT**\n\n"
        "Puedes enviar todos los datos en un solo mensaje:\n"
        "`1728970128, orden de pago, tipo A, primera vez`\n\n"
        "O indícame el número de **cédula** (10 dígitos) para comenzar.",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message or not update.message.text:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    text = update.message.text.strip()
    if not text:
        return

    # Si estábamos esperando que el usuario enviara el nombre manual para la denuncia
    estado_previo = user_states.get(user_id, {})
    if estado_previo.get("esperando_nombre") and estado_previo.get("cedula"):
        cedula = estado_previo["cedula"]
        sector = estado_previo.get("sector") or "QUITO"
        tipo_denuncia = estado_previo.get("tipo_denuncia", "cedula")
        tipo_licencia = estado_previo.get("tipo_licencia")
        nombre_manual = text.strip()

        del user_states[user_id]

        await procesar_y_responder(
            update,
            cedula=cedula,
            sector=sector,
            tipo_denuncia=tipo_denuncia,
            tipo_licencia=tipo_licencia,
            nombre_manual=nombre_manual
        )
        return

    # Revisa si es flujo Record Policial
    if detectar_mensaje_record(text) or user_states.get(user_id, {}).get("flujo") == "record":
        cedula = parsear_mensaje_record(text) or user_states.get(user_id, {}).get("cedula")
        
        if not cedula:
            diag = analizar_cedula_con_diagnostico(text)
            if diag.get("mensaje_error"):
                await update.message.reply_text(f"👮 **Récord Policial:**\n\n{diag['mensaje_error']}", parse_mode="Markdown")
                return
            user_states[user_id] = {"flujo": "record"}
            await update.message.reply_text("Para generar el Récord Policial, por favor indicame el número de **cédula** (10 dígitos).", parse_mode="Markdown")
            return
            
        if user_id in user_states:
            del user_states[user_id]
            
        return await handle_message_record(update, context, cedula)

    # Revisa si es flujo Bachiller (Ministerio de Educación)
    if detectar_mensaje_bachiller(text) or user_states.get(user_id, {}).get("flujo") == "bachiller":
        cedula = parsear_mensaje_bachiller(text) or user_states.get(user_id, {}).get("cedula")
        
        if not cedula:
            diag = analizar_cedula_con_diagnostico(text)
            if diag.get("mensaje_error"):
                await update.message.reply_text(f"🎓 **Título de Bachiller:**\n\n{diag['mensaje_error']}", parse_mode="Markdown")
                return
            user_states[user_id] = {"flujo": "bachiller"}
            await update.message.reply_text("Para consultar el **Título de Bachiller**, por favor indícame el número de **cédula** (10 dígitos).", parse_mode="Markdown")
            return
            
        if user_id in user_states:
            del user_states[user_id]
            
        return await handle_message_bachiller(update, context, cedula)

    # Revisa si es flujo Orden de Pago ANT (Licencias)
    if detectar_mensaje_ant(text) or user_states.get(user_id, {}).get("flujo") == "orden_pago":
        parsed_ant = parsear_mensaje_ant(text)
        return await handle_message_ant(update, context, parsed_ant, user_states)

    # Parsear el mensaje inteligentemente
    parsed = parsear_mensaje(text)

    # Si hay error explícito de cédula mal tipeada (>10 dígitos, con letras, etc.), avisar de inmediato
    if parsed.get("error_cedula"):
        await update.message.reply_text(parsed["error_cedula"], parse_mode="Markdown")
        return

    cedula = parsed["cedula"]
    sector = parsed["sector"]
    tipo_licencia = parsed["tipo_licencia"]
    tipo_denuncia = parsed["tipo_denuncia"]

    # Si el usuario ya tenia datos guardados de una conversacion anterior, combinar
    estado_previo = user_states.get(user_id, {})

    if not cedula and estado_previo.get("cedula"):
        cedula = estado_previo["cedula"]
    if not sector and estado_previo.get("sector"):
        sector = estado_previo["sector"]
    if not tipo_licencia and estado_previo.get("tipo_licencia"):
        tipo_licencia = estado_previo["tipo_licencia"]
    if estado_previo.get("tipo_denuncia"):
        tipo_denuncia = estado_previo["tipo_denuncia"]

    # Si el tipo de denuncia es licencia y falta el tipo, preguntar
    if tipo_denuncia == "licencia" and not tipo_licencia:
        # Revisar si el sector tiene la letra (ej: solo enviaron "B")
        if sector and len(sector.strip()) == 1 and sector.strip().upper() in TIPOS_LICENCIA_VALIDOS:
            tipo_licencia = sector.strip().upper()
            sector = estado_previo.get("sector", None)

    # Determinar que falta
    falta_cedula = not cedula
    falta_sector = not sector or len(sector.strip()) < 2
    falta_tipo_licencia = (tipo_denuncia == "licencia") and not tipo_licencia

    if falta_cedula:
        user_states[user_id] = {
            "sector": sector,
            "tipo_licencia": tipo_licencia,
            "tipo_denuncia": tipo_denuncia
        }
        await update.message.reply_text(
            "Por favor indicame el numero de **cedula** (10 digitos).",
            parse_mode="Markdown"
        )
        return

    if falta_sector:
        user_states[user_id] = {
            "cedula": cedula,
            "tipo_licencia": tipo_licencia,
            "tipo_denuncia": tipo_denuncia
        }
        await update.message.reply_text(
            f"Cedula recibida: `{cedula}`\n\n"
            "En que **sector o lugar** ocurrio el extravio?\n"
            "*(Ejemplo: Solanda, El Recreo, Chillogallo, Centro Historico...)*",
            parse_mode="Markdown"
        )
        return

    if falta_tipo_licencia:
        user_states[user_id] = {
            "cedula": cedula,
            "sector": sector,
            "tipo_denuncia": "licencia"
        }
        await update.message.reply_text(
            f"Cedula: `{cedula}` | Sector: `{sector}`\n\n"
            "Que **tipo de licencia** es? (A, B, C, D, E, F...)",
            parse_mode="Markdown"
        )
        return

    # Tenemos todos los datos
    if user_id in user_states:
        del user_states[user_id]

    await procesar_y_responder(update, cedula, sector, tipo_denuncia, tipo_licencia)


async def procesar_y_responder(update: Update, cedula: str, sector: str, tipo_denuncia: str = "cedula", tipo_licencia: str = None, nombre_manual: str = None):
    if tipo_denuncia == "licencia":
        desc_tipo = f"Licencia tipo {tipo_licencia}" if tipo_licencia else "Licencia de conducir"
        emoji_doc = "🪪"
    else:
        desc_tipo = "Cédula de Identidad"
        emoji_doc = "🆔"

    msg_espera = await update.message.reply_text(
        f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
        f"{emoji_doc} Documento: `{desc_tipo}`\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"📍 Sector: `{sector}`\n\n"
        f"*Procesando, por favor espera...*",
        parse_mode="Markdown"
    )

    t0 = time.time()
    ultimo_error = "No se pudo emitir el documento."

    for intento in range(1, MAX_RETRIES_DENUNCIA + 1):
        if time.time() - t0 > TOTAL_TIMEOUT_DENUNCIA:
            break

        success, result, _ = await procesar_denuncia_judicial(cedula, sector, tipo_denuncia, tipo_licencia, nombre_manual)

        if success:
            elapsed = time.time() - t0
            pdf_bytes = result["pdf_bytes"]
            nombre = result["nombre"]
            sector_fmt = result["sector"]

            if tipo_denuncia == "licencia":
                caption = (
                    f"✅ **DENUNCIA DE LICENCIA GENERADA CON ÉXITO!** ({elapsed:.1f}s)\n\n"
                    f"👤 **Nombre:** {nombre}\n"
                    f"🆔 **Cédula:** `{cedula}`\n"
                    f"🪪 **Documento:** {desc_tipo}\n"
                    f"📍 **Lugar:** {sector_fmt}\n\n"
                    f"📄 *Documento oficial emitido por el Consejo de la Judicatura.*"
                )
                nombre_archivo = f"Denuncia_Licencia_{cedula}.pdf"
            else:
                caption = (
                    f"✅ **DENUNCIA GENERADA CON ÉXITO!** ({elapsed:.1f}s)\n\n"
                    f"👤 **Nombre:** {nombre}\n"
                    f"🆔 **Cédula:** `{cedula}`\n"
                    f"📍 **Lugar:** {sector_fmt}\n\n"
                    f"📄 *Documento oficial emitido por el Consejo de la Judicatura (válido para trámites legales y Registro Civil).*"
                )
                nombre_archivo = f"Denuncia_{cedula}.pdf"

            pdf_file = io.BytesIO(pdf_bytes)
            pdf_file.name = nombre_archivo

            await update.message.reply_document(
                document=InputFile(pdf_file, filename=nombre_archivo),
                caption=caption,
                parse_mode="Markdown"
            )
            try:
                await msg_espera.delete()
            except Exception:
                pass
            return

        # 1. Si la plataforma solicita los nombres (porque el Registro Civil no los autocompletó):
        if isinstance(result, dict) and result.get("tipo") == "REQUIERE_NOMBRES":
            user_id = update.effective_user.id if update.effective_user else 0
            user_states[user_id] = {
                "flujo": "denuncia",
                "cedula": cedula,
                "sector": sector,
                "tipo_denuncia": tipo_denuncia,
                "tipo_licencia": tipo_licencia,
                "esperando_nombre": True
            }
            msg_pide_nombres = (
                f"👤 **El portal del Consejo de la Judicatura solicita los nombres.**\n\n"
                f"🆔 Cédula: `{cedula}`\n\n"
                f"⚠️ El sistema no pudo consultar automáticamente el nombre en el Registro Civil.\n\n"
                f"✍️ *Por favor responde directamente a este mensaje con los **Nombres y Apellidos completos** del titular.*"
            )
            try:
                await msg_espera.edit_text(msg_pide_nombres, parse_mode="Markdown")
            except Exception:
                try:
                    await update.message.reply_text(msg_pide_nombres, parse_mode="Markdown")
                except Exception:
                    pass
            return

        # 2. Si la plataforma rechazó la cédula (cédula inválida o no registrada):
        if isinstance(result, dict) and result.get("tipo") == "ERROR_CEDULA_PLATAFORMA":
            detalle = result.get("mensaje", "Número de cédula no válido")
            msg_err_portal = (
                f"❌ **Cédula rechazada por el Consejo de la Judicatura**\n\n"
                f"🆔 Cédula: `{cedula}`\n"
                f"⚠️ **Mensaje del portal:** {detalle}\n\n"
                f"💡 *Por favor verifica que el número de cédula esté bien escrito e inténtalo de nuevo.*"
            )
            try:
                await msg_espera.edit_text(msg_err_portal, parse_mode="Markdown")
            except Exception:
                try:
                    await update.message.reply_text(msg_err_portal, parse_mode="Markdown")
                except Exception:
                    pass
            return

        if isinstance(result, str):
            ultimo_error = result

        if intento < MAX_RETRIES_DENUNCIA:
            try:
                await msg_espera.edit_text(
                    f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
                    f"{emoji_doc} Documento: `{desc_tipo}`\n"
                    f"🆔 Cédula: `{cedula}`\n"
                    f"📍 Sector: `{sector}`\n\n"
                    f"🔄 *Reintentando automáticamente (prueba {intento + 1} de {MAX_RETRIES_DENUNCIA})...*",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await asyncio.sleep(2)

    # Si no se logró tras agotar las 15 pruebas:
    tiempo_total = time.time() - t0
    mensaje_fallo = (
        f"❌ **No se pudo generar la denuncia tras {MAX_RETRIES_DENUNCIA} pruebas.** ({tiempo_total:.1f}s)\n\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"⚠️ **Motivo:** El portal del Consejo de la Judicatura no respondió a tiempo o presentó saturación.\n\n"
        f"💡 **Recomendación:**\n"
        f"• Verifica que el número de cédula `{cedula}` sea correcto.\n"
        f"• Vuelve a enviar el mensaje en unos minutos.\n"
        f"• O consulta otro trámite como Récord Policial, Bachiller o Licencia."
    )
    try:
        await msg_espera.edit_text(mensaje_fallo, parse_mode="Markdown")
    except Exception:
        try:
            await update.message.reply_text(mensaje_fallo, parse_mode="Markdown")
        except Exception:
            pass


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Manejador global de errores para asegurar que ninguna excepción no controlada detenga el bot."""
    print(f"[ERROR GLOBAL] Excepción en actualización: {context.error}", flush=True)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Ocurrió un error inesperado al procesar la solicitud. Por favor intenta de nuevo en unos momentos.",
                parse_mode="Markdown"
            )
        except Exception:
            pass


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        token_file = os.path.join(os.path.dirname(__file__), "telegram_token.txt")
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()

    if not token:
        print("====================================================================")
        print("                 CONFIGURACION DEL BOT DE TELEGRAM")
        print("====================================================================")
        print("Por favor pega tu TOKEN de Telegram (obtenido de @BotFather):")
        try:
            token = input("Token de Telegram: ").strip()
            if token:
                with open(os.path.join(os.path.dirname(__file__), "telegram_token.txt"), "w", encoding="utf-8") as f:
                    f.write(token)
        except Exception:
            pass

    if not token:
        print("ERROR: Se requiere un TOKEN de Telegram para iniciar.")
        return

    print("====================================================================", flush=True)
    print("           BOT DE TELEGRAM DE DENUNCIAS INICIADO", flush=True)
    print("====================================================================", flush=True)
    print("El bot esta escuchando mensajes en tiempo real.", flush=True)
    print("Soporta denuncias de Cedula y de Licencia de conducir.", flush=True)
    print("====================================================================", flush=True)

    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .post_shutdown(lambda application: reset_shared_browser())
        .build()
    )
    app.add_error_handler(global_error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("actualizar", update_command))
    app.add_handler(CommandHandler("orden_pago", orden_pago_command))
    app.add_handler(CommandHandler("licencia", orden_pago_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()