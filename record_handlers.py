from telegram import Update, InputFile
from telegram.ext import ContextTypes
import io
import os
import time
import asyncio
from record_policial import procesar_record_policial

async def handle_message_record(update: Update, context: ContextTypes.DEFAULT_TYPE, cedula: str):
    msg_espera = await update.message.reply_text(
        f"⏳ **Generando Certificado de Récord Policial...**\n"
        f"🆔 Cédula: `{cedula}`\n"
        f"📝 Motivo: `realizar un tramite`\n\n"
        f"*Conectando con el Ministerio del Interior, por favor espera...*",
        parse_mode="Markdown"
    )
    
    t0 = time.time()
    
    # Intentar un par de veces por si hay fallo de conexion
    intentos = 2
    success, data, nombre = False, None, None
    for i in range(intentos):
        success, data, nombre = await procesar_record_policial(cedula)
        if success:
            break
        # Si el error es de Incapsula (WAF), no reintentamos
        if not success and isinstance(data, str) and "Incapsula" in data:
            break
            
        if i < intentos - 1:
            try:
                await msg_espera.edit_text(f"🔄 Reintentando conexion con el Ministerio (Intento {i+2})...")
            except: pass
            await asyncio.sleep(2)

    elapsed = time.time() - t0
    if success:
        nombre_str = f"👤 **Nombre:** {nombre}\n" if nombre and nombre != "CIUDADANO" else ""
        caption = (
            f"✅ **RÉCORD POLICIAL GENERADO!** ({elapsed:.1f}s)\n\n"
            f"{nombre_str}"
            f"🆔 **Cédula:** `{cedula}`\n"
            f"📄 *Certificado oficial del Ministerio del Interior.*"
        )
        nombre_archivo = f"Record_Policial_{cedula}.pdf"
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
        error_msg = data if isinstance(data, str) else "No se pudo obtener el certificado."
        try:
            await msg_espera.edit_text(
                f"❌ **Error al generar el récord policial:**\n{error_msg}",
                parse_mode="Markdown"
            )
            # Si se generó captura de pantalla del error, enviarla
            if os.path.exists("error_record_screen.png"):
                try:
                    with open("error_record_screen.png", "rb") as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption="📸 *Captura del navegador al momento de la falla.*",
                            parse_mode="Markdown"
                        )
                    os.remove("error_record_screen.png")
                except Exception:
                    pass
        except: pass