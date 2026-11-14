# -*- coding: utf-8 -*-
"""
Semana 3 — Prueba de la API desde un cliente externo (local, Docker o Render).

Uso:
  python test_api.py                                         # http://localhost:8000
  python test_api.py https://sensores-api-render-5ouo.onrender.com

Opcional: si existe la variable de entorno API_KEY, también prueba los endpoints protegidos
  (CMD)        set API_KEY=mi-clave && python test_api.py https://...
  (PowerShell) $env:API_KEY="mi-clave"; python test_api.py https://...

Cada prueba imprime el código HTTP, la latencia total (navegador → nube → navegador) y el
tiempo de cómputo en el servidor (cabecera X-Response-Time-ms).
"""
from __future__ import annotations

import os
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
RESULTADOS: list[tuple[str, bool, float, str]] = []


def llamar(metodo: str, ruta: str, timeout: float = 30, **kw) -> tuple[requests.Response, float]:
    t0 = time.perf_counter()
    r = requests.request(metodo, f"{BASE}{ruta}", timeout=timeout, **kw)
    return r, (time.perf_counter() - t0) * 1000


def prueba(nombre: str):
    def deco(fn):
        def envoltura():
            print("─" * 72)
            print(nombre)
            try:
                ms = fn()
                RESULTADOS.append((nombre, True, ms, ""))
                print(f"   ✅ OK ({ms:.0f} ms)")
            except AssertionError as e:
                RESULTADOS.append((nombre, False, 0, str(e)))
                print(f"   ❌ FALLÓ: {e}")
        return envoltura
    return deco


def mostrar(r: requests.Response, ms: float, campos: list[str] | None = None) -> None:
    srv = r.headers.get("X-Response-Time-ms", "?")
    print(f"   HTTP {r.status_code} · total {ms:.0f} ms · servidor {srv} ms · request-id {r.headers.get('X-Request-ID', '-')}")
    if campos and r.headers.get("content-type", "").startswith("application/json"):
        d = r.json()
        print("   " + " | ".join(f"{c}={d.get(c)}" for c in campos))


@prueba("1. GET /health  (despierta el servicio si estaba dormido: hasta 90 s)")
def t_health():
    r, ms = llamar("GET", "/health", timeout=90)
    mostrar(r, ms, ["status", "model", "csv", "modelo", "version"])
    if ms > 5000:
        print(f"   ⏱  Arranque en frío detectado: {ms / 1000:.1f} s")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    return ms


@prueba("2. GET /version  (commit desplegado, entorno, memoria)")
def t_version():
    r, ms = llamar("GET", "/version")
    mostrar(r, ms, ["entorno", "commit_corto", "rama", "uptime_s", "memoria_mb"])
    assert r.status_code == 200
    return ms


@prueba("3. POST /predict  (caso de la guía: temp=70, vib=0.5 → sin corriente)")
def t_predict_guia():
    r, ms = llamar("POST", "/predict", params={"temperatura": 70, "vibracion": 0.5})
    mostrar(r, ms, ["prediccion", "probabilidad_falla", "zona_global", "corriente_asumida"])
    assert r.status_code == 200 and r.json()["prediccion"] == 0
    return ms


@prueba("4. POST /predict  (motor sano: 72 °C, 2.0 mm/s, 47 A)")
def t_predict_sano():
    r, ms = llamar("POST", "/predict", params={"temperatura": 72, "vibracion": 2.0, "corriente": 47})
    mostrar(r, ms, ["etiqueta", "probabilidad_falla", "zona_global"])
    assert r.json()["prediccion"] == 0
    return ms


@prueba("5. POST /predict  (falla mecánica: 93 °C, 5.8 mm/s, 54 A)")
def t_predict_falla():
    r, ms = llamar("POST", "/predict", params={"temperatura": 93, "vibracion": 5.8, "corriente": 54})
    mostrar(r, ms, ["etiqueta", "probabilidad_falla", "zona_global", "recomendacion"])
    assert r.json()["prediccion"] == 1 and r.json()["zona_global"] == "D"
    return ms


@prueba("6. POST /predict  (dato inválido: vibración = −1 → se espera 422)")
def t_predict_invalido():
    r, ms = llamar("POST", "/predict", params={"temperatura": 72, "vibracion": -1, "corriente": 47})
    mostrar(r, ms)
    print(f"   detalle: {r.json()['detail'][0]['msg']}")
    assert r.status_code == 422
    return ms


@prueba("7. POST /monitor  (formato de la guía, lote con degradación → PLANIFICAR)")
def t_monitor():
    params = {"size": 200, "t_mean": 81, "t_std": 3.5, "v_mean": 3.5, "v_std": 0.5, "c_mean": 49, "c_std": 1.5}
    r, ms = llamar("POST", "/monitor", params=params, timeout=90)
    mostrar(r, ms, ["nivel_alerta", "diagnostico"])
    d = r.json()
    print(f"   Límites:  {d['limits']}")
    print(f"   Actuales: {d['current_means']}")
    print(f"   Alertas:  {d['alerts']}")
    assert r.status_code == 200 and d["nivel_alerta"] == "PLANIFICAR"
    return ms


@prueba("8. GET /trayectoria/58  (día 58 de la simulación → DETENER)")
def t_trayectoria():
    r, ms = llamar("GET", "/trayectoria/58")
    mostrar(r, ms, ["nivel_alerta", "diagnostico", "tasa_falla_modelo"])
    assert r.json()["nivel_alerta"] == "DETENER"
    return ms


@prueba("9. Seguridad: endpoint protegido sin API key → 401 (o 503 si no hay clave en el servidor)")
def t_sin_clave():
    r, ms = llamar("GET", "/historial")
    mostrar(r, ms)
    print(f"   detalle: {r.json()['detail']}")
    assert r.status_code in (401, 503)
    return ms


@prueba("10. Seguridad: cabeceras HTTP del panel")
def t_cabeceras():
    r, ms = llamar("GET", "/")
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Strict-Transport-Security"):
        print(f"   {h}: {r.headers.get(h, '(no presente)')}")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    return ms


@prueba("11. Observabilidad: GET /metrics (Prometheus) y /ops/metricas")
def t_metricas():
    r, ms = llamar("GET", "/metrics")
    lineas = [l for l in r.text.splitlines() if l.startswith(("http_requests_total", "modelo_predicciones_total", "proceso_"))]
    print("   " + "\n   ".join(lineas[:6]))
    m = llamar("GET", "/ops/metricas")[0].json()
    print(f"   peticiones={m['peticiones_total']} · p50={m['latencia_ms']['p50']} ms · p95={m['latencia_ms']['p95']} ms · memoria={m['memoria_mb']} MB")
    assert r.status_code == 200 and "http_requests_total" in r.text
    return ms


@prueba("12. Integración: lote CSV con API key (solo si la variable API_KEY está definida)")
def t_lote_csv():
    if not API_KEY:
        print("   (omitida: define API_KEY para probarla)")
        return 0
    csv = "temperatura;vibracion;corriente\n72;2,0;47\n84;3,6;50,5\n93;5,8;54\nerror;1;40\n"
    r, ms = llamar("POST", "/predict/lote/csv", headers={"X-API-Key": API_KEY},
                   files={"archivo": ("turno.csv", csv.encode(), "text/csv")})
    mostrar(r, ms)
    d = r.json()
    print(f"   resumen: {d['resumen']}")
    print(f"   filas inválidas: {d['filas_invalidas']}")
    assert r.status_code == 200 and d["resumen"]["n_lecturas"] == 3
    return ms


if __name__ == "__main__":
    print(f"\n🔗 Base URL: {BASE}\n")
    for t in (t_health, t_version, t_predict_guia, t_predict_sano, t_predict_falla, t_predict_invalido,
              t_monitor, t_trayectoria, t_sin_clave, t_cabeceras, t_metricas, t_lote_csv):
        t()
    print("═" * 72)
    ok = sum(1 for _, b, _, _ in RESULTADOS if b)
    print(f"Resultado: {ok}/{len(RESULTADOS)} pruebas superadas")
    for nombre, b, ms, err in RESULTADOS:
        print(f"  {'✅' if b else '❌'} {nombre[:62]:<62} {ms:>7.0f} ms {err}")
    sys.exit(0 if ok == len(RESULTADOS) else 1)
