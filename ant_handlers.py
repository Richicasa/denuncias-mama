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
    Soporta formato en una sola línea o diálogo interactivo por pasos si faltan datos.
    """
    user_id = update.effective_user.id if update.effective_user else 0
    cedula = parsed_data.get("cedula")
    id_servicio = parsed_data.get("id_servicio", 1)
    tipo_licencia = parsed_data.get("tipo_licencia", "B")
    servicio_nombre = parsed_data.get("servicio_nombre") or SERVICIOS_ANT.get(id_servicio, "RENOVACIÓN DE LICENCIA")

    # Si falta la cédula o no es válida
    if not cedula or not validar_cedula_ec(cedula):
        user_states[user_id] = {
            "flujo": "orden_pago",
            "id_servicio": id_servicio,
            "tipo_licencia": tipo_licencia,
            "servicio_nombre": servicio_nombre
        }
        await update.message.reply_text(
            "🚗 **Orden de Pago de Licencias ANT**\n\n"
            "Por favor, ingresa el número de **cédula** (10 dígitos) del titular.\n\n"
            "_Ejemplo: 1710034065_",
            parse_mode="Markdown"
        )
        return

    # Si tenemos cédula válida, limpiar el estado previo
    if user_id in user_states:
        del user_states[user_id]

    # Mensaje temporal de espera
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
