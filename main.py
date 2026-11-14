# -*- coding: utf-8 -*-
"""
Semana 3 — API de monitoreo predictivo en la nube (FastAPI + Docker + Render).

Caso: motor de inducción de 30 kW + bomba centrífuga (modelo de la Semana 2),
con tres sensores: temperatura del rodamiento, vibración y corriente.

Ejecutar en local:
    python train_model.py
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Panel web: /  (también /ui)      Swagger: /docs      Métricas: /metrics
"""
from __future__ import annotations

import io
import threading
import time
import unicodedata
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi import Path as ParamRuta
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from motor import config
from motor.config import (APP_VERSION, DATA_PATH, EQUIPO, ESCENARIOS, FEATURES, I_NOMINAL, MODEL_PATH,
                          NIVELES_ALERTA, REF_PATH, REPORT_HTML, STATIC_DIR, TRAYECTORIA, UMBRALES, ZONAS)
from motor.inferencia import Predictor
from motor.monitoreo import drift_evidently, evaluar_batch, evaluar_lectura
from motor.observabilidad import METRICAS, T_INICIO_PROCESO, anonimizar_ip, info_despliegue, usuario_no_root
from motor.seguridad import LIMITADOR, PREFIJOS_LIMITADOS, cabeceras_seguridad, verificar_api_key
from motor.simulacion import generar_batch, simular_trayectoria

ESTADO: dict = {}
HISTORIAL: deque = deque(maxlen=500)          # últimas predicciones (memoria del proceso)
_LOCK_REPORTE = threading.Lock()              # un solo reporte de Evidently se escribe a la vez
RUTAS_SIN_TRAZA = ("/static", "/ops", "/metrics", "/favicon")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not MODEL_PATH.exists() or not REF_PATH.exists():
        raise RuntimeError("Faltan artefactos del modelo. Ejecuta: python train_model.py")
    ESTADO["bundle"] = joblib.load(MODEL_PATH)
    ESTADO["ref"] = pd.read_csv(REF_PATH)
    # Inferencia optimizada para 0,1 CPU, verificada contra scikit-learn con todo el dataset
    ESTADO["bundle"]["predictor"] = Predictor(ESTADO["bundle"], pd.read_csv(DATA_PATH)[FEATURES])
    ESTADO["tray"] = simular_trayectoria(
        dias=TRAYECTORIA["dias"], lecturas_por_dia=TRAYECTORIA["lecturas_por_dia"],
        inicio_degradacion=TRAYECTORIA["inicio_degradacion"], dia_falla=TRAYECTORIA["dia_falla"])
    ESTADO["tray_cache"] = {}
    METRICAS.t_listo = time.time()
    METRICAS.log({"evento": "arranque", "version": APP_VERSION, "modelo": ESTADO["bundle"]["nombre_modelo"],
                  "inferencia": ESTADO["bundle"]["predictor"].descripcion,
                  "arranque_s": round(METRICAS.t_listo - T_INICIO_PROCESO, 2),
                  "api_key_configurada": bool(config.API_KEY)})
    yield
    METRICAS.log({"evento": "apagado"})
    ESTADO.clear()


DESCRIPCION = """
API REST de **monitoreo predictivo** de un motor de inducción de 30 kW acoplado a una bomba centrífuga
(ISO 10816-3). Combina un **modelo de ML** (probabilidad de falla con umbral de mínimo costo), **drift
estadístico** (Evidently / K-S), **límites k·σ** y **zonas normativas A–D**.

**Seguridad:** HTTPS (Render) · validación de entradas (422) · límite de peticiones por IP (429) ·
API key en la cabecera `X-API-Key` para los endpoints de **integración** · cabeceras de seguridad.

**Observabilidad:** logs JSON por petición (cabecera `X-Request-ID`) · `/metrics` (Prometheus) ·
`/ops/metricas` (JSON) · `/version` (commit desplegado).
"""

app = FastAPI(
    title="API Monitoreo Predictivo — Motor 30 kW (Semana 3 · Cloud)",
    description=DESCRIPCION,
    version=APP_VERSION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "info", "description": "Estado, versión, equipo y modelo."},
        {"name": "predicción", "description": "Predicción individual (compatible con la guía: query params)."},
        {"name": "monitoreo", "description": "Drift, límites k·σ, zonas y simulación hasta la falla."},
        {"name": "integración", "description": "Lotes de lecturas reales (CSV / JSON). **Requieren X-API-Key.**"},
        {"name": "operación", "description": "Observabilidad del servicio: métricas, logs y postura de seguridad."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,                 # no se usan cookies: la autenticación va en cabecera
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-ms", "X-RateLimit-Remaining", "Retry-After"],
)


@app.middleware("http")
async def middleware_operacion(request: Request, call_next):
    """Request-ID, límite de tasa, latencia, cabeceras de seguridad, log JSON y métricas."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    ruta_real = request.url.path
    ip = request.client.host if request.client else None
    t0 = time.perf_counter()

    restantes = None
    if request.method == "POST" or ruta_real.startswith("/trayectoria"):
        if ruta_real.startswith(PREFIJOS_LIMITADOS):
            ok, restantes, reintento = LIMITADOR.permitir(ip or "-")
            if not ok:
                resp = JSONResponse(status_code=429, headers={"Retry-After": str(reintento)}, content={
                    "detail": f"Límite de {config.RATE_LIMIT_POR_MIN} peticiones/min por IP superado. "
                              f"Reintente en {reintento} s."})
                _cerrar(request, resp, rid, ruta_real, ip, t0)
                return resp

    try:
        resp = await call_next(request)
    except Exception as e:                       # error no controlado -> 500 con traza en el log
        METRICAS.log({"evento": "error", "request_id": rid, "ruta": ruta_real, "error": repr(e)}, 40)
        resp = JSONResponse(status_code=500, content={"detail": "Error interno", "request_id": rid})
    if restantes is not None:
        resp.headers["X-RateLimit-Remaining"] = str(restantes)
    _cerrar(request, resp, rid, ruta_real, ip, t0)
    return resp


def _cerrar(request: Request, resp, rid: str, ruta_real: str, ip: str | None, t0: float) -> None:
    seg = time.perf_counter() - t0
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.headers.update(cabeceras_seguridad(ruta_real, https))
    resp.headers["X-Request-ID"] = rid
    resp.headers["X-Response-Time-ms"] = f"{seg * 1000:.1f}"
    if ruta_real.startswith(RUTAS_SIN_TRAZA):
        return
    route = request.scope.get("route")
    plantilla = getattr(route, "path", None) or "(no encontrada)"
    METRICAS.registrar_peticion(request.method, plantilla, resp.status_code, seg)
    METRICAS.log({"evento": "http", "request_id": rid, "metodo": request.method, "ruta": ruta_real,
                  "status": resp.status_code, "ms": round(seg * 1000, 1), "ip": anonimizar_ip(ip)},
                 30 if resp.status_code >= 400 else 20)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class EntradaSensor(BaseModel):
    temperatura: float = Field(..., ge=-20, le=200, description="Temperatura del rodamiento (°C)")
    vibracion: float = Field(..., ge=0, le=50, description="Vibración global (mm/s RMS)")
    corriente: float = Field(..., ge=0, le=200, description="Corriente del motor (A)")

    model_config = {"json_schema_extra": {"examples": [
        {"temperatura": 73.0, "vibracion": 2.1, "corriente": 47.0}]}}


class LoteLecturas(BaseModel):
    """Lecturas reales (p. ej., último turno exportado del SCADA) para monitorear sin simulación."""
    lecturas: list[EntradaSensor] = Field(..., min_length=30, max_length=config.MAX_LOTE)
    k_sigma: float = Field(2.0, ge=0.5, le=4.0)


class LotePrediccion(BaseModel):
    lecturas: list[EntradaSensor] = Field(..., min_length=1, max_length=config.MAX_LOTE)


# ---------------------------------------------------------------------------
# Lógica de predicción
# ---------------------------------------------------------------------------
def _zonas_vectorizadas(df: pd.DataFrame) -> pd.DataFrame:
    salida = {}
    for v in FEATURES:
        a, b, c = UMBRALES[v]["limites"]
        x = df[v].to_numpy()
        salida[v] = np.select([x < a, x < b, x <= c], ["A", "B", "C"], default="D")
    z = pd.DataFrame(salida, index=df.index)
    z["zona_global"] = z[FEATURES].max(axis=1)        # A < B < C < D
    return z


def _recomendacion(prediccion: int, zona_global: str) -> str:
    if prediccion and zona_global == "D":
        return "Detener el equipo: el modelo y la norma coinciden en una condición de falla."
    if prediccion:
        return "El modelo detecta un patrón de falla aunque ninguna variable está en zona D: inspección inmediata."
    if zona_global == "D":
        return "Una variable está en zona D: detener y verificar (falla incipiente o posible error del sensor)."
    if zona_global == "C":
        return "Todavía no hay falla, pero sí degradación: planificar mantenimiento en la próxima ventana."
    return "Operación normal: sin acciones requeridas."


def _predecir_df(df: pd.DataFrame, origen: str) -> pd.DataFrame:
    bundle = ESTADO["bundle"]
    proba = bundle["predictor"].proba_falla(df)
    res = df[FEATURES].copy()
    res["probabilidad_falla"] = np.round(proba, 4)
    res["prediccion"] = (proba >= bundle["umbral"]).astype(int)
    z = _zonas_vectorizadas(df)
    for v in FEATURES:
        res[f"zona_{v}"] = z[v]
    res["zona_global"] = z["zona_global"]
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for fila in res.tail(HISTORIAL.maxlen).to_dict(orient="records"):
        HISTORIAL.append({"ts": ts, "origen": origen, **fila})
    METRICAS.contar_prediccion(True, int(res["prediccion"].sum()))
    METRICAS.contar_prediccion(False, int(len(res) - res["prediccion"].sum()))
    return res


def _resumen_lote(res: pd.DataFrame) -> dict:
    return {
        "n_lecturas": int(len(res)),
        "n_fallas_predichas": int(res["prediccion"].sum()),
        "tasa_fallas": round(float(res["prediccion"].mean()), 4) if len(res) else 0.0,
        "probabilidad_media": round(float(res["probabilidad_falla"].mean()), 4) if len(res) else 0.0,
        "zonas": {z: int((res["zona_global"] == z).sum()) for z in "ABCD"},
        "umbral_decision": ESTADO["bundle"]["umbral"],
    }


# ---------------------------------------------------------------------------
# Panel web (front-end estático servido por la misma API)
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def panel():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/ui", tags=["info"], summary="Dashboard web (sustituye al dashboard Gradio de la guía)")
def panel_ui():
    """Panel HTML/JS propio, sin dependencias externas: más liviano que Gradio en 512 MB de RAM."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Info
# ---------------------------------------------------------------------------
@app.get("/api", tags=["info"])
def indice():
    return {"servicio": app.title, "version": APP_VERSION, "panel": "/", "docs": "/docs",
            "health": "/health", "metricas": "/metrics"}


@app.get("/health", tags=["info"])
def health():
    """Estado del servicio (mismos campos que la guía + datos del modelo y del proceso)."""
    bundle = ESTADO.get("bundle", {})
    return {
        "status": "ok" if bundle else "sin_modelo",
        "model": MODEL_PATH.name,
        "csv": DATA_PATH.exists(),
        "modelo": bundle.get("nombre_modelo"),
        "umbral_decision": bundle.get("umbral"),
        "fecha_entrenamiento": bundle.get("fecha_entrenamiento"),
        "reference_rows": len(ESTADO.get("ref", [])),
        "version": APP_VERSION,
        "uptime_s": round(time.time() - T_INICIO_PROCESO, 1),
    }


@app.get("/version", tags=["info"])
def version():
    """Qué versión está desplegada: commit, rama, entorno, instancia y versiones de software."""
    bundle = ESTADO.get("bundle", {})
    return {**info_despliegue(APP_VERSION, config.GITHUB_REPO),
            "modelo": {"nombre": bundle.get("nombre_modelo"), "fecha_entrenamiento": bundle.get("fecha_entrenamiento"),
                       "version_sklearn": bundle.get("version_sklearn"), "umbral": bundle.get("umbral"),
                       "inferencia": bundle["predictor"].descripcion if "predictor" in bundle else None}}


@app.get("/equipo", tags=["info"])
def equipo():
    """Ficha técnica, corriente nominal, umbrales por zona y niveles de alerta."""
    return {"equipo": EQUIPO, "corriente_nominal_A": round(I_NOMINAL, 2),
            "umbrales": UMBRALES, "zonas": ZONAS, "niveles_alerta": NIVELES_ALERTA}


@app.get("/modelo", tags=["info"])
def modelo_info():
    """Ficha del modelo (model card): algoritmo, umbral, costos, métricas y comparación en CV."""
    datos = {k: v for k, v in ESTADO["bundle"].items() if k not in ("modelo", "predictor")}
    return {**datos, "inferencia": ESTADO["bundle"]["predictor"].descripcion}


# ---------------------------------------------------------------------------
# Predicción
# ---------------------------------------------------------------------------
@app.post("/predict", tags=["predicción"])
def predict(
    temperatura: float = Query(..., ge=-20, le=200, description="Temperatura del rodamiento (°C)"),
    vibracion: float = Query(..., ge=0, le=50, description="Vibración global (mm/s RMS)"),
    corriente: Optional[float] = Query(None, ge=0, le=200,
                                       description="Corriente (A). Si se omite se asume 47 A (operación nominal)"),
):
    """Probabilidad de falla (modelo) + zona normativa por variable (reglas) + recomendación.

    Compatible con la guía: `POST /predict?temperatura=70&vibracion=0.5` sigue funcionando.
    """
    asumida = corriente is None
    lectura = {"temperatura": temperatura, "vibracion": vibracion, "corriente": 47.0 if asumida else corriente}
    r = _predecir_df(pd.DataFrame([lectura]), "predict").iloc[0]
    pred, zg = int(r["prediccion"]), str(r["zona_global"])
    return {
        "prediccion": pred,
        "etiqueta": "Falla" if pred else "Sin falla",
        "probabilidad_falla": float(r["probabilidad_falla"]),
        "umbral_decision": ESTADO["bundle"]["umbral"],
        "zona_global": zg,
        "accion_norma": ZONAS[zg]["accion"],
        "recomendacion": _recomendacion(pred, zg),
        "detalle_variables": evaluar_lectura(lectura),
        "corriente_asumida": asumida,
        "modelo": ESTADO["bundle"]["nombre_modelo"],
    }


# ---------------------------------------------------------------------------
# Monitoreo
# ---------------------------------------------------------------------------
def _monitorear(curr: pd.DataFrame, k_sigma: float, con_evidently: bool) -> dict:
    """Evalúa el lote y deja guardado para que /report genere el HTML de Evidently si se pide."""
    if con_evidently:
        with _LOCK_REPORTE:
            r = evaluar_batch(ESTADO["ref"], curr, k_sigma, ESTADO["bundle"], REPORT_HTML)
            ESTADO["reporte_pendiente"] = None
    else:
        r = evaluar_batch(ESTADO["ref"], curr, k_sigma, ESTADO["bundle"], usar_evidently=False)
        ESTADO["reporte_pendiente"] = curr
    METRICAS.contar_monitoreo(r["nivel_alerta"])
    return r


@app.get("/escenarios", tags=["monitoreo"])
def escenarios():
    """Escenarios predefinidos de lote para /monitor."""
    return ESCENARIOS


@app.post("/monitor", tags=["monitoreo"])
def monitor(
    size: int = Query(200, ge=50, le=5000, description="Tamaño del lote simulado"),
    t_mean: float = Query(72.0, description="Media de temperatura (°C)"),
    t_std: float = Query(3.5, gt=0, description="Desviación de temperatura"),
    v_mean: float = Query(2.0, description="Media de vibración (mm/s RMS)"),
    v_std: float = Query(0.35, gt=0, description="Desviación de vibración"),
    c_mean: float = Query(47.0, description="Media de corriente (A)"),
    c_std: float = Query(1.5, gt=0, description="Desviación de corriente"),
    k_sigma: float = Query(2.0, ge=0.5, le=4.0, description="k para el límite ref_mean + k·ref_std"),
    aleatorio: bool = Query(False, description="Nueva muestra aleatoria (si no, semilla fija 43)"),
    evidently: bool = Query(False, description="Calcular el drift con Evidently en esta misma petición "
                                               "(más lento en 0,1 CPU). Por defecto: K-S de SciPy + reporte bajo demanda"),
):
    """Simula un lote y devuelve drift (K-S), límites k·σ, zonas, nivel de alerta y diagnóstico.

    Mantiene los campos de la guía (`limits`, `current_means`, `alerts`) y agrega la tercera variable.
    El reporte HTML de Evidently de este lote se genera bajo demanda en `GET /report`.
    """
    curr = generar_batch(size=size, temp_mean=t_mean, temp_std=t_std, vib_mean=v_mean, vib_std=v_std,
                         corr_mean=c_mean, corr_std=c_std, seed=None if aleatorio else 43)
    r = _monitorear(curr, k_sigma, evidently)
    ks = r["limites_k_sigma"]
    corto = {"temperatura": "temp", "vibracion": "vib", "corriente": "corr"}
    return {
        "limits": {f"{corto[v]}_upper": ks[v]["upper_limit"] for v in FEATURES},
        "current_means": {corto[v]: ks[v]["current_mean"] for v in FEATURES},
        "alerts": {corto[v]: ks[v]["alert"] for v in FEATURES},
        **r,
        "report_url": "/report",
    }


@app.get("/trayectoria/{dia}", tags=["monitoreo"])
def trayectoria_dia(dia: int = ParamRuta(..., ge=0, le=TRAYECTORIA["dias"] - 1)):
    """Evalúa el lote de un día de la trayectoria simulada hasta la falla (144 lecturas)."""
    cache = ESTADO["tray_cache"]
    if dia not in cache:
        tray = ESTADO["tray"]
        g = tray[tray["dia"] == dia]
        r = evaluar_batch(ESTADO["ref"], g[FEATURES], 2.0, ESTADO["bundle"], usar_evidently=False)
        cache[dia] = {
            "dia": dia, "config": TRAYECTORIA,
            "salud_h": float(g["salud_h"].iloc[0]),
            "medias": {v: round(float(g[v].mean()), 3) for v in FEATURES},
            "p05": {v: round(float(g[v].quantile(0.05)), 3) for v in FEATURES},
            "p95": {v: round(float(g[v].quantile(0.95)), 3) for v in FEATURES},
            "p_valores": {v: r["drift"]["columnas"][v]["estadistico"] for v in FEATURES},
            "n_drift": r["drift"]["n_drifted_features"],
            "zonas": {v: r["zonas_norma"][v]["zona"] for v in FEATURES},
            "codigo_alerta": r["codigo_alerta"], "nivel_alerta": r["nivel_alerta"],
            "diagnostico": r["diagnostico"],
            "tasa_falla_modelo": r["modelo"]["tasa_lecturas_con_falla"],
        }
    return cache[dia]


@app.get("/report", tags=["monitoreo"])
def report_html():
    """Reporte HTML de Evidently del último lote monitoreado.

    Se genera bajo demanda: la primera vez tras un arranque en frío importa Evidently
    (puede tardar ~1 min en 0,1 CPU); las siguientes, unos segundos.
    """
    with _LOCK_REPORTE:
        pendiente = ESTADO.get("reporte_pendiente")
        if pendiente is not None:
            t0 = time.perf_counter()
            drift_evidently(ESTADO["ref"], pendiente, REPORT_HTML)
            ESTADO["reporte_pendiente"] = None
            METRICAS.log({"evento": "reporte_evidently", "filas": len(pendiente),
                          "segundos": round(time.perf_counter() - t0, 2)})
    if not REPORT_HTML.exists():
        raise HTTPException(status_code=404, detail="No existe reporte. Ejecuta /monitor primero.")
    return FileResponse(path=str(REPORT_HTML), media_type="text/html")


# ---------------------------------------------------------------------------
# Integración (protegido con API key)
# ---------------------------------------------------------------------------
def _normalizar_columna(c: str) -> str:
    c = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().strip().lower()
    return c.split("(")[0].strip().replace(" ", "_")


def _leer_csv(contenido: bytes) -> tuple[pd.DataFrame, list[dict]]:
    """Lee un CSV (separador , o ;, decimal . o ,) y separa filas válidas de inválidas."""
    texto = contenido.decode("utf-8-sig", errors="replace")
    try:
        df = pd.read_csv(io.StringIO(texto), sep=None, engine="python", dtype=str)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"CSV ilegible: {e}") from e
    df.columns = [_normalizar_columna(c) for c in df.columns]
    faltan = [v for v in FEATURES if v not in df.columns]
    if faltan:
        raise HTTPException(status_code=422, detail=f"Faltan columnas: {faltan}. Se esperan {FEATURES}.")
    if len(df) > config.MAX_LOTE:
        raise HTTPException(status_code=413, detail=f"Máximo {config.MAX_LOTE} filas por archivo.")
    num = df[FEATURES].apply(lambda s: pd.to_numeric(s.str.strip().str.replace(",", ".", regex=False),
                                                     errors="coerce"))
    rangos = {"temperatura": (-20, 200), "vibracion": (0, 50), "corriente": (0, 200)}
    invalidas = []
    for i, fila in num.iterrows():
        motivos = [f"{v} no numérica" if pd.isna(fila[v]) else f"{v} fuera de rango {rangos[v]}"
                   for v in FEATURES if pd.isna(fila[v]) or not rangos[v][0] <= fila[v] <= rangos[v][1]]
        if motivos:
            invalidas.append({"fila": int(i) + 2, "motivo": "; ".join(motivos)})   # +2: cabecera y base 1
    validas = num.drop(index=[f["fila"] - 2 for f in invalidas])
    return validas, invalidas


@app.post("/predict/lote/csv", tags=["integración"], dependencies=[Depends(verificar_api_key)])
async def predecir_lote_csv(archivo: UploadFile = File(..., description="CSV con columnas temperatura, vibracion, corriente")):
    """Predicción por lotes desde un archivo CSV (p. ej., exportación del SCADA). Requiere `X-API-Key`."""
    contenido = await archivo.read(config.MAX_CSV_BYTES + 1)
    if len(contenido) > config.MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera 1 MB.")
    df, invalidas = _leer_csv(contenido)
    if df.empty:
        raise HTTPException(status_code=422, detail={"mensaje": "Ninguna fila válida.", "filas_invalidas": invalidas[:50]})
    res = _predecir_df(df.reset_index(drop=True), "lote_csv")
    METRICAS.log({"evento": "lote_csv", "archivo": archivo.filename, "filas": len(res), "invalidas": len(invalidas)})
    return {"archivo": archivo.filename, "resumen": _resumen_lote(res),
            "filas_invalidas": invalidas[:200], "resultados": res.to_dict(orient="records")}


@app.post("/predict/lote", tags=["integración"], dependencies=[Depends(verificar_api_key)])
def predecir_lote(lote: LotePrediccion):
    """Predicción por lotes en JSON (integración máquina a máquina). Requiere `X-API-Key`."""
    df = pd.DataFrame([l.model_dump() for l in lote.lecturas])[FEATURES]
    res = _predecir_df(df, "lote_json")
    return {"resumen": _resumen_lote(res), "resultados": res.to_dict(orient="records")}


@app.post("/monitor/lecturas", tags=["integración"], dependencies=[Depends(verificar_api_key)])
def monitor_lecturas(lote: LoteLecturas):
    """Monitoreo de drift con lecturas reales (mín. 30), sin simulación. Requiere `X-API-Key`."""
    curr = pd.DataFrame([l.model_dump() for l in lote.lecturas])[FEATURES]
    return {**_monitorear(curr, lote.k_sigma, False), "report_url": "/report"}


@app.get("/historial", tags=["integración"], dependencies=[Depends(verificar_api_key)])
def historial(n: int = Query(100, ge=1, le=500)):
    """Últimas predicciones atendidas (almacenamiento en memoria del contenedor). Requiere `X-API-Key`."""
    return {"n": min(n, len(HISTORIAL)), "capacidad": HISTORIAL.maxlen,
            "nota": "Memoria del proceso: se borra al redesplegar o tras un arranque en frío.",
            "registros": list(HISTORIAL)[-n:][::-1]}


# ---------------------------------------------------------------------------
# Operación / observabilidad
# ---------------------------------------------------------------------------
@app.get("/metrics", tags=["operación"], response_class=PlainTextResponse)
def metrics():
    """Métricas en formato Prometheus (peticiones, latencia, predicciones, memoria, uptime)."""
    return PlainTextResponse(METRICAS.prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/ops/metricas", tags=["operación"])
def ops_metricas():
    """Resumen JSON de métricas para el panel (latencia p50/p95/p99, errores, serie de 5 min)."""
    return METRICAS.resumen()


@app.get("/ops/logs", tags=["operación"])
def ops_logs(n: int = Query(40, ge=1, le=300)):
    """Últimas líneas del log estructurado (IPs anonimizadas)."""
    return {"logs": METRICAS.ultimos_logs(n)}


@app.get("/ops/seguridad", tags=["operación"])
def ops_seguridad(request: Request):
    """Postura de seguridad del despliegue, verificada en vivo."""
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    return {
        "https": https,
        "api_key_configurada": bool(config.API_KEY),
        "rate_limit_por_min": config.RATE_LIMIT_POR_MIN,
        "cors_origins": config.CORS_ORIGINS,
        "usuario_no_root": usuario_no_root(),
        "validacion_entradas": True,
        "max_lote_filas": config.MAX_LOTE,
        "max_csv_bytes": config.MAX_CSV_BYTES,
        "endpoints_protegidos": ["/predict/lote", "/predict/lote/csv", "/monitor/lecturas", "/historial"],
    }
