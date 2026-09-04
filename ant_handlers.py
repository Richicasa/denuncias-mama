from telegram import Update, InputFile
from telegram.ext import ContextTypes
import io
import time
import asyncio
from ant_orden_pago import parsear_mensaje_ant, procesar_orden_pago_ant

async def handle_message_ant(update: Update, context: ContextTypes.DEFAULT_TYPE, user_states: dict):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    parsed = parsear_mensaje_ant(text)
    estado_previo = user_states.get(user_id, {})
    
    cedula = parsed.get("cedula") or estado_previo.get("cedula")
    tipo_tramite = parsed.get("tipo_tramite") or estado_previo.get("tipo_tramite")
    tipo_licencia = parsed.get("tipo_licencia") or estado_previo.get("tipo_licencia")
    
    if not cedula:
        user_states[user_id] = {"flujo": "ant", "tipo_tramite": tipo_tramite, "tipo_licencia": tipo_licencia}
        await update.message.reply_text("Para generar la orden de pago, por favor indicame el numero de **cedula**.", parse_mode="Markdown")
        return
        
    if not tipo_tramite:
        user_states[user_id] = {"flujo": "ant", "cedula": cedula, "tipo_licencia": tipo_licencia}
        await update.message.reply_text("Cual es el **tipo de tramite**? (Renovacion, Duplicado, o Primera vez)", parse_mode="Markdown")
        return
        
    if not tipo_licencia:
        user_states[user_id] = {"flujo": "ant", "cedula": cedula, "tipo_tramite": tipo_tramite}
        await update.message.reply_text("Cual es el **tipo de licencia**? (A, B, C...)", parse_mode="Markdown")
        return

    if user_id in user_states:
        del user_states[user_id]
        
    await procesar_y_responder_ant(update, cedula, tipo_tramite, tipo_licencia)

async def procesar_y_responder_ant(update: Update, cedula: str, tipo_tramite: str, tipo_licencia: str):
    msg_espera = await update.message.reply_text(
        f"⏳ **Generando Orden de Pago ANT...**\n"
        f"🆔 Cedula: `{cedula}`\n"
        f"🚗 Tramite: `{tipo_tramite}`\n"
        f"🪪 Tipo: `{tipo_licencia}`\n\n"
        f"*Conectando con el portal de la ANT, por favor espera...*",
        parse_mode="Markdown"
    )
    
    t0 = time.time()
    
    # El portal ANT no es infinitamente reintentable en el mismo sentido, 
    # pero podemos hacer unos 2 intentos si hay error generico.
    intentos = 2
    success, data, nombre = False, None, None
    for i in range(intentos):
        success, data, nombre = await procesar_orden_pago_ant(cedula, tipo_tramite, tipo_licencia)
        if success:
            break
        if not success and isinstance(data, str) and "correo" in data.lower():
            # Hard stop for email or specific validations
            break
        
        if i < intentos - 1:
            try:
                await msg_espera.edit_text(f"🔄 Reintentando conexion con ANT (Intento {i+2})...")
            except: pass
            await asyncio.sleep(2)

    elapsed = time.time() - t0
    
    if success:
        caption = (
            f"✅ **ORDEN DE PAGO ANT GENERADA!** ({elapsed:.1f}s)\n\n"
            f"👤 **Nombre:** {nombre}\n"
            f"🆔 **Cedula:** `{cedula}`\n"
            f"🚗 **Tramite:** {tipo_tramite}\n"
            f"🪪 **Tipo:** {tipo_licencia}"
        )
        nombre_archivo = f"Orden_Pago_{cedula}.pdf"
        pdf_file = io.BytesIO(data)
        pdf_file.name = nombre_archivo
        
        await update.message.reply_document(
            document=InputFile(pdf_file, filename=nombre_archivo),
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await msg_espera.delete()
        except: pass
    else:
        # Error (ej. falta email, cedula no existe, etc)
        error_msg = data if isinstance(data, str) else "No se pudo obtener la orden de pago."
        try:
            await msg_espera.edit_text(
                f"❌ **Error al generar la orden:**\n{error_msg}",
                parse_mode="Markdown"
            )
        except: pass