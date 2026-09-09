"""
bachiller.py - Módulo para consulta y descarga automática del Certificado Oficial de Registro de Título de Bachiller
Ministerio de Educación del Ecuador (servicios.educacion.gob.ec)
"""

import asyncio
import io
import os
import re
import sys
import time
from typing import Dict, Optional, Tuple
from playwright.async_api import async_playwright
from PIL import Image

try:
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

KEYWORDS_BACHILLER = [
    "bachiller",
    "bachillerato",
    "titulo bachiller",
    "titulo de bachiller",
    "certificado bachiller",
    "certificado de bachiller",
    "acta de grado",
    "refrendacion",
    "refrendado",
    "refrendados",
    "mineduc"
]

def detectar_mensaje_bachiller(texto: str) -> bool:
    """Retorna True si el mensaje del usuario hace referencia al certificado o título de bachiller."""
    t_lower = texto.lower()
    return any(kw in t_lower for kw in KEYWORDS_BACHILLER)

def parsear_mensaje_bachiller(texto: str) -> Optional[str]:
    """Extrae el número de cédula (10 dígitos consecutivos) del mensaje."""
    match = re.search(r'\b(\d{10})\b', texto)
    return match.group(1) if match else None

async def _resolver_captcha_mineduc(image_bytes: bytes) -> str:
    """Resuelve el captcha del portal del Ministerio de Educación."""
    # 1. ddddocr (ultra rápido y preciso para este tipo de captcha)
    if _ocr_instance:
        try:
            res = _ocr_instance.classification(image_bytes)
            clean = re.sub(r'[^A-Za-z0-9]', '', res).strip()
            if len(clean) >= 5:
                return clean
        except Exception:
            pass

    # 2. WinOCR en Windows
    if winocr:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            scaled = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
            res = await winocr.recognize_pil(scaled, lang="es")
            raw = res.text if hasattr(res, "text") else str(res)
            clean = re.sub(r'[^A-Za-z0-9]', '', raw).strip()
            if len(clean) >= 5:
                return clean
        except Exception:
            pass

    # 3. Pytesseract con escalado y umbrales
    if pytesseract:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("L")
            scaled = img.resize((img.width * 4, img.height * 4), Image.Resampling.LANCZOS)
            for th in [160, 180, 140, 200]:
                bin_img = scaled.point(lambda p: 0 if p > th else 255)
                txt = pytesseract.image_to_string(
                    bin_img,
                    config="--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                ).strip()
                clean = re.sub(r"[^A-Za-z0-9]", "", txt)
                if len(clean) >= 5:
                    return clean
        except Exception:
            pass

    return ""

async def procesar_certificado_bachiller(cedula: str) -> Tuple[bool, dict | str, None]:
    """
    Ejecuta el flujo completo en el portal del Ministerio de Educación:
    1. Abre consulta de títulos de bachiller.
    2. Selecciona identificación y coloca cédula.
    3. Resuelve captcha y consulta.
    4. Abre 'Ver información'.
    5. Clic en 'Imprimir' y descarga el PDF oficial (431 KB con firma electrónica y QR).
    """
    url_portal = "https://servicios.educacion.gob.ec/titulacion25-web/faces/paginas/consulta-titulos-refrendados.xhtml"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-EC"
        )
        page = await context.new_page()

        captured_pdf = None

        # Interceptar descargas y respuestas binarias de PDF
        async def route_handler(route, request):
            nonlocal captured_pdf
            try:
                resp = await route.fetch()
                ct = resp.headers.get("content-type", "").lower()
                body = await resp.body()
                if "application/pdf" in ct or "pdf" in ct or body.startswith(b"%PDF"):
                    if len(body) > 15000:
                        captured_pdf = body
                await route.fulfill(response=resp)
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await context.route("**/*", route_handler)

        async def on_download(download):
            nonlocal captured_pdf
            try:
                path = await download.path()
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        data = f.read()
                        if data.startswith(b"%PDF"):
                            captured_pdf = data
            except Exception:
                pass

        page.on("download", on_download)

        try:
            # 1. Navegar al portal oficial
            await page.goto(url_portal, timeout=30000, wait_until="domcontentloaded")

            # 2. Seleccionar radio 'Nº de Identificación'
            radio_id = await page.query_selector('input[id*="selecItem:0"]')
            if radio_id:
                await radio_id.click()
            await page.wait_for_timeout(200)

            # 3. Llenar campo cédula
            cedula_input = await page.wait_for_selector('input[id*=":cedula"]', timeout=5000)
            await cedula_input.fill(cedula)

            # 4. Resolver y enviar Captcha con reintentos in-situ
            c_el = await page.wait_for_selector('img[id*="capimg"]', state="visible", timeout=6000)
            ver_btn_ready = False

            for _ in range(6):
                c_bytes = await c_el.screenshot()
                code = await _resolver_captcha_mineduc(c_bytes)

                if len(code) >= 5:
                    await page.fill('input[id*=":captcha"]', code)
                    await page.click('input[id*="clBuscar"]')

                    # Esperar si aparece 'Ver información' o mensaje de error
                    try:
                        ver_btn_ready = await page.wait_for_selector('input[value*="Ver informaci"]', state="visible", timeout=2500)
                        if ver_btn_ready:
                            break
                    except Exception:
                        pass

                # Verificar si el sistema dice que no existen títulos
                body_text_check = await page.evaluate("() => document.body.innerText")
                if "no se encontraron" in body_text_check.lower() or "no registra" in body_text_check.lower():
                    await browser.close()
                    return False, f"No se encontraron títulos de bachiller registrados para la cédula {cedula} en el Ministerio de Educación.", None

                # Refrescar imagen de captcha
                cambiar_btn = await page.query_selector('input[value="Cambiar imagen"]')
                if cambiar_btn:
                    await cambiar_btn.click()
                else:
                    await page.evaluate("document.getElementById('formBusqueda:capimg').src = 'Captcha.jpg?rand=' + Math.random();")
                await page.wait_for_timeout(400)

            if not ver_btn_ready:
                # Comprobar si hubo error o simplemente no hay títulos
                body_text = await page.evaluate("() => document.body.innerText")
                await browser.close()
                if "no se encontraron" in body_text.lower() or "no registra" in body_text.lower():
                    return False, f"No constan títulos de bachiller registrados para la cédula {cedula}.", None
                return False, "No se pudo superar la validación del portal del Ministerio de Educación.", None

            # 5. Extraer Nombres
            nombres = "CIUDADANO REGISTRADO"
            try:
                info_text = await page.evaluate("() => document.body.innerText")
                m_nom = re.search(r'Nombres:\s*([^\n\r]+)', info_text, re.IGNORECASE)
                if m_nom:
                    nombres = m_nom.group(1).strip()
            except Exception:
                pass

            # 6. Clic en 'Ver información'
            await ver_btn_ready.click()

            # 7. Esperar a que cargue la tabla con el título
            try:
                await page.wait_for_selector('a:has-text("Imprimir"), input[value*="Imprimir"]', timeout=8000)
            except Exception:
                pass

            # Extraer detalles del título de la tabla
            detalles = await page.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('tr'));
                for (const r of rows) {
                    const txt = r.innerText;
                    if (txt.includes('Bachiller') || txt.includes('ME-REF-') || txt.includes('CIENCIAS') || txt.includes('TECNICO')) {
                        const cells = Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim());
                        if (cells.length >= 6) {
                            return {
                                institucion: cells[1] || '',
                                titulo: cells[2] || '',
                                especialidad: cells[3] || '',
                                fecha_grado: cells[4] || '',
                                refrendacion: cells[5] || ''
                            };
                        }
                    }
                }
                return null;
            }""") or {}

            # 8. Clic en Imprimir
            imprimir_btn = await page.wait_for_selector('a:has-text("Imprimir"), input[value*="Imprimir"]', timeout=5000)
            await imprimir_btn.click()

            # 9. Esperar flujo PDF oficial
            for _ in range(20):
                if captured_pdf and len(captured_pdf) > 20000 and captured_pdf.startswith(b"%PDF"):
                    break
                await asyncio.sleep(0.5)

            await browser.close()

            if captured_pdf and captured_pdf.startswith(b"%PDF") and len(captured_pdf) > 20000:
                return True, {
                    "pdf_bytes": captured_pdf,
                    "cedula": cedula,
                    "nombres": nombres,
                    "institucion": detalles.get("institucion", "UNIDAD EDUCATIVA REGISTRADA"),
                    "titulo": detalles.get("titulo", "Bachiller"),
                    "especialidad": detalles.get("especialidad", "GENERAL"),
                    "fecha_grado": detalles.get("fecha_grado", ""),
                    "refrendacion": detalles.get("refrendacion", "ME-REF-OFICIAL")
                }, None
            else:
                return False, "El Ministerio de Educación no entregó el archivo PDF a tiempo.", None

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Error al consultar el título de bachiller: {str(e)}", None
