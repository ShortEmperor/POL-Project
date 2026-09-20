"""Modelo de tráfico del Motor de Simulación (design §6.2 y §6.3, Requirement 7).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). Recibe el estado de la simulación y actualiza, por tick, la
demanda y la asignación de ancho de banda de cada ONT, la potencia óptica, y la
agregación de throughput por puerto PON y por estadio.

Contenido (design §6.2):

* :func:`f_llegada` — curva de llegada de espectadores (fracción de aforo).
* :func:`factor_actividad` — factor de actividad por fase del partido.
* :func:`f_gol` — pico transitorio de actividad por gol (decae exponencial).
* Fórmulas de ancho de banda por servicio (``bw_wifi``, ``bw_cctv``, ...).
* :func:`utilizacion` — cociente carga/capacidad utilizable de un puerto.

Ruido (design §6.3):

* :func:`aplicar_ruido_bw` — multiplicativo gaussiano del 4% sobre throughput.
* :func:`aplicar_ruido_optico` — ±0.3 dB gaussiano sobre la potencia óptica.
* El generador ``rng`` se siembra con ``state.semilla`` para reproducibilidad.

Entrada principal:

* :func:`actualizar` — recorre la topología del estadio activo, calcula por ONT
  la demanda/asignación de bw y la potencia óptica (con ruido), y agrega el
  throughput por puerto PON y por estadio. Escribe los resultados en las
  dataclasses de ``sim/state.py``.

Escenarios (Req 7.4/7.5): un factor de escala por escenario ("diseno" vs
"estres") lleva la **utilización PON pico** (K1) a su objetivo:

* ``"diseno"`` → ~50.6% de utilización pico (dentro del 10%).
* ``"estres"`` → 55–65% (60% ± 5%).

El factor se **calibra a partir de la propia topología** (ver
:func:`factor_escenario`) para que se mantenga válido aunque cambie el
dimensionamiento; ver la nota de calibración en esa función.

Nota de alcance: ``SimState`` y el bucle ``tick`` se definen en la tarea 5.3
(design §6.1). Aquí se usa *duck typing* / ``TYPE_CHECKING`` sobre el estado
para que este módulo importe de forma independiente. Se asume que ``state``
expone: ``t_min``, ``escenario``, ``estadio_activo``, ``estadios`` y ``semilla``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from sim.state import ArbolPON, Estadio, Estado, PuertoPON, Servicio

if TYPE_CHECKING:  # pragma: no cover - solo para anotaciones de tipo
    # SimState se define en la tarea 5.3; se referencia por nombre para no
    # crear una dependencia real (evita import circular y permite importar
    # este módulo de forma aislada).
    from sim.state import SimState


# ===========================================================================
# 6.2 Curva de llegada, factores de actividad y evento de gol
# ===========================================================================
def f_llegada(t: float) -> float:
    """Fracción de espectadores presentes (0..1) en el minuto de partido ``t``.

    Curva por tramos exactamente como en design §6.2:

    * ``t < -120``  → 5% (recinto casi vacío).
    * ``-120..-30`` → rampa de ingreso de 5% a 90%.
    * ``-30..0``    → de 90% a 100% (últimos rezagados).
    * ``0..90``     → 100% (partido, aforo lleno).
    * ``90..135``   → salida, decae linealmente a 0%.
    * ``t >= 135``  → 0%.
    """
    if t < -120:
        return 0.05
    if t < -30:
        return 0.05 + 0.85 * (t + 120) / 90   # rampa de ingreso
    if t < 0:
        return 0.90 + 0.10 * (t + 30) / 30
    if t < 90:
        return 1.00                            # partido: aforo lleno
    if t < 135:
        return max(0.0, 1.00 - (t - 90) / 45)  # salida
    return 0.0


# Factor de actividad por fase del partido (design §6.2). Multiplica la demanda
# base por servicio. El medio tiempo (1.8) es el pico de actividad: la gente se
# levanta, usa el WiFi, compra en los POS, etc.
FACTOR_INGRESO = 1.2
FACTOR_PRIMER_TIEMPO = 0.7
FACTOR_MEDIO_TIEMPO = 1.8
FACTOR_SEGUNDO_TIEMPO = 0.7
FACTOR_SALIDA = 1.1

# Factor de actividad máximo alcanzable en cualquier fase (usado por la
# calibración de escenario; debe coincidir con el máximo de los anteriores).
FACTOR_ACTIVIDAD_PICO = FACTOR_MEDIO_TIEMPO


def fase_partido(t: float) -> str:
    """Devuelve la fase del partido para el minuto ``t``.

    Fases (design §6.2): ingreso (antes del saque), primer tiempo (0–45),
    medio tiempo (45–60), segundo tiempo (60–90) y salida (después del pitido
    final). Fuera de rango se trata como ingreso/salida según el signo.
    """
    if t < 0:
        return "ingreso"
    if t < 45:
        return "primer_tiempo"
    if t < 60:
        return "medio_tiempo"
    if t < 90:
        return "segundo_tiempo"
    return "salida"


def factor_actividad(t: float) -> float:
    """Factor de actividad por fase para el minuto ``t`` (design §6.2)."""
    fase = fase_partido(t)
    return {
        "ingreso": FACTOR_INGRESO,
        "primer_tiempo": FACTOR_PRIMER_TIEMPO,
        "medio_tiempo": FACTOR_MEDIO_TIEMPO,
        "segundo_tiempo": FACTOR_SEGUNDO_TIEMPO,
        "salida": FACTOR_SALIDA,
    }[fase]


def f_gol(t: float, t_gol: float) -> float:
    """Multiplicador de actividad por un gol en ``t_gol``; decae exponencial.

    Exactamente como design §6.2: antes del gol el multiplicador es 1.0; en el
    instante del gol salta a 3.0 (1 + 2) y decae con constante de tiempo 1.5
    minutos.
    """
    if t < t_gol:
        return 1.0
    return 1.0 + 2.0 * math.exp(-(t - t_gol) / 1.5)


def multiplicador_goles(t: float, goles: list[float] | tuple[float, ...]) -> float:
    """Multiplicador combinado de una lista de goles en ``t``.

    Toma el efecto más fuerte entre los goles ya ocurridos (los picos no se
    suman: representan un mismo pico de actividad de la afición). Si no hay
    goles, devuelve 1.0.
    """
    if not goles:
        return 1.0
    return max(f_gol(t, tg) for tg in goles)


# ===========================================================================
# 6.2 Fórmulas de ancho de banda por servicio (Mbps por ONT activa)
# ===========================================================================
# Las formas base provienen de design §6.2. Donde el diseño no fija una
# constante explícita (signage, macro, prensa, esports) se adopta un valor
# coherente con el tipo de servicio, documentado aquí. Todas las fórmulas
# devuelven Mbps de **bajada** (downstream) por ONT; la subida se deriva con un
# factor de asimetría por servicio (ver RATIO_SUBIDA).

def bw_wifi(activos: float, factor: float) -> float:
    """WiFi de asistentes: 4 Mbps por ONT activa, modulado por el factor."""
    return activos * 4.0 * factor


def bw_cctv(camaras: float) -> float:
    """CCTV: 8 Mbps por cámara, constante (no depende del factor de fase)."""
    return camaras * 8.0


def bw_iptv(palcos: float, factor: float) -> float:
    """IPTV / palcos: 12 Mbps por ONT, modulado por el factor."""
    return palcos * 12.0 * factor


# Alias explícito para palcos (mismo modelo que IPTV).
bw_palcos = bw_iptv


def bw_pos(tpv: float, factor: float) -> float:
    """Puntos de venta (TPV): 0.2 Mbps por ONT, modulado por el factor."""
    return tpv * 0.2 * factor


def bw_torn(molinetes: float, factor: float) -> float:
    """Torniquetes: 0.1 Mbps por ONT, modulado por el factor."""
    return molinetes * 0.1 * factor


def bw_voz(lineas: float, factor: float) -> float:
    """VoIP: 0.1 Mbps por ONT, modulado por el factor."""
    return lineas * 0.1 * factor


def bw_signage(pantallas: float, factor: float) -> float:
    """Cartelería digital: 6 Mbps por pantalla, ligeramente modulado."""
    return pantallas * 6.0 * factor


def bw_macro(celdas: float, factor: float) -> float:
    """Cobertura macro (backhaul móvil): 4 Mbps por ONT, modulado."""
    return celdas * 4.0 * factor


def bw_prensa(puestos: float, factor: float) -> float:
    """Sala de prensa (subida/transmisión): 20 Mbps por puesto, modulado."""
    return puestos * 20.0 * factor


def bw_esports(estaciones: float, factor: float) -> float:
    """Zona de esports: 15 Mbps por estación, modulado."""
    return estaciones * 15.0 * factor


# Ancho de banda de **bajada** base por ONT (factor = 1.0) por servicio. Se usa
# tanto en :func:`bw_ont_bajada` como en la calibración de escenario para no
# duplicar constantes.
BW_BASE_BAJADA_MBPS: dict[Servicio, float] = {
    Servicio.WIFI: 4.0,
    Servicio.PALCOS: 12.0,
    Servicio.CCTV: 8.0,
    Servicio.POS: 0.2,
    Servicio.TORNIQUETES: 0.1,
    Servicio.VOZ: 0.1,
    Servicio.SIGNAGE: 6.0,
    Servicio.MACRO: 4.0,
    Servicio.PRENSA: 20.0,
    Servicio.ESPORTS: 15.0,
}

# Servicios cuya demanda **no** depende del factor de fase (carga constante).
SERVICIOS_CONSTANTES: frozenset[Servicio] = frozenset({Servicio.CCTV})

# Relación subida/bajada por servicio (asimetría típica). La mayoría de los
# servicios de consumo son asimétricos (poca subida); prensa y esports suben
# mucho (transmisión, juego en línea); voz es simétrica.
RATIO_SUBIDA: dict[Servicio, float] = {
    Servicio.WIFI: 0.25,
    Servicio.PALCOS: 0.10,
    Servicio.CCTV: 0.90,        # las cámaras suben casi todo su tráfico
    Servicio.POS: 0.50,
    Servicio.TORNIQUETES: 0.50,
    Servicio.VOZ: 1.00,         # simétrico
    Servicio.SIGNAGE: 0.05,
    Servicio.MACRO: 0.40,
    Servicio.PRENSA: 3.00,      # sube más de lo que baja (transmisión)
    Servicio.ESPORTS: 0.60,
}


def bw_ont_bajada(servicio: Servicio, factor: float) -> float:
    """Demanda de **bajada** (Mbps) de una ONT del servicio dado.

    ``factor`` combina llegada, actividad de fase y goles. Los servicios
    constantes (CCTV) ignoran el factor.
    """
    base = BW_BASE_BAJADA_MBPS[servicio]
    if servicio in SERVICIOS_CONSTANTES:
        return base
    return base * factor


def bw_ont_subida(servicio: Servicio, factor: float) -> float:
    """Demanda de **subida** (Mbps) de una ONT del servicio dado."""
    return bw_ont_bajada(servicio, factor) * RATIO_SUBIDA.get(servicio, 0.2)


# ===========================================================================
# 6.2 Utilización de puerto
# ===========================================================================
def utilizacion(carga_mbps: float, capacidad_util_mbps: float) -> float:
    """Utilización del puerto (0..1+). Dispara PON-001 al superar el umbral.

    Guarda de capacidad no positiva → 0.0 (design §6.2), para no dividir por
    cero ni producir utilización negativa/indefinida.
    """
    if capacidad_util_mbps <= 0:
        return 0.0
    return carga_mbps / capacidad_util_mbps


# ===========================================================================
# 6.3 Ruido
# ===========================================================================
def aplicar_ruido_bw(valor: float, rng: np.random.Generator) -> float:
    """Ruido multiplicativo gaussiano del 4% sobre un valor de throughput."""
    return valor * (1.0 + rng.normal(0.0, 0.04))


def aplicar_ruido_optico(dbm: float, rng: np.random.Generator) -> float:
    """Ruido gaussiano de ±0.3 dB (σ) sobre una potencia óptica en dBm."""
    return dbm + rng.normal(0.0, 0.3)


# ===========================================================================
# Escenarios: calibración del factor de escala (Req 7.4 / 7.5)
# ===========================================================================
# Objetivos de utilización PON **pico** (K1) por escenario (design §6.2, Req 7).
UTIL_PICO_OBJETIVO: dict[str, float] = {
    "diseno": 0.506,   # ~50.6% (valor del dimensionamiento)
    "estres": 0.60,    # 60% (centro del intervalo 55–65%)
}

# Potencia óptica nominal de referencia (dBm) para la simulación de nivel Rx.
POTENCIA_OPTICA_NOMINAL_DBM = -22.0


def _carga_bajada_puerto_pico(puerto: PuertoPON) -> float:
    """Carga de bajada del puerto en el **pico teórico** (sin ruido, sin gol).

    Pico = ``f_llegada = 1.0`` y ``factor_actividad = FACTOR_ACTIVIDAD_PICO``.
    Se usa para calibrar el factor de escenario a partir de la topología real,
    de modo que el objetivo de utilización pico no dependa de números mágicos.
    """
    carga = 0.0
    for arbol in puerto.arboles:
        for ont in arbol.onts:
            if ont.servicio in SERVICIOS_CONSTANTES:
                carga += BW_BASE_BAJADA_MBPS[ont.servicio]
            else:
                carga += BW_BASE_BAJADA_MBPS[ont.servicio] * FACTOR_ACTIVIDAD_PICO
    return carga


def _puerto_mas_cargado(estadio: Estadio) -> tuple[float, float]:
    """Devuelve (carga_pico_bajada, capacidad_util) del puerto más cargado.

    Recorre los puertos activos (con árboles) y encuentra el que tendría mayor
    utilización en el pico teórico. Es la base de la calibración del escenario
    (el escenario apunta a un objetivo de utilización **pico** = K1, que es el
    máximo por puerto).
    """
    mejor_util = -1.0
    mejor_carga = 0.0
    mejor_cap = 0.0
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if not puerto.arboles:
                continue
            carga = _carga_bajada_puerto_pico(puerto)
            util = utilizacion(carga, puerto.capacidad_util_mbps)
            if util > mejor_util:
                mejor_util = util
                mejor_carga = carga
                mejor_cap = puerto.capacidad_util_mbps
    return mejor_carga, mejor_cap


def factor_escenario(estadio: Estadio, escenario: str) -> float:
    """Factor de escala de demanda para que K1 alcance el objetivo del escenario.

    Calibración (Req 7.4/7.5):

    Las fórmulas de bw por ONT del diseño son deliberadamente pequeñas frente a
    la capacidad utilizable del puerto (8.5 Gbps XGS-PON), por lo que la carga
    "cruda" solo alcanza ~14% de utilización pico. Para reproducir el
    dimensionamiento (~50.6% en "diseno") se aplica un **factor de escenario**
    ``k`` que escala uniformemente la demanda de todas las ONTs.

    ``k`` se deriva de la propia topología: se localiza el puerto que quedaría
    más cargado en el pico teórico (``f_llegada=1``, ``factor=1.8``) y se
    resuelve ``k`` tal que su utilización iguale el objetivo::

        util_pico(k) = k * carga_pico / cap_util = objetivo
        =>  k = objetivo * cap_util / carga_pico

    Así, si el dimensionamiento cambia (más/menos ONTs por puerto), ``k`` se
    reajusta y el objetivo pico se mantiene. Para la topología Azteca actual
    esto da ``k ≈ 3.51`` ("diseno", 50.6%) y ``k ≈ 4.17`` ("estres", 60%).

    El ruido del 4% y los goles pueden perturbar el pico instantáneo, pero
    permanecen dentro de la tolerancia (diseño ±10%, estrés ±5%).
    """
    objetivo = UTIL_PICO_OBJETIVO.get(escenario, UTIL_PICO_OBJETIVO["diseno"])
    carga_pico, cap_util = _puerto_mas_cargado(estadio)
    if carga_pico <= 0 or cap_util <= 0:
        return 1.0
    return objetivo * cap_util / carga_pico


# ===========================================================================
# Entrada principal: actualizar el tráfico del estadio activo por tick
# ===========================================================================
def _rng_para_tick(state: "SimState") -> np.random.Generator:
    """Crea un generador determinista para el tick actual.

    Se siembra combinando ``state.semilla`` con el minuto de partido para que:

    * La corrida sea reproducible con la misma semilla (Req 17.3, Property 10).
    * Cada tick tenga ruido distinto (series con textura, no líneas planas).

    Usar un ``SeedSequence`` con (semilla, t_min discretizado) evita depender
    del orden de llamadas previas al generador.
    """
    semilla = int(getattr(state, "semilla", 42))
    # Discretiza t_min a centésimas de minuto para una clave entera estable.
    t_clave = int(round(float(getattr(state, "t_min", 0.0)) * 100))
    seq = np.random.SeedSequence([semilla, t_clave & 0xFFFFFFFF])
    return np.random.default_rng(seq)


def actualizar(state: "SimState") -> None:
    """Actualiza el tráfico del estadio activo para el tick actual (Req 7.1–7.5).

    Recorre la topología del estadio activo y, por cada ONT:

    * Calcula la demanda de bajada y de subida (Mbps) según su servicio, la
      curva de llegada, el factor de actividad de fase, los goles y el factor
      de escenario, con ruido multiplicativo del 4%.
    * Actualiza su potencia óptica Rx aplicando ruido de ±0.3 dB sobre el
      nominal (más la atenuación del árbol si la hubiera).
    * Asigna el ancho de banda tras contención por puerto (design §6.2).

    Agrega el throughput de bajada por **puerto PON** y por **estadio**
    (Req 7.2) y expone las series en ``state`` para KPIs/gráficas.

    Las ONTs cuyo elemento está FUERA no generan demanda ni reciben asignación.

    El estado se modifica **in situ** (las dataclasses de topología). Además se
    guardan agregados en atributos dinámicos de ``state`` para consumo de
    KPIs/vistas:

    * ``state.throughput_bajada_mbps`` / ``state.throughput_subida_mbps``:
      totales del estadio activo.
    * ``state.throughput_por_servicio``: dict servicio → (bajada, subida).
    * ``state.throughput_por_puerto``: dict puerto_id → bajada (Mbps).
    * ``state.util_por_puerto``: dict puerto_id → utilización (0..1+).
    """
    estadios = getattr(state, "estadios", {})
    estadio_id = getattr(state, "estadio_activo", None)
    estadio = estadios.get(estadio_id) if estadios else None
    if estadio is None:
        return

    t = float(getattr(state, "t_min", 0.0))
    escenario = getattr(state, "escenario", "diseno")
    goles = list(getattr(state, "goles", []) or [])
    rng = _rng_para_tick(state)

    llegada = f_llegada(t)
    fase_factor = factor_actividad(t)
    gol_mult = multiplicador_goles(t, goles)
    k_escenario = factor_escenario(estadio, escenario)

    # Factor de demanda combinado (adimensional) aplicado a servicios no
    # constantes. La curva de llegada modula la población activa; la fase y los
    # goles modulan la intensidad; el escenario escala al objetivo de K1.
    factor_demanda = llegada * fase_factor * gol_mult * k_escenario

    # Acumuladores por estadio y por servicio.
    total_bajada = 0.0
    total_subida = 0.0
    por_servicio: dict[Servicio, list[float]] = {
        s: [0.0, 0.0] for s in Servicio
    }
    por_puerto: dict[str, float] = {}
    util_puerto: dict[str, float] = {}

    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            carga_puerto_bajada = _actualizar_puerto(
                puerto,
                factor_demanda=factor_demanda,
                k_escenario=k_escenario,
                rng=rng,
                por_servicio=por_servicio,
            )
            por_puerto[puerto.id] = carga_puerto_bajada
            util_puerto[puerto.id] = utilizacion(
                carga_puerto_bajada, puerto.capacidad_util_mbps
            )
            total_bajada += carga_puerto_bajada
            # La subida agregada se acumula vía por_servicio; sumamos aparte.

    # Total de subida = suma de las subidas por servicio.
    total_subida = sum(v[1] for v in por_servicio.values())

    # Exponer agregados en el estado (atributos dinámicos, consumidos por KPIs).
    state.throughput_bajada_mbps = total_bajada
    state.throughput_subida_mbps = total_subida
    state.throughput_por_servicio = {
        s: (v[0], v[1]) for s, v in por_servicio.items()
    }
    state.throughput_por_puerto = por_puerto
    state.util_por_puerto = util_puerto


def _actualizar_puerto(
    puerto: PuertoPON,
    *,
    factor_demanda: float,
    k_escenario: float,
    rng: np.random.Generator,
    por_servicio: dict[Servicio, list[float]],
) -> float:
    """Actualiza las ONTs de un puerto y devuelve su carga de bajada (Mbps).

    Calcula la demanda por ONT (con ruido), asigna bw tras contención por la
    capacidad utilizable del puerto y actualiza la potencia óptica. Acumula el
    throughput de bajada/subida por servicio en ``por_servicio``.
    """
    # Puerto/tarjeta fuera de servicio: sin tráfico.
    if puerto.estado == Estado.FUERA:
        for arbol in puerto.arboles:
            for ont in arbol.onts:
                ont.bw_demandado_mbps = 0.0
                ont.bw_asignado_mbps = 0.0
        return 0.0

    # 1) Demanda por ONT (con ruido) y demanda total del puerto.
    demanda_total = 0.0
    for arbol in puerto.arboles:
        _demanda_arbol(
            arbol,
            factor_demanda=factor_demanda,
            k_escenario=k_escenario,
            rng=rng,
        )
        for ont in arbol.onts:
            demanda_total += ont.bw_demandado_mbps

    # 2) Contención: si la demanda excede la capacidad utilizable, se asigna de
    #    forma proporcional (design §6.2). Si cabe, se asigna la demanda íntegra.
    cap = puerto.capacidad_util_mbps
    if demanda_total > cap > 0:
        ratio_asignacion = cap / demanda_total
    else:
        ratio_asignacion = 1.0

    carga_bajada = 0.0
    for arbol in puerto.arboles:
        for ont in arbol.onts:
            asignado = ont.bw_demandado_mbps * ratio_asignacion
            ont.bw_asignado_mbps = asignado
            carga_bajada += asignado

            # Acumular por servicio (bajada asignada + subida demandada).
            # La subida se deriva del bw de bajada base escalado igual que la
            # demanda de bajada, por la relación subida/bajada del servicio.
            if ont.servicio in SERVICIOS_CONSTANTES:
                subida_base = BW_BASE_BAJADA_MBPS[ont.servicio] * k_escenario
            else:
                subida_base = bw_ont_bajada(ont.servicio, factor_demanda)
            subida = subida_base * RATIO_SUBIDA.get(ont.servicio, 0.2)
            # La subida no sufre la contención de bajada (enlaces separados en
            # XGS-PON); se le aplica ruido para textura.
            subida = max(0.0, aplicar_ruido_bw(subida, rng))
            por_servicio[ont.servicio][0] += asignado
            por_servicio[ont.servicio][1] += subida

    return carga_bajada


def _demanda_arbol(
    arbol: ArbolPON,
    *,
    factor_demanda: float,
    k_escenario: float,
    rng: np.random.Generator,
) -> None:
    """Calcula y escribe ``bw_demandado_mbps`` y potencia óptica por ONT.

    Aplica el factor de demanda a servicios no constantes, ruido del 4% al bw y
    ±0.3 dB a la potencia óptica (design §6.3). Descuenta la atenuación del
    árbol de la potencia óptica (falla óptica → señal más baja).

    Distinción de factores:

    * Servicios no constantes → ``factor_demanda`` (= llegada·fase·gol·k).
    * Servicios constantes (CCTV) → solo el escalado de escenario
      ``k_escenario`` (no dependen de llegada/fase/goles, pero sí se escalan al
      objetivo de utilización pico de forma coherente con el resto).
    """
    for ont in arbol.onts:
        # ONT fuera de servicio: sin demanda.
        if ont.estado == Estado.FUERA:
            ont.bw_demandado_mbps = 0.0
            ont.bw_asignado_mbps = 0.0
            continue

        if ont.servicio in SERVICIOS_CONSTANTES:
            # bw base constante, escalado por el factor de escenario.
            demanda = BW_BASE_BAJADA_MBPS[ont.servicio] * k_escenario
        else:
            demanda = bw_ont_bajada(ont.servicio, factor_demanda)

        demanda = max(0.0, aplicar_ruido_bw(demanda, rng))
        ont.bw_demandado_mbps = demanda

        # Potencia óptica: nominal + ruido - atenuación del árbol.
        pot = aplicar_ruido_optico(POTENCIA_OPTICA_NOMINAL_DBM, rng)
        pot -= arbol.atenuacion_db
        ont.potencia_optica_dbm = pot
