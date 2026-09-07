import asyncio
import io
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
    m = re.search(r"\b(\d{10})\b", texto)
    if m:
        return m.group(1)
    return None

async def procesar_record_policial(cedula: str) -> tuple:
    """
    Genera el Certificado de Antecedentes Penales (Record Policial).
    Retorna: (success: bool, pdf_bytes_or_error: bytes|str, nombre: str|None)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security'
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="es-EC",
            accept_downloads=True
        )

        # Evasion basica de deteccion
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # Variable para capturar el PDF por cualquiera de los 3 metodos posibles
        captured_pdf = None

        # 1. Metodo de captura: Intercepcion de respuestas de red (HTTP route)
        async def route_interceptor(route, request):
            nonlocal captured_pdf
            try:
                resp = await route.fetch()
                ct = resp.headers.get("content-type", "").lower()
                body = await resp.body()
                if ("application/pdf" in ct or "pdf" in ct or body.startswith(b"%PDF")) and len(body) > 5000:
                    captured_pdf = body
                await route.fulfill(response=resp)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route("**/*", route_interceptor)

        # 2. Metodo de captura: Evento de descarga del navegador (download)
        async def on_download(download):
            nonlocal captured_pdf
            try:
                path = await download.path()
                if path:
                    with open(path, "rb") as f:
                        data = f.read()
                        if len(data) > 5000:
                            captured_pdf = data
            except Exception:
                pass

        page.on("download", on_download)

        # 3. Metodo de captura: Manejo de ventanas emergentes (popups / target=_blank)
        async def on_popup(popup):
            nonlocal captured_pdf
            try:
                await popup.wait_for_load_state("domcontentloaded")
                popup.on("download", on_download)
            except Exception:
                pass

        page.on("popup", on_popup)

        # 4. Capturar mensajes de alerta de JavaScript nativo
        dialog_message = None
        async def on_dialog(dialog):
            nonlocal dialog_message
            dialog_message = dialog.message
            try:
                await dialog.accept()
            except Exception:
                pass

        page.on("dialog", on_dialog)

        try:
            # Navegar sin esperar networkidle (networkidle causaba timeout por scripts de analiticas)
            await page.goto(URL_RECORD, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            # Verificar si hay bloqueo directo por firewall Incapsula
            html_content = await page.content()
            if "Incapsula" in html_content and "txtCi" not in html_content:
                await browser.close()
                return False, "⚠️ El servidor del Ministerio tiene activo el filtro de seguridad Incapsula. Intenta en unos minutos.", None

            # Esperar a que el campo de cedula (#txtCi) este disponible
            try:
                await page.wait_for_selector("#txtCi", state="visible", timeout=15000)
            except Exception:
                if dialog_message:
                    await browser.close()
                    return False, f"Alerta del Ministerio: {dialog_message}", None
                await browser.close()
                return False, "No se pudo cargar el formulario del Ministerio del Interior a tiempo.", None

            # Asegurar que el radio button de 'Cedula de Identidad' este marcado
            try:
                radio_ced = page.locator("#rbtType1")
                if await radio_ced.is_visible():
                    await radio_ced.check()
            except Exception:
                pass

            # 1. Ingresar numero de cedula
            await page.fill("#txtCi", cedula)
            await page.wait_for_timeout(300)

            # 2. Clic en Siguiente 1 (#btnSig1)
            btn_sig1 = page.locator("#btnSig1")
            await btn_sig1.click()

            # 3. Esperar a que cargue el segundo paso (aparece #txtMotivo)
            try:
                await page.wait_for_selector("#txtMotivo", state="visible", timeout=12000)
            except Exception:
                if dialog_message:
                    await browser.close()
                    return False, f"El portal informo: {dialog_message}", None
                
                # Revisar si mostro un mensaje de error en la pagina
                body_text = await page.inner_text("body")
                for err_kw in ["no se encuentra", "no existe", "incorrecta", "error", "no registrada"]:
                    if err_kw in body_text.lower():
                        await browser.close()
                        return False, "La cedula ingresada no se encuentra registrada o es invalida.", None

                await browser.close()
                return False, "El portal no respondio con los datos de la cedula.", None

            # Extraer el nombre si esta disponible en el campo oculto #hdName o en pantalla
            nombre = None
            try:
                nombre_val = await page.locator("#hdName").input_value()
                if nombre_val and len(nombre_val.strip()) > 3:
                    nombre = nombre_val.strip()
            except Exception:
                pass

            # 4. Llenar motivo de consulta en #txtMotivo
            await page.fill("#txtMotivo", "realizar un tramite")
            await page.wait_for_timeout(400)

            # 5. Clic en Siguiente 2 (#btnSig2)
            btn_sig2 = page.locator("#btnSig2")
            await btn_sig2.click()

            # 6. Esperar a que aparezca el boton para visualizar el certificado (#btnOpen)
            try:
                await page.wait_for_selector("#btnOpen", state="visible", timeout=12000)
            except Exception:
                if dialog_message:
                    await browser.close()
                    return False, f"El portal informo: {dialog_message}", None
                await browser.close()
                return False, "No se habilito el boton para generar el certificado.", None

            # 7. Clic en Visualizar Certificado (#btnOpen)
            btn_open = page.locator("#btnOpen")
            
            # Intentar clic capturando descarga si se dispara
            try:
                async with page.expect_download(timeout=8000) as download_info:
                    await btn_open.click()
                dl = await download_info.value
                dl_path = await dl.path()
                if dl_path:
                    with open(dl_path, "rb") as f:
                        captured_pdf = f.read()
            except Exception:
                # Si expect_download no se disparo, tal vez abrio popup o via route_interceptor
                pass

            # Si aun no tenemos el PDF, esperar unos segundos a que la intercepcion o el popup lo capture
            if not (captured_pdf and len(captured_pdf) > 5000):
                for _ in range(10):
                    if captured_pdf and len(captured_pdf) > 5000:
                        break
                    await asyncio.sleep(1)

            await browser.close()

            if captured_pdf and len(captured_pdf) > 5000:
                return True, captured_pdf, nombre or "CIUDADANO"
            else:
                return False, "El portal proceso la solicitud pero no entrego el documento PDF.", None

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error en procesamiento: {str(e)}", None