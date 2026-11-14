# -*- coding: utf-8 -*-
"""
Pruebas automáticas de la API (se ejecutan en GitHub Actions antes de cada despliegue).

Requisito: python train_model.py
Ejecutar:  python -m pytest -q        (o sin pytest:  python -m tests.test_app)
"""
from fastapi.testclient import TestClient

import main
from motor import config

CLAVE = "clave-de-prueba"


def _cliente() -> TestClient:
    return TestClient(main.app)


def test_health_y_version():
    with _cliente() as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["model"] == "modelo_sensores.pkl" and h["csv"] is True
        v = c.get("/version").json()
        assert v["version"] == config.APP_VERSION and v["modelo"]["nombre"]


def test_predict_compatible_con_la_guia():
    with _cliente() as c:
        r = c.post("/predict", params={"temperatura": 70, "vibracion": 0.5})
        assert r.status_code == 200
        assert r.json()["prediccion"] == 0 and r.json()["corriente_asumida"] is True


def test_predict_sano_y_falla():
    with _cliente() as c:
        sano = c.post("/predict", params={"temperatura": 72, "vibracion": 2.0, "corriente": 47}).json()
        falla = c.post("/predict", params={"temperatura": 93, "vibracion": 5.8, "corriente": 54}).json()
        assert sano["prediccion"] == 0 and sano["zona_global"] == "B"
        assert falla["prediccion"] == 1 and falla["zona_global"] == "D"


def test_validacion_entrada_422():
    with _cliente() as c:
        assert c.post("/predict", params={"temperatura": 72, "vibracion": -1}).status_code == 422
        assert c.post("/predict", params={"temperatura": "abc", "vibracion": 2}).status_code == 422


def test_monitor_escenarios():
    esperado = {"sin_drift": "NORMAL", "degradacion": "PLANIFICAR", "falla": "DETENER"}
    nombres = {"temp_mean": "t_mean", "temp_std": "t_std", "vib_mean": "v_mean", "vib_std": "v_std",
               "corr_mean": "c_mean", "corr_std": "c_std"}
    with _cliente() as c:
        for esc, nivel in esperado.items():
            params = {nombres[k]: v for k, v in config.ESCENARIOS[esc]["params"].items()}
            r = c.post("/monitor", params={"size": 200, **params})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["nivel_alerta"] == nivel, esc
            assert set(d["limits"]) == {"temp_upper", "vib_upper", "corr_upper"}   # formato de la guía
        assert c.get("/report").status_code == 200                                  # Evidently bajo demanda


def test_concordancia_evidently_scipy():
    """El K-S de SciPy (ruta rápida) y Evidently deben llegar a las mismas conclusiones."""
    params = {"size": 200, "t_mean": 78}          # escenario estacional: drift solo en temperatura
    with _cliente() as c:
        rapido = c.post("/monitor", params=params).json()
        completo = c.post("/monitor", params={**params, "evidently": True}).json()
    assert rapido["drift"]["motor"] == "scipy" and completo["drift"]["motor"] == "evidently"
    assert rapido["nivel_alerta"] == completo["nivel_alerta"]
    for v in ("temperatura", "vibracion", "corriente"):
        assert rapido["drift"]["columnas"][v]["drift"] == completo["drift"]["columnas"][v]["drift"], v


def test_inferencia_vectorizada_identica_a_sklearn():
    """La optimización de inferencia no debe cambiar ninguna probabilidad."""
    import joblib
    import numpy as np
    from motor.inferencia import Predictor
    from motor.simulacion import generar_batch
    bundle = joblib.load(config.MODEL_PATH)
    X = generar_batch(size=2000, temp_mean=85, temp_std=12, vib_mean=3.5, vib_std=2,
                      corr_mean=50, corr_std=5, seed=11)                       # incluye valores extremos
    p = Predictor(bundle, X)
    assert p.rapido is not None, p.descripcion
    ref = bundle["modelo"].predict_proba(X[bundle["features"]])[:, 1]
    assert np.abs(p.proba_falla(X) - ref).max() < 1e-9


def test_trayectoria():
    with _cliente() as c:
        sano, falla = c.get("/trayectoria/5").json(), c.get("/trayectoria/58").json()
        assert sano["codigo_alerta"] <= 1 and falla["nivel_alerta"] == "DETENER"
        assert c.get("/trayectoria/99").status_code == 422


def test_api_key_protege_integracion():
    csv = "temperatura;vibracion;corriente\n72,0;2,0;47\n93;5.8;54\nabc;1;40\n"
    original = config.API_KEY
    try:
        config.API_KEY = ""
        with _cliente() as c:
            assert c.get("/historial").status_code == 503              # sin clave configurada
        config.API_KEY = CLAVE
        with _cliente() as c:
            assert c.get("/historial").status_code == 401
            assert c.get("/historial", headers={"X-API-Key": "otra"}).status_code == 401
            r = c.post("/predict/lote/csv", headers={"X-API-Key": CLAVE},
                       files={"archivo": ("lecturas.csv", csv.encode(), "text/csv")})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["resumen"]["n_lecturas"] == 2 and d["resumen"]["n_fallas_predichas"] == 1
            assert d["filas_invalidas"][0]["fila"] == 4
            h = c.get("/historial", headers={"X-API-Key": CLAVE}).json()
            assert h["n"] >= 2
    finally:
        config.API_KEY = original


def test_rate_limit_429():
    from motor.seguridad import LIMITADOR
    limite_original = LIMITADOR.limite
    try:
        LIMITADOR.limite = 3
        LIMITADOR._marcas.clear()
        with _cliente() as c:
            codigos = [c.post("/predict", params={"temperatura": 72, "vibracion": 2}).status_code for _ in range(5)]
        assert codigos[:3] == [200, 200, 200] and codigos[3] == 429
    finally:
        LIMITADOR.limite = limite_original
        LIMITADOR._marcas.clear()


def test_observabilidad_y_cabeceras():
    with _cliente() as c:
        r = c.get("/")
        assert r.status_code == 200 and "Content-Security-Policy" in r.headers
        assert r.headers["X-Content-Type-Options"] == "nosniff" and "X-Request-ID" in r.headers
        assert c.get("/ui").status_code == 200 and c.get("/static/app.js").status_code == 200
        c.post("/predict", params={"temperatura": 72, "vibracion": 2})
        m = c.get("/metrics").text
        assert "http_requests_total" in m and "modelo_predicciones_total" in m
        assert c.get("/ops/metricas").json()["peticiones_total"] >= 1
        assert len(c.get("/ops/logs").json()["logs"]) >= 1
        assert c.get("/ops/seguridad").json()["validacion_entradas"] is True


if __name__ == "__main__":
    pruebas = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in pruebas:
        t()
        print(f"[OK] {t.__name__}")
    print(f"{len(pruebas)} pruebas superadas")
