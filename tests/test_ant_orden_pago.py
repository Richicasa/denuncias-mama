import pytest
from ant_orden_pago import ANTOrdenPagoClient

@pytest.mark.asyncio
async def test_ant_invalid_cedula():
    client = ANTOrdenPagoClient()
    res = await client.obtener_orden_pago_pdf("0000000000", id_servicio=1)
    assert res["success"] is False
    assert "inválida" in res["error_mensaje"].lower() or "cedula" in res["error_mensaje"].lower()

@pytest.mark.asyncio
async def test_ant_client_structure():
    client = ANTOrdenPagoClient()
    assert hasattr(client, "obtener_orden_pago_pdf")
    assert hasattr(client, "_obtener_browser")
