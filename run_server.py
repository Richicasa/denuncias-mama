import io
import os
import socket
import sys
import qrcode
import uvicorn

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def main():
    local_ip = get_local_ip()
    port = 8000
    url = f"http://{local_ip}:{port}"
    
    print("\n" + "="*60)
    print(" >>> SERVIDOR DE DENUNCIAS PARA TU MAMA INICIADO CON EXITO")
    print("="*60)
    print(f"\n [*] En el celular de tu mama, abre este enlace:")
    print(f"     👉 {url}")
    print(f"\n [*] En esta computadora puedes abrir:")
    print(f"     👉 http://localhost:{port}\n")
    print(" [*] Codigo QR para escanear con la camara del celular:\n")
    
    try:
        qr = qrcode.QRCode()
        qr.add_data(url)
        f = io.StringIO()
        qr.print_ascii(out=f, invert=True)
        f.seek(0)
        print(f.read())
    except Exception:
        pass
        
    print("="*60)
    print(" Presiona Ctrl + C para detener el servidor cuando termines.\n")
    
    uvicorn.run("backend:app", host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    main()
