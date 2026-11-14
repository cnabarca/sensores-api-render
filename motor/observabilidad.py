# -*- coding: utf-8 -*-
"""
Observabilidad del servicio: logging estructurado, métricas y datos del despliegue.

- Cada petición genera una línea de log en JSON (visible en la pestaña Logs de Render).
- Se mantienen contadores y latencias en memoria y se exponen en dos formatos:
    * /metrics      -> texto Prometheus (lo que leería Prometheus/Grafana en producción)
    * /ops/metricas -> JSON resumido para el panel web
- Las direcciones IP se anonimizan (último octeto) antes de registrarse.

Todo vive en memoria del proceso: se reinicia con cada despliegue o arranque en frío.
En producción se enviaría a un backend persistente (Prometheus, Grafana Cloud, CloudWatch).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

T_INICIO_PROCESO = time.time()
BUCKETS_S = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


# ---------------------------------------------------------------------------
# Logging estructurado (JSON por línea)
# ---------------------------------------------------------------------------
class _FormatoJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        datos = getattr(record, "datos", None) or {"mensaje": record.getMessage()}
        return json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                           "nivel": record.levelname, **datos}, ensure_ascii=False)


def _crear_logger() -> logging.Logger:
    lg = logging.getLogger("motor.api")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_FormatoJSON())
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg


LOGGER = _crear_logger()


def anonimizar_ip(ip: str | None) -> str:
    if not ip:
        return "-"
    if "." in ip:
        partes = ip.split(".")
        return ".".join(partes[:3] + ["x"]) if len(partes) == 4 else ip
    return ip.rsplit(":", 2)[0] + ":x" if ":" in ip else ip


# ---------------------------------------------------------------------------
# Almacén de métricas
# ---------------------------------------------------------------------------
class Metricas:
    def __init__(self, ventana: int = 2000, max_logs: int = 300):
        self._lock = threading.Lock()
        self.peticiones = Counter()                     # (metodo, ruta, status) -> n
        self.hist = defaultdict(lambda: [0] * (len(BUCKETS_S) + 1))  # ruta -> conteo por bucket
        self.suma_s = defaultdict(float)                # ruta -> suma de latencias
        self.recientes: deque = deque(maxlen=ventana)   # (t, ruta, status, ms)
        self.logs: deque = deque(maxlen=max_logs)
        self.predicciones = Counter()                   # sin_falla / falla
        self.monitoreos = Counter()                     # nivel de alerta -> n
        self.rechazos = Counter()                       # 401 / 422 / 429
        self.t_listo: float | None = None
        self.t_primera_peticion: float | None = None

    # -- registro ----------------------------------------------------------
    def registrar_peticion(self, metodo: str, ruta: str, status: int, seg: float) -> None:
        with self._lock:
            ahora = time.time()
            if self.t_primera_peticion is None:
                self.t_primera_peticion = ahora
            self.peticiones[(metodo, ruta, status)] += 1
            idx = next((i for i, b in enumerate(BUCKETS_S) if seg <= b), len(BUCKETS_S))
            self.hist[ruta][idx] += 1
            self.suma_s[ruta] += seg
            self.recientes.append((ahora, ruta, status, seg * 1000))
            if status in (401, 403, 422, 429):
                self.rechazos[status] += 1

    def log(self, datos: dict, nivel: int = logging.INFO) -> None:
        LOGGER.log(nivel, "", extra={"datos": datos})
        with self._lock:
            self.logs.append({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                              "nivel": logging.getLevelName(nivel), **datos})

    def contar_prediccion(self, falla: bool, n: int = 1) -> None:
        with self._lock:
            self.predicciones["falla" if falla else "sin_falla"] += n

    def contar_monitoreo(self, nivel: str) -> None:
        with self._lock:
            self.monitoreos[nivel] += 1

    # -- lectura -----------------------------------------------------------
    def ultimos_logs(self, n: int = 50) -> list[dict]:
        with self._lock:
            return list(self.logs)[-n:][::-1]

    def resumen(self) -> dict:
        """Resumen para el panel: totales, latencias, errores, serie temporal y por endpoint."""
        with self._lock:
            recientes = list(self.recientes)
            peticiones = dict(self.peticiones)
            pred, mon, rech = dict(self.predicciones), dict(self.monitoreos), dict(self.rechazos)

        total = sum(peticiones.values())
        errores_5xx = sum(n for (_, _, s), n in peticiones.items() if s >= 500)
        ms = np.array([r[3] for r in recientes]) if recientes else np.array([0.0])

        por_ruta: dict[str, dict] = {}
        for (metodo, ruta, status), n in peticiones.items():
            d = por_ruta.setdefault(f"{metodo} {ruta}", {"endpoint": f"{metodo} {ruta}", "n": 0, "errores": 0})
            d["n"] += n
            d["errores"] += n if status >= 400 else 0
        lat_ruta = defaultdict(list)
        for _, ruta, _, m in recientes:
            lat_ruta[ruta].append(m)
        for d in por_ruta.values():
            vals = lat_ruta.get(d["endpoint"].split(" ", 1)[1], [])
            d["p50_ms"] = round(float(np.percentile(vals, 50)), 1) if vals else None
            d["p95_ms"] = round(float(np.percentile(vals, 95)), 1) if vals else None

        # Serie de los últimos 5 minutos en ventanas de 10 s
        ahora = time.time()
        serie = []
        for k in range(30, 0, -1):
            ini, fin = ahora - k * 10, ahora - (k - 1) * 10
            vals = [r[3] for r in recientes if ini <= r[0] < fin]
            serie.append({"t": round(fin - ahora), "n": len(vals),
                          "p95_ms": round(float(np.percentile(vals, 95)), 1) if vals else None})

        return {
            "uptime_s": round(ahora - T_INICIO_PROCESO, 1),
            "arranque_s": round(self.t_listo - T_INICIO_PROCESO, 2) if self.t_listo else None,
            "peticiones_total": total,
            "errores_5xx": errores_5xx,
            "tasa_error_5xx": round(errores_5xx / total, 4) if total else 0.0,
            "rechazos_seguridad": rech,
            "latencia_ms": {"p50": round(float(np.percentile(ms, 50)), 1),
                            "p95": round(float(np.percentile(ms, 95)), 1),
                            "p99": round(float(np.percentile(ms, 99)), 1),
                            "muestras": len(recientes)},
            "predicciones": pred,
            "monitoreos_por_nivel": mon,
            "memoria_mb": memoria_mb(),
            "serie": serie,
            "por_endpoint": sorted(por_ruta.values(), key=lambda d: -d["n"]),
        }

    def prometheus(self) -> str:
        """Exposición en formato de texto de Prometheus 0.0.4."""
        with self._lock:
            peticiones = dict(self.peticiones)
            hist = {k: list(v) for k, v in self.hist.items()}
            sumas = dict(self.suma_s)
            pred, mon = dict(self.predicciones), dict(self.monitoreos)
        out = ["# HELP http_requests_total Peticiones HTTP atendidas.",
               "# TYPE http_requests_total counter"]
        for (m, r, s), n in sorted(peticiones.items()):
            out.append(f'http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}')
        out += ["# HELP http_request_duration_seconds Latencia de las peticiones.",
                "# TYPE http_request_duration_seconds histogram"]
        for ruta, cuentas in sorted(hist.items()):
            acum = 0
            for b, c in zip(list(BUCKETS_S) + ["+Inf"], cuentas):
                acum += c
                out.append(f'http_request_duration_seconds_bucket{{route="{ruta}",le="{b}"}} {acum}')
            out.append(f'http_request_duration_seconds_sum{{route="{ruta}"}} {sumas.get(ruta, 0):.6f}')
            out.append(f'http_request_duration_seconds_count{{route="{ruta}"}} {acum}')
        out += ["# HELP modelo_predicciones_total Lecturas evaluadas por el modelo.",
                "# TYPE modelo_predicciones_total counter"]
        out += [f'modelo_predicciones_total{{resultado="{k}"}} {v}' for k, v in sorted(pred.items())]
        out += ["# HELP monitoreo_lotes_total Lotes evaluados por nivel de alerta.",
                "# TYPE monitoreo_lotes_total counter"]
        out += [f'monitoreo_lotes_total{{nivel="{k}"}} {v}' for k, v in sorted(mon.items())]
        out += ["# HELP proceso_uptime_seconds Segundos desde el arranque del proceso.",
                "# TYPE proceso_uptime_seconds gauge",
                f"proceso_uptime_seconds {time.time() - T_INICIO_PROCESO:.1f}"]
        mem = memoria_mb()
        if mem is not None:
            out += ["# HELP proceso_memoria_rss_bytes Memoria residente del proceso.",
                    "# TYPE proceso_memoria_rss_bytes gauge",
                    f"proceso_memoria_rss_bytes {int(mem * 1024 * 1024)}"]
        return "\n".join(out) + "\n"


METRICAS = Metricas()


# ---------------------------------------------------------------------------
# Información del proceso y del despliegue
# ---------------------------------------------------------------------------
def memoria_mb() -> float | None:
    """Memoria residente (RSS) en MB; solo Linux (/proc), que es donde corre el contenedor."""
    try:
        for linea in Path("/proc/self/status").read_text().splitlines():
            if linea.startswith("VmRSS:"):
                return round(int(linea.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def entorno_ejecucion() -> str:
    if os.getenv("RENDER"):
        return "Render (nube)"
    if Path("/.dockerenv").exists():
        return "Docker (local)"
    return "Local (desarrollo)"


def usuario_no_root() -> bool | None:
    try:
        return os.geteuid() != 0
    except AttributeError:      # Windows
        return None


def info_despliegue(version: str, repo_defecto: str) -> dict:
    """Metadatos del despliegue. Render inyecta las variables RENDER_* en tiempo de ejecución."""
    commit = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or ""
    return {
        "version": version,
        "entorno": entorno_ejecucion(),
        "servicio": os.getenv("RENDER_SERVICE_NAME", "local"),
        "url_publica": os.getenv("RENDER_EXTERNAL_URL", ""),
        "instancia": os.getenv("RENDER_INSTANCE_ID", platform.node()),
        "commit": commit,
        "commit_corto": commit[:7],
        "rama": os.getenv("RENDER_GIT_BRANCH", ""),
        "repositorio": os.getenv("RENDER_GIT_REPO_SLUG") or repo_defecto,
        "python": platform.python_version(),
        "sistema": f"{platform.system()} {platform.machine()}",
        "puerto": os.getenv("PORT", ""),
        "usuario_no_root": usuario_no_root(),
        "inicio_proceso_utc": datetime.fromtimestamp(T_INICIO_PROCESO, timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": round(time.time() - T_INICIO_PROCESO, 1),
        "memoria_mb": memoria_mb(),
        "limite_memoria_mb": 512 if os.getenv("RENDER") else None,
    }
