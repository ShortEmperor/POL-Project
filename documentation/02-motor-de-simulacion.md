# 02 — Motor de simulación (`sim/`)

El motor es Python puro (sin Dash). Recibe un `SimState` y avanza la simulación
un paso con `engine.tick()`. Este documento describe el estado, el reloj, el
bucle, el modelo de tráfico y el modelo de fallas.

## Modelo de estado (`sim/state.py`)

### Jerarquía de topología

La red se modela con dataclasses anidadas que reflejan la topología física:

```
Estadio
└── OLT
    └── Tarjeta (línea PON)
        └── PuertoPON
            └── ArbolPON (splitter 1:32 / 1:64)
                └── ONT (equipo terminal por servicio)
```

Cada nivel guarda su `id`, su `estado` y, donde aplica, su esquema de protección
y capacidad. Campos relevantes:

- **`ONT`** — `servicio`, `estado`, `proteccion`, `bw_demandado_mbps`,
  `bw_asignado_mbps`, `potencia_optica_dbm` (nominal −22 dBm), y los
  acumuladores de disponibilidad `ticks_totales` / `ticks_en_linea`.
- **`PuertoPON`** — `capacidad_mbps` (nominal, p.ej. 10 Gbps XGS-PON),
  `capacidad_util_mbps` (utilizable tras overhead, p.ej. 8.5 Gbps) y
  `es_reserva` (puerto en espera para conmutación Type B).
- **`ArbolPON`** — `ratio_split` y `atenuacion_db` (degradación óptica).

### Enumeraciones

| Enum | Valores | Significado |
| ---- | ------- | ----------- |
| `Estado` | `EN_LINEA`, `DEGRADADO`, `FUERA`, `RESERVA` | Estado operativo de un elemento. |
| `Proteccion` | `NINGUNA`, `TYPE_B`, `TYPE_C` | Sin redundancia / puerto OLT redundante / redundancia completa. |
| `Servicio` | `PALCOS`, `WIFI`, `CCTV`, `TORNIQUETES`, `POS`, `SIGNAGE`, `MACRO`, `VOZ`, `PRENSA`, `ESPORTS` | Servicio que sirve la ONT. |

### `SimState`

Estado dinámico completo de una corrida. Una única instancia vive a nivel de
módulo en `app/live.py`. Campos principales:

| Campo | Por defecto | Descripción |
| ----- | ----------- | ----------- |
| `t_min` | `-180.0` | Minuto de partido (T-180 a T+135). |
| `corriendo` | `False` | Marcha/pausa del reloj. |
| `velocidad` | `1.0` | Minutos simulados por tick; restringida a {1, 5, 15}. |
| `escenario` | `"diseno"` | `"diseno"` (~50.6% util pico) o `"estres"` (~60%). |
| `estadio_activo` | `"azteca"` | Estadio en foco. |
| `estadios` | `{}` | Mapa `id → Estadio` con la topología cargada. |
| `alarmas` | `[]` | Alarmas de sesión (vivas + cerradas). |
| `historial` | `{}` | `serie → deque(maxlen=600)` para graficar. |
| `goles` | `[]` | Minutos en que ocurrió un gol. |
| `semilla` | `42` | Semilla determinista del ruido. |

Se construye con la fábrica `SimState.crear()`, que carga los tres estadios
desde `sim.topology.cargar_todas()` y devuelve un estado listo para arrancar
(pausado, T-180, 1x).

### Acumulación de disponibilidad

Funciones puras que sostienen la evidencia de convergencia de SLA:

- `acumular_disponibilidad(ont)` — cada tick incrementa `ticks_totales` y, si la
  ONT está `EN_LINEA` o `DEGRADADO`, también `ticks_en_linea`. **DEGRADADO cuenta
  como disponible.**
- `disponibilidad_ont(ont)` — `ticks_en_linea / ticks_totales`; devuelve `1.0`
  si aún no hay ticks (no penaliza corridas recién iniciadas).
- `disponibilidad_servicio(onts)` — media ponderada por ticks del conjunto.

Los objetivos de SLA por protección son: `TYPE_C` → 99.999%, `TYPE_B` → 99.99%,
`NINGUNA` → 99.9%, con una tolerancia de un lado de 0.5%.

## Reloj y bucle (`sim/engine.py`)

`tick(state)` avanza la simulación un paso. Precondición: la simulación está
corriendo. Orden del ciclo:

```
t_min += velocidad (topado en +135)
  → traffic.actualizar
  → faults.aplicar
  → alarms.evaluar
  → kpis.calcular (empuja al historial)
  → acumular disponibilidad (ONTs del estadio activo)
```

Postcondiciones garantizadas:

- El reloj es **monótono no decreciente** y **topado en +135.0**: nunca decrece
  ni supera 135.0.
- Tráfico, fallas, alarmas y KPIs quedan recalculados y consistentes.
- Cada serie del historial crece a lo sumo 1 punto y respeta `maxlen=600`.

Los pasos de fallas y alarmas se invocan con import protegido (`try/except
ImportError` + `hasattr`): el bucle funciona aunque esos módulos no existan y los
recoge automáticamente cuando aparecen.

## Modelo de tráfico (`sim/traffic.py`)

`traffic.actualizar(state)` recorre el estadio activo y, por cada ONT, calcula
demanda y asignación de ancho de banda, actualiza la potencia óptica y agrega el
throughput por puerto y por estadio. Todo el cálculo es agregado (no paquete a
paquete).

### Factores que modulan la demanda

La demanda de bajada de una ONT no constante es:

```
demanda = bw_base(servicio) · f_llegada(t) · factor_actividad(t) · mult_goles(t) · k_escenario
```

- **`f_llegada(t)`** — fracción de aforo por tramos: 5% antes de T-120, rampa de
  ingreso hasta 90% a T-30, 100% durante el partido (0–90), y salida lineal a 0%
  entre +90 y +135.
- **`factor_actividad(t)`** — por fase: ingreso 1.2, primer tiempo 0.7, medio
  tiempo 1.8 (pico), segundo tiempo 0.7, salida 1.1.
- **`f_gol(t, t_gol)`** — pico transitorio: salta a 3.0 en el instante del gol y
  decae exponencial con constante 1.5 min. Varios goles no se suman; se toma el
  efecto más fuerte.
- **`k_escenario`** — factor de escala calibrado desde la propia topología para
  que la utilización PON pico (K1) alcance el objetivo del escenario (~50.6% en
  "diseno", ~60% en "estres"). Se recalibra solo si cambia el dimensionamiento.

CCTV es un **servicio constante**: su demanda no depende de la fase (cámaras
transmiten de forma continua), solo se escala por `k_escenario`.

### Contención y asignación

Por puerto, si la demanda total supera la capacidad utilizable, se asigna de
forma **proporcional** (`ratio = cap / demanda`); si cabe, se asigna la demanda
íntegra. Un puerto o tarjeta `FUERA` no genera tráfico.

### Ruido y determinismo

- **Throughput:** ruido multiplicativo gaussiano del 4%.
- **Potencia óptica:** ruido gaussiano de ±0.3 dB (σ) sobre el nominal, menos la
  atenuación del árbol.

El generador se siembra combinando `state.semilla` con el minuto de partido
discretizado, de modo que la corrida es **reproducible** con la misma semilla y
cada tick tiene ruido distinto (series con textura). Dos corridas con la misma
semilla, escenario y activación producen la misma secuencia.

### Agregados publicados en el estado

`actualizar` deja en `state` los agregados que consumen KPIs y vistas:
`throughput_bajada_mbps`, `throughput_subida_mbps`, `throughput_por_servicio`,
`throughput_por_puerto` y `util_por_puerto`.

## Modelo de fallas y protección (`sim/faults.py`)

### Tipos de falla

Cinco tipos, cada uno mapeado a un nivel de la jerarquía:

| Tipo | Efecto |
| ---- | ------ |
| `tarjeta_caida` | La tarjeta y sus puertos/árboles/ONTs pasan a FUERA. |
| `corte_troncal` | Un árbol PON (troncal/splitter) y sus ONTs. |
| `chasis_caido` | La OLT completa (todo el estadio). |
| `degradacion_optica` | Eleva la atenuación de un árbol; ONTs → DEGRADADO. |
| `pico_capacidad` | Fuerza utilización alta en un puerto (marca de pico). |

### Conmutación de protección

`inyectar_falla(state, tipo, objetivo_id)` registra la falla y **resuelve la
conmutación en el mismo tick**:

- **`TYPE_B` / `TYPE_C`:** si hay una reserva operativa ese tick, se conmuta (la
  reserva pasa a `EN_LINEA`, las ONTs se mantienen `EN_LINEA`) y se registra
  `ultimo_tiempo_conmutacion_ms` = 8.0 ms (< 50 ms, umbral de K12). Si la reserva
  **no** está disponible ese tick, las ONTs caen **de inmediato** a `FUERA` (o
  `DEGRADADO`) **sin reintentar** en ticks posteriores.
- **`NINGUNA`:** las ONTs dependientes pasan a `FUERA`.

Las fallas quedan en `state.fallas_activas` y se **reaplican de forma
idempotente** cada tick por `faults.aplicar()`, de modo que persisten hasta que
`limpiar_fallas()` (reset) las descarta.

### Fallas estocásticas (opt-in)

`paso_estocastico(state)` está **desactivado por defecto**. Solo actúa si
`state.fallas_estocasticas_activas` es verdadero: entonces calcula la
probabilidad de falla del tick con un modelo exponencial de MTBF y decide de
forma determinista (misma siembra que el tráfico) si inyecta un `corte_troncal`
sobre un árbol del estadio activo.

## Frontera con la UI (`app/live.py`)

`app/live.py` mantiene la instancia única `ESTADO` y expone las operaciones que
la UI cablea, sin poner cálculo en la capa de presentación:

- **Controles:** `alternar_corriendo`, `set_velocidad`, `alternar_escenario` /
  `set_escenario`, `reset`, `set_estadio`.
- **Eventos:** `inyectar_falla(tipo)` (elige un objetivo por defecto sensato por
  tipo y delega en `sim.faults`), `gol()` (registra el minuto actual).
- **Lecturas:** `snapshot()` (KPIs ya calculados + historial + metadatos),
  `contexto_topbar()`, `alarmas_snapshot()`.

Todas las operaciones de la frontera son fail-soft: si la topología o los módulos
no están disponibles, degradan sin propagar la excepción a la web.
