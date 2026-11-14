# Dockerfile – API de monitoreo predictivo (Semana 3 · producción en Render)

FROM python:3.11-slim

# ── Variables de entorno ──────────────────────────────────────
# PYTHONUNBUFFERED → los logs JSON aparecen en Render en tiempo real
# PIP_NO_CACHE_DIR → no guarda caché de pip (imagen más liviana)
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Nota: a diferencia de la guía, no se instalan build-essential ni git. Todas las
# dependencias tienen wheels precompilados para Linux, así la imagen pesa menos y
# expone menos superficie de ataque.

# ── Usuario no-root (seguridad) ───────────────────────────────
RUN useradd -m -d /home/appuser -s /bin/bash appuser

WORKDIR /app

# ── Dependencias Python (capa cacheada) ───────────────────────
# Se copian antes que el código: si requirements.txt no cambia, Docker reutiliza esta capa.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Carpetas de runtime ───────────────────────────────────────
RUN mkdir -p /app/data /app/reports /app/models && chown -R appuser:appuser /app

# ── Código fuente ─────────────────────────────────────────────
COPY --chown=appuser:appuser . /app

# ── Entrenar el modelo durante el build ───────────────────────
# El .pkl queda dentro de la imagen: el contenedor arranca listo y la versión de
# scikit-learn que entrena es la misma que sirve.
USER appuser
RUN python train_model.py

# ── Puerto dinámico de Render ─────────────────────────────────
ENV PORT=10000
EXPOSE $PORT

# Chequeo de salud para Docker local (Render usa su propio Health Check Path: /health)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"10000\")}/health')" || exit 1

# ── Arranque (formato shell para resolver $PORT) ──────────────
# --proxy-headers: toma la IP real del cliente desde X-Forwarded-For (proxy de Render),
#                  necesaria para el límite de peticiones por IP.
# --no-access-log: el middleware ya escribe un log JSON por petición.
CMD uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --no-access-log
