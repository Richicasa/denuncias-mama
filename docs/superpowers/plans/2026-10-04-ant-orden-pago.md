# Orden de Pago ANT (Licencias) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a new independent tool for the Telegram bot that generates and delivers the official ANT payment order PDF (`application/pdf`) for driver licenses (Renovación, Primera Vez, Duplicado) from a single one-shot natural language message.

**Architecture:** A modular client (`ant_orden_pago.py`) launches a headless browser session to authenticate against the ANT portal, queries citizen data and creates or retrieves payment orders via internal JSON endpoints (`svp_json_recupera_datos.jsp` & `svp_json_genera_tramite.jsp`), and downloads the native vector PDF directly from JasperReports (`svtr0010_parametros.jsp`). A smart parser (`ant_parser.py` / `ant_handlers.py`) extracts cédula, service type, and license category from natural messages with typo tolerance. The Telegram bot dispatches to `ant_handlers.py` without touching existing tools.

**Tech Stack:** Python 3.12, `python-telegram-bot` 22.8, `patchright` / `playwright` (Chromium/Firefox), `asyncio`, `pytest`.

**Spec:** [`docs/superpowers/specs/2026-10-04-ant-orden-pago-design.md`](file:///C:/Users/richi/.gemini/antigravity/scratch/denuncias-app/docs/superpowers/specs/2026-10-04-ant-orden-pago-design.md)

## Global Constraints

- Existing tools (`backend.py`, `record_policial.py`, `bachiller.py`, Judicatura scraping) MUST remain 100% untouched and functional.
- Zero captcha bypass needed (ANT portal has no Cloudflare/hCaptcha/reCAPTCHA).
- Linux and Windows cross-platform compatibility (browser headless launch flags: `--no-sandbox`, `--disable-dev-shm-usage`).
- Deliver strictly the official ANT PDF file (`application/pdf`) as a Telegram document.

## Review Focus

1. **Typo tolerance in input:** Phrases like `orden d pago`, `oden de pago`, `comprovante de pago` must be properly detected as ANT payment order requests.
2. **Citizen with pre-existing order (`cantTramites > 0`):** Must NOT call `genera_tramite` to avoid duplicate billing; must directly download the existing order's PDF.
3. **Invalid Ecuadorian cédula format:** Must fail fast locally via modulo 10 verification before wasting browser network cycles.
4. **ANT business validation errors (e.g., license type mismatch, fines):** Must retrieve human-readable error messages from `svp_retorna_mensaje.jsp` instead of crashing.
5. **Slow network/timeout handling:** Must cleanly close browser contexts and inform the user with a friendly error if the ANT portal times out.

---

### Task 1: Parser Natural & Validador de Cédula (`ant_parser.py`)

**Files:**
- Create: `ant_parser.py`
- Test: `tests/test_ant_parser.py`

**Interfaces:**
- Produces:
  - `validar_cedula_ec(cedula: str) -> bool`
  - `detectar_mensaje_ant(texto: str) -> bool`
  - `parsear_mensaje_ant(texto: str) -> dict`: Returns `{"cedula": str|None, "id_servicio": int, "servicio_nombre": str, "tipo_licencia": str|None, "es_valido": bool}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ant_parser.py
from ant_parser import validar_cedula_ec, detectar_mensaje_ant, parsear_mensaje_ant

def test_validar_cedula_ec():
    assert validar_cedula_ec("1710034065") is True
    assert validar_cedula_ec("0925483927") is True
    assert validar_cedula_ec("1234567890") is False
    assert validar_cedula_ec("abc") is False

def test_detectar_mensaje_ant():
    assert detectar_mensaje_ant("1728970128, orden de pago, tipo A, primera vez") is True
    assert detectar_mensaje_ant("orden pago 0925483927 renovacion") is True
    assert detectar_mensaje_ant("comprobante de pago licencia 1710034065") is True
    assert detectar_mensaje_ant("record policial 1710034065") is False
    assert detectar_mensaje_ant("denuncia de robo en Solanda") is False

def test_parsear_mensaje_ant_completo():
    res = parsear_mensaje_ant("1710034065, orden de pago, tipo B, primera vez")
    assert res["cedula"] == "1710034065"
    assert res["id_servicio"] == 5
    assert res["servicio_nombre"] == "PRIMERA VEZ"
    assert res["tipo_licencia"] == "B"
    assert res["es_valido"] is True

def test_parsear_mensaje_ant_default_renovacion():
    res = parsear_mensaje_ant("orden de pago 0925483927 tipo A")
    assert res["cedula"] == "0925483927"
    assert res["id_servicio"] == 1
    assert res["servicio_nombre"] == "RENOVACION"
    assert res["tipo_licencia"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_parser.py`
Expected: FAIL (ModuleNotFoundError: No module named 'ant_parser')

- [ ] **Step 3: Write implementation**

Create `ant_parser.py` with:
- Modulo 10 verification algorithm in `validar_cedula_ec`.
- Regex with typo tolerance and fuzzy keywords in `detectar_mensaje_ant`.
- Extraction logic for cédula, servicio (1: RENOVACIÓN, 5: PRIMERA VEZ, 1004: DUPLICADO), and license category (A-G) in `parsear_mensaje_ant`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_parser.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ant_parser.py tests/test_ant_parser.py
git commit -m "feat: add ant_parser module with one-shot message parser and cedula validation"
```

---

### Task 2: Cliente ANT Headless y Descarga de PDF (`ant_orden_pago.py`)

**Files:**
- Create: `ant_orden_pago.py`
- Test: `tests/test_ant_orden_pago.py`

**Interfaces:**
- Consumes: `validar_cedula_ec` from `ant_parser.py`
- Produces:
  - `class ANTOrdenPagoClient`
  - `async def obtener_orden_pago_pdf(cedula: str, id_servicio: int = 1, tipo_licencia: str = "B") -> dict`: Returns `{"success": bool, "cedula": str, "nombres": str, "tramite_id": str, "pdf_path": str, "error_mensaje": str|None}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ant_orden_pago.py
import pytest
from ant_orden_pago import ANTOrdenPagoClient

@pytest.mark.asyncio
async def test_ant_invalid_cedula():
    client = ANTOrdenPagoClient()
    res = await client.obtener_orden_pago_pdf("0000000000", id_servicio=1)
    assert res["success"] is False
    assert "inválida" in res["error_mensaje"].lower() or "cedula" in res["error_mensaje"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_orden_pago.py`
Expected: FAIL (ModuleNotFoundError: No module named 'ant_orden_pago')

- [ ] **Step 3: Write implementation in `ant_orden_pago.py`**

- Initialize browser session against ANT portal `https://consultaweb.ant.gob.ec/SVT/paginas/portal/svf_solicitar_servicio.jsp?ps_param_tip_serv=LIC`.
- Query `svp_json_recupera_datos.jsp` with cédula and service ID.
- Verify citizen data, handle `emailValido == "N"` or ANT warning codes by calling `svp_retorna_mensaje.jsp`.
- If `cantTramites == 0`, call `svp_json_genera_tramite.jsp` to generate the new order number.
- Stream and save the official PDF from `/SVT/paginas/svtr0010_parametros.jsp?P_EMPRESA=01&P_CLIENTE={idCliente}&P_SERVICIO={servicio}&P_TRAMITE={tramite}`.
- Verify `%PDF-1.4` magic bytes.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_orden_pago.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ant_orden_pago.py tests/test_ant_orden_pago.py
git commit -m "feat: implement ant_orden_pago client with official PDF generation"
```

---

### Task 3: Manejador Telegram de Orden de Pago (`ant_handlers.py`)

**Files:**
- Create: `ant_handlers.py`
- Test: `tests/test_ant_handlers.py`

**Interfaces:**
- Consumes:
  - `parsear_mensaje_ant` from `ant_parser.py`
  - `ANTOrdenPagoClient` from `ant_orden_pago.py`
- Produces:
  - `async def handle_message_ant(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed_data: dict, user_states: dict)`

- [ ] **Step 1: Write test for handler workflow logic**

```python
# tests/test_ant_handlers.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from ant_handlers import handle_message_ant

@pytest.mark.asyncio
async def test_handle_message_ant_missing_cedula():
    update = MagicMock()
    update.message = AsyncMock()
    context = MagicMock()
    user_states = {}
    
    parsed = {"cedula": None, "id_servicio": 1, "servicio_nombre": "RENOVACION", "tipo_licencia": "B", "es_valido": False}
    await handle_message_ant(update, context, parsed, user_states)
    
    assert update.message.reply_text.called
    assert "cédula" in update.message.reply_text.call_args[0][0].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_handlers.py`
Expected: FAIL

- [ ] **Step 3: Implement `ant_handlers.py`**

- If cédula is missing: records state in `user_states[user_id]` and prompts user for 10-digit cédula.
- If all data is present:
  - Sends temporary status message: `"⏳ Conectando con la ANT y generando orden de pago de licencia..."`.
  - Sends chat action `ChatAction.UPLOAD_DOCUMENT`.
  - Invokes `ANTOrdenPagoClient.obtener_orden_pago_pdf()`.
  - On success: replies with `update.message.reply_document(document=pdf_file, filename=f"Orden_Pago_ANT_{cedula}.pdf", caption=caption)` and deletes the temporary status message.
  - On failure: replies with clear explanation of the ANT response.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ant_handlers.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ant_handlers.py tests/test_ant_handlers.py
git commit -m "feat: implement ant_handlers for telegram bot interaction"
```

---

### Task 4: Integración en `telegram_bot.py` y Verificación End-to-End

**Files:**
- Modify: `telegram_bot.py`
- Verify: Full test suite

**Interfaces:**
- Integrates `detectar_mensaje_ant`, `parsear_mensaje_ant`, `handle_message_ant` into `telegram_bot.py:handle_text_message`.

- [ ] **Step 1: Add import and message routing to `telegram_bot.py`**

```python
from ant_parser import detectar_mensaje_ant, parsear_mensaje_ant
from ant_handlers import handle_message_ant
```
In `handle_text_message`:
```python
    # Revisa si es flujo Orden de Pago ANT (Licencias)
    if detectar_mensaje_ant(text) or user_states.get(user_id, {}).get("flujo") == "orden_pago":
        parsed_ant = parsear_mensaje_ant(text)
        return await handle_message_ant(update, context, parsed_ant, user_states)
```

- [ ] **Step 2: Add `/orden_pago` and `/licencia` command handlers**

Allow users to also initiate via explicit commands `/orden_pago` or `/licencia`.

- [ ] **Step 3: Run comprehensive verification**

- Run `pytest tests/` to confirm all existing and new tests pass.
- Test one-shot format: `1710034065, orden de pago, tipo B, primera vez`.

- [ ] **Step 4: Commit**

```bash
git add telegram_bot.py
git commit -m "feat: integrate ANT orden de pago tool into telegram_bot"
```
