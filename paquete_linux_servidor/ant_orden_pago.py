"""
ant_orden_pago.py
Modulo que maneja la generacion de la Orden de Pago de Licencia en el portal ANT.
URL: https://consultaweb.ant.gob.ec/SVT/paginas/portal/svf_solicitar_servicio.jsp?ps_param_tip_serv=LIC
"""
import asyncio
import re
from playwright.async_api import async_playwright

ANT_URL = "https://consultaweb.ant.gob.ec/SVT/paginas/portal/svf_solicitar_servicio.jsp?ps_param_tip_serv=LIC"

# Palabras clave que activan el flujo ANT
KEYWORDS_ANT = [
    "orden de pago", "orden pago", "pago ant", "ant licencia", "ant orden",
    "orden ant", "solicitar orden", "pago de licencia ant",
]

# Mapeo de lo que dice el usuario al texto que aparece en el select del portal
TRAMITE_MAP = [
    ("primera vez", "PRIMERA"),
    ("primera",     "PRIMERA"),
    ("emision",     "PRIMERA"),
    ("duplicado",   "DUPLICADO"),
    ("duplicar",    "DUPLICADO"),
    ("renovacion",  "RENOVACION"),
    ("renovar",     "RENOVACION"),
    ("renov",       "RENOVACION"),
]


def detectar_mensaje_ant(texto: str) -> bool:
    """Retorna True si el mensaje parece ser una solicitud de orden de pago ANT."""
    t = texto.lower()
    for kw in KEYWORDS_ANT:
        if kw in t:
            return True
    # Tambien si dice "orden" junto con alguna palabra de tipo de tramite
    if "orden" in t and any(k in t for k, _ in TRAMITE_MAP):
        return True
    return False


def parsear_mensaje_ant(texto: str) -> dict:
    """
    Extrae cedula, tipo_tramite y tipo_licencia del mensaje.
    Retorna dict con keys: cedula, tipo_tramite, tipo_licencia
    (cualquiera puede ser None si no se encontro)
    """
    resultado = {"cedula": None, "tipo_tramite": None, "tipo_licencia": None}
    t = texto.lower()

    # Cedula (10 digitos)
    m = re.search(r"\b(\d{10})\b", texto)
    if m:
        resultado["cedula"] = m.group(1)

    # Tipo de tramite
    for keyword, valor in TRAMITE_MAP:
        if keyword in t:
            resultado["tipo_tramite"] = valor
            break

    # Tipo de licencia (letra A-F despues de "tipo", "licencia tipo", o sola al final)
    m_lic = re.search(
        r"(?:tipo\s+|licencia\s+tipo\s+|licencia\s+)([A-Fa-f])\b"
        r"|(?<!\w)([A-Fa-f])(?!\w)(?=\s*[,.\n]|\s*$)",
        texto, re.IGNORECASE
    )
    if m_lic:
        letra = next((g for g in m_lic.groups() if g), None)
        if letra:
            resultado["tipo_licencia"] = letra.upper()

    return resultado


async def _cerrar_modal_ant(page):
    """Cierra cualquier modal jQuery abierto en el portal ANT."""
    try:
        await page.evaluate("""
            () => {
                try {
                    if (jQuery && jQuery('#IFrameDiv').length) {
                        jQuery('#IFrameDiv').dialog('close');
                    }
                } catch(e) {}
            }
        """)
    except Exception:
        pass
    await page.wait_for_timeout(400)


async def _seleccionar_tramite(fl, tramite_kw: str) -> str | None:
    """Selecciona el tipo de tramite en el select del iframe."""
    try:
        resultado = await fl.locator("#id_servicio").evaluate(f"""
            (select) => {{
                const kw = '{tramite_kw}';
                for (const opt of select.options) {{
                    if (opt.text.toUpperCase().includes(kw)) {{
                        select.value = opt.value;
                        select.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return opt.text.trim();
                    }}
                }}
                return null;
            }}
        """)
        return resultado
    except Exception:
        return None


async def _actualizar_correo_en_pagina(page, fl, correo_nuevo: str) -> bool:
    """
    Maneja el flujo de actualizacion de correo en el portal ANT.
    Asume que el modal de correo ya esta mostrado.
    Retorna True si se actualizo correctamente.
    """
    try:
        # 1. Cerrar el modal informativo
        await _cerrar_modal_ant(page)

        # 2. Hacer clic en el icono de actualizar correo (dispara div_actualiza_correo())
        await fl.locator("img[src*='icoActualizar']").click()
        await page.wait_for_timeout(3500)

        # 3. Ahora el iframe muestra la pagina de actualizacion de correo
        #    Buscar campo de email y llenarlo
        for sel in [
            "input[type='email']",
            "input[name*='mail']",
            "input[id*='mail']",
            "input[name*='correo']",
            "input[id*='correo']",
        ]:
            try:
                el = fl.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.fill(correo_nuevo)
                    await page.wait_for_timeout(300)
                    break
            except Exception:
                continue

        # 4. Buscar y hacer clic en el boton de guardar/aceptar
        for sel in [
            "img[alt*='Guardar']", "img[alt*='Aceptar']", "img[alt*='Actualizar']",
            "img[src*='icoAceptar']", "img[src*='icoGuardar']",
            "input[type='submit']", "input[value*='Guardar']",
            "input[value*='Aceptar']", "button[type='submit']",
        ]:
            try:
                el = fl.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue

        return False

    except Exception:
        return False


async def procesar_orden_pago_ant(
    cedula: str,
    tipo_tramite_kw: str,
    tipo_licencia: str,
    correo_nuevo: str = None
) -> tuple:
    """
    Genera la orden de pago de licencia en el portal ANT.

    Parametros:
      cedula: numero de cedula (10 digitos)
      tipo_tramite_kw: "RENOVACION", "DUPLICADO" o "PRIMERA"
      tipo_licencia: "A", "B", "C", etc.
      correo_nuevo: si no es None, intenta actualizar el correo antes de continuar

    Retorna tupla (status, data, nombre):
      ("ok", pdf_bytes, nombre)          -> exito
      ("email_needed", correo_actual, None) -> el portal pide actualizar correo
      (False, mensaje_error, None)        -> falla
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
            locale="es-EC"
        )
        page = await context.new_page()

        # Capturar PDF via intercepcion de respuestas
        captured_pdf = None
        captured_pdf_ant = None

        async def route_handler(route, request):
            nonlocal captured_pdf_ant
            try:
                resp = await route.fetch()
                ct = resp.headers.get("content-type", "").lower()
                body = await resp.body()
                if ("pdf" in ct or body.startswith(b"%PDF")) and len(body) > 5000:
                    captured_pdf_ant = body
                await route.fulfill(response=resp)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route("**/*", route_handler)

        # Capturar descargas directas
        pdf_from_download = None

        async def on_download(download):
            nonlocal pdf_from_download
            try:
                path = await download.path()
                if path:
                    with open(path, "rb") as f:
                        pdf_from_download = f.read()
            except Exception:
                pass

        page.on("download", on_download)

        try:
            # 1. Navegar al portal ANT
            await page.goto(ANT_URL, timeout=45000)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2500)

            # 2. Obtener el frame_locator del iframe principal
            fl = page.frame_locator("#iframe_detalle")

            # 3. Esperar que el select de servicios se llene via AJAX
            try:
                await fl.locator("#id_servicio").wait_for(timeout=15000)
                await page.wait_for_timeout(1500)
            except Exception:
                await browser.close()
                return False, "El portal ANT no respondio a tiempo.", None

            # 4. Seleccionar tipo de tramite
            tramite_texto = await _seleccionar_tramite(fl, tipo_tramite_kw)
            if not tramite_texto:
                await browser.close()
                return False, f"Tipo de tramite '{tipo_tramite_kw}' no encontrado en el portal ANT.", None

            await page.wait_for_timeout(1500)

            # 5. Llenar cedula y presionar Consultar
            await fl.locator("#txtIdentificacion").fill(cedula)
            await page.wait_for_timeout(300)

            # Hacer clic en el boton Consultar (img con id="validar" o onclick="recuperaNombres()")
            consultado = False
            for sel in [
                "#validar",
                "img[onclick*='recuperaNombres']",
                "img[alt='Regresar'][onclick*='recuperaNombres']",
            ]:
                try:
                    el = fl.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        consultado = True
                        break
                except Exception:
                    continue

            if not consultado:
                # Fallback: presionar Enter en el campo
                await fl.locator("#txtIdentificacion").press("Enter")

            # 6. Esperar respuesta AJAX del servidor ANT
            await page.wait_for_timeout(4500)

            # 7. Revisar si hay errores en modal
            dialog_msg = await page.evaluate("""
                () => {
                    const el = document.getElementById('divMSJ');
                    return el ? el.innerText.trim() : '';
                }
            """)

            # 8. Revisar si se pide actualizar correo
            parent_msg = await page.evaluate("""
                () => {
                    const el = document.getElementById('divMENSAJE');
                    return el ? el.innerText.trim() : '';
                }
            """)

            email_update_needed = (
                "actualice" in parent_msg.lower()
                or "actualizar" in parent_msg.lower()
                or "correo" in parent_msg.lower()
                or "email" in parent_msg.lower()
            )

            if email_update_needed:
                await browser.close()
                return False, "⚠️ *La ANT requiere que actualices tu correo electrónico.* No se puede generar la orden por aquí. Por favor actualízalo directamente en la página de la ANT y vuelve a intentarlo.", None

            # 9. Verificar que el nombre del cliente fue cargado (consulta exitosa)
            nombre = "CLIENTE ANT"
            try:
                nombre_val = await fl.locator("#text_nombre_cliente").input_value()
                if nombre_val and nombre_val.strip():
                    nombre = nombre_val.strip()
            except Exception:
                pass

            # Si el nombre sigue vacio, probablemente la cedula no existe
            if nombre == "CLIENTE ANT" and dialog_msg:
                await browser.close()
                return False, f"El portal ANT informo: {dialog_msg}", None

            # 10. Esperar que aparezca el combo de tipo de licencia
            await page.wait_for_timeout(1500)

            # Seleccionar tipo de licencia en el combo dinamico
            if tipo_licencia:
                try:
                    await fl.locator("#div_combo_parametro_servicio select").evaluate(f"""
                        (select) => {{
                            const val = '{tipo_licencia.upper()}';
                            for (const opt of select.options) {{
                                if (opt.value.toUpperCase() === val || opt.text.toUpperCase().includes(val)) {{
                                    select.value = opt.value;
                                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                                    return;
                                }}
                            }}
                        }}
                    """)
                    await page.wait_for_timeout(600)
                except Exception:
                    pass

            # 11. Hacer clic en el boton Continuar/Siguiente
            continuar_clickeado = False

            # Intentar hacer clic en cualquier elemento dentro de #div_boton_continuar
            for sel in [
                "#div_boton_continuar img",
                "#div_boton_continuar input",
                "#div_boton_continuar button",
                "#div_boton_continuar a",
            ]:
                try:
                    el = fl.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.click()
                        continuar_clickeado = True
                        break
                except Exception:
                    continue

            if not continuar_clickeado:
                # Fallback: llamar generaTramite() via JS desde el iframe
                try:
                    frame_obj = None
                    for frm in page.frames:
                        if "svp_solicitar" in frm.url or "svp_solicitar" in (frm.name or ""):
                            frame_obj = frm
                            break
                    if frame_obj:
                        await frame_obj.evaluate("if (typeof generaTramite === 'function') generaTramite();")
                        continuar_clickeado = True
                except Exception:
                    pass

            if not continuar_clickeado:
                await browser.close()
                return False, "No se pudo hacer clic en el boton Continuar del portal ANT.", None

            await page.wait_for_timeout(4000)

            # 12. Buscar y hacer clic en el link de descarga "aqui"
            for _ in range(15):
                pdf_final = captured_pdf_ant or pdf_from_download
                if pdf_final and pdf_final.startswith(b"%PDF") and len(pdf_final) > 5000:
                    break

                # Intentar hacer clic en el link de descarga si aparece
                for link_sel in [
                    "a:text('aqui')", "a:text('aquí')",
                    "a:text-matches('Descargue', 'i')",
                    "a:text-matches('orden', 'i')",
                    "a[href*='pdf']", "a[href*='orden']",
                    "a[onclick*='pdf']",
                ]:
                    try:
                        el = fl.locator(link_sel).first
                        if await el.is_visible(timeout=1000):
                            async with page.expect_download(timeout=10000) as dl_info:
                                await el.click()
                            dl = await dl_info.value
                            dl_path = await dl.path()
                            if dl_path:
                                with open(dl_path, "rb") as f:
                                    pdf_from_download = f.read()
                            break
                    except Exception:
                        continue

                await asyncio.sleep(1)

            await browser.close()

            pdf_final = captured_pdf_ant or pdf_from_download
            if pdf_final and len(pdf_final) > 5000:
                return "ok", pdf_final, nombre
            else:
                return False, "No se pudo obtener el PDF de la orden de pago del portal ANT.", None

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error inesperado: {str(e)}", None