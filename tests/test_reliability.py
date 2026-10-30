import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import telegram_bot
from ant_parser import analizar_cedula_con_diagnostico

@pytest.mark.asyncio
async def test_procesar_y_responder_stops_on_repeated_failure():
    """Verifica que procesar_y_responder realice hasta MAX_RETRIES_DENUNCIA (15) intentos y no bucle infinito."""
    update = MagicMock()
    msg_espera = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_espera)

    with patch("telegram_bot.procesar_denuncia_judicial", new_callable=AsyncMock) as mock_judicial, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_judicial.return_value = (False, "Falla simulada de portal judicial", None)

        await telegram_bot.procesar_y_responder(update, "1708927502", "Solanda")

        # Debe llamarse exactamente MAX_RETRIES_DENUNCIA veces (15)
        assert mock_judicial.call_count == telegram_bot.MAX_RETRIES_DENUNCIA
        assert telegram_bot.MAX_RETRIES_DENUNCIA == 15
        assert msg_espera.edit_text.called
        last_edit_call = msg_espera.edit_text.call_args[0][0]
        assert "15 pruebas" in last_edit_call
        assert "1708927502" in last_edit_call


@pytest.mark.asyncio
async def test_procesar_y_responder_success_on_first_try():
    """Verifica que en caso de éxito entregue el documento de inmediato."""
    update = MagicMock()
    msg_espera = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_espera)
    update.message.reply_document = AsyncMock()

    pdf_fake = b"%PDF-1.4 Fake PDF Content that exceeds twenty thousand bytes" + (b"0" * 20000)

    with patch("telegram_bot.procesar_denuncia_judicial", new_callable=AsyncMock) as mock_judicial:
        mock_judicial.return_value = (True, {
            "nombre": "JUAN PEREZ",
            "cedula": "1708927502",
            "sector": "SOLANDA",
            "pdf_bytes": pdf_fake
        }, None)

        await telegram_bot.procesar_y_responder(update, "1708927502", "Solanda")

        assert mock_judicial.call_count == 1
        assert update.message.reply_document.called
        assert msg_espera.delete.called


@pytest.mark.asyncio
async def test_procesar_y_responder_asks_for_names_when_platform_requires():
    """Verifica que si la plataforma no autocompleta el nombre, pida los nombres de inmediato sin reintentar 15 veces."""
    update = MagicMock()
    update.effective_user.id = 55555
    msg_espera = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_espera)

    with patch("telegram_bot.procesar_denuncia_judicial", new_callable=AsyncMock) as mock_judicial:
        mock_judicial.return_value = (False, {"tipo": "REQUIERE_NOMBRES", "cedula": "1708927502"}, None)

        await telegram_bot.procesar_y_responder(update, "1708927502", "Solanda")

        # Debe detenerse en el intento 1
        assert mock_judicial.call_count == 1
        # Debe haber guardado el estado esperando nombre
        assert telegram_bot.user_states.get(55555, {}).get("esperando_nombre") is True
        # Debe avisar al usuario
        assert msg_espera.edit_text.called
        text_sent = msg_espera.edit_text.call_args[0][0]
        assert "solicita los nombres" in text_sent.lower()


@pytest.mark.asyncio
async def test_procesar_y_responder_aborts_on_platform_rejected_cedula():
    """Verifica que si la plataforma rechaza la cédula, aborte de inmediato sin reintentar 15 veces."""
    update = MagicMock()
    update.effective_user.id = 66666
    msg_espera = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_espera)

    with patch("telegram_bot.procesar_denuncia_judicial", new_callable=AsyncMock) as mock_judicial:
        mock_judicial.return_value = (False, {"tipo": "ERROR_CEDULA_PLATAFORMA", "mensaje": "El número no existe en el Registro Civil"}, None)

        await telegram_bot.procesar_y_responder(update, "1708927502", "Solanda")

        # Debe detenerse en el intento 1
        assert mock_judicial.call_count == 1
        assert msg_espera.edit_text.called
        text_sent = msg_espera.edit_text.call_args[0][0]
        assert "rechazada" in text_sent.lower()
        assert "Registro Civil" in text_sent


@pytest.mark.asyncio
async def test_handle_message_diagnoses_malformed_cedulas():
    """Verifica que el bot detecte y avise claramente cuando la cédula esté mal tipeada."""
    context = MagicMock()

    # 1. Cédula con más de 10 dígitos (11 dígitos)
    up1 = MagicMock()
    up1.effective_user.id = 101
    up1.message.text = "17089275021 Solanda"
    up1.message.reply_text = AsyncMock()
    await telegram_bot.handle_message(up1, context)
    assert up1.message.reply_text.called
    msg1 = up1.message.reply_text.call_args[0][0]
    assert "11 dígitos" in msg1

    # 2. Cédula con letras mezcladas (letra O)
    up2 = MagicMock()
    up2.effective_user.id = 102
    up2.message.text = "17089275O2 Solanda"
    up2.message.reply_text = AsyncMock()
    await telegram_bot.handle_message(up2, context)
    assert up2.message.reply_text.called
    msg2 = up2.message.reply_text.call_args[0][0]
    assert "letras mezcladas" in msg2.lower()

    # 3. Cédula con menos de 10 dígitos (8 dígitos)
    up3 = MagicMock()
    up3.effective_user.id = 103
    up3.message.text = "17089275 Solanda"
    up3.message.reply_text = AsyncMock()
    await telegram_bot.handle_message(up3, context)
    assert up3.message.reply_text.called
    msg3 = up3.message.reply_text.call_args[0][0]
    assert "8 dígitos" in msg3

    # 4. Cédula de 10 dígitos pero módulo 10 inválido
    up4 = MagicMock()
    up4.effective_user.id = 104
    up4.message.text = "1234567890 Solanda"
    up4.message.reply_text = AsyncMock()
    await telegram_bot.handle_message(up4, context)
    assert up4.message.reply_text.called
    msg4 = up4.message.reply_text.call_args[0][0]
    assert "inválida" in msg4.lower() or "no es una cédula" in msg4.lower()


@pytest.mark.asyncio
async def test_handle_message_provides_name_on_followup():
    """Verifica que si el bot estaba esperando nombre, lo tome del siguiente mensaje y proceda."""
    telegram_bot.user_states[77777] = {
        "flujo": "denuncia",
        "cedula": "1708927502",
        "sector": "SOLANDA",
        "tipo_denuncia": "cedula",
        "tipo_licencia": None,
        "esperando_nombre": True
    }

    update = MagicMock()
    update.effective_user.id = 77777
    update.message.text = "MARIA ESPERANZA GOMEZ"
    context = MagicMock()

    with patch("telegram_bot.procesar_y_responder", new_callable=AsyncMock) as mock_proc:
        await telegram_bot.handle_message(update, context)
        assert mock_proc.called
        kwargs = mock_proc.call_args[1]
        assert kwargs["nombre_manual"] == "MARIA ESPERANZA GOMEZ"
        assert kwargs["cedula"] == "1708927502"
        # El estado debió limpiarse
        assert 77777 not in telegram_bot.user_states


@pytest.mark.asyncio
async def test_reset_shared_browser():
    """Verifica que reset_shared_browser cierre y limpie los objetos globales."""
    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()

    telegram_bot._global_browser = mock_browser
    telegram_bot._global_playwright = mock_playwright

    await telegram_bot.reset_shared_browser()

    assert mock_browser.close.called
    assert mock_playwright.stop.called
    assert telegram_bot._global_browser is None
    assert telegram_bot._global_playwright is None


@pytest.mark.asyncio
async def test_handle_message_safe_on_empty_or_none():
    """Verifica que mensajes sin texto no provoquen excepciones no controladas."""
    update = MagicMock()
    update.message = None
    context = MagicMock()

    await telegram_bot.handle_message(update, context)

    update.message = MagicMock()
    update.message.text = None
    await telegram_bot.handle_message(update, context)


@pytest.mark.asyncio
async def test_procesar_denuncia_judicial_timeout():
    """Verifica que un timeout en la ejecución del formulario no cuelgue y retorne False limpiamente."""
    mock_browser = MagicMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)

    with patch("telegram_bot.get_shared_browser", new_callable=AsyncMock, return_value=mock_browser), \
         patch("telegram_bot._ejecutar_formulario", new_callable=AsyncMock) as mock_form:
        async def slow_form(*args, **kwargs):
            await asyncio.sleep(100)
            return None, None, None
        mock_form.side_effect = slow_form

        with patch("asyncio.wait_for", side_effect=[mock_browser, asyncio.TimeoutError]):
            success, err_msg, _ = await telegram_bot.procesar_denuncia_judicial("1708927502", "Solanda")
            assert success is False
            assert "agotado" in err_msg.lower()
            assert mock_context.close.called


@pytest.mark.asyncio
async def test_procesar_denuncia_judicial_crash_triggers_reset():
    """Verifica que una caída del navegador (target closed) active el reset_shared_browser."""
    mock_browser = MagicMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)

    async def fail_form(*args, **kwargs):
        raise Exception("Target closed")

    with patch("telegram_bot.get_shared_browser", new_callable=AsyncMock, return_value=mock_browser), \
         patch("telegram_bot._ejecutar_formulario", side_effect=fail_form), \
         patch("telegram_bot.reset_shared_browser", new_callable=AsyncMock) as mock_reset:
        success, err_msg, _ = await telegram_bot.procesar_denuncia_judicial("1708927502", "Solanda")
        assert success is False
        assert mock_reset.called
