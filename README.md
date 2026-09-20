# POL Command Center

Prueba de Concepto (PoC) en forma de aplicación web de página única que simula la operación de una red **POL (Passive Optical LAN)** en tres estadios (Azteca, BBVA y Akron) durante el transcurso de un partido, presentada tal como la vería un operador desde un centro de comando.

La aplicación persigue tres propósitos:

1. **Demostrar** que la arquitectura dimensionada se comporta según lo calculado bajo carga (los KPIs convergen a los valores teóricos).
2. **Explorar** escenarios operativos (OLT caída, pico de capacidad, árbol degradado) mediante inyección manual de fallas.
3. **Defender la inversión** con paneles económicos (CAPEX / OPEX / TCO / break-even) y de SLA (disponibilidad por servicio).

El corazón del sistema es un motor de simulación en Python puro, desacoplado de la capa de presentación, que avanza un reloj de partido (de T-180 a T+135 minutos) y recalcula estado, tráfico, fallas, alarmas y KPIs en cada *tick*. La capa de presentación usa Plotly Dash: el mecanismo `dcc.Interval` + callbacks resuelve de forma nativa la simulación en vivo.

## Alcance

**Lo que es:**

- Aplicación web de página única que simula la red POL de tres estadios durante un partido.
- Presentación orientada a operador de centro de comando: Resumen, Monitoreo, Simulación y Negocio.
- Modelo de comportamiento **agregado** de la red, alimentado por los resultados del dimensionamiento.

**Lo que no es:**

- No habla con equipo real (SNMP / NETCONF / gNMI). Todos los datos son simulados.
- No persiste series de tiempo: el historial se pierde al reiniciar el contenedor.
- No tiene autenticación, roles ni multiusuario. Todos los visitantes comparten la misma simulación.
- No es responsivo para móvil. Está diseñado para 1920x1080.
- No es un simulador de red de precisión (no es ns-3 ni OMNeT++). Es un modelo agregado, no paquete a paquete.

## Arquitectura

El proyecto aplica un **principio de separación** estricto: el paquete `sim/` no importa nada de Dash. Es Python puro que recibe un estado y devuelve el siguiente, lo que habilita pruebas con `pytest` sin levantar la web, trabajo en paralelo entre motor y UI, y portabilidad del motor.

- **Estado compartido:** existe una sola instancia de `SimState` a nivel de módulo. Todos los navegadores conectados ven la misma simulación (deliberado para una demo en sala).
- **Proceso único:** un solo contenedor Docker, un solo proceso Python, servidor HTTP en el puerto 8050. `gunicorn --workers 1` es **obligatorio** porque el estado vive en memoria del proceso; múltiples workers verían simulaciones divergentes.

```
POL-Project/
├── app/                  # Capa de presentación (Plotly Dash)
│   ├── main.py           # Punto de entrada; expone `server` para gunicorn
│   ├── layout.py         # Retícula de 12 columnas y barra superior persistente
│   ├── callbacks.py      # dcc.Interval (bucle en vivo) + controles del operador
│   ├── components.py     # Componentes reutilizables
│   ├── live.py           # Cableado del bucle en vivo
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
│   ├── topology_*.json   # Topología semilla por estadio (azteca, bbva, akron)
│   ├── prices.yaml       # Precios y parámetros económicos del modelo
│   └── stadium_map.svg   # Mapa del estadio
├── tools/
│   └── gen_topology.py   # Generador offline de las topologías semilla
├── tests/                # Suite de pytest + property-based (hypothesis)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Requisitos previos

- Python 3.12
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

Para un arranque tipo producción con gunicorn (Linux/macOS o WSL):

```bash
gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8050 app.main:server
```

## Ejecución con Docker

```bash
docker compose up --build
```

Esto construye la imagen y expone el servicio en el puerto 8050. Variables de entorno configurables (ver `docker-compose.yml`):

| Variable      | Por defecto | Descripción                                  |
| ------------- | ----------- | -------------------------------------------- |
| `POL_ENV`     | `demo`      | Entorno de ejecución                         |
| `POL_TICK_MS` | `1000`      | Milisegundos por *tick* del `dcc.Interval`   |
| `POL_SEED`    | `42`        | Semilla para la simulación estocástica       |

## Datos de configuración

El comportamiento del modelo se controla desde `data/` sin tocar código:

- **`prices.yaml`** — precios y parámetros económicos (CAPEX, OPEX anual, horizonte de TCO, comparativa contra cobre, supuestos de propuesta de valor). Editar cualquier valor actualiza los paneles económicos automáticamente.
- **`topology_*.json`** — topología semilla de cada estadio, generada offline por `tools/gen_topology.py`. La aplicación **nunca** genera topología en runtime; solo la lee al arrancar.

Ambos módulos aplican **fail-fast**: si un archivo falta, no se puede parsear o viola sus invariantes (por ejemplo, la conservación de ONTs), el arranque aborta antes de servir cifras inventadas.

## Pruebas

La suite combina pruebas unitarias con pruebas basadas en propiedades (`hypothesis`).

```powershell
pytest
```

Cubre, entre otros, la conservación de ONTs, la convergencia de SLA, el modelo económico y el punto de equilibrio, el comportamiento del reloj, la propagación de fallas con y sin protección, y la correlación de alarmas.

## Stack

- **Presentación:** Dash 2.18, Dash Bootstrap Components 1.6, Plotly 5.24
- **Datos y cálculo:** NumPy 2.1, pandas 2.2, PyYAML 6.0
- **Servidor:** Gunicorn 23.0
- **Pruebas:** pytest, hypothesis

## Notas

Este es un proyecto de PoC orientado a una demostración de sala. No está pensado para uso multiusuario ni producción real. La documentación de diseño y requisitos vive en `.kiro/specs/pol-command-center/`.
