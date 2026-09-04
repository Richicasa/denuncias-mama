import asyncio
import datetime
import io
import os
import re
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

try:
    import winocr
except ImportError:
    winocr = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

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
    """Intenta leer el captcha usando WinOCR (Windows) o Tesseract como respaldo."""
    raw = ""
    if winocr:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            result = await winocr.recognize_pil(img, "es")
            raw = result.text if hasattr(result, "text") else str(result)
        except Exception:
            pass

    if not raw and pytesseract:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            raw = pytesseract.image_to_string(img, config="--psm 8 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        except Exception:
            pass

    # Limpiar: solo letras y numeros, exactamente 6 caracteres
    code = re.sub(r"[^A-Za-z0-9]", "", raw).strip()
    return code[:6] if len(code) >= 6 else code


def parsear_mensaje(texto: str) -> dict:
    """
    Parser inteligente: extrae cedula (10 digitos), sector y tipo_licencia
    del mensaje sin importar el orden. Detecta si es denuncia de cedula o licencia.
    """
    resultado = {
        "cedula": None,
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

    # 2. Extraer cedula (10 digitos consecutivos)
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


async def _ejecutar_formulario(page, context, cedula, dir_domicilio, dir_circunstancia, tipo_denuncia, tipo_licencia=None):
    """
    Ejecuta el flujo completo del formulario en el portal de la Judicatura.
    tipo_denuncia: "cedula" o "licencia"
    tipo_licencia: "A", "B", etc. (solo si tipo_denuncia == "licencia")
    Retorna (captured_pdf_bytes, nombre_completo)
    """
    captured_pdf = None

    async def route_interceptor(route, request):
        nonlocal captured_pdf
        try:
            resp = await route.fetch()
            ct = resp.headers.get("content-type", "").lower()
            if "application/pdf" in ct or "pdf" in ct or "impresionreporte" in request.url.lower():
                body = await resp.body()
                if body.startswith(b"%PDF"):
                    captured_pdf = body
            await route.fulfill(response=resp)
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    await page.route("**/*", route_interceptor)

    async def handle_popup(popup):
        try:
            await popup.wait_for_load_state("networkidle")
        except Exception:
            pass
    context.on("page", handle_popup)

    await page.goto(
        "https://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf",
        timeout=45000
    )
    await page.wait_for_load_state("networkidle")

    # Captcha
    valid_captcha_code = ""
    for _ in range(12):
        c_el = await page.query_selector("#imgCaptchaId")
        if not c_el:
            break
        c_bytes = await c_el.screenshot()
        code = await solve_winocr_strict(c_bytes)
        if len(code) == 6:
            valid_captcha_code = code
            break
        await page.reload()
        await page.wait_for_load_state("networkidle")

    if not valid_captcha_code:
        return None, None

    # 1. Cedula
    await page.fill("#numeroIdentificacion", cedula)
    await page.locator("#numeroIdentificacion").blur()
    await page.wait_for_timeout(1800)

    nombre = await page.input_value("#nombreCompleto")
    if not nombre:
        nombre = "CIUDADANO REGISTRADO"

    # 2. Domicilio
    await page.select_option("#provinciaDomicilio", value="17")
    await page.wait_for_timeout(800)
    await page.select_option("#cantonDomicilio", label="QUITO")
    await page.fill("#direccionDomicilio", dir_domicilio)

    # 3. Extravio
    await page.select_option("#provinciaExtravio", value="17")
    await page.wait_for_timeout(800)
    await page.select_option("#cantonExtravio", label="QUITO")
    await page.fill("#direccionCircunstancia", dir_circunstancia)

    # 4. Fecha habil
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

    # 5. Agregar documento
    await page.locator('input[value="+ Agregar un nuevo documento"]').click(force=True)
    await page.wait_for_timeout(800)

    if tipo_denuncia == "licencia":
        # Buscar el valor del option "Licencia de conducir" en el select dinamicamente
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
                    // Buscar la opcion por texto como fallback
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
        """)
    else:
        # Denuncia de CEDULA (comportamiento original)
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

    # 6. Enviar Captcha
    await page.fill("#captchaTxt", valid_captcha_code)
    await page.evaluate("""
        const btn = document.getElementById('j_idt170') || document.querySelector('input[value="Aceptar"]');
        if (btn) btn.click();
    """)
    await page.wait_for_timeout(2500)

    # 7. Confirmar Si
    await page.evaluate("""
        if (window.si) {
            window.si();
        } else {
            const btn = document.querySelector('#frmPopups\\\\:confirmForm input[value="Si"]') || document.getElementById('frmPopups:j_idt220');
            if (btn) btn.click();
        }
    """)

    # 8. Clic en Ver formulario
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
                } else if (attempts >= 30) {
                    clearInterval(interval);
                    resolve(false);
                }
            }, 250);
        });
    """)

    # 9. Esperar PDF oficial
    for _ in range(12):
        if captured_pdf and captured_pdf.startswith(b"%PDF"):
            break
        await asyncio.sleep(1)

    return captured_pdf, nombre


async def procesar_denuncia_judicial(cedula: str, raw_sector: str, tipo_denuncia: str = "cedula", tipo_licencia: str = None):
    sector_info = limpiar_y_corregir_sector(raw_sector)
    dir_domicilio = sector_info["direccion_domicilio"]
    dir_circunstancia = sector_info["direccion_circunstancia"]
    sector_limpio = sector_info["sector_limpio"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC"
        )
        page = await context.new_page()

        try:
            captured_pdf, nombre = await _ejecutar_formulario(
                page, context, cedula, dir_domicilio, dir_circunstancia, tipo_denuncia, tipo_licencia
            )
            await browser.close()

            if captured_pdf and captured_pdf.startswith(b"%PDF") and len(captured_pdf) > 20000:
                return True, {
                    "nombre": nombre or "CIUDADANO REGISTRADO",
                    "cedula": cedula,
                    "sector": sector_limpio,
                    "pdf_bytes": captured_pdf
                }, None
            else:
                return False, "Reintentando emision...", None

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error: {str(e)}", None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Hola! Soy tu Asistente para Denuncias Judiciales de Extravio.**\n\n"
        "Puedo generar tu denuncia oficial del Consejo de la Judicatura y enviarte el PDF listo para imprimir.\n\n"
        "📝 **Puedo procesar dos tipos de denuncia:**\n"
        "• **Cedula de Identidad** — *Ejemplo:* `1708927502 Sector El Recreo`\n"
        "• **Licencia de conducir** — *Ejemplo:* `1708927502 Solanda licencia tipo B`\n\n"
        "💡 Puedes escribir los datos en cualquier orden, los reconozco automaticamente."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Parsear el mensaje inteligentemente
    parsed = parsear_mensaje(text)

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


async def procesar_y_responder(update: Update, cedula: str, sector: str, tipo_denuncia: str = "cedula", tipo_licencia: str = None):
    if tipo_denuncia == "licencia":
        desc_tipo = f"Licencia tipo {tipo_licencia}" if tipo_licencia else "Licencia de conducir"
        emoji_doc = "🪪"
    else:
        desc_tipo = "Cedula de Identidad"
        emoji_doc = "🆔"

    msg_espera = await update.message.reply_text(
        f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
        f"{emoji_doc} Documento: `{desc_tipo}`\n"
        f"🆔 Cedula: `{cedula}`\n"
        f"📍 Sector: `{sector}`\n\n"
        f"*Procesando, por favor espera...*",
        parse_mode="Markdown"
    )

    intento = 1
    t0 = time.time()

    while True:
        success, result, _ = await procesar_denuncia_judicial(cedula, sector, tipo_denuncia, tipo_licencia)

        if success:
            elapsed = time.time() - t0
            pdf_bytes = result["pdf_bytes"]
            nombre = result["nombre"]
            sector_fmt = result["sector"]

            if tipo_denuncia == "licencia":
                caption = (
                    f"✅ **DENUNCIA DE LICENCIA GENERADA CON EXITO!** ({elapsed:.1f}s)\n\n"
                    f"👤 **Nombre:** {nombre}\n"
                    f"🆔 **Cedula:** `{cedula}`\n"
                    f"🪪 **Documento:** {desc_tipo}\n"
                    f"📍 **Lugar:** {sector_fmt}\n\n"
                    f"📄 *Documento oficial emitido por el Consejo de la Judicatura.*"
                )
                nombre_archivo = f"Denuncia_Licencia_{cedula}.pdf"
            else:
                caption = (
                    f"✅ **DENUNCIA GENERADA CON EXITO!** ({elapsed:.1f}s)\n\n"
                    f"👤 **Nombre:** {nombre}\n"
                    f"🆔 **Cedula:** `{cedula}`\n"
                    f"📍 **Lugar:** {sector_fmt}\n\n"
                    f"📄 *Documento oficial emitido por el Consejo de la Judicatura (valido para tramites legales y Registro Civil).*"
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

        # No hubo PDF: reintentar silenciosamente
        intento += 1
        try:
            await msg_espera.edit_text(
                f"⏳ **Generando denuncia oficial ante el Consejo de la Judicatura...**\n"
                f"{emoji_doc} Documento: `{desc_tipo}`\n"
                f"🆔 Cedula: `{cedula}`\n"
                f"📍 Sector: `{sector}`\n\n"
                f"🔄 *Reintentando automaticamente (intento {intento})...*",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await asyncio.sleep(2)


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

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()