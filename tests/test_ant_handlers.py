import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ant_handlers import handle_message_ant

@pytest.mark.asyncio
async def test_handle_message_ant_missing_cedula():
    update = MagicMock()
    update.message = AsyncMock()
    update.effective_user.id = 12345
    context = MagicMock()
    user_states = {}

    parsed = {
        "cedula": None,
        "id_servicio": 1,
        "servicio_nombre": "RENOVACION",
        "tipo_licencia": "B",
        "datos_faltantes": ["cedula"],
        "es_completo": False,
        "error": None
    }

    await handle_message_ant(update, context, parsed, user_states)

    assert update.message.reply_text.called
    args = update.message.reply_text.call_args[0][0]
    assert "cédula" in args.lower()
    assert 12345 in user_states
    assert user_states[12345]["flujo"] == "orden_pago"

@pytest.mark.asyncio
async def test_handle_message_ant_missing_multiple():
    update = MagicMock()
    update.message = AsyncMock()
    update.effective_user.id = 12345
    context = MagicMock()
    user_states = {}

    # Solo cédula
    parsed = {
        "cedula": "1710034065",
        "id_servicio": None,
        "servicio_nombre": None,
        "tipo_licencia": None,
        "datos_faltantes": ["tipo_tramite", "tipo_licencia"],
        "es_completo": False,
        "error": None
    }

    await handle_message_ant(update, context, parsed, user_states)

    assert update.message.reply_text.called
    args = update.message.reply_text.call_args[0][0]
    assert "1710034065" in args
    assert "trámite" in args.lower() or "tramite" in args.lower()
    assert "tipo de licencia" in args.lower()
    assert 12345 in user_states

@pytest.mark.asyncio
async def test_handle_message_ant_multistep_merge():
    update = MagicMock()
    update.message = AsyncMock()
    msg_espera = AsyncMock()
    update.message.reply_text.return_value = msg_espera
    update.effective_user.id = 12345
    context = MagicMock()
    # Ya teníamos la cédula guardada del mensaje anterior
    user_states = {
        12345: {
            "flujo": "orden_pago",
            "cedula": "1710034065"
        }
    }

    # Ahora responde con los datos que faltaban
    parsed = {
        "cedula": None,
        "id_servicio": 1,
        "servicio_nombre": "RENOVACION",
        "tipo_licencia": "B",
        "datos_faltantes": ["cedula"],
        "es_completo": False,
        "error": None
    }

    mock_res = {
        "success": True,
        "cedula": "1710034065",
        "nombres": "ESPINOSA FLORES DORA MARGARITA",
        "tramite_id": "7654321",
        "servicio_nombre": "RENOVACIÓN DE LICENCIA",
        "pdf_path": "/tmp/test.pdf",
        "pdf_bytes": b"%PDF-1.4 mock",
        "error_mensaje": None
    }

    with patch("ant_handlers.ANTOrdenPagoClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.obtener_orden_pago_pdf = AsyncMock(return_value=mock_res)

        await handle_message_ant(update, context, parsed, user_states)

        # Como se combinaron la cédula previa y los nuevos datos, debe completar y enviar el PDF
        assert update.message.reply_document.called
        assert 12345 not in user_states

@pytest.mark.asyncio
async def test_handle_message_ant_success():
    update = MagicMock()
    update.message = AsyncMock()
    msg_espera = AsyncMock()
    update.message.reply_text.return_value = msg_espera
    update.effective_user.id = 12345
    context = MagicMock()
    user_states = {12345: {"flujo": "orden_pago"}}

    parsed = {
        "cedula": "1710034065",
        "id_servicio": 1,
        "servicio_nombre": "RENOVACION",
        "tipo_licencia": "B",
        "datos_faltantes": [],
        "es_completo": True,
        "error": None
    }

    mock_res = {
        "success": True,
        "cedula": "1710034065",
        "nombres": "ESPINOSA FLORES DORA MARGARITA",
        "tramite_id": "7654321",
        "servicio_nombre": "RENOVACIÓN DE LICENCIA",
        "pdf_path": "/tmp/test.pdf",
        "pdf_bytes": b"%PDF-1.4 mock",
        "error_mensaje": None
    }

    with patch("ant_handlers.ANTOrdenPagoClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.obtener_orden_pago_pdf = AsyncMock(return_value=mock_res)

        await handle_message_ant(update, context, parsed, user_states)

        assert update.message.reply_document.called
        assert msg_espera.delete.called
        assert 12345 not in user_states
