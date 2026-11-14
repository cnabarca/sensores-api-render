# Semana 3 — Monitoreo predictivo en la nube (FastAPI + Docker + Render)

**Caso de estudio (continuación de la Semana 2):** un motor de inducción de 30 kW acoplado a una bomba centrífuga (Grupo 2, ISO 10816-3), supervisado con tres sensores: **temperatura del rodamiento**, **vibración** y **corriente**.

El modelo de la Semana 2 se convierte en un **microservicio en la nube**. Una API REST (FastAPI) corre en un contenedor Docker publicado en **Render** y se redespliega automáticamente con cada `git push` (CI/CD). El mismo servicio entrega un **panel web propio**, que reemplaza al dashboard Gradio de la guía.

**URL pública:** https://sensores-api-render-5ouo.onrender.com
(Plan Free: tras ~15 min sin tráfico el servicio se suspende y la primera petición tarda ~30–60 s).

---

## Qué se mejoró respecto de la guía

| Aspecto | Guía (Semana 3) | Este proyecto |
|---|---|---|
| Modelo | 2 variables, RandomForest, umbral 0,5 | 3 variables, 3 modelos comparados en CV, **umbral de mínimo costo** (FN = 8000 USD, FP = 300 USD) |
| Predicción | `prediccion` + `prob_retraso` | + zona normativa A–D por variable, recomendación de mantenimiento |
| Monitoreo | regla ±2σ | drift K-S + límites k·σ + zonas ISO + modelo → 5 niveles de alerta y diagnóstico del modo de falla; reporte **Evidently** bajo demanda |
| Dashboard | Gradio en `/ui` | Panel HTML/JS sin dependencias (6 pestañas), en `/` y `/ui` |
| Seguridad | CORS abierto | API key (`X-API-Key`) desde variable de entorno, límite de peticiones por IP (429), validación (422), cabeceras CSP/nosniff/HSTS, usuario no root, límites de tamaño |
| Observabilidad | — | Log JSON por petición con `X-Request-ID`, `/metrics` (Prometheus), `/ops/metricas`, `/ops/logs`, `/version` (commit desplegado) |
| Integración | — | Predicción por lotes desde **CSV** o JSON, historial de predicciones |
| CI/CD | Auto-deploy de Render | + **GitHub Actions**: pytest + `docker build` + smoke test con 512 MB antes del despliegue |
| Rendimiento en 0,1 CPU | — | Inferencia del bosque **vectorizada** (73× más rápida, idéntica a scikit-learn) y Evidently diferido: `/predict` ≈ 20–100 ms, `/monitor` ≈ 0,4 s |
| Compatibilidad | — | `POST /predict?temperatura=70&vibracion=0.5` y `POST /monitor?size=…&t_mean=…` de la guía siguen funcionando |

---

## Estructura

```
sensores-api-render/
├── Dockerfile                 # python:3.11-slim, usuario no root, entrena en el build, $PORT de Render
├── main.py                    # API FastAPI: endpoints, middleware de seguridad/observabilidad, panel
├── train_model.py             # Paso 1: genera datos y entrena (se ejecuta dentro del docker build)
├── test_api.py                # Cliente externo: prueba la API local o en Render (12 pruebas)
├── motor/
│   ├── config.py              # Única fuente de verdad: equipo, umbrales, escenarios, ajustes por variables de entorno
│   ├── simulacion.py          # Dataset, lotes y trayectoria de 60 días hasta la falla
│   ├── entrenamiento.py       # Comparación en CV, umbral por costo, holdout, bundle .pkl
│   ├── monitoreo.py           # Drift (K-S / Evidently), k·σ, zonas, niveles y diagnóstico
│   ├── inferencia.py          # Bosque vectorizado (optimización para 0,1 CPU, verificada)
│   ├── seguridad.py           # API key, limitador de tasa, cabeceras de seguridad
│   └── observabilidad.py      # Logs JSON, métricas Prometheus, datos del despliegue
├── static/                    # Panel web: index.html, styles.css, app.js
├── tests/test_app.py          # 11 pruebas automáticas (se ejecutan en GitHub Actions)
├── .github/workflows/ci.yml   # CI: pytest → docker build → smoke test
├── requirements.txt           # Producción (versiones fijadas)
└── requirements-dev.txt       # + pytest, httpx, requests
```

`data/`, `models/` y `reports/` se generan al ejecutar y no se versionan.

---

## Ejecución local

```bash
pip install -r requirements-dev.txt
python train_model.py
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
python -m pytest -q                      # o: python -m tests.test_app
python test_api.py                       # cliente externo contra http://localhost:8000
```

Panel: http://localhost:8000/ · Swagger: http://localhost:8000/docs

## Docker (simulando el plan Free de Render)

```bash
docker build -t sensores-api .
docker run --rm -p 8000:10000 -m 512m --cpus 0.1 -e API_KEY=clave-local sensores-api
```

## Despliegue en Render

1. Web Service → repositorio `sensores-api-render`, rama `main`, runtime **Docker**, plan **Free**.
2. **Environment** → agregar la variable secreta `API_KEY` (habilita los endpoints de integración).
3. **Settings → Health Check Path** → `/health`.
4. Opcional: **Settings → Auto-Deploy → After CI Checks Pass**, para que solo se despliegue un commit con GitHub Actions en verde.

Prueba contra la nube:

```bash
python test_api.py https://sensores-api-render-5ouo.onrender.com
```

---

## Endpoints

| Método | Ruta | Descripción | Acceso |
|---|---|---|---|
| GET | `/` · `/ui` | Panel web | público |
| GET | `/health` | Estado del servicio y del modelo | público |
| GET | `/version` | Commit, rama, entorno, instancia, memoria | público |
| GET | `/equipo` · `/modelo` · `/escenarios` | Ficha técnica, model card, escenarios | público |
| POST | `/predict` | Predicción individual (query params; `corriente` opcional) | público |
| POST | `/monitor` | Lote simulado → drift, k·σ, zonas, nivel, diagnóstico | público |
| GET | `/trayectoria/{dia}` | Día 0–59 de la simulación hasta la falla | público |
| GET | `/report` | Reporte HTML de Evidently del último lote (bajo demanda) | público |
| POST | `/predict/lote/csv` | Predicción por lotes desde un CSV | **X-API-Key** |
| POST | `/predict/lote` | Predicción por lotes en JSON | **X-API-Key** |
| POST | `/monitor/lecturas` | Monitoreo con lecturas reales (≥ 30) | **X-API-Key** |
| GET | `/historial` | Últimas predicciones atendidas | **X-API-Key** |
| GET | `/metrics` | Métricas en formato Prometheus | público |
| GET | `/ops/metricas` · `/ops/logs` · `/ops/seguridad` | Métricas JSON, logs y postura de seguridad | público |

## Niveles de alerta

| Nivel | Disparador | Acción |
|---|---|---|
| NORMAL | Ninguna capa activa | Operación sin restricción |
| VIGILANCIA | Drift K-S en alguna variable | Aumentar la frecuencia de inspección |
| PREALERTA | Media del lote > μ_ref + k·σ_ref | Diagnóstico y orden de trabajo |
| PLANIFICAR | Alguna variable en zona C | Planificar mantenimiento |
| DETENER | Alguna variable en zona D | Detener el equipo (con supervisión humana) |

## Variables de entorno

| Variable | Defecto | Uso |
|---|---|---|
| `PORT` | 10000 | Puerto que asigna Render |
| `API_KEY` | (vacía) | Clave de los endpoints de integración; vacía → responden 503 |
| `RATE_LIMIT_POR_MIN` | 600 | Peticiones por minuto e IP en `/predict*`, `/monitor*`, `/trayectoria` |
| `CORS_ORIGINS` | `*` | Orígenes permitidos (en producción, solo dominios propios) |
| `MAX_LOTE` | 5000 | Filas máximas por lote |

## Limitaciones conocidas (plan Free)

- **Almacenamiento efímero:** el historial, las métricas y el reporte de Evidently viven en la memoria y el disco del contenedor; se pierden al redesplegar o tras la suspensión. En producción irían a una base de datos (p. ej., PostgreSQL) y a Prometheus/Grafana.
- **0,1 CPU compartida:** por eso Evidently se carga solo al abrir `/report` (la primera vez tarda ~1 min) y la inferencia está vectorizada.
- **Datos simulados:** los umbrales de temperatura y corriente deben validarse con el fabricante del equipo.
