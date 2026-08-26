import datetime
import random

def generate_judicial_pdf_html(cedula: str, nombre: str, dir_domicilio: str, dir_circunstancia: str, fecha_extravio: str = None, codigo_doc: str = None):
    now = datetime.datetime.now()
    if not fecha_extravio:
        fecha_extravio = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if not codigo_doc:
        codigo_doc = str(random.randint(150000000, 250000000))
        
    fecha_registro = now.strftime("%Y-%m-%d %H:%M")
    footer_timestamp = now.strftime("%m/%d/%y %I:%M %p")
    
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
    @page {{
        size: A4;
        margin: 15mm 15mm 15mm 15mm;
    }}
    body {{
        font-family: Arial, Helvetica, sans-serif;
        color: #000;
        margin: 0;
        padding: 0;
        font-size: 11px;
        line-height: 1.35;
    }}
    .header-table {{
        width: 100%;
        margin-bottom: 8px;
        border-collapse: collapse;
    }}
    .logo-img {{
        font-size: 26px;
        font-weight: 900;
        letter-spacing: -1px;
        color: #333;
        line-height: 1;
    }}
    .logo-sub {{
        display: block;
        width: 130px;
        height: 3px;
        background: linear-gradient(to right, #ffd700 33%, #003399 33%, #003399 66%, #cc0000 66%);
        margin-top: 3px;
    }}
    .header-banner {{
        background-color: #00387b;
        color: #ffffff;
        font-size: 13px;
        font-weight: bold;
        text-align: right;
        padding: 8px 14px;
        border-radius: 2px;
    }}
    .meta-table {{
        width: 100%;
        margin-bottom: 12px;
        border-collapse: collapse;
        font-size: 11px;
    }}
    .meta-table td {{
        padding: 4px 0;
    }}
    .barcode-box {{
        text-align: right;
        font-family: 'Courier New', Courier, monospace;
        font-weight: bold;
        font-size: 10px;
    }}
    .barcode-lines {{
        display: inline-block;
        letter-spacing: 2px;
        font-size: 20px;
        font-weight: bold;
        transform: scaleY(0.7);
    }}
    .section-title {{
        background-color: #00387b;
        color: #ffffff;
        font-size: 11.5px;
        font-weight: bold;
        padding: 4px 8px;
        margin-top: 10px;
        margin-bottom: 6px;
    }}
    .info-table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 8px;
    }}
    .info-table td {{
        padding: 3px 6px;
        vertical-align: top;
        font-size: 10.5px;
    }}
    .info-table td.label {{
        width: 32%;
        font-weight: bold;
        color: #000;
    }}
    .info-table td.value {{
        width: 68%;
        color: #111;
    }}
    .doc-table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 6px;
        margin-bottom: 15px;
        border: 1px solid #999;
    }}
    .doc-table th {{
        background-color: #0056b3;
        color: #ffffff;
        font-size: 10.5px;
        font-weight: bold;
        padding: 6px;
        text-align: left;
        border: 1px solid #0056b3;
    }}
    .doc-table td {{
        padding: 8px 6px;
        font-size: 10.5px;
        border: 1px solid #ccc;
    }}
    .footer-decl-table {{
        width: 100%;
        margin-top: 15px;
        margin-bottom: 12px;
        border-collapse: collapse;
    }}
    .footer-decl-text {{
        width: 70%;
        font-size: 9.5px;
        text-align: justify;
        line-height: 1.3;
    }}
    .footer-decl-logo {{
        width: 30%;
        text-align: right;
        font-size: 18px;
        font-weight: bold;
        color: #333;
    }}
    .legal-notice {{
        font-size: 9px;
        color: #222;
        margin-top: 8px;
        line-height: 1.35;
        text-align: justify;
    }}
    .legal-link {{
        color: #0044cc;
        font-weight: bold;
        text-decoration: none;
    }}
    .page-footer {{
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        display: flex;
        justify-content: space-between;
        font-size: 9px;
        color: #555;
        border-top: 1px solid #ddd;
        padding-top: 4px;
    }}
</style>
</head>
<body>

    <!-- Encabezado Principal -->
    <table class="header-table">
        <tr>
            <td style="width: 40%;">
                <div class="logo-img">FUNCIÓN JUDICIAL</div>
                <div class="logo-sub"></div>
            </td>
            <td style="width: 60%;">
                <div class="header-banner">
                    Formulario de Constancia de Documentos Extraviados
                </div>
            </td>
        </tr>
    </table>

    <!-- Metadatos de Registro y Código de Barras -->
    <table class="meta-table">
        <tr>
            <td style="width: 38%;">
                <strong>Fecha de registro:</strong> {fecha_registro}
            </td>
            <td style="width: 32%;">
                <strong>Código del documento:</strong> {codigo_doc}
            </td>
            <td style="width: 30%;" class="barcode-box">
                <div class="barcode-lines">||||||||||||||||||||||||</div>
                <div>{codigo_doc}</div>
            </td>
        </tr>
    </table>

    <!-- Sección 1: Datos del solicitante -->
    <div class="section-title">Datos del solicitante</div>
    <table class="info-table">
        <tr>
            <td class="label">Tipo de identificación:</td>
            <td class="value">Cédula de Ciudadanía</td>
        </tr>
        <tr>
            <td class="label">Número de identificación:</td>
            <td class="value">{cedula}</td>
        </tr>
        <tr>
            <td class="label">Nombres completos:</td>
            <td class="value">{nombre.upper()}</td>
        </tr>
        <tr>
            <td class="label">Provincia de domicilio:</td>
            <td class="value">PICHINCHA</td>
        </tr>
        <tr>
            <td class="label">Cantón de domicilio:</td>
            <td class="value">QUITO</td>
        </tr>
        <tr>
            <td class="label">Dirección:</td>
            <td class="value">{dir_domicilio.upper()}</td>
        </tr>
    </table>

    <!-- Sección 2: Datos del extravío -->
    <div class="section-title">Datos del extravío</div>
    <table class="info-table">
        <tr>
            <td class="label">Provincia de extravío:</td>
            <td class="value">PICHINCHA</td>
        </tr>
        <tr>
            <td class="label">Cantón de extravío:</td>
            <td class="value">QUITO</td>
        </tr>
        <tr>
            <td class="label">Fecha cuando extravió los documentos:</td>
            <td class="value">{fecha_extravio}</td>
        </tr>
        <tr>
            <td class="label">Dirección o circunstancia de extravío:</td>
            <td class="value">{dir_circunstancia.upper()}</td>
        </tr>
    </table>

    <!-- Sección 3: Documentos extraviados -->
    <div class="section-title">Documentos extraviados</div>
    <table class="doc-table">
        <thead>
            <tr>
                <th style="width: 40%;">Tipo de documento</th>
                <th style="width: 25%;">Número</th>
                <th style="width: 35%;">Descripción</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>Cédula de Ciudadanía / Identidad</td>
                <td>{cedula}</td>
                <td>CÉDULA DE IDENTIDAD</td>
            </tr>
        </tbody>
    </table>

    <!-- Declaración y Firma Legal -->
    <table class="footer-decl-table">
        <tr>
            <td class="footer-decl-text">
                Yo, {nombre.upper()}, declaro que toda la información constante en el presente formulario es verídica, y asumo cualquier tipo de responsabilidad civil, penal o administrativa por error o falsedad en la misma.
            </td>
            <td class="footer-decl-logo">
                <div style="font-size: 16px; font-weight: 900; color: #444;">FUNCIÓN JUDICIAL</div>
                <div style="width: 100px; height: 2.5px; background: linear-gradient(to right, #ffd700 33%, #003399 33%, #003399 66%, #cc0000 66%); float: right; margin-top: 2px;"></div>
            </td>
        </tr>
    </table>

    <!-- Notas Legales -->
    <div class="legal-notice">
        El presente formulario NO REEMPLAZA A LOS DOCUMENTOS EXTRAVIADOS, únicamente constituye una constancia de su pérdida. Ninguna entidad pública o privada deberá solicitar al titular algún sello o certificación adicional en este documento para justificar su contenido.
    </div>
    <div class="legal-notice" style="margin-top: 4px;">
        El contenido íntegro del presente Formulario de Constancia de Documentos Extraviados está disponible para su verificación al ingresar el código del documento en la pestaña de "Consulta de Documentos", disponible en la dirección electrónica:<br>
        <span class="legal-link">http://appsj.funcionjudicial.gob.ec/documentosExtraviados/publico/formulario.jsf</span>
    </div>

    <!-- Pie de página -->
    <div style="margin-top: 25px; display: flex; justify-content: space-between; font-size: 9px; color: #444;">
        <span>{footer_timestamp}</span>
        <span style="float: right;">Página 1 de 1</span>
    </div>

</body>
</html>
"""
    return html
