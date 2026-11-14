# -*- coding: utf-8 -*-
"""
Seguridad básica del servicio.

1. API key (cabecera X-API-Key) para los endpoints de integración que reciben datos
   reales o devuelven el historial. La clave vive en una variable de entorno de Render,
   nunca en el repositorio, y se compara en tiempo constante.
2. Límite de peticiones por IP (ventana deslizante de 60 s) en los endpoints de cómputo,
   para proteger una instancia de 0,1 CPU frente a abusos (HTTP 429 + Retry-After).
3. Cabeceras de seguridad HTTP (nosniff, frame, referrer, HSTS y CSP en el panel).

La validación de entradas la hace Pydantic/FastAPI (HTTP 422) y el cifrado TLS lo
termina Render (HTTPS automático).
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from . import config

CABECERA_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False,
                                description="Clave configurada en la variable de entorno API_KEY")


def verificar_api_key(clave: str | None = Security(CABECERA_API_KEY)) -> None:
    """Dependencia de FastAPI: exige la cabecera X-API-Key correcta."""
    if not config.API_KEY:
        raise HTTPException(status_code=503,
                            detail="Endpoint protegido deshabilitado: el servidor no tiene API_KEY configurada.")
    if not clave or not secrets.compare_digest(clave.encode(), config.API_KEY.encode()):
        raise HTTPException(status_code=401, detail="API key ausente o inválida (cabecera X-API-Key).",
                            headers={"WWW-Authenticate": "API-Key"})


class LimitadorTasa:
    """Ventana deslizante en memoria: máximo `limite` peticiones por IP cada `ventana_s` segundos."""

    def __init__(self, limite: int, ventana_s: float = 60.0):
        self.limite, self.ventana_s = limite, ventana_s
        self._marcas: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> tuple[bool, int, int]:
        """Devuelve (permitido, restantes, segundos_para_reintentar)."""
        ahora = time.monotonic()
        with self._lock:
            q = self._marcas[clave]
            while q and ahora - q[0] > self.ventana_s:
                q.popleft()
            if len(q) >= self.limite:
                return False, 0, max(1, int(self.ventana_s - (ahora - q[0])) + 1)
            q.append(ahora)
            return True, self.limite - len(q), 0


LIMITADOR = LimitadorTasa(config.RATE_LIMIT_POR_MIN)
PREFIJOS_LIMITADOS = ("/predict", "/monitor", "/trayectoria")

CSP_PANEL = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data:; connect-src 'self' https://api.github.com; frame-src 'self'; "
             "frame-ancestors 'self'; base-uri 'self'; form-action 'self'")


def cabeceras_seguridad(ruta: str, https: bool) -> dict[str, str]:
    h = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if https:
        h["Strict-Transport-Security"] = "max-age=31536000"
    if ruta in ("/", "/ui"):
        h["Content-Security-Policy"] = CSP_PANEL
    return h
