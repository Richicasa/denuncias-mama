import io
import logging
from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ant_orden_pago import ANTOrdenPagoClient, SERVICIOS_ANT
from ant_parser import validar_cedula_ec

logger = logging.getLogger("ant_handlers")

async def handle_message_ant(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parsed_data: dict,
    user_states: dict
):
    """
    Manejador conversacional de Telegram para órdenes de pago de licencias ANT.
    Permite enviar todo en un solo mensaje o por partes, avisando con exactitud
    qué datos se tienen y qué datos faltan.
    """
    user_id = update.effective_user.id if update.effective_user else 0
    estado_previo = user_states.get(user_id, {})

    # Combinar datos recibidos con el estado previo acumulado
    cedula = parsed_data.get("cedula") or estado_previo.get("cedula")
    id_servicio = parsed_data.get("id_servicio") or estado_previo.get("id_servicio")
    tipo_licencia = parsed_data.get("tipo_licencia") or estado_previo.get("tipo_licencia")
    servicio_nombre = parsed_data.get("servicio_nombre") or estado_previo.get("servicio_nombre")

    if id_servicio and not servicio_nombre:
        servicio_nombre = SERVICIOS_ANT.get(id_servicio, "RENOVACIÓN DE LICENCIA")

    # Identificar qué datos faltan
    faltantes = []
    if not cedula or not validar_cedula_ec(cedula):
        faltantes.append("cedula")
    if not id_servicio:
        faltantes.append("tipo_tramite")
    if not tipo_licencia:
        faltantes.append("tipo_licencia")

    # Si falta cualquier dato, informar detalladamente
    if faltantes:
        user_states[user_id] = {
            "flujo": "orden_pago",
            "cedula": cedula if (cedula and validar_cedula_ec(cedula)) else None,
            "id_servicio": id_servicio,
            "tipo_licencia": tipo_licencia,
            "servicio_nombre": servicio_nombre
        }

        # Sección de datos ya recibidos
        lineas_recibidas = []
        if cedula and validar_cedula_ec(cedula):
            lineas_recibidas.append(f"✅ **Cédula:** `{cedula}`")
        if id_servicio and servicio_nombre:
            lineas_recibidas.append(f"✅ **Trámite:** {servicio_nombre}")
        if tipo_licencia:
            lineas_recibidas.append(f"✅ **Tipo de Licencia:** Tipo {tipo_licencia}")

        # Sección de datos que faltan
        lineas_faltantes = []
        if "cedula" in faltantes:
            lineas_faltantes.append("• **Número de cédula:** 10 dígitos del titular.")
        if "tipo_tramite" in faltantes:
            lineas_faltantes.append("• **Tipo de trámite:** ¿Es *Renovación*, *Primera vez* o *Duplicado*?")
        if "tipo_licencia" in faltantes:
            lineas_faltantes.append("• **Tipo de licencia:** ¿Qué tipo es? *(A, B, C, D, E, F, G)*")

        bloque_recibido = ""
        if lineas_recibidas:
            bloque_recibido = "📋 **Datos recibidos:**\n" + "\n".join(lineas_recibidas) + "\n\n"

        bloque_faltante = "⚠️ **Faltan los siguientes datos para emitir la orden:**\n" + "\n".join(lineas_faltantes)

        # Sugerencia de ejemplo dinámico según lo que falte
        if "cedula" in faltantes and "tipo_tramite" in faltantes and "tipo_licencia" in faltantes:
            ejemplo = "_Ejemplo: 1710034065, orden de pago, tipo B, renovación_"
        elif "tipo_tramite" in faltantes and "tipo_licencia" in faltantes:
            ejemplo = "_Ejemplo: Renovación tipo B_"
        elif "tipo_licencia" in faltantes:
            ejemplo = "_Ejemplo: Tipo B_"
        elif "tipo_tramite" in faltantes:
            ejemplo = "_Ejemplo: Renovación_"
        else:
            ejemplo = "_Ejemplo: 1710034065_"

        mensaje_aviso = (
            f"🚗 **Orden de Pago de Licencias ANT**\n\n"
            f"{bloque_recibido}"
            f"{bloque_faltante}\n\n"
            f"💬 *Puedes responder directamente con lo que falta:*\n{ejemplo}"
        )

        await update.message.reply_text(mensaje_aviso, parse_mode="Markdown")
        return

    # Si todos los datos están completos, limpiar estado y proceder
    if user_id in user_states:
        del user_states[user_id]

    msg_espera = await update.message.reply_text(
        f"⏳ **Consultando la Agencia Nacional de Tránsito (ANT)...**\n\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"📋 Servicio: `{servicio_nombre}`\n"
        f"🚗 Tipo Licencia: `{tipo_licencia}`\n\n"
        f"_Por favor espera unos segundos mientras generamos el PDF oficial..._",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_DOCUMENT
        )
    except Exception:
        pass

    try:
        client = ANTOrdenPagoClient()
        res = await client.obtener_orden_pago_pdf(
            cedula=cedula,
            id_servicio=id_servicio,
            tipo_licencia=tipo_licencia
        )

        if res.get("success"):
            pdf_bytes = res.get("pdf_bytes")
            pdf_path = res.get("pdf_path")
            tramite_id = res.get("tramite_id", "")
            nombres = res.get("nombres", "CIUDADANO")

            caption = (
                f"✅ **ORDEN DE PAGO ANT GENERADA CON ÉXITO**\n\n"
                f"👤 **Nombre:** {nombres}\n"
                f"🆔 **Cédula:** `{cedula}`\n"
                f"📋 **Trámite Nº:** `{tramite_id}`\n"
                f"🚗 **Servicio:** {servicio_nombre} (Tipo {tipo_licencia})\n\n"
                f"📄 _Documento PDF oficial emitido por la Agencia Nacional de Tránsito._\n"
                f"💳 _Válido para cancelar en Banco del Pacífico, Servipagos o en línea._"
            )
            nombre_archivo = f"Orden_Pago_ANT_{cedula}_{tramite_id}.pdf"

            if pdf_bytes:
                pdf_file = io.BytesIO(pdf_bytes)
                pdf_file.name = nombre_archivo
                await update.message.reply_document(
                    document=InputFile(pdf_file, filename=nombre_archivo),
                    caption=caption,
                    parse_mode="Markdown"
                )
            elif pdf_path:
                with open(pdf_path, "rb") as f_pdf:
                    await update.message.reply_document(
                        document=InputFile(f_pdf, filename=nombre_archivo),
                        caption=caption,
                        parse_mode="Markdown"
                    )

            try:
                await msg_espera.delete()
            except Exception:
                pass

        else:
            error_msg = res.get("error_mensaje") or "No se pudo procesar la orden en la ANT."
            try:
                await msg_espera.delete()
            except Exception:
                pass

            await update.message.reply_text(
                f"❌ **No se pudo generar la Orden de Pago en la ANT**\n\n"
                f"🆔 Cédula: `{cedula}`\n"
                f"ℹ️ Motivo: {error_msg}\n\n"
                f"_Por favor verifica los datos o intenta nuevamente más tarde._",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error en handle_message_ant para {cedula}: {e}", exc_info=True)
        try:
            await msg_espera.delete()
        except Exception:
            pass
        await update.message.reply_text(
            f"❌ **Error inesperado de comunicación con la ANT**\n\n"
            f"Detalle técnico: `{str(e)}`",
            parse_mode="Markdown"
        )
