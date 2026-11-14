# -*- coding: utf-8 -*-
"""
Simulación de datos de sensores del motor de inducción + bomba centrífuga.

Tres generadores:
1. generar_dataset()      -> dataset etiquetado para entrenar el modelo.
2. generar_batch()        -> batch "actual" para monitoreo de drift.
3. simular_trayectoria()  -> serie temporal run-to-failure (degradación progresiva).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CORRELACION, FEATURES, PERFILES, SEED

_LIMITES_FISICOS = {"temperatura": (20.0, 140.0), "vibracion": (0.05, 20.0), "corriente": (0.0, 120.0)}


def _muestrear(media, std, n: int, rng: np.random.Generator, correlacion=CORRELACION) -> np.ndarray:
    """Muestra n lecturas de una normal multivariante con la correlación física indicada."""
    std = np.asarray(std, dtype=float)
    cov = np.outer(std, std) * np.asarray(correlacion, dtype=float)
    return rng.multivariate_normal(np.asarray(media, dtype=float), cov, size=n)


def _recortar(df: pd.DataFrame) -> pd.DataFrame:
    for col, (lo, hi) in _LIMITES_FISICOS.items():
        if col in df:
            df[col] = df[col].clip(lo, hi)
    return df


def generar_dataset(seed: int = SEED) -> pd.DataFrame:
    """Dataset etiquetado: estados A, B, C (sin falla) y D mecánica/eléctrica (falla=1)."""
    rng = np.random.default_rng(seed)
    bloques = []
    for nombre, p in PERFILES.items():
        x = _muestrear(p["media"], p["std"], p["n"], rng)
        bloque = pd.DataFrame(x, columns=FEATURES)
        bloque["estado"] = p["estado"]
        bloque["modo_falla"] = p["modo_falla"]
        bloque["falla"] = p["falla"]
        bloques.append(bloque)
    df = pd.concat(bloques, ignore_index=True)
    df = _recortar(df).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    df[FEATURES] = df[FEATURES].round(3)
    return df


def generar_batch(size: int = 200,
                  temp_mean: float = 72.0, temp_std: float = 3.5,
                  vib_mean: float = 2.0, vib_std: float = 0.35,
                  corr_mean: float = 47.0, corr_std: float = 1.5,
                  seed: int | None = SEED + 1,
                  correlacionado: bool = True) -> pd.DataFrame:
    """Batch actual de lecturas (sin etiqueta) con parámetros controlables."""
    rng = np.random.default_rng(seed)
    media = (temp_mean, vib_mean, corr_mean)
    std = (temp_std, vib_std, corr_std)
    corr = CORRELACION if correlacionado else np.eye(3)
    df = pd.DataFrame(_muestrear(media, std, size, rng, corr), columns=FEATURES)
    return _recortar(df)


def simular_trayectoria(dias: int = 60,
                        lecturas_por_dia: int = 144,
                        inicio_degradacion: int = 20,
                        dia_falla: int = 56,
                        forma: float = 2.2,
                        amplitud_diaria: float = 1.0,
                        seed: int = SEED) -> pd.DataFrame:
    """
    Serie temporal run-to-failure (1 lectura cada 10 min si lecturas_por_dia=144).

    Índice de salud h(t) ∈ [0, 1]:
        h = 0                                   si t < inicio_degradacion
        h = ((t - t0) / (tf - t0)) ** forma     (desgaste acelerado, típico de rodamientos)
    Las medias y desviaciones se interpolan entre el perfil B (sano) y D mecánico.
    Se agrega un ciclo diario de temperatura ambiente (± amplitud_diaria °C).
    """
    rng = np.random.default_rng(seed)
    mu_b, sd_b = np.array(PERFILES["B"]["media"]), np.array(PERFILES["B"]["std"])
    mu_d, sd_d = np.array(PERFILES["D_mecanica"]["media"]), np.array(PERFILES["D_mecanica"]["std"])

    filas = []
    inicio = pd.Timestamp("2026-01-01")
    for dia in range(dias):
        x = (dia - inicio_degradacion) / (dia_falla - inicio_degradacion)
        h = float(np.clip(x, 0, 1) ** forma)
        media = mu_b + h * (mu_d - mu_b)
        std = sd_b + h * (sd_d - sd_b)
        lect = _muestrear(media, std, lecturas_por_dia, rng)
        horas = np.arange(lecturas_por_dia) * (24 / lecturas_por_dia)
        lect[:, 0] += amplitud_diaria * np.sin(2 * np.pi * (horas - 9) / 24)  # pico a media tarde
        bloque = pd.DataFrame(lect, columns=FEATURES)
        bloque["timestamp"] = inicio + pd.to_timedelta(dia * 24 + horas, unit="h")
        bloque["dia"] = dia
        bloque["salud_h"] = round(h, 4)
        filas.append(bloque)
    df = pd.concat(filas, ignore_index=True)
    return _recortar(df)[["timestamp", "dia", "salud_h", *FEATURES]]
