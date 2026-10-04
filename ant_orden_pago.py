import asyncio
import json
import logging
import os
import time
from typing import Optional, Dict, Any

from ant_parser import validar_cedula_ec

logger = logging.getLogger("ant_orden_pago")

# Map de servicios
SERVICIOS_ANT = {
    1: "RENOVACIÓN DE LICENCIA",
    5: "EMISIÓN DE LICENCIA POR PRIMERA VEZ",
    1004: "DUPLICADO DE LICENCIA EN LÍNEA"
}

class ANTOrdenPagoClient:
    """
    Cliente para consultar y generar órdenes de pago de licencias de conducir
    en el portal de la Agencia Nacional de Tránsito (ANT) de Ecuador.
    """

    def __init__(self, headless: bool = True, timeout: int = 35000):
        self.headless = headless
        self.timeout = timeout
        self.base_url = "https://consultaweb.ant.gob.ec/SVT/paginas/portal/svf_solicitar_servicio.jsp?ps_param_tip_serv=LIC"
        self.downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)

    async def _obtener_browser(self):
        """
        Inicia el navegador preferente (patchright con fallback a playwright)
        con flags headless seguros tanto para Windows como Linux.
        """
        browser_launcher = None
        playwright_mgr = None

        try:
            from patchright.async_api import async_playwright
            playwright_mgr = async_playwright()
        except ImportError:
            from playwright.async_api import async_playwright
            playwright_mgr = async_playwright()

        p = await playwright_mgr.start()
        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled"
        ]

        # Priorizar Chromium, con fallback a Firefox
        try:
            browser = await p.chromium.launch(headless=self.headless, args=args)
            return p, browser
        except Exception as e_cr:
            logger.warning(f"No se pudo iniciar Chromium ({e_cr}), intentando con Firefox...")
            try:
                browser = await p.firefox.launch(headless=self.headless, args=args)
                return p, browser
            except Exception as e_ff:
                await p.stop()
                raise RuntimeError(f"Fallo al iniciar navegador headless: {e_ff}")

    async def _consultar_mensaje_error(self, frame_or_page, codigo_error: str) -> str:
        """
        Consulta la descripción humana en español para códigos de error devueltos por la ANT
        (ej: VAL006, ERC003, etc.)
        """
        try:
            js = f'''async () => {{
                try {{
                    const resp = await fetch("svp_retorna_mensaje.jsp?ps_codigo={codigo_error}");
                    const data = await resp.json();
                    return data.mensaje || data.mensajeCab || "";
                }} catch(e) {{
                    return "";
                }}
            }}'''
            msg = await frame_or_page.evaluate(js)
            if msg and isinstance(msg, str) and len(msg.strip()) > 0:
                # Quitar etiquetas HTML si las hubiera
                import re
                clean = re.sub(r'<[^>]+>', ' ', msg).strip()
                return clean
        except Exception:
            pass
        return f"Error reportado por el sistema ANT (Código: {codigo_error})"

    async def obtener_orden_pago_pdf(
        self,
        cedula: str,
        id_servicio: int = 1,
        tipo_licencia: str = "B"
    ) -> Dict[str, Any]:
        """
        Consulta o genera la orden de pago para la cédula y servicio indicados,
        y descarga el PDF oficial emitido por JasperReports.
        """
        # 1. Validación previa rápida de cédula ecuatoriana
        cedula = (cedula or "").strip()
        if not validar_cedula_ec(cedula):
            return {
                "success": False,
                "cedula": cedula,
                "nombres": None,
                "tramite_id": None,
                "servicio_nombre": SERVICIOS_ANT.get(id_servicio, "LICENCIA"),
                "pdf_path": None,
                "pdf_bytes": None,
                "error_mensaje": f"La cédula '{cedula}' es inválida según el registro civil."
            }

        servicio_nombre = SERVICIOS_ANT.get(id_servicio, "RENOVACIÓN DE LICENCIA")
        p = None
        browser = None
        context = None

        try:
            # 2. Iniciar navegador y sesión
            p, browser = await self._obtener_browser()
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="es-EC"
            )
            page = await context.new_page()

            # Navegar al portal para inicializar cookies y sesión
            await page.goto(self.base_url, wait_until="load", timeout=self.timeout)
            await page.wait_for_selector("#iframe_detalle", timeout=15000)

            # Localizar el frame de operaciones
            frame = None
            for f in page.frames:
                if "svp_solicitar_servicio.jsp" in f.url:
                    frame = f
                    break
            if not frame:
                frame = page

            # 3. Consultar datos del ciudadano en el endpoint interno
            recupera_url = (
                f"svp_json_recupera_datos.jsp?ps_identificacion={cedula}"
                f"&ps_id_servicio={id_servicio}&ps_placa=&ps_parametro1=&ps_id_grupo_servicio=LIC"
            )

            res_recupera_raw = await frame.evaluate(f'''async () => {{
                const resp = await fetch("{recupera_url}");
                return await resp.text();
            }}''')

            try:
                datos = json.loads(res_recupera_raw.strip())
            except Exception as e_json:
                return {
                    "success": False,
                    "cedula": cedula,
                    "nombres": None,
                    "tramite_id": None,
                    "servicio_nombre": servicio_nombre,
                    "pdf_path": None,
                    "pdf_bytes": None,
                    "error_mensaje": f"Respuesta no válida del portal ANT: {res_recupera_raw[:150]}"
                }

            # Validar si ANT reportó error (ej: VAL006 u otros)
            cod_error = datos.get("codigoError")
            if cod_error:
                msj_ant = await self._consultar_mensaje_error(frame, cod_error)
                return {
                    "success": False,
                    "cedula": cedula,
                    "nombres": datos.get("nombres"),
                    "tramite_id": None,
                    "servicio_nombre": servicio_nombre,
                    "pdf_path": None,
                    "pdf_bytes": None,
                    "error_mensaje": msj_ant
                }

            nombres = datos.get("nombres", "CIUDADANO REGISTRADO")
            id_cliente = datos.get("idCliente")
            cant_tramites = int(datos.get("cantTramites") or 0)

            tramite_id = None

            # 4. Determinar trámite: Existente vs Generar nuevo
            if cant_tramites > 0:
                # El ciudadano ya tiene un trámite previo pendiente
                tramite_id = str(cant_tramites)
                logger.info(f"Cédula {cedula} tiene trámite activo: {tramite_id}")
            else:
                # Generar nuevo trámite en la ANT
                genera_url = (
                    f"svp_json_genera_tramite.jsp?ps_idCliente={id_cliente}&ps_id_servicio={id_servicio}"
                    f"&ps_Parametro1=&ps_valorParametro1={tipo_licencia}&ps_Parametro2=&ps_valorParametro2="
                    f"&ps_valorParametro3=&ps_valorParametro4=&ps_valorParametro5=&ps_Parametro6=&ps_valorParametro6="
                    f"&ps_id_grupo_servicio=LIC&ps_id_empresa="
                )
                res_genera_raw = await frame.evaluate(f'''async () => {{
                    const resp = await fetch("{genera_url}");
                    return await resp.text();
                }}''')

                try:
                    datos_gen = json.loads(res_genera_raw.strip())
                except Exception:
                    datos_gen = {}

                cod_gen = datos_gen.get("codigoError")
                if cod_gen != "OK":
                    msj_gen = await self._consultar_mensaje_error(frame, cod_gen or "ERROR_DESCONOCIDO")
                    return {
                        "success": False,
                        "cedula": cedula,
                        "nombres": nombres,
                        "tramite_id": None,
                        "servicio_nombre": servicio_nombre,
                        "pdf_path": None,
                        "pdf_bytes": None,
                        "error_mensaje": f"No se pudo generar el trámite en la ANT: {msj_gen}"
                    }

                tramite_id = str(datos_gen.get("tramite"))

            if not tramite_id:
                return {
                    "success": False,
                    "cedula": cedula,
                    "nombres": nombres,
                    "tramite_id": None,
                    "servicio_nombre": servicio_nombre,
                    "pdf_path": None,
                    "pdf_bytes": None,
                    "error_mensaje": "No se obtuvo un número de trámite válido de la ANT."
                }

            # 5. Descargar PDF oficial desde el reporte JasperReports
            report_url = (
                f"https://consultaweb.ant.gob.ec/SVT/paginas/svtr0010_parametros.jsp"
                f"?P_EMPRESA=01&P_CLIENTE={id_cliente}&P_SERVICIO={id_servicio}&P_TRAMITE={tramite_id}"
            )

            resp_pdf = await page.request.get(report_url, timeout=20000)
            pdf_bytes = await resp_pdf.body()

            if not (pdf_bytes and pdf_bytes.startswith(b"%PDF")):
                return {
                    "success": False,
                    "cedula": cedula,
                    "nombres": nombres,
                    "tramite_id": tramite_id,
                    "servicio_nombre": servicio_nombre,
                    "pdf_path": None,
                    "pdf_bytes": None,
                    "error_mensaje": "El portal de la ANT no devolvió el archivo PDF de la orden de pago."
                }

            # Guardar en archivo local
            filename = f"Orden_Pago_ANT_{cedula}_{tramite_id}.pdf"
            pdf_path = os.path.join(self.downloads_dir, filename)
            with open(pdf_path, "wb") as f_out:
                f_out.write(pdf_bytes)

            return {
                "success": True,
                "cedula": cedula,
                "nombres": nombres,
                "tramite_id": tramite_id,
                "servicio_nombre": servicio_nombre,
                "pdf_path": pdf_path,
                "pdf_bytes": pdf_bytes,
                "error_mensaje": None
            }

        except Exception as e:
            logger.error(f"Error procesando orden de pago ANT para {cedula}: {e}", exc_info=True)
            return {
                "success": False,
                "cedula": cedula,
                "nombres": None,
                "tramite_id": None,
                "servicio_nombre": servicio_nombre,
                "pdf_path": None,
                "pdf_bytes": None,
                "error_mensaje": f"Error de conexión con la ANT: {str(e)}"
            }
        finally:
            # Limpieza rigurosa de contextos y navegador para no dejar procesos huérfanos
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            try:
                if p:
                    await p.stop()
            except Exception:
                pass
