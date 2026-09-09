from telegram import Update, InputFile
from telegram.ext import ContextTypes
import io
import os
import time
import asyncio
from bachiller import procesar_certificado_bachiller

async def handle_message_bachiller(update: Update, context: ContextTypes.DEFAULT_TYPE, cedula: str):
    msg_espera = await update.message.reply_text(
        f"⏳ **Consultando Registro de Título de Bachiller...**\n"
        f"🆔 Cédula: `{cedula}`\n\n"
        f"*Conectando con el portal del Ministerio de Educación del Ecuador, por favor espera...*",
        parse_mode="Markdown"
    )
    
    t0 = time.time()
    
    # Intentos de consulta por si hay saturación momentánea en el servidor del Ministerio
    intentos = 2
    success, data = False, None
    for i in range(intentos):
        success, data, _ = await procesar_certificado_bachiller(cedula)
        if success:
            break
            
        # Si el resultado indica expresamente que no hay títulos registrados, no reintentamos
        if not success and isinstance(data, str) and ("no se encontraron" in data.lower() or "no registra" in data.lower() or "no constan" in data.lower()):
            break
            
        if i < intentos - 1:
            try:
                await msg_espera.edit_text(
                    f"⏳ **Consultando Registro de Título de Bachiller...**\n"
                    f"🆔 Cédula: `{cedula}`\n\n"
                    f"🔄 Reintentando consulta en el Ministerio de Educación (Intento {i+2})...",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await asyncio.sleep(2)

    elapsed = time.time() - t0
    
    if success and isinstance(data, dict):
        nombres = data.get("nombres", "CIUDADANO REGISTRADO")
        institucion = data.get("institucion", "UNIDAD EDUCATIVA REGISTRADA")
        titulo = data.get("titulo", "Bachiller")
        especialidad = data.get("especialidad", "GENERAL")
        refrendacion = data.get("refrendacion", "ME-REF-OFICIAL")
        fecha_grado = data.get("fecha_grado", "")

        fecha_str = f"📅 **Fecha de Grado:** {fecha_grado}\n" if fecha_grado else ""

        caption = (
            f"✅ **TÍTULO DE BACHILLER ENCONTRADO!** ({elapsed:.1f}s)\n\n"
            f"👤 **Estudiante:** {nombres}\n"
            f"🆔 **Cédula:** `{cedula}`\n"
            f"🏫 **Institución:** {institucion}\n"
            f"🎓 **Título:** {titulo}\n"
            f"📚 **Especialidad:** {especialidad}\n"
            f"🔢 **Refrendación:** `{refrendacion}`\n"
            f"{fecha_str}\n"
            f"📄 *Certificado oficial de registro emitido por el Ministerio de Educación (con firma electrónica, código de barras y plena validez legal).*"
        )
        nombre_archivo = f"Certificado_Bachiller_{cedula}.pdf"
        pdf_file = io.BytesIO(data["pdf_bytes"])
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
    else:
        error_msg = data if isinstance(data, str) else "No se pudo obtener el certificado de bachiller."
        try:
            await msg_espera.edit_text(
                f"❌ **Resultado de la consulta de Bachiller:**\n\n{error_msg}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
