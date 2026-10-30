import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import telegram_bot

@pytest.mark.asyncio
async def test_procesar_y_responder_stops_on_repeated_failure():
    """Verifica que procesar_y_responder NO entre en bucle infinito y se detenga tras MAX_RETRIES_DENUNCIA."""
    update = MagicMock()
    msg_espera = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_espera)

    with patch("telegram_bot.procesar_denuncia_judicial", new_callable=AsyncMock) as mock_judicial, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        # Siempre falla
        mock_judicial.return_value = (False, "Falla simulada de portal judicial", None)

        await telegram_bot.procesar_y_responder(update, "1708927502", "Solanda")

        # Debe llamarse exactamente MAX_RETRIES_DENUNCIA veces (3), NO infinito
        assert mock_judicial.call_count == telegram_bot.MAX_RETRIES_DENUNCIA
        # Debe haber editado el mensaje avisando a la usuaria del fallo
        assert msg_espera.edit_text.called
        last_edit_call = msg_espera.edit_text.call_args[0][0]
        assert "No se pudo generar la denuncia" in last_edit_call
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

    # No debe levantar ninguna excepción
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
            return None, None
        mock_form.side_effect = slow_form

        # El primer wait_for es get_shared_browser, el segundo es _ejecutar_formulario
        with patch("asyncio.wait_for", side_effect=[mock_browser, asyncio.TimeoutError]):
            success, err_msg, _ = await telegram_bot.procesar_denuncia_judicial("1708927502", "Solanda")
            assert success is False
            assert "agotado" in err_msg.lower()
            # El contexto debe cerrarse siempre en el bloque finally
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
