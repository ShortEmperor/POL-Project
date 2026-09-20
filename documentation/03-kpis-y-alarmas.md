# 03 — KPIs y alarmas

Toda la lógica de KPIs vive en `sim/kpis.py` y la de alarmas en `sim/alarms.py`.
La capa de presentación solo **lee** valores ya calculados; nunca recalcula.

## KPIs (`sim/kpis.py`)

`kpis.calcular(state)` calcula los 12 KPIs del estadio activo y empuja cada valor
al historial (`serie → deque(maxlen=600)`, una serie por código `K1`..`K12`).
Lee los agregados de tráfico que dejó `traffic.actualizar` (nunca recalcula
tráfico). Si aún no hay estadio activo o agregados (antes del primer tick), los
KPIs afectados devuelven `0.0` en lugar de fallar.

### Catálogo de los 12 KPIs

| Código | Nombre | Fórmula | Unidad | Umbral |
| ------ | ------ | ------- | ------ | ------ |
| K1 | Utilización PON pico | `max(carga/cap_util)` por puerto | % | ≤ 60% |
| K2 | Utilización PON media | media de `carga/cap_util` por puerto | % | ≤ 50% |
| K3 | Throughput total | Σ bw asignado de todas las ONTs | Gbps | — |
| K4 | ONTs en línea | conteo de estado ∈ {EN_LINEA, DEGRADADO} | conteo | ≥ 99.9% |
| K5 | Disponibilidad por servicio | `ticks_en_linea/ticks_totales` ponderado | % | según protección |
| K6 | Potencia óptica media | media de `potencia_optica_dbm` | dBm | ≥ −28 dBm |
| K7 | Alarmas activas | conteo de alarmas ∈ {NUEVA, ACTIVA, RECONOCIDA} | conteo | — |
| K8 | Alarmas críticas | conteo de alarmas críticas activas | conteo | 0 |
| K9 | Latencia estimada | modelo carga→latencia | ms | ≤ 10 ms |
| K10 | Servicios afectados | conteo de servicios con ONTs FUERA | conteo | 0 |
| K11 | Margen de capacidad | `1 − K1` | % | ≥ 40% |
| K12 | Tiempo de conmutación | último switch de protección | ms | ≤ 50 ms |

El catálogo (`CATALOGO`) es la **fuente única de metadatos**: nombre, fórmula,
unidad, umbral y sentido de comparación (`<=`, `>=`, `==` o `None`). La función
`verificar_completitud()` asegura que estén definidos exactamente K1..K12.

### Notas de cálculo

- **K1 / K2** derivan de `util_por_puerto`. K1 es el máximo; K2 la media.
- **K3** convierte `throughput_bajada_mbps` a Gbps (÷ 1000).
- **K4 / K6 / K10** recorren las ONTs una sola vez. K6 sin ONTs cae al nominal
  −22 dBm (no reporta 0 dBm irreal).
- **K5** usa la media global ponderada por ticks (`disponibilidad_servicio`).
- **K9 — modelo carga→latencia:** análogo a M/M/1, la latencia crece con la
  utilización:

  ```
  latencia(u) = LAT_BASE_MS + LAT_COLA_MS · u / (1 − min(u, U_MAX))
  ```

  con `LAT_BASE_MS = 2.0`, `LAT_COLA_MS = 1.5`, `U_MAX = 0.98`. A ~50.6% da
  ≈ 3.5 ms; cerca del 85% se acerca al umbral de 10 ms y dispara `LAT-001`.
- **K12** lee `ultimo_tiempo_conmutacion_ms`, que escribe el módulo de fallas al
  conmugar con éxito (8 ms). Por defecto 0.0 (sin conmutaciones aún).

## Alarmas (`sim/alarms.py`)

`alarms.evaluar(state)` se invoca cada tick (después de tráfico y fallas).
Detecta condiciones del catálogo contra el estado actual, gestiona el ciclo de
vida de cada alarma y correlaciona las derivadas bajo su causa raíz.

### Catálogo de las 10 alarmas

| Código | Severidad | Disparo | Cierre (con histéresis) |
| ------ | --------- | ------- | ----------------------- |
| PON-001 | mayor | utilización de puerto > 60% | < 55% |
| PON-002 | crítica | utilización > 90% (saturación) | < 85% |
| ONT-001 | mayor | ONT pasa a FUERA | ONT vuelve a EN_LINEA |
| ONT-002 | menor | ONT DEGRADADO o potencia < −28 dBm | EN_LINEA y potencia ≥ −28 dBm |
| OLT-001 | crítica | chasis OLT caído | chasis restaurado |
| OLT-002 | mayor | tarjeta caída | tarjeta restaurada / conmutación |
| OLT-003 | crítica | corte de fibra troncal | fibra restaurada / conmutación Type C |
| PoE-001 | menor | presupuesto PoE excedido en un nodo | carga normaliza |
| WIFI-001 | menor | densidad WiFi > capacidad AP | densidad baja |
| LAT-001 | menor | latencia estimada > 10 ms | < 8 ms |

La **histéresis** (banda muerta entre el umbral de disparo y el de cierre) evita
que una alarma parpadee alrededor del umbral. Por eso `cierre` no es
simplemente `not disparo`.

### Ciclo de vida

```
[*] → NUEVA        (la condición se cumple)
NUEVA → ACTIVA     (persiste tras 1 tick)
ACTIVA → RECONOCIDA (el operador reconoce, alarms.reconocer)
ACTIVA → CERRADA   (la condición desaparece, auto)
RECONOCIDA → CERRADA
```

Los estados **vivos** son {NUEVA, ACTIVA, RECONOCIDA}; son los que cuentan K7 y
K8. Las CERRADA se conservan en `state.alarmas` como **histórico de sesión** (la
UI filtra por estado). `alarms.reconocer(alarma)` mueve ACTIVA → RECONOCIDA (ack
del operador); es no-op desde cualquier otro estado.

### Correlación bajo causa raíz

Las alarmas raíz son **OLT-001** (chasis) y **OLT-003** (corte troncal). Mientras
una raíz esté viva, cada **ONT-001** derivada que dependa de ella queda marcada
con `raiz_id` y **no se lista individualmente**: el operador ve la causa, no
cientos de ONTs.

- **Pertenencia:** una ONT-001 pertenece a OLT-001 si comparte OLT; pertenece a
  OLT-003 si es del mismo árbol. La relación se deduce del id jerárquico
  (`OLT-<estadio>-T..-P..-A-O..`).
- **Raíz única (ante varias):** si una ONT-001 podría atribuirse a más de una
  raíz, se agrupa bajo **una sola** — la de mayor severidad y, a igualdad, la
  primera detectada.
- **Cierre en cascada:** al cerrarse una raíz, sus derivadas se cierran también.
- **Desagrupado:** si una derivada deja de pertenecer a cualquier raíz viva, se
  limpia su `raiz_id` para volver a listarse sola.

`correlacionar()` es idempotente: reejecutarla sobre un estado ya correlacionado
no cambia nada.

### Consultas para la UI

- `alarmas_activas(state)` — alarmas **vivas no derivadas** (lo que muestra la
  tabla de activas y el contador de la barra superior).
- `historico_alarmas(state)` — todas las alarmas de la sesión (vivas + cerradas).

Ambas se leen desde `app/live.py` (`alarmas_snapshot`, `_conteo_alarmas`).
