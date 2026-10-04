# Documento de Diseño: Nueva Herramienta "Orden de Pago ANT (Licencias)"

**Fecha:** 2026-10-04  
**Estado:** Propuesta de Diseño para Aprobación  
**Módulo:** `ant_orden_pago.py` + `ant_handlers.py` en `denuncias-app`  
**Checkpoint de Seguridad:** `checkpoint-pre-orden-pago` (Git tag & branch)

---

## 1. Resumen Ejecutivo y Objetivos

El objetivo de esta funcionalidad es incorporar al bot de Telegram una nueva herramienta independiente que permita generar y consultar **Órdenes de Pago de Licencias de Conducir** de la Agencia Nacional de Tránsito (ANT) del Ecuador (`https://consultaweb.ant.gob.ec/SVT/paginas/portal/svf_solicitar_servicio.jsp?ps_param_tip_serv=LIC`), entregando al usuario directamente el **documento PDF oficial** emitido por el sistema JasperReports de la ANT.

### Requisitos y Decisiones Clave:
1. **Entrada en un Solo Mensaje (One-Shot Natural Parsing):**
   - El usuario puede enviar todo en una sola línea. Ejemplo:
     `1728970128, orden de pago, tipo A, primera vez`
   - El parser (`detectar_mensaje_ant` y `parsear_mensaje_ant`) tolerará variaciones y errores tipográficos comunes:
     - Frases clave: `orden de pago`, `orden pago`, `oden de pago`, `orden d pago`, `comprobante de pago`, `pago licencia`, etc.
     - Trámites reconocidos:
       - *Primera vez*: `primera vez`, `1ra vez`, `primera`, `emision` -> ID `5`
       - *Duplicado*: `duplicado`, `copia` -> ID `1004`
       - *Renovación*: `renovacion`, `renovar`, `renovación` -> ID `1` (Por defecto si no se especifica)
     - Tipo de licencia: `tipo A`, `tipo B`, `tipo C`, `tipo D`, `tipo E`, `tipo F`, `tipo G` (o letra aislada `A`, `B`...).
     - Cédula: 10 dígitos continuos validados con algoritmo Módulo 10.
   - Si el usuario solo envía parte de la información (ej. `orden de pago 1728970128`), el bot mantendrá estado y preguntará amablemente el dato faltante (o botones rápidos).
2. **Generación Automática:**
   - Si el ciudadano ya posee un trámite activo registrado en la ANT (`cantTramites > 0`), se recupera de inmediato su orden de pago y PDF sin duplicar cobros.
   - Si no posee trámite (`cantTramites == 0`), el sistema lo genera automáticamente invocando el endpoint transaccional de la ANT y descarga la orden resultante.
3. **Entrega Oficial Exclusiva:** El bot enviará exclusivamente el **documento PDF oficial** adjunto (`application/pdf`) de la ANT, listo para imprimir o pagar.
4. **Cero Afectación:** Aislamiento total. Las herramientas operativas (`backend.py`, `record_policial.py`, `bachiller.py`, `telegram_bot.py`) se mantendrán intactas en su lógica existente. La nueva herramienta residirá en sus propios módulos independientes (`ant_orden_pago.py`, `ant_handlers.py`) y solo se conectará en el despachador de mensajes.

---

## 2. Arquitectura Técnica

### 2.1 Flujo Operativo y de Datos

```mermaid
flowchart TD
    User([Usuario Telegram]) -->|/orden_pago o botón| Bot[telegram_bot.py]
    Bot -->|Despacha a| Handlers[ant_handlers.py]
    Handlers -->|Solicita cédula y muestra botones| User
    User -->|Selecciona Servicio y envía Cédula| Handlers
    Handlers -->|Llama| Engine[ant_orden_pago.py]
    
    subgraph "Motor ANT (Headless Browser Session)"
        Engine -->|1. Inicia sesión activa| Portal[Portal ANT SVT]
        Engine -->|2. Consulta datos ciudadano| API1[svp_json_recupera_datos.jsp]
        API1 -->|Retorna nombres, idCliente, cantTramites| Engine
        
        alt cantTramites > 0 (Trámite Existente)
            Engine -->|Usa trámite existente| Reporte[svtr0010_parametros.jsp]
        else cantTramites == 0 (Nuevo Trámite)
            Engine -->|3. Genera nuevo trámite| API2[svp_json_genera_tramite.jsp]
            API2 -->|Retorna nuevo idTramite| Reporte
        end
        
        Reporte -->|4. Descarga stream binario PDF JasperReports| Engine
    end
    
    Engine -->|Retorna ruta de archivo .pdf| Handlers
    Handlers -->|send_document documento oficial PDF| User
```

### 2.2 Componentes e Interfaces

1. **`ant_orden_pago.py` (Motor Scraper / API Client):**
   - Clase principal: `ANTOrdenPagoClient`.
   - Método asíncrono principal:
     ```python
     async def obtener_orden_pago_pdf(cedula: str, id_servicio: int = 1) -> dict:
         """
         Retorna:
         {
             "success": bool,
             "cedula": str,
             "nombres": str,
             "tramite_id": str,
             "servicio_nombre": str,
             "pdf_path": str,  # Ruta al archivo PDF guardado localmente
             "error_mensaje": Optional[str]
         }
         """
     ```
   - Manejo de navegadores: Compatible tanto con Firefox (`playwright.firefox`) como con Chromium (`patchright.chromium` / `playwright.chromium`), auto-detectando el entorno (Windows de desarrollo o Linux de producción).
   - Manejo de errores específicos de ANT: Si la cédula es incorrecta (`VAL006`), si existen bloqueos o impedimentos legales, o si el portal exige actualización de correo (`emailValido == 'N'`), se consulta dinámicamente `svp_retorna_mensaje.jsp?ps_codigo=...` para entregar un mensaje claro al usuario en español.

2. **`ant_handlers.py` (Manejador de Telegram y Parser Inteligente):**
   - Funciones de detección y extracción:
     - `detectar_mensaje_ant(texto: str) -> bool`: Detecta si el texto solicita orden/comprobante de pago de ANT (con soporte para erratas tipográficas y variaciones).
     - `parsear_mensaje_ant(texto: str) -> dict`: Extrae cédula, tipo de trámite (renovación / primera vez / duplicado) y tipo de licencia (A, B, C...).
   - Flujo de interacción:
     - Si el mensaje contiene todos los datos (ej: `1728970128, orden de pago, tipo A, primera vez`), ejecuta de inmediato el scraping y responde enviando el PDF oficial sin hacer preguntas adicionales.
     - Si el usuario solo envía parte de los datos (por ejemplo solo `orden de pago 1728970128`), conserva el contexto en `user_states` y solicita amablemente el dato faltante (o botones de selección rápida).
     - Mientras procesa, envía feedback visual con `ChatAction.UPLOAD_DOCUMENT` y mensaje temporal (*"⏳ Conectando con la ANT y generando su orden de pago..."*).
     - Entrega: envía el archivo PDF adjunto con el nombre formateado `Orden_Pago_ANT_{cedula}.pdf` y elimina el mensaje de espera temporal.

3. **`telegram_bot.py` (Integración Limpia):**
   - Se importa `detectar_mensaje_ant`, `parsear_mensaje_ant`, `handle_message_ant`.
   - Se añade al bloque de enrutamiento principal de mensajes (junto a Récord Policial y Bachiller), garantizando cero interferencia con los flujos ya existentes.

---

## 3. Plan de Pruebas y Criterios de Aceptación

1. **Prueba Unitaria de Validación de Cédula:**
   - Cédulas inválidas son rechazadas inmediatamente antes de consultar la red.
2. **Prueba End-to-End en Modo Headless:**
   - Ejecución de `ant_orden_pago.py` con cédula real.
   - Verificación de que el archivo generado comience con `%PDF-1.4` y tenga un tamaño superior a 10 KB con contenido binario válido.
3. **Prueba de Compatibilidad Linux:**
   - Soporte para ejecutar en Linux con Firefox nativo o Chromium headless sin errores de dependencias.
4. **Verificación de No-Regresión:**
   - Comprobación de que `/start`, consultas de denuncias, récord policial y bachiller continúen respondiendo al 100% sin alteraciones.
