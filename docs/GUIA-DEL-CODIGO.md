# Guía del código

Recorrido por el código de NEXUS BI para entender cómo está construido. Los fragmentos son copias literales del repositorio; los comentarios del código están en inglés y la explicación en español.

## Contenido

1. [Cómo está organizado](#1-cómo-está-organizado)
2. [El recorrido de los datos](#2-el-recorrido-de-los-datos)
3. [Pipeline: de los CSV a PostgreSQL](#3-pipeline-de-los-csv-a-postgresql)
4. [Capa analítica en SQL](#4-capa-analítica-en-sql)
5. [Machine learning](#5-machine-learning)
6. [API con FastAPI](#6-api-con-fastapi)
7. [Analista de IA: preguntas en lenguaje natural sin números inventados](#7-analista-de-ia-preguntas-en-lenguaje-natural-sin-números-inventados)
8. [Frontend](#8-frontend)
9. [Tests](#9-tests)

## 1. Cómo está organizado

```
data_pipeline/      Ingesta, validación, limpieza, transformación y carga en PostgreSQL
  setup_db.py       Construye todo desde cero: esquema → pipeline → vistas → modelos
sql/                schema.sql (tablas) · views.sql (capa analítica) · analytics.sql · roles.sql
machine_learning/   segmentation/ · forecasting/ · churn/ · anomaly_detection/ · common.py
ai_analyst/         validation.py (guardas) · sql_generator.py · analyst.py (Claude)
                    local_llm.py (Ollama) · guided.py (biblioteca sin IA)
backend/            main.py · api/ (rutas) · services/ (SQL) · schemas/ (Pydantic) · models/ (conexión)
frontend/           HTML, CSS y JavaScript sin framework, gráficos con Plotly.js
tests/              Tests unitarios y de integración (base de datos y API)
notebooks/          Exploración y evaluación de los modelos, con resultados ejecutados
```

La regla principal: **PostgreSQL es la única fuente de verdad**. Nada está escrito a mano en el frontend: cada cifra, gráfico e insight se calcula consultando la base en el momento de la petición.

## 2. El recorrido de los datos

```
CSV de Olist ─▶ data_pipeline ─▶ PostgreSQL (core, ops)
                                   │
                                   ├─▶ sql/views.sql ─▶ esquema analytics (vistas y funciones)
                                   │
                                   ├─▶ machine_learning ─▶ esquema ml (predicciones)
                                   │
                                   └─▶ FastAPI (solo lectura) ─▶ dashboard
                                                             └─▶ analista de IA (SELECT validado)
```

`python -m data_pipeline.setup_db` ejecuta todo en orden: crea el esquema, carga los datos, crea las vistas (las materializadas necesitan datos) y entrena los modelos (que leen las vistas). Docker usa ese mismo comando en el servicio `init`.

## 3. Pipeline: de los CSV a PostgreSQL

Cinco etapas, cada una en su archivo:

| Etapa | Archivo | Qué hace |
|---|---|---|
| Ingesta | `ingestion.py` | Descarga los CSV si no están y los lee con tipos explícitos |
| Validación | `validation.py` | 105 comprobaciones; cada problema se guarda como un `QualityIssue` |
| Limpieza | `cleaning.py` | Duplicados, ceros perdidos en códigos postales, ciudades mal escritas, fechas imposibles |
| Transformación | `transformation.py` | Construye el modelo normalizado (clientes reales, ubicaciones, calendario) |
| Carga | `load.py` | Escribe en PostgreSQL con `COPY`, todo o nada |

La carga (`data_pipeline/load.py`) es la parte más delicada:

```python
def load_model(conn: psycopg.Connection, model: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Replace the contents of the core tables with ``model``. Returns rows loaded per table."""
    loaded: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(f"core.{t}" for t in CORE_TABLES) + " RESTART IDENTITY CASCADE")
        for step in LOAD_STEPS:
            table, staging = step["table"], f"stg_{step['table']}"
            df = model[table]
            cur.execute(f"CREATE TEMP TABLE {staging} ({step['staging']}) ON COMMIT DROP")
            _copy_frame(cur, staging, df)
            cur.execute(step["insert"])
            if cur.rowcount != len(df):
                raise RuntimeError(
                    f"core.{table}: staged {len(df):,} rows but inserted {cur.rowcount:,}; "
                    "some natural keys did not resolve. Load rolled back.")
```

Paso a paso:

1. **Una sola transacción** (`conn.transaction()`): si algo falla, la base queda exactamente como estaba.
2. **`COPY`** en lugar de `INSERT` fila a fila: carga cientos de miles de filas en segundos.
3. **Tablas de staging temporales** (`ON COMMIT DROP`): los datos entran primero con sus claves naturales (los hashes de Olist) y luego un `INSERT ... SELECT` las traduce a claves enteras con joins.
4. **Conciliación**: si se preparan N filas pero se insertan menos (por ejemplo, un pedido cuyo cliente no existe), se lanza un error y **se revierte toda la carga**.

Las ejecuciones y los problemas de calidad se guardan en una conexión aparte, en modo autocommit, para que una carga fallida también deje su rastro en `ops`.

## 4. Capa analítica en SQL

`sql/views.sql` define el esquema `analytics`, que es lo único que leen la API, los modelos y la IA:

- **`v_reporting_period`** calcula la ventana de datos fiables. El extracto de Olist se apaga después del 23 de agosto de 2018; una consulta de *gaps and islands* sobre el volumen diario detecta dónde empiezan y terminan los datos completos, para no comparar nunca contra meses a medio capturar.
- **`v_orders`** y **`v_customer_summary`** son **vistas materializadas**: precalculan ingresos por pedido, días de entrega, retrasos y el historial de cada cliente.
- **`kpi_summary(inicio, fin, estado, categoría)`** es una función SQL que devuelve las tarjetas de KPIs comparando cualquier periodo con el anterior (meses de calendario contra meses de calendario).

Así la API no repite lógica: llama a la función y le da formato (`backend/services/sales.py`):

```python
@cached
def kpis(start: date, end: date, state: str | None, category: int | None) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM analytics.kpi_summary(%(start)s, %(end)s, %(state)s, %(category)s::smallint)",
        {"start": start, "end": end, "state": state, "category": category},
    )
```

Los parámetros siempre van como `%(nombre)s`: psycopg los envía aparte del SQL, así que no hay inyección SQL posible.

## 5. Machine learning

Cuatro módulos en `machine_learning/`, todos con la misma estructura: leer de las vistas, entrenar varios candidatos, compararlos con modelos simples de referencia (*baselines*) y guardar el ganador en el esquema `ml`.

| Módulo | Qué produce | Tabla |
|---|---|---|
| `segmentation/rfm.py` | Segmento de cada cliente (VIP, Loyal, Potential, At Risk, Lost) | `ml.customer_segments` |
| `forecasting/forecast.py` | Pronóstico diario de 8 semanas con intervalo del 80 % | `ml.sales_forecast` |
| `churn/model.py` | Probabilidad de abandono y factores de riesgo | `ml.churn_scores` |
| `anomaly_detection/detect.py` | Días, productos y clientes fuera de lo normal | `ml.anomalies` |

Los umbrales de la segmentación no son inventados: salen del ciclo real de recompra (el 75 % de las recompras ocurre antes de 168 días y el 90 % antes de 280). Así se asignan los segmentos (`segmentation/rfm.py`):

```python
def assign_segments(df: pd.DataFrame, gap: dict[str, float]) -> pd.Series:
    repeat = df["frequency"] >= 2
    conditions = [
        df["recency_days"] > gap["p90"],
        df["recency_days"] > gap["p75"],
        repeat & (df["m_score"] == 5),
        repeat,
    ]
    return pd.Series(np.select(conditions, ["Lost", "At Risk", "VIP", "Loyal"], default="Potential"),
                     index=df.index)
```

`np.select` evalúa las condiciones en orden y asigna la primera que se cumple, así que "Lost" tiene prioridad sobre "VIP".

Todos los modelos guardan sus resultados con `ModelRun` (`machine_learning/common.py`), un *context manager* que abre una transacción. Al terminar sin errores desactiva la ejecución anterior y activa la nueva:

```python
def __exit__(self, exc_type, exc, tb) -> None:
    try:
        if exc_type is not None:
            self._conn.rollback()
            return
        with self._conn.cursor() as cur:
            cur.execute("UPDATE ml.model_runs SET is_active = FALSE WHERE model_name = %s AND is_active",
                        (self.model_name,))
            cur.execute(
                "UPDATE ml.model_runs SET params = %s, metrics = %s, is_active = TRUE WHERE run_id = %s",
                (json.dumps(_json_safe(self.params)), json.dumps(_json_safe(self.metrics)), self.run_id))
```

Si el entrenamiento falla a mitad, el dashboard sigue mostrando el modelo anterior completo, nunca uno a medias.

## 6. API con FastAPI

- `backend/main.py` crea la aplicación, abre el pool de conexiones y sirve el frontend.
- `backend/api/` tiene un archivo de rutas por área (`sales.py`, `customers.py`, `geo.py`, `ml.py`, `ai.py`...).
- `backend/services/` contiene el SQL; `backend/schemas/` los modelos Pydantic que validan y documentan cada respuesta.
- La documentación interactiva se genera sola en `/docs`.

**Toda la API es de solo lectura**, y no por convención: cada conexión del pool se configura así (`backend/models/database.py`):

```python
def _configure(conn: psycopg.Connection) -> None:
    settings = get_settings()
    conn.adapters.register_loader("numeric", FloatLoader)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute(f"SET statement_timeout = {int(settings.db_statement_timeout_ms)}")
    conn.commit()
```

- `default_transaction_read_only = on`: PostgreSQL rechaza cualquier escritura que intentara la API.
- `statement_timeout`: ninguna consulta puede tardar más del límite configurado.
- Los resultados se guardan en una caché en memoria (`services/cache.py`, decorador `@cached`) durante `API_CACHE_TTL_SECONDS`.

## 7. Analista de IA: preguntas en lenguaje natural sin números inventados

```
PREGUNTA → MODELO DE LENGUAJE → SQL → VALIDACIÓN → POSTGRESQL (solo lectura) → RESULTADO → RESPUESTA → VERIFICACIÓN DE CIFRAS
```

Hay tres modos, que se eligen solos: **Claude** si hay `ANTHROPIC_API_KEY`, **Ollama** (un modelo local y gratuito) si está instalado, y si no hay ninguno, el **modo guiado** (`guided.py`), que responde 8 preguntas de negocio con SQL preparado.

La pieza clave es `ai_analyst/validation.py`. No busca palabras peligrosas en el texto: **analiza el árbol sintáctico** del SQL con sqlglot:

```python
if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
    raise SQLValidationError("Only SELECT queries are allowed.")
for node in tree.walk():
    if isinstance(node, FORBIDDEN_NODES):
        raise SQLValidationError(f"'{node.key.upper()}' is not allowed: the analyst is read-only.")

for fn in tree.find_all(exp.Func):
    name = (fn.sql_name() if not isinstance(fn, exp.Anonymous) else fn.name).lower()
    if name in FORBIDDEN_FUNCTIONS:
        raise SQLValidationError(f"The function {name}() is not allowed.")
```

Qué comprueba, en orden:

1. Exactamente **una** sentencia, y que sea un `SELECT`.
2. Ningún nodo de escritura en todo el árbol (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `SELECT INTO`, `COPY`...), aunque esté escondido dentro de una subconsulta.
3. Ninguna función peligrosa (`pg_sleep`, `pg_read_file`, `dblink`, `set_config`...).
4. Solo tablas de una **lista permitida** de vistas de `analytics`; los catálogos del sistema quedan fuera.
5. Envuelve la consulta en un `LIMIT` exterior.

Después, `run_sql` (`ai_analyst/sql_generator.py`) la ejecuta en una transacción de solo lectura, con tiempo límite, y la revierte siempre:

```python
with _connect() as conn:
    conn.execute("SET TRANSACTION READ ONLY")
    conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
    cur = conn.execute(validated.executable)
    rows = cur.fetchall()
    step.columns = [d.name for d in cur.description]
    conn.rollback()
```

Por último, `unverified_numbers` revisa la respuesta final: **cada cifra que menciona debe coincidir con algún valor devuelto por las consultas** (con el redondeo que implica el texto). Si el modelo escribe un número que no sale de los datos, el usuario ve una advertencia. Y siempre se muestran el SQL y la tabla de resultados junto a la respuesta.

## 8. Frontend

- **HTML, CSS y JavaScript sin framework** (módulos ES), con gráficos de Plotly.js.
- `frontend/index.html` es la portada con datos en vivo de la API; `frontend/dashboard.html` es la aplicación.
- `js/app.js` hace el enrutado entre vistas; `js/state.js` guarda los filtros (periodo, estado, categoría); `js/api.js` hace las llamadas.
- Cada pantalla es un módulo en `js/views/` (`overview.js`, `geography.js`, `churn.js`, `ask.js`...).
- `css/tokens.css` define los colores y la tipografía con variables CSS, con modo claro y oscuro.

## 9. Tests

105 tests con **pytest**:

| Archivo | Qué prueba |
|---|---|
| `tests/test_pipeline.py` | Limpieza y transformación: códigos postales, ciudades, fechas, duplicados |
| `tests/test_ai_validation.py` | El validador de SQL contra 16 ataques (escrituras escondidas, funciones peligrosas, catálogos) y la verificación de cifras |
| `tests/test_ml.py` | Orden de las reglas RFM, métricas del pronóstico y que el modelo de churn no use información posterior a la fecha de corte |
| `tests/test_integration.py` | Contra la base real: los ingresos coinciden con los CSV, cada modelo activo tiene resultados, la API responde, valida filtros y no permite inyección SQL |

Los tests de integración se saltan solos si la base no está cargada.

```bash
python -m pytest                                  # todos los tests
python -m pytest tests/test_ai_validation.py -v   # solo las guardas de la IA
```
