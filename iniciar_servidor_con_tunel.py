import asyncio
import os
import re
import subprocess
import sys
import time

async def main():
    print("====================================================================")
    print("      INICIANDO SERVIDOR Y TÚNEL SEGURO PARA EL CELULAR")
    print("====================================================================")
    print("Iniciando motor de denuncias local...")
    
    # 1. Iniciar uvicorn local en background
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Esperar 2 segundos a que el servidor esté activo
    time.sleep(2)
    
    # 2. Iniciar cloudflared tunnel
    cloudflared_path = os.path.join(os.path.dirname(__file__), "cloudflared.exe")
    tunnel_process = subprocess.Popen(
        [cloudflared_path, "tunnel", "--url", "http://localhost:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )
    
    public_url = ""
    print("Generando enlace seguro para cualquier red (WiFi o Datos 4G/5G)...")
    for line in iter(tunnel_process.stdout.readline, ''):
        if "trycloudflare.com" in line:
            match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
            if match:
                public_url = match.group(0)
                break
                
    if public_url:
        print("\n" + "=" * 68)
        print("  🎉 ¡ENLACE PÚBLICO LISTO PARA EL CELULAR DE TU MAMÁ!")
        print("=" * 68)
        print(f"\n  Tu mamá puede ingresar desde CUALQUIER LUGAR (Datos o WiFi) a:\n")
        print(f"       👉  {public_url}\n")
        print("  ⚡ Tu computadora procesará las denuncias en 13s de forma local.")
        print("  📥 El PDF original se descargará directo en su celular.")
        print("=" * 68)
        print("\n(Deja esta ventana abierta mientras tu mamá use la aplicación).")
        print("Presiona Ctrl+C para detener el servicio cuando termines.\n")
    else:
        print("No se pudo detectar el enlace público. Usa http://192.168.1.40:8000 en la misma red WiFi.")
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando servidor y túnel...")
        server_process.terminate()
        tunnel_process.terminate()

if __name__ == "__main__":
    asyncio.run(main())
