import pytest
from ant_parser import validar_cedula_ec, detectar_mensaje_ant, parsear_mensaje_ant

def test_validar_cedula_ec():
    assert validar_cedula_ec("1710034065") is True
    assert validar_cedula_ec("0925483927") is True
    assert validar_cedula_ec("1728970128") is True
    assert validar_cedula_ec("1234567890") is False
    assert validar_cedula_ec("abc") is False
    assert validar_cedula_ec("") is False

def test_detectar_mensaje_ant():
    # Mensaje típico one-shot
    assert detectar_mensaje_ant("1728970128, orden de pago, tipo A, primera vez") is True
    # Con variaciones de espaciado o erratas comunes
    assert detectar_mensaje_ant("orden pago 0925483927 renovacion") is True
    assert detectar_mensaje_ant("comprobante de pago 1710034065 tipo B") is True
    assert detectar_mensaje_ant("oden de pago 1710034065") is True
    assert detectar_mensaje_ant("orden d pago 1710034065") is True
    assert detectar_mensaje_ant("pago licencia 1710034065") is True
    assert detectar_mensaje_ant("comprovante pago 1710034065") is True
    # Casos que NO deben ser detectados como ANT
    assert detectar_mensaje_ant("record policial 1710034065") is False
    assert detectar_mensaje_ant("denuncia de robo en Solanda") is False
    assert detectar_mensaje_ant("titulo de bachiller 1710034065") is False

def test_parsear_mensaje_ant_completo():
    res = parsear_mensaje_ant("1710034065, orden de pago, tipo B, primera vez")
    assert res["cedula"] == "1710034065"
    assert res["id_servicio"] == 5
    assert res["servicio_nombre"] == "PRIMERA VEZ"
    assert res["tipo_licencia"] == "B"
    assert res["es_completo"] is True
    assert res["datos_faltantes"] == []

def test_parsear_mensaje_ant_duplicado():
    res = parsear_mensaje_ant("orden de pago 0925483927 duplicado tipo C")
    assert res["cedula"] == "0925483927"
    assert res["id_servicio"] == 1004
    assert res["servicio_nombre"] == "DUPLICADO"
    assert res["tipo_licencia"] == "C"
    assert res["es_completo"] is True
    assert res["datos_faltantes"] == []

def test_parsear_mensaje_ant_renovacion_completa():
    res = parsear_mensaje_ant("orden de pago 0925483927 tipo A renovacion")
    assert res["cedula"] == "0925483927"
    assert res["id_servicio"] == 1
    assert res["servicio_nombre"] == "RENOVACION"
    assert res["tipo_licencia"] == "A"
    assert res["es_completo"] is True
    assert res["datos_faltantes"] == []

def test_parsear_mensaje_ant_falta_servicio_y_tipo():
    # Solo envía la cédula y la frase orden de pago
    res = parsear_mensaje_ant("orden de pago 1710034065")
    assert res["cedula"] == "1710034065"
    assert res["id_servicio"] is None
    assert res["tipo_licencia"] is None
    assert res["es_completo"] is False
    assert "tipo_tramite" in res["datos_faltantes"]
    assert "tipo_licencia" in res["datos_faltantes"]

def test_parsear_mensaje_ant_falta_cedula():
    # Envía tipo y servicio pero sin cédula
    res = parsear_mensaje_ant("orden de pago tipo B primera vez")
    assert res["cedula"] is None
    assert res["id_servicio"] == 5
    assert res["tipo_licencia"] == "B"
    assert res["es_completo"] is False
    assert res["datos_faltantes"] == ["cedula"]

def test_parsear_mensaje_ant_falta_tipo():
    # Envía cédula y renovación pero no el tipo de licencia
    res = parsear_mensaje_ant("orden de pago 1710034065 renovacion")
    assert res["cedula"] == "1710034065"
    assert res["id_servicio"] == 1
    assert res["tipo_licencia"] is None
    assert res["es_completo"] is False
    assert res["datos_faltantes"] == ["tipo_licencia"]

def test_parsear_mensaje_ant_cedula_invalida():
    res = parsear_mensaje_ant("orden de pago 1234567890 tipo A renovacion")
    assert res["es_completo"] is False
    assert "cedula" in res["datos_faltantes"]
    assert "inválida" in res["error"].lower()
