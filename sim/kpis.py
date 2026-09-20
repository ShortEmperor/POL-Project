"""Cálculo de los 12 KPIs del Motor de Simulación (design §7, Requirement 2).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). **Toda** la lógica de KPI vive aquí; los callbacks de la Capa de
Presentación solo leen los valores ya calculados (Req 2.2, design §7).

Contenido:

* :data:`CATALOGO` — catálogo de los 12 KPIs (K1..K12) con **nombre, fórmula,
  unidad y umbral** (Req 2.1). Es la fuente única de verdad de metadatos; la
  vista de KPIs (tarea 5.9) y la verificación de completitud (tarea 9.9) lo
  consumen.
* :func:`calcular` — calcula los valores numéricos K1..K12 para el estadio
  activo y los empuja al historial (design §7, Req 2.2).

Cómo lee el estado (design §6.2): los agregados de tráfico (throughput total,
por puerto, por servicio y utilización por puerto) los **precalcula**
``sim.traffic.actualizar`` y los deja en atributos del ``state``. Este módulo
los **lee**, nunca recalcula tráfico. La disponibilidad por servicio (K5) usa
las funciones puras de ``sim.state`` (§6.5).

Nota de alcance (duck typing): ``SimState`` y el bucle ``tick`` se definen en la
tarea 5.3 (design §6.1), y los módulos de fallas (7.1) y alarmas (7.5) aún no
existen. Por eso este módulo **no** define ``SimState`` y accede a los atributos
del estado de forma defensiva (``getattr`` con valores por defecto), de modo que
importa y corre de forma aislada. Los atributos que consume:

* ``estadios``, ``estadio_activo`` — topología del estadio activo.
* ``throughput_bajada_mbps``, ``throughput_por_puerto``, ``util_por_puerto``,
  ``throughput_por_servicio`` — agregados de tráfico (tarea 5.1).
* ``historial`` — dict serie → ``deque(maxlen=600)`` (design §6.1); si no
  existe, se crea aquí como dict de deques.
* ``alarmas`` — lista de alarmas (tarea 7.5); si no existe, K7/K8/K10 → 0.
* ``ultimo_tiempo_conmutacion_ms`` — último tiempo de switch de protección
  (tarea 7.1); si no existe, K12 usa un valor por defecto sensato.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from sim.state import (
    ONT,
    Estadio,
    Estado,
    Servicio,
    disponibilidad_servicio,
)

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones
    # SimState se define en la tarea 5.3; se referencia por nombre para no
    # crear una dependencia real (permite importar este módulo de forma aislada).
    from sim.state import SimState


# ===========================================================================
# Catálogo de KPIs (design §7): nombre, fórmula, unidad y umbral (Req 2.1)
# ===========================================================================
@dataclass(frozen=True)
class KPI:
    """Metadatos de un KPI del catálogo (Req 2.1).

    Atributos:
        codigo: identificador del catálogo ("K1".."K12").
        nombre: nombre legible del KPI.
        formula: fórmula conceptual (design §7).
        unidad: unidad de la medida ("%", "Gbps", "dBm", "ms", "conteo").
        umbral: umbral numérico si aplica, o ``None`` cuando el KPI no define
            umbral (K3 throughput total). K5 usa umbral "según protección", que
            no es un único número; se modela como ``None`` y su cumplimiento se
            evalúa por servicio en la vista de SLA (tarea 9.6/9.8).
        umbral_texto: descripción legible del umbral (para la UI y trazabilidad).
        comparacion: sentido del umbral: ``"<="`` (no exceder), ``">="``
            (alcanzar como mínimo), ``"=="`` (valor exacto, p.ej. 0) o ``None``.
    """

    codigo: str
    nombre: str
    formula: str
    unidad: str
    umbral: float | None
    umbral_texto: str
    comparacion: str | None


# Orden fijo K1..K12 (design §7). El catálogo es la fuente única de metadatos.
CATALOGO: dict[str, KPI] = {
    "K1": KPI(
        codigo="K1",
        nombre="Utilización PON pico",
        formula="max(carga/cap_util) por puerto",
        unidad="%",
        umbral=60.0,
        umbral_texto="≤ 60%",
        comparacion="<=",
    ),
    "K2": KPI(
        codigo="K2",
        nombre="Utilización PON media",
        formula="media(carga/cap_util) por puerto",
        unidad="%",
        umbral=50.0,
        umbral_texto="≤ 50%",
        comparacion="<=",
    ),
    "K3": KPI(
        codigo="K3",
        nombre="Throughput total",
        formula="Σ bw_asignado de todas las ONTs",
        unidad="Gbps",
        umbral=None,
        umbral_texto="—",
        comparacion=None,
    ),
    "K4": KPI(
        codigo="K4",
        nombre="ONTs en línea",
        formula="conteo Estado∈{EN_LINEA,DEGRADADO}",
        unidad="conteo",
        umbral=99.9,
        umbral_texto="≥ 99.9%",
        comparacion=">=",
    ),
    "K5": KPI(
        codigo="K5",
        nombre="Disponibilidad por servicio",
        formula="ticks_en_linea/ticks_totales ponderado",
        unidad="%",
        umbral=None,
        umbral_texto="según protección",
        comparacion=">=",
    ),
    "K6": KPI(
        codigo="K6",
        nombre="Potencia óptica media",
        formula="media(potencia_optica_dbm)",
        unidad="dBm",
        umbral=-28.0,
        umbral_texto="≥ -28 dBm",
        comparacion=">=",
    ),
    "K7": KPI(
        codigo="K7",
        nombre="Alarmas activas",
        formula="conteo alarmas ∈ {NUEVA,ACTIVA,RECONOCIDA}",
        unidad="conteo",
        umbral=None,
        umbral_texto="—",
        comparacion=None,
    ),
    "K8": KPI(
        codigo="K8",
        nombre="Alarmas críticas",
        formula="conteo alarmas críticas activas",
        unidad="conteo",
        umbral=0.0,
        umbral_texto="0",
        comparacion="==",
    ),
    "K9": KPI(
        codigo="K9",
        nombre="Latencia estimada",
        formula="modelo carga→latencia",
        unidad="ms",
        umbral=10.0,
        umbral_texto="≤ 10 ms",
        comparacion="<=",
    ),
    "K10": KPI(
        codigo="K10",
        nombre="Servicios afectados",
        formula="conteo servicios con ONTs FUERA",
        unidad="conteo",
        umbral=0.0,
        umbral_texto="0",
        comparacion="==",
    ),
    "K11": KPI(
        codigo="K11",
        nombre="Margen de capacidad",
        formula="1 - K1",
        unidad="%",
        umbral=40.0,
        umbral_texto="≥ 40%",
        comparacion=">=",
    ),
    "K12": KPI(
        codigo="K12",
        nombre="Tiempo de conmutación",
        formula="último tiempo de switch de protección",
        unidad="ms",
        umbral=50.0,
        umbral_texto="≤ 50 ms",
        comparacion="<=",
    ),
}

# Códigos del catálogo en orden fijo (usado por la verificación de completitud,
# tarea 9.9 / Req 2.4).
CODIGOS_KPI: tuple[str, ...] = tuple(f"K{i}" for i in range(1, 13))


# ===========================================================================
# Constantes de modelos auxiliares
# ===========================================================================
# K9 — modelo carga→latencia (design §7 "modelo carga→latencia").
#
# Se usa un modelo de latencia que crece con la utilización del enlace, análogo
# a la fórmula M/M/1 de teoría de colas (el retardo de encolamiento diverge al
# acercarse la utilización a 1). Se parametriza con:
#
#   latencia(u) = LAT_BASE_MS + LAT_COLA_MS * u / (1 - min(u, U_MAX))
#
# donde ``u`` es la utilización PON pico (K1 en fracción 0..1):
#   * LAT_BASE_MS: retardo fijo de propagación/procesamiento a carga baja.
#   * LAT_COLA_MS: escala del retardo de encolamiento.
#   * U_MAX: tope de utilización para acotar la latencia (evita dividir por 0
#     y explosiones numéricas cuando u ≥ 1 por sobre-suscripción transitoria).
#
# Calibración: a la utilización de diseño (~50.6%) da ≈ 3.5 ms, holgadamente
# bajo el umbral de 10 ms; a ~85% se acerca al umbral, disparando LAT-001.
LAT_BASE_MS: float = 2.0
LAT_COLA_MS: float = 1.5
LAT_U_MAX: float = 0.98

# Conversión de Mbps a Gbps para K3.
MBPS_POR_GBPS: float = 1000.0

# Estados de alarma que cuentan como "activa" para K7 (design §7, §8.4).
_ESTADOS_ALARMA_ACTIVA: frozenset[str] = frozenset({"nueva", "activa", "reconocida"})
# Token de severidad crítica (design §8.1).
_SEVERIDAD_CRITICA: str = "critica"

# Longitud máxima del historial por serie (design §6.1, Req 16.4).
HISTORIAL_MAXLEN: int = 600


# ===========================================================================
# Utilidades internas
# ===========================================================================
def _estadio_activo(state: "SimState") -> Estadio | None:
    """Devuelve el :class:`Estadio` activo del estado, o ``None`` si no hay."""
    estadios = getattr(state, "estadios", None) or {}
    estadio_id = getattr(state, "estadio_activo", None)
    return estadios.get(estadio_id) if estadios else None


def _iter_onts(estadio: Estadio):
    """Itera todas las ONTs del estadio en orden de topología."""
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            for arbol in puerto.arboles:
                yield from arbol.onts


def _estado_alarma(alarma) -> str:
    """Token de estado de una alarma como string (tolerante a Enum o str)."""
    est = getattr(alarma, "estado", None)
    valor = getattr(est, "value", est)
    return str(valor).lower() if valor is not None else ""


def _severidad_alarma(alarma) -> str:
    """Token de severidad de una alarma como string (tolerante a Enum o str)."""
    sev = getattr(alarma, "severidad", None)
    valor = getattr(sev, "value", sev)
    return str(valor).lower() if valor is not None else ""


def _latencia_estimada_ms(util_pico: float) -> float:
    """Modelo carga→latencia (K9). ``util_pico`` es la utilización pico (0..1+).

    Ver la nota de calibración en las constantes ``LAT_*``. Se acota la
    utilización a ``LAT_U_MAX`` para no dividir por cero ni divergir cuando hay
    sobre-suscripción transitoria (u ≥ 1).
    """
    u = max(0.0, min(util_pico, LAT_U_MAX))
    return LAT_BASE_MS + LAT_COLA_MS * u / (1.0 - u)


# ===========================================================================
# Empuje al historial (design §6.1)
# ===========================================================================
def _empujar_historial(state: "SimState", valores: dict[str, float]) -> None:
    """Empuja los valores de KPI al historial del estado (design §6.1).

    ``state.historial`` es un ``dict`` serie → ``deque(maxlen=600)``. Si no
    existe (SimState aún no creado, tarea 5.3), se crea aquí por duck typing.
    Cada serie crece en <= 1 punto por llamada y respeta ``maxlen=600``
    (Req 16.4). La serie de cada KPI se nombra por su código ("K1".."K12").
    """
    historial = getattr(state, "historial", None)
    if not isinstance(historial, dict):
        historial = {}
        state.historial = historial

    for codigo, valor in valores.items():
        serie = historial.get(codigo)
        if not isinstance(serie, deque):
            # Preserva puntos previos si la serie existía como lista/otra cosa.
            iterable = serie if isinstance(serie, (list, tuple, deque)) else ()
            serie = deque(iterable, maxlen=HISTORIAL_MAXLEN)
            historial[codigo] = serie
        serie.append(valor)


# ===========================================================================
# Cálculo de los 12 KPIs (design §7, Req 2.2)
# ===========================================================================
def calcular(state: "SimState") -> dict[str, float]:
    """Calcula los 12 KPIs (K1..K12) para el estadio activo (design §7, Req 2.2).

    Lee los agregados de tráfico ya calculados por ``sim.traffic.actualizar``
    (nunca recalcula tráfico) y las estructuras de topología/alarmas del estado.

    Postcondiciones (design §7):

    * Devuelve un ``dict`` con **exactamente** las claves ``K1``..``K12``, todas
      numéricas (Req 2.1/2.4).
    * Empuja cada valor al historial del estado para graficarse (design §6.1).

    Robustez (duck typing): si no hay estadio activo o faltan agregados de
    tráfico (p.ej. antes del primer ``tick``), los KPIs afectados devuelven 0.0
    en lugar de fallar, de modo que la vista nunca queda vacía (Req 15.3).
    """
    estadio = _estadio_activo(state)

    # --- Agregados de tráfico precalculados (tarea 5.1). ---
    util_por_puerto: dict[str, float] = getattr(state, "util_por_puerto", None) or {}
    throughput_bajada_mbps: float = float(
        getattr(state, "throughput_bajada_mbps", 0.0) or 0.0
    )
    utils = list(util_por_puerto.values())

    # --- K1: Utilización PON pico (%) = max utilización por puerto. ---
    k1_frac = max(utils) if utils else 0.0
    k1 = k1_frac * 100.0

    # --- K2: Utilización PON media (%) = media de utilización por puerto. ---
    k2_frac = (sum(utils) / len(utils)) if utils else 0.0
    k2 = k2_frac * 100.0

    # --- K3: Throughput total (Gbps) = Σ bw asignado (viene en Mbps). ---
    k3 = throughput_bajada_mbps / MBPS_POR_GBPS

    # --- K4/K6/K10: recorrer ONTs una sola vez. ---
    onts_en_linea = 0
    total_onts = 0
    suma_potencia = 0.0
    servicios_afectados: set[Servicio] = set()
    if estadio is not None:
        for ont in _iter_onts(estadio):
            total_onts += 1
            if ont.estado in (Estado.EN_LINEA, Estado.DEGRADADO):
                onts_en_linea += 1
            suma_potencia += ont.potencia_optica_dbm
            if ont.estado == Estado.FUERA:
                servicios_afectados.add(ont.servicio)

    # K4: ONTs en línea (conteo). El umbral (≥99.9%) se evalúa en la UI contra
    # el total; aquí exponemos el conteo tal como pide el catálogo (unidad
    # "conteo", design §7).
    k4 = float(onts_en_linea)

    # K6: Potencia óptica media (dBm). Sin ONTs, se usa el nominal por defecto
    # de la dataclass ONT para no reportar 0 dBm (valor irreal).
    k6 = (suma_potencia / total_onts) if total_onts > 0 else ONT.potencia_optica_dbm

    # K10: Servicios afectados (conteo de servicios distintos con ONTs FUERA).
    k10 = float(len(servicios_afectados))

    # --- K5: Disponibilidad por servicio (%) = media global ponderada. ---
    # Media ponderada por ticks de todas las ONTs del estadio (la vista de SLA,
    # tarea 9.6/9.8, desglosa por servicio y compara contra el objetivo por
    # protección). Ver §6.5.
    todas_onts: list[ONT] = list(_iter_onts(estadio)) if estadio is not None else []
    k5 = disponibilidad_servicio(todas_onts) * 100.0

    # --- K7/K8: alarmas (tarea 7.5; si no existe la lista, 0). ---
    alarmas = getattr(state, "alarmas", None) or []
    activas = [a for a in alarmas if _estado_alarma(a) in _ESTADOS_ALARMA_ACTIVA]
    k7 = float(len(activas))
    k8 = float(
        sum(1 for a in activas if _severidad_alarma(a) == _SEVERIDAD_CRITICA)
    )

    # --- K9: Latencia estimada (ms) desde el modelo carga→latencia sobre K1. ---
    k9 = _latencia_estimada_ms(k1_frac)

    # --- K11: Margen de capacidad (%) = 1 - K1 (mínimo 0). ---
    k11 = max(0.0, 100.0 - k1)

    # --- K12: Tiempo de conmutación (ms) = último switch de protección. ---
    # Lo escribe el módulo de fallas (tarea 7.1) en state.ultimo_tiempo_conmutacion_ms.
    # Por defecto 0.0 ms: sin conmutaciones aún, no hay tiempo que reportar.
    k12 = float(getattr(state, "ultimo_tiempo_conmutacion_ms", 0.0) or 0.0)

    valores: dict[str, float] = {
        "K1": k1,
        "K2": k2,
        "K3": k3,
        "K4": k4,
        "K5": k5,
        "K6": k6,
        "K7": k7,
        "K8": k8,
        "K9": k9,
        "K10": k10,
        "K11": k11,
        "K12": k12,
    }

    _empujar_historial(state, valores)
    return valores


# ===========================================================================
# Completitud del catálogo (Req 2.4, usado por la verificación de la tarea 9.9)
# ===========================================================================
def verificar_completitud() -> None:
    """Verifica que los 12 KPIs del catálogo estén definidos (Req 2.4).

    Falla con ``AssertionError`` si falta algún código K1..K12 en :data:`CATALOGO`
    o si aparece uno fuera del catálogo. La prueba de la tarea 9.9 usa esta
    función (o el catálogo directamente) para hacer cumplir Req 2.3/2.4.
    """
    claves = set(CATALOGO.keys())
    esperadas = set(CODIGOS_KPI)
    faltan = esperadas - claves
    sobran = claves - esperadas
    assert not faltan, f"KPIs del catálogo sin implementar: {sorted(faltan)}"
    assert not sobran, f"KPIs fuera del catálogo K1..K12: {sorted(sobran)}"
