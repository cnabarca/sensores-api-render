# -*- coding: utf-8 -*-
"""
Optimización de la inferencia para una instancia de 0,1 CPU.

`RandomForestClassifier.predict_proba` recorre los 300 árboles uno por uno en Python:
para UNA lectura cuesta ~28 ms de CPU, que en el plan Free de Render (0,1 CPU)
se convierten en ~280 ms. Aquí el bosque se "compila" a arreglos planos de NumPy y
los 300 árboles se recorren a la vez, nivel por nivel (profundidad ~20 iteraciones
vectorizadas en lugar de 300 llamadas).

El resultado es numéricamente idéntico al de scikit-learn: se verifica al arrancar
contra la línea base y, si hubiera cualquier diferencia, se usa el modelo original.
"""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier


class BosqueVectorizado:
    """Recorrido simultáneo de todos los árboles de un RandomForest binario."""

    def __init__(self, rf: RandomForestClassifier, tamano_bloque: int = 1000):
        arboles = [e.tree_ for e in rf.estimators_]
        offs = np.cumsum([0] + [t.node_count for t in arboles[:-1]])

        def unir(attr, desplazar=False):
            partes = []
            for t, o in zip(arboles, offs):
                a = getattr(t, attr)
                partes.append(np.where(a >= 0, a + o, -1) if desplazar else a)
            return np.concatenate(partes)

        self.izq = unir("children_left", True)
        self.der = unir("children_right", True)
        self.var = np.maximum(unir("feature"), 0)           # hojas: -2 -> 0 (no se usa)
        self.umbral = unir("threshold")
        valores = np.concatenate([t.value[:, 0, :] for t in arboles])
        self.p1 = valores[:, 1] / valores.sum(axis=1)       # prob. de clase 1 en cada nodo
        self.raices = offs
        self.n_arboles = len(arboles)
        self.tamano_bloque = tamano_bloque
        self.profundidad = max(e.get_depth() for e in rf.estimators_)

    def _bloque(self, X: np.ndarray) -> np.ndarray:
        n = X.shape[0]
        nodos = np.broadcast_to(self.raices, (n, self.n_arboles)).copy()
        filas = np.arange(n)[:, None]
        for _ in range(self.profundidad + 1):
            hijo_izq = self.izq[nodos]
            activos = hijo_izq >= 0
            if not activos.any():
                break
            # scikit-learn compara en float32: X[i, f] <= umbral
            ir_izq = X[filas, self.var[nodos]] <= self.umbral[nodos]
            nodos = np.where(activos, np.where(ir_izq, hijo_izq, self.der[nodos]), nodos)
        return self.p1[nodos].mean(axis=1)

    def proba_falla(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        return np.concatenate([self._bloque(X[i:i + self.tamano_bloque])
                               for i in range(0, len(X), self.tamano_bloque)]) if len(X) else np.empty(0)


class Predictor:
    """Interfaz única de inferencia: usa el bosque vectorizado si está verificado."""

    def __init__(self, bundle: dict, X_verificacion):
        self.modelo = bundle["modelo"]
        self.features = bundle["features"]
        self.rapido: BosqueVectorizado | None = None
        self.diferencia_max = None
        if isinstance(self.modelo, RandomForestClassifier):
            candidato = BosqueVectorizado(self.modelo)
            Xv = np.asarray(X_verificacion, dtype=float)
            ref = self._sklearn(Xv)
            self.diferencia_max = float(np.max(np.abs(candidato.proba_falla(Xv) - ref)))
            if self.diferencia_max < 1e-9:
                self.rapido = candidato

    def _sklearn(self, X) -> np.ndarray:
        import pandas as pd
        return self.modelo.predict_proba(pd.DataFrame(np.asarray(X, dtype=float), columns=self.features))[:, 1]

    @property
    def descripcion(self) -> str:
        if self.rapido:
            return f"bosque vectorizado NumPy ({self.rapido.n_arboles} árboles, verificado: dif. máx. {self.diferencia_max:.1e})"
        return "scikit-learn predict_proba"

    def proba_falla(self, df) -> np.ndarray:
        X = df[self.features].to_numpy(dtype=float) if hasattr(df, "columns") else np.asarray(df, dtype=float)
        return self.rapido.proba_falla(X) if self.rapido else self._sklearn(X)
