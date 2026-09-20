# 01 — Arquitectura

## Visión general

El POL Command Center es una aplicación web de página única construida sobre
**Plotly Dash**. Simula la operación de una red POL en tres estadios (Azteca,
BBVA, Akron) durante un partido y la presenta como la vería un operador de un
centro de comando.

El sistema se divide en dos capas con una frontera clara:

```
┌───────────────────────────────────────────────┐
│  app/  — Capa de presentación (Plotly Dash)     │
│  main.py · layout.py · callbacks.py · live.py   │
│  components.py · theme.py · views/              │
└───────────────────────┬─────────────────────────┘
                         │  lee (nunca al revés)
                         ▼
┌───────────────────────────────────────────────┐
│  sim/  — Motor de simulación (Python puro)      │
│  state · engine · traffic · faults · alarms     │
│  kpis · economics · topology                    │
└───────────────────────┬─────────────────────────┘
                         │  carga al arrancar
                         ▼
┌───────────────────────────────────────────────┐
│  data/ — Datos de entrada                       │
│  topology_*.json · prices.yaml · stadium_map    │
└───────────────────────────────────────────────┘
```

## Principio de separación

El paquete `sim/` **no importa nada de Dash**. Es Python puro que recibe un
estado y devuelve el siguiente. Esto habilita:

- **Pruebas sin web:** todo el motor se ejercita con `pytest` sin levantar Dash.
- **Trabajo en paralelo:** el motor y la UI evolucionan por separado.
- **Portabilidad:** el motor podría reutilizarse en otro frontend.

La dependencia es unidireccional: `app/` importa de `sim/`; `sim/` nunca importa
de `app/`. La frontera concreta es `app/live.py`, que mantiene la instancia
única del estado y expone lecturas de solo lectura (`snapshot`, `contexto_topbar`,
`alarmas_snapshot`) a las vistas.

## Estado compartido y proceso único

- **Una sola instancia de `SimState`** vive a nivel de módulo en `app/live.py`
  (símbolo `ESTADO`). Todos los navegadores conectados ven la misma simulación:
  es deliberado para una demo de sala.
- **Un solo proceso:** un contenedor Docker, un proceso Python, servidor HTTP en
  el puerto 8050.
- **`gunicorn --workers 1` es obligatorio.** El estado vive en la memoria del
  proceso; varios workers tendrían cada uno su propia `SimState` y verían
  simulaciones divergentes. El Dockerfile fija `--workers 1 --threads 4`: un
  solo estado, varias peticiones concurrentes atendidas por hilos.

## Flujo de un tick (bucle en vivo)

El corazón del funcionamiento en vivo es el mecanismo `dcc.Interval` + callbacks
de Dash:

1. `app/live.py` expone **un único** `dcc.Interval` (`INTERVALO_ID`). Su período
   viene de la variable de entorno `POL_TICK_MS` (por defecto 1000 ms, mínimo 200).
2. Un **callback maestro** en `app/callbacks.py` es el dueño de los indicadores
   de la barra superior. Se dispara con el intervalo y con todos los controles
   del operador, identifica el disparador con `dash.ctx` y:
   - Si fue el intervalo → llama `live.avanzar()`, que ejecuta `engine.tick()`
     **solo si la simulación está corriendo**.
   - Si fue un control → aplica la mutación de estado (play/pausa, velocidad,
     escenario, reset, selector de estadio).
   - Siempre → reconstruye la barra superior leyendo el estado ya calculado.
3. Cada `engine.tick()` avanza el reloj y recalcula, en orden, tráfico → fallas
   → alarmas → KPIs → disponibilidad (ver [02 — Motor de simulación](02-motor-de-simulacion.md)).

Tener un solo callback dueño de esas salidas evita salidas duplicadas (Dash
rechaza dos callbacks que escriban el mismo `Output`) y garantiza el orden
"mutar → renderizar" en el mismo ciclo.

### Regla de solo lectura de KPIs

La capa de presentación **no calcula ningún KPI ni ninguna alarma**. Todo el
cálculo vive en `sim/`; los callbacks y vistas solo leen valores ya calculados
a través de `app/live.py`. Esta regla mantiene la lógica de negocio testeable y
en un único lugar.

## Las cuatro vistas

La aplicación presenta una barra superior persistente y cuatro vistas
(`app/views/`), conmutadas por un callback de ruteo mínimo en `app/main.py`:

- **Resumen** (`resumen.py`) — panorama general de KPIs y estado.
- **Monitoreo** (`monitoreo.py`) — vista operativa de la red y sus alarmas.
- **Simulación** (`simulacion.py`) — controles del operador: play/pausa,
  velocidad, escenario, reset, inyección de fallas y evento de gol.
- **Negocio** (`negocio.py`) — paneles económicos (CAPEX/OPEX/TCO/break-even) y
  la propuesta de valor.

## Arranque y siembra

Al importar `app/live.py`, el estado se **siembra** corriendo el motor unos
pocos ticks (`_TICKS_SEMILLA = 30`) para que el historial tenga puntos y las
gráficas no arranquen vacías. Tras la siembra, el reloj queda pausado en el
estado de arranque de la demo (T-180, 1x). Si la topología no puede cargarse en
un entorno acotado, se degrada a un `SimState()` vacío para que la app siga
levantando (fail-soft de arranque).

## Estructura de carpetas

```
POL-Project/
├── app/                  # Capa de presentación (Plotly Dash)
│   ├── main.py           # Punto de entrada; expone `server` para gunicorn
│   ├── layout.py         # Retícula de 12 columnas y barra superior persistente
│   ├── callbacks.py      # dcc.Interval (bucle en vivo) + controles del operador
│   ├── components.py     # Componentes reutilizables
│   ├── live.py           # Instancia única del estado + frontera con sim/
│   ├── theme.py          # Tokens de estilo
│   └── views/            # Resumen, Monitoreo, Simulación, Negocio
├── sim/                  # Motor de simulación (Python puro, sin Dash)
│   ├── state.py          # SimState + dataclasses de topología (OLT, ONT, etc.)
│   ├── engine.py         # Reloj y bucle `tick`
│   ├── topology.py       # Carga de topología semilla (fail-fast)
│   ├── traffic.py        # Modelo de tráfico agregado
│   ├── faults.py         # Inyección y propagación de fallas
│   ├── alarms.py         # Catálogo y evaluación de alarmas
│   ├── kpis.py           # Cálculo de los 12 KPIs (K1–K12)
│   └── economics.py      # CAPEX / OPEX / TCO / break-even y propuesta de valor
├── data/                 # Datos de entrada
├── tools/                # Generador offline de topologías (gen_topology.py)
├── tests/                # Suite de pytest + property-based (hypothesis)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Lo que el sistema NO es

- No habla con equipo real (SNMP / NETCONF / gNMI): todos los datos son simulados.
- No persiste series de tiempo: el historial se pierde al reiniciar el contenedor.
- No tiene autenticación, roles ni multiusuario.
- No es responsivo para móvil (diseñado para 1920x1080).
- No es un simulador de red paquete a paquete (no es ns-3 ni OMNeT++): es un
  modelo **agregado**.
