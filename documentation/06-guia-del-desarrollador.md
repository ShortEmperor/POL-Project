# 06 — Guía del desarrollador

Cómo instalar, ejecutar y probar el POL Command Center, más las convenciones que
conviene respetar al modificar el código.

## Requisitos previos

- **Python 3.12**
- (Opcional) Docker y Docker Compose para el despliegue en contenedor

## Instalación y ejecución local

```powershell
# Crear y activar entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar en modo desarrollo
python -m app.main
```

La aplicación queda disponible en http://localhost:8050.

En modo desarrollo, `app/main.py` levanta el servidor de Dash con `debug=True`.
Para un arranque tipo producción con gunicorn (Linux/macOS o WSL):

```bash
gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8050 app.main:server
```

> **`--workers 1` es obligatorio.** El estado vive en la memoria del proceso;
> varios workers verían simulaciones divergentes. Los hilos (`--threads 4`)
> atienden varias peticiones sobre el mismo estado compartido.

## Ejecución con Docker

```bash
docker compose up --build
```

Construye la imagen (Python 3.12 slim, usuario no-root) y expone el servicio en
el puerto 8050. Las variables de entorno se ajustan en `docker-compose.yml` (ver
[05 — Datos y configuración](05-datos-y-configuracion.md)).

## Pruebas

La suite combina pruebas unitarias con pruebas basadas en propiedades
(`hypothesis`).

```powershell
pytest
```

Los archivos en `tests/` cubren, entre otros:

| Archivo | Qué verifica |
| ------- | ------------ |
| `test_property_conservacion.py` | Conservación de ONTs en la topología. |
| `test_property_reloj.py` | Reloj monótono y topado en +135. |
| `test_property_disponibilidad.py` | Invariantes de acumulación de disponibilidad. |
| `test_convergencia_sla.py` | Convergencia de la disponibilidad al objetivo por protección. |
| `test_kpis.py` | Cálculo y completitud del catálogo K1–K12. |
| `test_traffic.py` | Curvas de llegada, factores y contención de tráfico. |
| `test_faults_proteccion.py` / `test_faults_sin_proteccion.py` | Conmutación con y sin protección. |
| `test_faults_estocastica.py` | Modelo estocástico de fallas (opt-in). |
| `test_alarms_correlacion.py` / `test_alarms_cascada.py` | Correlación bajo raíz y cierre en cascada. |
| `test_economics.py` / `test_property_break_even.py` / `test_property_presupuesto.py` | CAPEX/OPEX/TCO, break-even y presupuesto. |
| `test_topology.py` / `test_topology_failfast.py` / `test_gen_topology.py` | Carga de topología, fail-fast y generador offline. |
| `test_callbacks_live.py` | Frontera UI ↔ motor (`app/live.py`). |

> `hypothesis` guarda su base de datos de ejemplos en `.hypothesis/`.

### Ejecutar un subconjunto

```powershell
pytest tests/test_kpis.py            # un archivo
pytest -k "correlacion"              # por nombre
pytest -q                            # salida compacta
```

## Convenciones al modificar el código

Estas reglas mantienen la arquitectura descrita en [01](01-arquitectura.md) y
[02](02-motor-de-simulacion.md):

1. **`sim/` no importa de `app/` ni de Dash.** El motor debe poder ejecutarse y
   probarse sin levantar la web. Si necesitas datos del estado en la UI, exponlos
   como lectura en `app/live.py`.
2. **El cálculo vive en `sim/`.** Los callbacks y vistas **solo leen** valores ya
   calculados (KPIs, alarmas, economía). No pongas fórmulas en la capa de
   presentación.
3. **Un solo `dcc.Interval`.** El bucle en vivo tiene un único intervalo
   (`app/live.py`); no crees otro en una vista. Un único callback maestro es
   dueño de los indicadores de la barra superior (evita salidas duplicadas).
4. **Precios y topología nunca hardcodeados.** Todo número económico sale de
   `data/prices.yaml`; toda cantidad, del inventario de topología. Añadir un
   número a mano rompe el criterio del modelo.
5. **Fail-fast en la carga, fail-soft en la frontera.** La carga de datos aborta
   ante entradas inválidas; las lecturas de `app/live.py` degradan sin propagar
   excepciones a la web.
6. **Determinismo.** El ruido y las fallas estocásticas se siembran con
   `state.semilla`; conserva ese patrón para que las corridas sean reproducibles.

## Stack

- **Presentación:** Dash 2.18, Dash Bootstrap Components 1.6, Plotly 5.24
- **Datos y cálculo:** NumPy 2.1, pandas 2.2, PyYAML 6.0
- **Servidor:** Gunicorn 23.0
- **Pruebas:** pytest, hypothesis

## Referencias de diseño

La documentación de diseño y requisitos original vive en
`.kiro/specs/pol-command-center/`. Los docstrings del código citan secciones de
ese diseño (p.ej. "design §6.2") y requisitos ("Req 8.5") como trazabilidad.
