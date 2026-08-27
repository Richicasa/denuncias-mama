import re
import unicodedata

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto_sin_acentos = ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )
    return texto_sin_acentos.upper().strip()

def limpiar_y_corregir_sector(raw_input: str) -> dict:
    if not raw_input or not raw_input.strip():
        return {
            "sector_limpio": "CENTRO",
            "direccion_domicilio": "SECTOR CENTRO",
            "direccion_circunstancia": "EXTRAVIO EN SECTOR CENTRO"
        }
        
    limpio = normalizar_texto(raw_input)
    limpio = re.sub(r'[\r\n\t]+', ' ', limpio)
    limpio = re.sub(r'[^\w\s\-\.,#]', '', limpio)
    limpio = re.sub(r'\s+', ' ', limpio).strip()
    
    correcciones = {
        r'\bRECREO\b': 'EL RECREO',
        r'\bVILLAFLORA\b': 'LA VILLAFLORA',
        r'\bFLORESTA\b': 'LA FLORESTA',
        r'\bMARISCAL\b': 'LA MARISCAL',
        r'\bMAGDALENA\b': 'LA MAGDALENA',
        r'\bCAROLINA\b': 'LA CAROLINA',
        r'\bQUITUMBE\b': 'SECTOR QUITUMBE',
        r'\bCONDADO\b': 'EL CONDADO',
        r'\bINCA\b': 'EL INCA',
        r'\bCENTRO\b': 'CENTRO HISTORICO',
        r'\bCALDERON\b': 'CALDERON',
        r'\bCHILLOGALLO\b': 'CHILLOGALLO',
        r'\bSOLANDA\b': 'SOLANDA',
        r'\bCOOTOCOLLAO\b': 'COTOCOLLAO',
        r'\bCOTOCOLAO\b': 'COTOCOLLAO',
        r'\bCUMBAYA\b': 'CUMBAYA',
        r'\bTUMBACO\b': 'TUMBACO',
        r'\bGUAMANI\b': 'GUAMANI'
    }
    
    for patron, reemplazo in correcciones.items():
        if re.search(patron, limpio):
            limpio = re.sub(patron, reemplazo, limpio)
            break
            
    limpio = re.sub(r'\s+', ' ', limpio).strip()
    
    if len(limpio) < 3:
        limpio = "SECTOR CENTRO"
        
    direccion_domicilio = f"SECTOR {limpio}".replace("SECTOR SECTOR", "SECTOR").strip()
    direccion_circunstancia = f"EXTRAVIO EN SECTOR {limpio}".replace("SECTOR SECTOR", "SECTOR").strip()
    
    if len(direccion_domicilio) > 100:
        direccion_domicilio = direccion_domicilio[:100]
    if len(direccion_circunstancia) > 100:
        direccion_circunstancia = direccion_circunstancia[:100]
        
    return {
        "sector_limpio": limpio,
        "direccion_domicilio": direccion_domicilio,
        "direccion_circunstancia": direccion_circunstancia
    }
