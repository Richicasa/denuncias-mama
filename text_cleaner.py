import re
import difflib

# Diccionario ampliado de sectores y barrios de Quito y valles con ortografía oficial
SECTORES_QUITO = [
    "El Recreo", "La Magdalena", "Carapungo", "Centro Histórico", "Quitumbe",
    "Solanda", "Chillogallo", "Calderón", "La Mariscal", "La Carolina",
    "Cumbayá", "Tumbaco", "Guamaní", "Chimbacalle", "La Villaflora",
    "San Roque", "El Tejar", "El Condado", "El Inca", "El Bosque", "El Pintado",
    "La Floresta", "La Vicentina", "La Gasca", "La Tola", "La Armenia",
    "Cotocollao", "Ponceano", "Carcelén", "Conocoto", "San Antonio de Pichincha",
    "Pomasqui", "Nayón", "Zámbiza", "Llano Chico", "Llano Grande", "Guayllabamba",
    "Puengasí", "La Argelia", "La Ferroviaria", "La Ecuatoriana", "Turubamba",
    "San Bartolo", "Mena 2", "El Camal", "La Roldós", "Pisulí", "San Juan",
    "Itchimbía", "González Suárez", "Bellavista", "Monteserrín", "Granda Centeno",
    "Las Casas", "Miraflores", "Santa Prisca", "Guápulo", "San Carlos",
    "El Labrador", "La Ofelia", "Monjas", "San José de Morán", "San Isidro de El Inca",
    "Los Chillos", "Tumbaco", "Pifo", "Yaruquí", "Checa", "El Quinche", "Tababela",
    "Sangolquí", "Amaguaña", "Pintag", "Alangasí"
]

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
        
    # Capitalizar apropiadamente
    palabras = [w.capitalize() for w in texto_sin_prefijos.split()]
    articulos_menores = {"de", "del", "la", "el", "los", "las", "y", "en"}
    palabras_formateadas = []
    for i, p in enumerate(palabras):
        if i > 0 and p.lower() in articulos_menores:
            palabras_formateadas.append(p.lower())
        else:
            palabras_formateadas.append(p)
    texto_procesado = " ".join(palabras_formateadas)
    
    # 2. Búsqueda difusa (Fuzzy matching)
    mejor_coincidencia = None
    mejor_ratio = 0.0
    
    # Comprobación directa contra sectores conocidos (incluyendo sin artículo)
    for sector in SECTORES_QUITO:
        # Comparar con el nombre completo
        r1 = difflib.SequenceMatcher(None, texto_procesado.lower(), sector.lower()).ratio()
        # Comparar también quitando el artículo inicial ("El Recreo" -> "Recreo")
        sector_sin_art = re.sub(r'^(el|la|los|las)\s+', '', sector, flags=re.IGNORECASE)
        r2 = difflib.SequenceMatcher(None, texto_procesado.lower(), sector_sin_art.lower()).ratio()
        
        max_r = max(r1, r2)
        if max_r > 0.80 and max_r > mejor_ratio:
            mejor_ratio = max_r
            mejor_coincidencia = sector
            
    sector_final = mejor_coincidencia if mejor_coincidencia else texto_procesado
    
    # 3. Construcción gramatical correcta
    if sector_final.lower().startswith("el "):
        nombre_sin_articulo = sector_final[3:]
        domicilio = f"Sector {sector_final}"
        circunstancia = f"Documento extraviado en el sector del {nombre_sin_articulo}"
    elif sector_final.lower().startswith("la ") or sector_final.lower().startswith("las ") or sector_final.lower().startswith("los "):
        domicilio = f"Sector {sector_final}"
        circunstancia = f"Documento extraviado en el sector de {sector_final}"
    elif "centro" in sector_final.lower():
        domicilio = f"Sector {sector_final}"
        circunstancia = f"Documento extraviado en el sector del {sector_final}"
    else:
        domicilio = f"Sector {sector_final}"
        circunstancia = f"Documento extraviado en el sector de {sector_final}"
        
    return {
        "sector_limpio": sector_final,
        "direccion_domicilio": domicilio,
        "direccion_circunstancia": circunstancia
    }
