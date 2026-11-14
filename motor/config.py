# -*- coding: utf-8 -*-
"""
Configuración central del proyecto (única fuente de verdad).

Contiene:
- Versión del servicio y parámetros de despliegue (leídos de variables de entorno).
- Rutas de artefactos (datos, modelo, reportes).
- Ficha técnica del equipo monitoreado.
- Umbrales normativos por variable (zonas A-D).
- Perfiles de simulación por estado operativo.
- Escenarios de monitoreo reutilizados por la API y el panel web.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Servicio y despliegue
# ---------------------------------------------------------------------------
APP_VERSION = "3.0.0"

# Secretos y ajustes de operación: se configuran en Render (Environment), nunca en el repositorio.
API_KEY = os.getenv("API_KEY", "")                       # vacío -> endpoints protegidos deshabilitados
RATE_LIMIT_POR_MIN = int(os.getenv("RATE_LIMIT_POR_MIN", "600"))   # 10 req/s sostenidas por IP
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
MAX_LOTE = int(os.getenv("MAX_LOTE", "5000"))            # filas máximas por petición de lote
MAX_CSV_BYTES = 1_000_000                                 # 1 MB por archivo CSV
GITHUB_REPO = os.getenv("GITHUB_REPO", "cnabarca/sensores-api-render")  # Render usa RENDER_GIT_REPO_SLUG

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
STATIC_DIR = ROOT / "static"

DATA_PATH = DATA_DIR / "datos_sensor.csv"
REF_PATH = DATA_DIR / "referencia_monitoreo.csv"
MODEL_PATH = MODELS_DIR / "modelo_sensores.pkl"
REPORT_HTML = REPORTS_DIR / "reporte_drift.html"

SEED = 42
FEATURES = ["temperatura", "vibracion", "corriente"]
TARGET = "falla"

# ---------------------------------------------------------------------------
# Equipo monitoreado
# ---------------------------------------------------------------------------
EQUIPO = {
    "descripcion": "Motor de inducción trifásico acoplado a bomba centrífuga",
    "potencia_kw": 30.0,
    "tension_v": 440.0,
    "frecuencia_hz": 60.0,
    "factor_potencia": 0.85,
    "eficiencia": 0.92,
    "clasificacion_iso": "Grupo 2 (15–300 kW), montaje sobre base rígida",
    "norma_vibracion": "ISO 10816-3 (actual ISO 20816-3)",
    "aplicacion": "Estación de bombeo de agua / planta procesadora (p. ej., atuneras de Manta)",
}


def corriente_nominal(p_kw: float = EQUIPO["potencia_kw"],
                      v: float = EQUIPO["tension_v"],
                      fp: float = EQUIPO["factor_potencia"],
                      eta: float = EQUIPO["eficiencia"]) -> float:
    """I = P / (√3 · V · fp · η)  — corriente de línea a plena carga [A]."""
    return (p_kw * 1000.0) / (math.sqrt(3) * v * fp * eta)


I_NOMINAL = corriente_nominal()          # ≈ 50.3 A

# ---------------------------------------------------------------------------
# Umbrales normativos / de referencia (límite superior de las zonas A, B, C).
# Un valor por encima del tercer límite corresponde a la zona D.
# ---------------------------------------------------------------------------
UMBRALES = {
    "vibracion": {
        "unidad": "mm/s RMS",
        "limites": (1.4, 2.8, 4.5),
        "fuente": "ISO 10816-3, Grupo 2, base rígida",
    },
    "temperatura": {
        "unidad": "°C",
        "limites": (70.0, 80.0, 90.0),
        "fuente": "Práctica de mantenimiento en rodamientos (validar con fabricante)",
    },
    "corriente": {
        "unidad": "A",
        "limites": (46.0, 50.0, 53.0),
        "fuente": f"Corriente nominal ≈ {I_NOMINAL:.1f} A; 105 % In ≈ 53 A",
    },
}

ZONAS = {
    "A": {"nombre": "Nuevo / recién puesto en servicio", "accion": "Ninguna", "color": "#2e7d32"},
    "B": {"nombre": "Operación normal", "accion": "Operación sin restricción", "color": "#9ccc65"},
    "C": {"nombre": "Degradación", "accion": "Planificar mantenimiento", "color": "#ffb300"},
    "D": {"nombre": "Falla", "accion": "Detener el equipo", "color": "#e53935"},
}

# ---------------------------------------------------------------------------
# Perfiles de simulación (medias y desviaciones en el orden de FEATURES)
#   temperatura [°C], vibración [mm/s RMS], corriente [A]
# Se distinguen dos modos de falla para que el modelo y el diagnóstico
# necesiten las tres variables:
#   - mecánico: rodamiento dañado / desbalance  -> sube vibración y temperatura
#   - eléctrico: sobrecarga / obstrucción de la bomba -> sube corriente y temperatura
# ---------------------------------------------------------------------------
PERFILES = {
    "A": {"n": 350, "media": (60.0, 1.00, 43.0), "std": (4.0, 0.20, 1.5),
          "estado": "A", "modo_falla": "ninguno", "falla": 0},
    "B": {"n": 550, "media": (72.0, 2.00, 47.0), "std": (3.5, 0.35, 1.5),
          "estado": "B", "modo_falla": "ninguno", "falla": 0},
    "C": {"n": 220, "media": (83.0, 3.50, 51.0), "std": (3.5, 0.50, 1.5),
          "estado": "C", "modo_falla": "degradacion", "falla": 0},
    "D_mecanica": {"n": 110, "media": (92.0, 5.60, 52.5), "std": (4.0, 0.90, 2.0),
                   "estado": "D", "modo_falla": "mecanico", "falla": 1},
    "D_electrica": {"n": 70, "media": (91.0, 2.60, 56.5), "std": (4.0, 0.50, 2.0),
                    "estado": "D", "modo_falla": "electrico", "falla": 1},
}

# Correlación física entre sensores (temp, vib, corr):
# el roce/desgaste genera calor (temp~vib) y la sobrecarga también (temp~corr).
CORRELACION = (
    (1.00, 0.50, 0.40),
    (0.50, 1.00, 0.20),
    (0.40, 0.20, 1.00),
)

# ---------------------------------------------------------------------------
# Costos supuestos para la selección del umbral de decisión (USD)
# ---------------------------------------------------------------------------
COSTO_FN = 8000.0   # falla no detectada: parada no planificada + reparación + producción perdida
COSTO_FP = 300.0    # falsa alarma: inspección técnica innecesaria

# ---------------------------------------------------------------------------
# Escenarios de monitoreo (batch actual simulado)
# ---------------------------------------------------------------------------
ESCENARIOS = {
    "sin_drift": {
        "titulo": "Operación normal (sin drift)",
        "descripcion": "Mismo régimen que la línea base (zona B).",
        "params": {"temp_mean": 72, "temp_std": 3.5, "vib_mean": 2.0, "vib_std": 0.35,
                   "corr_mean": 47, "corr_std": 1.5},
        "falla_real": 0,
    },
    "estacional": {
        "titulo": "Cambio ambiental (verano / sala sin ventilación)",
        "descripcion": "Sube solo la temperatura ~6 °C; el equipo está sano.",
        "params": {"temp_mean": 78, "temp_std": 3.5, "vib_mean": 2.0, "vib_std": 0.35,
                   "corr_mean": 47, "corr_std": 1.5},
        "falla_real": 0,
    },
    "degradacion": {
        "titulo": "Degradación mecánica incipiente (zona C)",
        "descripcion": "Desgaste de rodamiento: sube vibración y, con retardo, temperatura.",
        "params": {"temp_mean": 81, "temp_std": 3.5, "vib_mean": 3.5, "vib_std": 0.5,
                   "corr_mean": 49, "corr_std": 1.5},
        "falla_real": 0,
    },
    "sobrecarga": {
        "titulo": "Sobrecarga eléctrica / obstrucción de la bomba",
        "descripcion": "Sube la corriente y la temperatura; la vibración se mantiene.",
        "params": {"temp_mean": 88, "temp_std": 4.0, "vib_mean": 2.4, "vib_std": 0.45,
                   "corr_mean": 55, "corr_std": 2.0},
        "falla_real": 1,
    },
    "falla": {
        "titulo": "Falla mecánica avanzada (zona D)",
        "descripcion": "Rodamiento dañado: las tres variables fuera de rango.",
        "params": {"temp_mean": 92, "temp_std": 4.0, "vib_mean": 5.6, "vib_std": 0.9,
                   "corr_mean": 54, "corr_std": 2.0},
        "falla_real": 1,
    },
}

# Trayectoria run-to-failure (simulación de mantenimiento y panel web)
TRAYECTORIA = {"dias": 60, "lecturas_por_dia": 144, "inicio_degradacion": 20, "dia_falla": 56, "persistencia": 3}

NIVELES_ALERTA = {
    0: {"nivel": "NORMAL", "accion": "Operación sin restricción", "color": "#2e7d32"},
    1: {"nivel": "VIGILANCIA", "accion": "Drift estadístico: aumentar frecuencia de inspección",
        "color": "#9ccc65"},
    2: {"nivel": "PREALERTA", "accion": "Media fuera de límites k·σ: diagnóstico y orden de trabajo",
        "color": "#fdd835"},
    3: {"nivel": "PLANIFICAR", "accion": "Zona C (norma): planificar mantenimiento",
        "color": "#ffb300"},
    4: {"nivel": "DETENER", "accion": "Zona D (norma): detener el equipo", "color": "#e53935"},
}
