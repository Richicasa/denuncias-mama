import re
import difflib
import urllib.request
import urllib.parse
import json

# Diccionario ampliado de sectores y barrios de Quito y valles con ortografia oficial
SECTORES_QUITO = [
    "El Recreo", "La Magdalena", "Carapungo", "Centro Historico", "Quitumbe",
    "Solanda", "Chillogallo", "Calderon", "La Mariscal", "La Carolina",
    "Cumbaya", "Tumbaco", "Guamani", "Chimbacalle", "La Villaflora",
    "San Roque", "El Tejar", "El Condado", "El Inca", "El Bosque", "El Pintado",
    "La Floresta", "La Vicentina", "La Gasca", "La Tola", "La Armenia",
    "Cotocollao", "Ponceano", "Carcelen", "Conocoto", "San Antonio de Pichincha",
    "Pomasqui", "Nayon", "Zambiza", "Llano Chico", "Llano Grande", "Guayllabamba",
    "Puengasi", "La Argelia", "La Ferroviaria", "La Ecuatoriana", "Turubamba",
    "San Bartolo", "Mena 2", "El Camal", "La Roldos", "Pisuli", "San Juan",
    "Itchimbia", "Gonzalez Suarez", "Bellavista", "Monteserin", "Granda Centeno",
    "Las Casas", "Miraflores", "Santa Prisca", "Guapulo", "San Carlos",
    "El Labrador", "La Ofelia", "Monjas", "San Jose de Moran", "San Isidro de El Inca",
    "Los Chillos", "Pifo", "Yaruqui", "Checa", "El Quinche", "Tababela",
    "Sangolqui", "Amaguanna", "Pintag", "Alangasi",
    "Lucha de los Pobres", "Atucucho", "Toctiuco", "Quito Sur", "Quito Norte",
    "Rumipamba", "Inaiquito", "El Ejido", "La Colmena", "Comite del Pueblo",
    "La Bota", "San Vicente", "El Troje", "El Beaterio",
    "Cochasqui", "Tabacundo", "Pedro Moncayo"
]

# Versiones con tildes para display final
DISPLAY_NOMBRES = {
    "centro historico": "Centro Historico",
    "calderon": "Calderon",
    "cumbaya": "Cumbaya",
    "guamani": "Guamani",
    "puengasi": "Puengasi",
    "nayon": "Nayon",
    "zambiza": "Zambiza",
    "carcelen": "Carcelen",
    "sangolqui": "Sangolqui",
    "lucha de los pobres": "Lucha de los Pobres",
    "itchimbia": "Itchimbia",
    "guapulo": "Guapulo",
    "pisuli": "Pisuli",
}

ARTICULOS_MENORES = {"de", "del", "la", "el", "los", "las", "y", "en", "a", "al"}


def _separar_palabras_pegadas(texto: str) -> str:
    """Corrige palabras pegadas a articulos/preposiciones.
    Ej: 'lucha delos pobres' -> 'lucha de los pobres'
    """
    correcciones = [
        (r'\bdelos\b', 'de los'),
        (r'\bdelas\b', 'de las'),
        (r'\bdela\b', 'de la'),
        (r'\balos\b', 'a los'),
        (r'\balas\b', 'a las'),
        (r'\bala\b', 'a la'),
        (r'\bpor los\b', 'por los'),
    ]
    for patron, reemplazo in correcciones:
        texto = re.sub(patron, reemplazo, texto, flags=re.IGNORECASE)
    return texto


def buscar_sector_en_web(texto: str) -> str | None:
    """Consulta Nominatim (OpenStreetMap) para obtener el nombre oficial del lugar."""
    try:
        query = "{}, Quito, Pichincha, Ecuador".format(texto)
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
            "accept-language": "es",
            "countrycodes": "ec"
        })
        url = "https://nominatim.openstreetmap.org/search?{}".format(params)
        req = urllib.request.Request(url, headers={"User-Agent": "DenunciasBot/1.0 (Ecuador)"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        if not data:
            return None

        hit = data[0]
        address = hit.get("address", {})

        ciudad = address.get("city", address.get("town", "")).lower()
        condado = address.get("county", "").lower()
        if "quito" not in ciudad and "pichincha" not in condado:
            return None

        for campo in ["suburb", "neighbourhood", "quarter", "village", "city_district", "district"]:
            nombre = address.get(campo)
            if nombre:
                return nombre.strip()

        return None
    except Exception:
        return None


def _capitalizar_nombre(texto: str) -> str:
    palabras = texto.split()
    resultado = []
    for i, p in enumerate(palabras):
        if i > 0 and p.lower() in ARTICULOS_MENORES:
            resultado.append(p.lower())
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)


def limpiar_y_corregir_sector(texto_original: str) -> dict:
    if not texto_original:
        return {
            "sector_limpio": "",
            "direccion_domicilio": "",
            "direccion_circunstancia": ""
        }

    texto = texto_original.strip()

    # 1. Eliminar prefijos comunes redundantes
    prefijos = [
        r"^documento\s+extraviado\s+en\s+(el\s+sector\s+(de|del)?\s*)?",
        r"^extraviado\s+en\s+(el\s+sector\s+(de|del)?\s*)?",
        r"^en\s+el\s+sector\s+(de|del)?\s*",
        r"^sector\s+(de|del)?\s*",
        r"^en\s+(el|la|los|las)?\s*",
        r"^por\s+(el|la|los|las)?\s*",
        r"^cerca\s+(de|al|a\s+la)?\s*",
        r"^barrio\s+(de|del)?\s*",
    ]

    texto_sin_prefijos = texto
    for p in prefijos:
        texto_sin_prefijos = re.sub(p, "", texto_sin_prefijos, flags=re.IGNORECASE).strip()

    if not texto_sin_prefijos:
        texto_sin_prefijos = texto

    # 2. Corregir palabras pegadas (ej: "delos" -> "de los")
    texto_sin_prefijos = _separar_palabras_pegadas(texto_sin_prefijos)

    # 3. Capitalizar apropiadamente
    texto_procesado = _capitalizar_nombre(texto_sin_prefijos)

    # 4. Busqueda difusa local (umbral 0.72)
    mejor_coincidencia_local = None
    mejor_ratio = 0.0

    for sector in SECTORES_QUITO:
        r1 = difflib.SequenceMatcher(None, texto_procesado.lower(), sector.lower()).ratio()
        sector_sin_art = re.sub(r"^(el|la|los|las)\s+", "", sector, flags=re.IGNORECASE)
        r2 = difflib.SequenceMatcher(None, texto_procesado.lower(), sector_sin_art.lower()).ratio()

        max_r = max(r1, r2)
        if max_r > 0.72 and max_r > mejor_ratio:
            mejor_ratio = max_r
            mejor_coincidencia_local = sector

    # 5. Si la coincidencia local es muy alta, usarla directamente
    if mejor_coincidencia_local and mejor_ratio >= 0.88:
        sector_final = mejor_coincidencia_local
    else:
        # 6. Consultar OpenStreetMap para sectores poco conocidos o en Kichwa
        nombre_osm = buscar_sector_en_web(texto_procesado)
        if nombre_osm:
            sector_final = _capitalizar_nombre(nombre_osm)
        elif mejor_coincidencia_local:
            sector_final = mejor_coincidencia_local
        else:
            sector_final = texto_procesado

    # 7. Construccion gramatical correcta
    sf_lower = sector_final.lower()
    if sf_lower.startswith("el "):
        nombre_sin_articulo = sector_final[3:]
        domicilio = "Sector {}".format(sector_final)
        circunstancia = "Documento extraviado en el sector del {}".format(nombre_sin_articulo)
    elif sf_lower.startswith("la ") or sf_lower.startswith("las ") or sf_lower.startswith("los "):
        domicilio = "Sector {}".format(sector_final)
        circunstancia = "Documento extraviado en el sector de {}".format(sector_final)
    elif "centro" in sf_lower:
        domicilio = "Sector {}".format(sector_final)
        circunstancia = "Documento extraviado en el sector del {}".format(sector_final)
    else:
        domicilio = "Sector {}".format(sector_final)
        circunstancia = "Documento extraviado en el sector de {}".format(sector_final)

    return {
        "sector_limpio": sector_final,
        "direccion_domicilio": domicilio,
        "direccion_circunstancia": circunstancia
    }