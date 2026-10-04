import re
import unicodedata

def _normalizar_texto(texto: str) -> str:
    """Normaliza quitando tildes y caracteres especiales, a minúsculas."""
    if not texto:
        return ""
    texto = unicodedata.normalize('NFKD', texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower()

def validar_cedula_ec(cedula: str) -> bool:
    """
    Valida si una cédula ecuatoriana de 10 dígitos es válida según el algoritmo Módulo 10.
    """
    if not cedula or not isinstance(cedula, str):
        return False
    cedula = cedula.strip()
    if not re.fullmatch(r'\d{10}', cedula):
        return False

    provincia = int(cedula[:2])
    # Provincias 01 a 24 o 30 para ecuatorianos registrados en el exterior
    if not (1 <= provincia <= 24 or provincia == 30):
        return False

    tercer_digito = int(cedula[2])
    # Personas naturales: tercer dígito < 6
    if tercer_digito >= 6:
        return False

    coeficientes = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    suma = 0
    for coef, digito in zip(coeficientes, cedula[:9]):
        valor = coef * int(digito)
        if valor >= 10:
            valor -= 9
        suma += valor

    digito_verificador = (10 - (suma % 10)) % 10
    return digito_verificador == int(cedula[9])

# Patrones para detectar intención de orden/comprobante de pago ANT
_PATRONES_ORDEN_PAGO = [
    r'orden\s+d?e?\s*pago',          # orden de pago, orden pago, orden d pago
    r'oden\s+d?e?\s*pago',           # oden de pago (error tipográfico común)
    r'comprobante\s+d?e?\s*pago',    # comprobante de pago, comprobante pago
    r'comprovante\s+d?e?\s*pago',    # comprovante de pago (error tipográfico común)
    r'pago\s+licencia',              # pago licencia
    r'orden\s+ant',                  # orden ant
    r'turno\s+pago',                 # turno pago
    r'comprobante\s+ant',            # comprobante ant
]

def detectar_mensaje_ant(texto: str) -> bool:
    """
    Detecta si el mensaje del usuario corresponde a una solicitud de orden de pago de la ANT.
    Tolerante a mayúsculas/minúsculas, tildes y erratas frecuentes.
    """
    if not texto:
        return False
    norm = _normalizar_texto(texto)
    for pat in _PATRONES_ORDEN_PAGO:
        if re.search(pat, norm):
            return True
    return False

def parsear_mensaje_ant(texto: str) -> dict:
    """
    Parsea un mensaje natural tipo:
    '1728970128, orden de pago, tipo A, primera vez'
    o variaciones, extrayendo de forma precisa:
    - cedula (10 dígitos validados)
    - id_servicio (1: RENOVACIÓN, 5: PRIMERA VEZ, 1004: DUPLICADO)
    - tipo_licencia (A, B, C, D, E, F, G)
    - datos_faltantes: lista de campos que no fueron proporcionados
    - es_completo: bool (True si no falta ningún dato)
    - error: mensaje explicativo si hay algún dato erróneo
    """
    if not texto:
        return {
            "cedula": None,
            "id_servicio": None,
            "servicio_nombre": None,
            "tipo_licencia": None,
            "datos_faltantes": ["cedula", "tipo_tramite", "tipo_licencia"],
            "es_completo": False,
            "error": "Mensaje vacío"
        }

    norm = _normalizar_texto(texto)
    datos_faltantes = []
    error = None

    # 1. Extraer cédula (primer bloque de 10 dígitos)
    match_cedula = re.search(r'\b(\d{10})\b', texto)
    cedula = match_cedula.group(1) if match_cedula else None

    if not cedula:
        datos_faltantes.append("cedula")
    elif not validar_cedula_ec(cedula):
        datos_faltantes.append("cedula")
        error = f"La cédula {cedula} es inválida según el registro civil ecuatoriano."

    # 2. Extraer tipo de servicio (si se menciona explícitamente)
    id_servicio = None
    servicio_nombre = None
    if re.search(r'primer[ao]|1ra\s*vez|primera\s*vez|emision', norm):
        id_servicio = 5
        servicio_nombre = "PRIMERA VEZ"
    elif re.search(r'duplicad[oa]|copia', norm):
        id_servicio = 1004
        servicio_nombre = "DUPLICADO"
    elif re.search(r'renovaci|renovar', norm):
        id_servicio = 1
        servicio_nombre = "RENOVACION"
    else:
        datos_faltantes.append("tipo_tramite")

    # 3. Extraer tipo de licencia (A, B, C, D, E, F, G)
    tipo_licencia = None
    match_tipo = re.search(r'tipo\s*([a-g])\b', norm)
    if match_tipo:
        tipo_licencia = match_tipo.group(1).upper()
    else:
        # Buscar letra aislada si es B-G (excluyendo 'a' sola que suele ser preposición)
        # o 'a' si está cerca de 'licencia'
        match_aislado = re.search(r'\b([b-g])\b', norm)
        if match_aislado:
            tipo_licencia = match_aislado.group(1).upper()
        elif re.search(r'\blicencia\s+([a-g])\b', norm):
            tipo_licencia = re.search(r'\blicencia\s+([a-g])\b', norm).group(1).upper()
        else:
            datos_faltantes.append("tipo_licencia")

    return {
        "cedula": cedula,
        "id_servicio": id_servicio,
        "servicio_nombre": servicio_nombre,
        "tipo_licencia": tipo_licencia,
        "datos_faltantes": datos_faltantes,
        "es_completo": len(datos_faltantes) == 0,
        "error": error
    }
