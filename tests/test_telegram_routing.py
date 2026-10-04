import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram_bot import handle_message

@pytest.mark.asyncio
async def test_handle_message_routes_to_ant():
    update = MagicMock()
    update.effective_user.id = 99999
    update.message.text = "1728970128, orden de pago, tipo A, primera vez"
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.handle_message_ant", new_callable=AsyncMock) as mock_handle_ant:
        await handle_message(update, context)
        assert mock_handle_ant.called
        args = mock_handle_ant.call_args[0]
        parsed = args[2]
        assert parsed["cedula"] == "1728970128"
        assert parsed["id_servicio"] == 5
        assert parsed["tipo_licencia"] == "A"

@pytest.mark.asyncio
async def test_handle_message_preserves_judicatura():
    update = MagicMock()
    update.effective_user.id = 99999
    update.message.text = "1708927502 Sector El Recreo"
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.procesar_y_responder", new_callable=AsyncMock) as mock_proc:
        await handle_message(update, context)
        assert mock_proc.called
        args = mock_proc.call_args[0]
        assert args[1] == "1708927502"
