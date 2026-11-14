# -*- coding: utf-8 -*-
"""
Semana 3 — Paso 1: entrenamiento y serialización del modelo.

Uso:  python train_model.py
Se ejecuta automáticamente dentro del Dockerfile (durante el build), de modo que la
imagen ya contiene el modelo y la versión de scikit-learn que lo entrena es la misma
que lo carga en producción.

Genera:
  data/datos_sensor.csv            dataset simulado (1300 lecturas, 3 sensores)
  data/referencia_monitoreo.csv    línea base sana para el monitoreo de drift
  models/modelo_sensores.pkl       bundle {modelo, umbral, métricas, metadatos}
"""
from motor.entrenamiento import entrenar

if __name__ == "__main__":
    entrenar()
