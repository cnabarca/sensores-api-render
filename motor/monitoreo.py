# -*- coding: utf-8 -*-
"""
Monitoreo del modelo y del equipo (heredado de la Semana 2, adaptado a la nube).

Combina tres capas de detección, de la más temprana a la más crítica:
    1. Drift estadístico (prueba K-S por variable)                  -> VIGILANCIA
    2. Límites de control k·σ respecto de la línea base             -> PREALERTA
    3. Zonas normativas (ISO 10816-3 + criterios de temperatura y
       corriente nominal)                                           -> PLANIFICAR / DETENER
y añade un diagnóstico del modo de falla según qué variables derivaron.

Cambio para la nube: Evidently se importa de forma diferida (solo cuando se pide
un reporte), así el arranque en frío del contenedor es más rápido. Las consultas
masivas (trayectoria de 60 días) usan la prueba K-S de SciPy, que es la misma
prueba que aplica Evidently a variables numéricas con menos de 1000 filas.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .config import FEATURES, NIVELES_ALERTA, UMBRALES, ZONAS

_LOCK_IMPORT = threading.Lock()


def cargar_evidently():
    """Importa Evidently una sola vez y de forma segura entre hilos.

    Dos hilos importando el paquete a la vez (precalentamiento + primera petición)
    provocan un ImportError por importación circular parcial; el candado lo evita.
    """
    with _LOCK_IMPORT:
        from evidently import Report
        from evidently.presets import DataDriftPreset
    return Report, DataDriftPreset


# ---------------------------------------------------------------------------
# Zonas normativas
# ---------------------------------------------------------------------------
def zona(variable: str, valor: float) -> str:
    """Devuelve la zona A/B/C/D de una lectura según UMBRALES."""
    a, b, c = UMBRALES[variable]["limites"]
    if valor < a:
        return "A"
    if valor < b:
        return "B"
    if valor <= c:
        return "C"
    return "D"


def evaluar_lectura(lectura: dict) -> dict:
    """Zona y acción por variable para una lectura puntual."""
    salida = {}
    for var in FEATURES:
        z = zona(var, float(lectura[var]))
        salida[var] = {"valor": float(lectura[var]), "zona": z, "accion": ZONAS[z]["accion"]}
    return salida


# ---------------------------------------------------------------------------
# Drift estadístico
# ---------------------------------------------------------------------------
def drift_evidently(ref: pd.DataFrame, curr: pd.DataFrame, html_path: str | Path | None = None) -> dict:
    """Ejecuta DataDriftPreset (Evidently ≥ 0.7) y devuelve un resumen serializable."""
    Report, DataDriftPreset = cargar_evidently()      # import diferido: acelera el arranque en frío
    snapshot = Report([DataDriftPreset()]).run(current_data=curr[FEATURES], reference_data=ref[FEATURES])
    if html_path is not None:
        Path(html_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot.save_html(str(html_path))

    resumen = {"motor": "evidently", "dataset_drift": False, "n_drifted_features": 0,
               "share_of_drifted_features": 0.0, "drift_share_umbral": 0.5, "columnas": {}}
    for m in snapshot.dict()["metrics"]:
        cfg, valor = m["config"], m["value"]
        tipo = cfg.get("type", "")
        if tipo.endswith("DriftedColumnsCount"):
            resumen["n_drifted_features"] = int(valor["count"])
            resumen["share_of_drifted_features"] = round(float(valor["share"]), 4)
            resumen["drift_share_umbral"] = float(cfg.get("drift_share", 0.5))
        elif tipo.endswith("ValueDrift"):
            metodo, umbral = cfg.get("method", ""), float(cfg.get("threshold", 0.05))
            es_pvalor = "p_value" in metodo
            hay_drift = (valor < umbral) if es_pvalor else (valor >= umbral)
            resumen["columnas"][cfg["column"]] = {
                "metodo": metodo, "estadistico": round(float(valor), 6),
                "umbral": umbral, "drift": bool(hay_drift),
            }
    resumen["dataset_drift"] = resumen["share_of_drifted_features"] >= resumen["drift_share_umbral"]
    return resumen


def drift_ks(ref: pd.DataFrame, curr: pd.DataFrame, alfa: float = 0.05) -> dict:
    """Versión ligera (SciPy) con el mismo formato de salida que drift_evidently."""
    columnas = {}
    for var in FEATURES:
        p = float(stats.ks_2samp(ref[var], curr[var]).pvalue)
        columnas[var] = {"metodo": "K-S p_value (scipy)", "estadistico": round(p, 6),
                         "umbral": alfa, "drift": bool(p < alfa)}
    n = sum(c["drift"] for c in columnas.values())
    share = n / len(FEATURES)
    return {"motor": "scipy", "dataset_drift": share >= 0.5, "n_drifted_features": n,
            "share_of_drifted_features": round(share, 4), "drift_share_umbral": 0.5, "columnas": columnas}


# ---------------------------------------------------------------------------
# Límites k-sigma
# ---------------------------------------------------------------------------
def limites_ksigma(ref: pd.DataFrame, curr: pd.DataFrame, k_sigma: float = 2.0) -> dict:
    """Compara la media del batch con ref_mean + k·ref_std (límite superior = alerta)."""
    salida = {"k_sigma": k_sigma}
    for var in FEATURES:
        m, s = float(ref[var].mean()), float(ref[var].std())
        sup = m + k_sigma * s
        actual = float(curr[var].mean())
        salida[var] = {"ref_mean": round(m, 3), "ref_std": round(s, 3), "upper_limit": round(sup, 3),
                       "current_mean": round(actual, 3), "alert": bool(actual > sup)}
    return salida


# ---------------------------------------------------------------------------
# Diagnóstico y nivel de alerta
# ---------------------------------------------------------------------------
def diagnosticar(variables_al_alza: set[str]) -> str:
    """Interpreta qué combinación de variables dominantes subió respecto de la línea base."""
    v = variables_al_alza
    if not v:
        return "Sin anomalía: operación dentro de la línea base."
    if v == set(FEATURES):
        return "Falla avanzada combinada: mecánica con sobrecarga asociada."
    if "vibracion" in v and "corriente" not in v:
        return "Origen mecánico (rodamiento, desbalance o desalineación)."
    if "corriente" in v and "vibracion" not in v:
        return "Origen eléctrico / de carga (sobrecarga, obstrucción de la bomba, desbalance de fases)."
    if v == {"temperatura"}:
        return "Origen térmico / ambiental (ventilación, temperatura ambiente, lubricación)."
    return "Patrón mixto: requiere inspección en sitio."


def evaluar_batch(ref: pd.DataFrame, curr: pd.DataFrame, k_sigma: float = 2.0,
                  bundle: dict | None = None, html_path: str | Path | None = None,
                  usar_evidently: bool = True) -> dict:
    """Resumen completo de monitoreo para un batch de lecturas."""
    ev = drift_evidently(ref, curr, html_path) if usar_evidently else drift_ks(ref, curr)
    ks = limites_ksigma(ref, curr, k_sigma)

    medias = {var: float(curr[var].mean()) for var in FEATURES}
    zonas = {var: zona(var, medias[var]) for var in FEATURES}

    # Desplazamiento estandarizado de la media: z = (media_actual - media_ref) / std_ref
    z = {var: (medias[var] - ks[var]["ref_mean"]) / ks[var]["ref_std"] for var in FEATURES}

    if "D" in zonas.values():
        codigo = 4
    elif "C" in zonas.values():
        codigo = 3
    elif any(ks[v]["alert"] for v in FEATURES):
        codigo = 2
    elif any(c["drift"] for c in ev["columnas"].values()):
        codigo = 1
    else:
        codigo = 0

    # Variables dominantes: suben al menos 1σ y al menos la mitad del mayor desplazamiento
    if codigo == 0:
        dominantes: set[str] = set()
    elif all(zonas[v] == "D" for v in FEATURES):
        dominantes = set(FEATURES)
    else:
        z_max = max(z.values())
        dominantes = {v for v in FEATURES if z[v] >= max(1.0, 0.5 * z_max)}

    resumen = {
        "nivel_alerta": NIVELES_ALERTA[codigo]["nivel"],
        "codigo_alerta": codigo,
        "accion_recomendada": NIVELES_ALERTA[codigo]["accion"],
        "diagnostico": (diagnosticar(dominantes) if dominantes or codigo == 0 else
                        "Drift estadístico sin desplazamiento relevante de la media (< 1σ): "
                        "posible ruido o cambio de dispersión; confirmar con persistencia."),
        "variables_dominantes": sorted(dominantes),
        "desplazamiento_sigmas": {v: round(z[v], 3) for v in FEATURES},
        "drift": ev,
        "limites_k_sigma": ks,
        "zonas_norma": {var: {"media": round(medias[var], 3), "zona": zonas[var],
                              "accion": ZONAS[zonas[var]]["accion"]} for var in FEATURES},
        "n_lecturas": int(len(curr)),
    }

    if bundle is not None:
        if "predictor" in bundle:                   # inferencia optimizada (motor/inferencia.py)
            proba = bundle["predictor"].proba_falla(curr)
        else:
            proba = bundle["modelo"].predict_proba(curr[bundle["features"]])[:, 1]
        resumen["modelo"] = {
            "probabilidad_media_falla": round(float(proba.mean()), 4),
            "tasa_lecturas_con_falla": round(float((proba >= bundle["umbral"]).mean()), 4),
            "umbral_decision": bundle["umbral"],
        }
    return resumen
