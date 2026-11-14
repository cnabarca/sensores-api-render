# -*- coding: utf-8 -*-
"""
Entrenamiento, comparación y serialización del modelo de fallas (lógica de la Semana 2).

Flujo: comparar 3 candidatos en validación cruzada -> elegir por PR-AUC ->
umbral de decisión de mínimo costo (out-of-fold) -> métricas en holdout -> bundle .pkl.
"""
from __future__ import annotations

import platform
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_validate, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import (APP_VERSION, COSTO_FN, COSTO_FP, DATA_DIR, DATA_PATH, FEATURES, MODEL_PATH,
                     MODELS_DIR, REF_PATH, SEED, TARGET)
from .simulacion import generar_dataset

METRICAS_CV = {
    "accuracy": "accuracy", "precision": "precision", "recall": "recall",
    "f1": "f1", "roc_auc": "roc_auc", "pr_auc": "average_precision",
}


def construir_modelos() -> dict:
    """Candidatos: un baseline lineal interpretable y dos ensambles de árboles."""
    return {
        "Regresión logística": make_pipeline(
            StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000)),
        "Random Forest": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
            random_state=SEED, n_jobs=-1),
        "Gradient Boosting": HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, class_weight="balanced", random_state=SEED),
    }


def comparar_modelos(train: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    """Validación cruzada estratificada; devuelve media y desviación por métrica."""
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=SEED)
    filas = []
    for nombre, modelo in construir_modelos().items():
        res = cross_validate(modelo, train[FEATURES], train[TARGET], cv=cv, scoring=METRICAS_CV)
        fila = {"modelo": nombre}
        for m in METRICAS_CV:
            fila[f"{m}_media"] = res[f"test_{m}"].mean()
            fila[f"{m}_std"] = res[f"test_{m}"].std()
        filas.append(fila)
    return pd.DataFrame(filas).set_index("modelo")


def seleccionar_umbral(modelo, train: pd.DataFrame) -> float:
    """Umbral de mínimo costo esperado usando predicciones out-of-fold (sin tocar el holdout)."""
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    oof = cross_val_predict(modelo, train[FEATURES], train[TARGET], cv=cv, method="predict_proba")[:, 1]
    y = train[TARGET].to_numpy()
    mejor_u, mejor_costo = 0.5, float("inf")
    for u in np.round(np.arange(0.05, 0.96, 0.01), 2):
        pred = (oof >= u).astype(int)
        costo = ((pred == 0) & (y == 1)).sum() * COSTO_FN + ((pred == 1) & (y == 0)).sum() * COSTO_FP
        if costo <= mejor_costo:          # ante empate se queda el umbral más alto (menos falsas alarmas)
            mejor_u, mejor_costo = float(u), costo
    return mejor_u


def metricas_holdout(y_true, proba, umbral: float) -> dict:
    pred = (proba >= umbral).astype(int)
    return {
        "accuracy": accuracy_score(y_true, pred),
        "precision": precision_score(y_true, pred, zero_division=0),
        "recall": recall_score(y_true, pred),
        "f1": f1_score(y_true, pred),
        "roc_auc": roc_auc_score(y_true, proba),
        "pr_auc": average_precision_score(y_true, proba),
    }


def entrenar(verbose: bool = True) -> dict:
    """Genera el dataset, entrena, evalúa y guarda modelo + línea base de monitoreo."""
    df = generar_dataset()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA_PATH, index=False)

    train, test = train_test_split(df, test_size=0.3, random_state=SEED, stratify=df[TARGET])
    comparacion = comparar_modelos(train)
    mejor = comparacion["pr_auc_media"].idxmax()
    modelo = construir_modelos()[mejor]

    umbral = seleccionar_umbral(modelo, train)
    modelo.fit(train[FEATURES], train[TARGET])
    if "n_jobs" in modelo.get_params():
        # En producción cada petición predice pocas filas: un solo hilo evita la sobrecarga
        # de crear hilos en una instancia con CPU compartida (Render free = 0,1 CPU).
        modelo.set_params(n_jobs=1)
    proba_test = modelo.predict_proba(test[FEATURES])[:, 1]

    bundle = {
        "modelo": modelo,
        "nombre_modelo": mejor,
        "features": FEATURES,
        "umbral": umbral,
        "metricas_holdout": {k: round(float(v), 4) for k, v in
                             metricas_holdout(test[TARGET], proba_test, umbral).items()},
        "comparacion_cv": comparacion.round(4).reset_index().to_dict(orient="records"),
        "costos": {"falso_negativo": COSTO_FN, "falso_positivo": COSTO_FP},
        "n_train": len(train), "n_test": len(test),
        "fecha_entrenamiento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version_sklearn": sklearn.__version__,
        "version_python": platform.python_version(),
        "version_servicio": APP_VERSION,
    }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    # Línea base para monitoreo: operación sana en servicio (zona B) del set de entrenamiento
    train.loc[train["estado"] == "B", FEATURES].to_csv(REF_PATH, index=False)

    if verbose:
        print(f"[OK] {DATA_PATH.name}: {len(df)} lecturas | fallas = {df[TARGET].sum()} ({df[TARGET].mean():.1%})")
        print(comparacion[[c for c in comparacion.columns if c.endswith("_media")]].round(3).to_string())
        print(f"\nModelo elegido (mayor PR-AUC en CV): {mejor}")
        print(f"Umbral de mínimo costo (FN={COSTO_FN:.0f} USD, FP={COSTO_FP:.0f} USD): {umbral:.2f}")
        print("Holdout:", bundle["metricas_holdout"])
        print(f"[OK] {MODEL_PATH.name} y {REF_PATH.name} guardados")
    return bundle
