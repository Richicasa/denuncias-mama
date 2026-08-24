# Dockerfile para despliegue gratuito en Render / Railway / Koyeb
FROM python:3.12-slim

# Instalar dependencias del sistema requeridas por Playwright y Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requerimientos e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copiar el código de la aplicación
COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
