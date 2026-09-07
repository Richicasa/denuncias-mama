"""
auto_runner.py - Supervisor automático para Linux Mint
Mantiene el bot corriendo, vigila GitHub cada 30 segundos,
y si hay una actualización, descarga los cambios y reinicia el bot automáticamente.
"""
import subprocess
import time
import sys
import os

CHECK_INTERVAL = 30  # segundos entre revisiones de GitHub

def get_hash(ref):
    try:
        res = subprocess.run(["git", "rev-parse", ref], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return None

def fetch_origin():
    try:
        subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=True)
        return True
    except Exception:
        return False

def pull_changes():
    try:
        res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, check=True)
        print("[AUTO-RUNNER] Cambios descargados:\n", res.stdout.strip())
        return True
    except Exception as e:
        print("[AUTO-RUNNER] Error al hacer git pull:", e)
        return False

def start_bot():
    print("\n[AUTO-RUNNER] 🚀 Iniciando bot de Telegram (telegram_bot.py)...")
    # Ejecuta telegram_bot.py con el mismo intérprete de python actual
    return subprocess.Popen([sys.executable, "telegram_bot.py"])

def main():
    print("====================================================================")
    print("      SUPERVISOR DE ACTUALIZACIONES AUTOMÁTICAS INICIADO")
    print(f"      Vigilando repositorio cada {CHECK_INTERVAL} segundos...")
    print("====================================================================")

    # Actualizar al arrancar por si acaso
    fetch_origin()
    pull_changes()

    bot_process = start_bot()

    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            # 1. Verificar si el proceso del bot se cayó por algún motivo inesperado
            if bot_process.poll() is not None:
                print(f"\n[AUTO-RUNNER] ⚠️ El bot se detuvo con código {bot_process.returncode}. Reiniciando en 3s...")
                time.sleep(3)
                fetch_origin()
                pull_changes()
                bot_process = start_bot()
                continue

            # 2. Consultar si hay nuevos commits en GitHub
            if fetch_origin():
                local_hash = get_hash("HEAD")
                remote_hash = get_hash("origin/main")

                if local_hash and remote_hash and local_hash != remote_hash:
                    print(f"\n[AUTO-RUNNER] 🔔 ¡Nueva versión detectada en GitHub!")
                    print(f"[AUTO-RUNNER] Actual: {local_hash[:7]} -> Nueva: {remote_hash[:7]}")
                    print("[AUTO-RUNNER] Deteniendo bot actual para actualizar...")
                    
                    # Terminar proceso actual
                    bot_process.terminate()
                    try:
                        bot_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        bot_process.kill()

                    # Descargar cambios
                    pull_changes()

                    # Reanudar bot con el código nuevo
                    bot_process = start_bot()
                    print("[AUTO-RUNNER] ✅ Bot actualizado y funcionando con la última versión.\n")

    except KeyboardInterrupt:
        print("\n[AUTO-RUNNER] Deteniendo supervisor y bot...")
        if bot_process and bot_process.poll() is None:
            bot_process.terminate()
            try:
                bot_process.wait(timeout=3)
            except Exception:
                bot_process.kill()
        print("[AUTO-RUNNER] Cerrado correctamente.")

if __name__ == "__main__":
    main()