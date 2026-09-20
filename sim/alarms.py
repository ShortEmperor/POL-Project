"""Motor de alarmas del Motor de Simulación (design §8, Requirement 4).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). Evalúa cada tick las condiciones del catálogo de alarmas contra el
estado actual, gestiona el ciclo de vida de las alarmas, las correlaciona bajo
una causa raíz y mantiene un histórico de sesión.

Contenido (design §8):

* :class:`EstadoAlarma` — ciclo de vida NUEVA → ACTIVA → RECONOCIDA → CERRADA
  (design §8.4).
* :class:`Alarma` — dataclass canónica de una alarma (design §8.4).
* :data:`CATALOGO` — catálogo de las **10** alarmas (design §8.2) con código,
  condición de disparo, severidad y condición de cierre. Cada entrada expone dos
  predicados puros ``disparo(ctx)`` y ``cierre(ctx)`` sobre un contexto de
  origen ya resuelto, de modo que la lógica de detección vive en un solo lugar.
* :func:`evaluar` — punto de entrada que ``sim.engine.tick`` invoca cada tick
  (import protegido, ver ``engine._evaluar_alarmas``). Detecta condiciones,
  genera/promueve/cierra alarmas (ciclo de vida) y correlaciona.
* :func:`correlacionar` — colapsa las ONT-001 derivadas bajo una raíz OLT-001 /
  OLT-003 activa (Req 4.5/4.6) y cierra en cascada las derivadas al cerrarse la
  raíz (Req 4.7).
* :func:`reconocer` — mueve una alarma ACTIVA → RECONOCIDA (ack del operador,
  para la tarea 7.8 de UI).

Integración con el estado (duck typing, design §6.1/§6.2):

* ``state.alarmas`` — lista de alarmas **de sesión** (activas + cerradas). K7/K8
  de ``sim.kpis`` la leen y solo cuentan las que están en estado
  {NUEVA, ACTIVA, RECONOCIDA}. Aquí mantenemos también las CERRADA como
  histórico de sesión (Req 4.8); la UI filtra por estado.
* ``state.util_por_puerto`` — dict puerto_id → utilización (0..1+), calculado por
  ``sim.traffic.actualizar`` (dispara PON-001 / PON-002).
* ``state.estadios`` / ``state.estadio_activo`` — topología para recorrer ONTs,
  árboles, puertos, tarjetas y la OLT.
* ``state.t_min`` — minuto de partido, para sellar apertura/cierre.

Nota de alcance: ``SimState`` (tarea 5.3) se referencia por ``TYPE_CHECKING``
para no crear una dependencia real (permite importar este módulo aislado). Este
módulo **no** recalcula tráfico ni fallas; solo **lee** el estado ya resuelto en
el orden del tick (traffic → faults → alarms, design §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

from sim.state import ONT, ArbolPON, Estadio, Estado, OLT, PuertoPON, Tarjeta

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones de tipo
    from sim.state import SimState


# ===========================================================================
# 8.4 Ciclo de vida y dataclass de alarma
# ===========================================================================
class EstadoAlarma(Enum):
    """Estados del ciclo de vida de una alarma (design §8.4).

    Transiciones (máquina de estados del diseño)::

        [*]       -> NUEVA       (la condición se cumple)
        NUEVA     -> ACTIVA      (persiste tras 1 tick)
        ACTIVA    -> RECONOCIDA  (el operador reconoce)
        ACTIVA    -> CERRADA     (la condición desaparece, auto)
        RECONOCIDA-> CERRADA     (la condición desaparece)
        CERRADA   -> [*]
    """

    NUEVA = "nueva"
    ACTIVA = "activa"
    RECONOCIDA = "reconocida"
    CERRADA = "cerrada"


# Estados en los que una alarma se considera "activa" (viva) — coincide con el
# conjunto que K7 cuenta en sim.kpis (design §7).
ESTADOS_VIVOS: frozenset[EstadoAlarma] = frozenset(
    {EstadoAlarma.NUEVA, EstadoAlarma.ACTIVA, EstadoAlarma.RECONOCIDA}
)


@dataclass
class Alarma:
    """Una alarma del catálogo instanciada sobre un elemento (design §8.4).

    Atributos:
        codigo: código del catálogo ("PON-001", etc.).
        severidad: token de severidad ("critica" | "mayor" | "menor" | "aviso",
            design §8.1).
        estado: estado del ciclo de vida (:class:`EstadoAlarma`).
        origen_id: id del elemento de la topología que la originó.
        t_min_apertura: minuto de partido en que se abrió (estado NUEVA).
        t_min_cierre: minuto de partido en que se cerró, o ``None`` si sigue viva.
        raiz_id: si es una alarma **derivada** correlacionada, el id del elemento
            raíz (OLT-001 / OLT-003) bajo el que se agrupa; ``None`` si no lo es.
        mensaje: descripción legible para la UI.
    """

    codigo: str
    severidad: str
    estado: EstadoAlarma
    origen_id: str
    t_min_apertura: float
    t_min_cierre: float | None = None
    raiz_id: str | None = None
    mensaje: str = ""

    @property
    def viva(self) -> bool:
        """``True`` si la alarma está en un estado vivo (no CERRADA)."""
        return self.estado in ESTADOS_VIVOS

    @property
    def derivada(self) -> bool:
        """``True`` si la alarma quedó correlacionada bajo una raíz (Req 4.5)."""
        return self.raiz_id is not None


# ===========================================================================
# 8.1 Severidades (tokens del diseño)
# ===========================================================================
SEV_CRITICA = "critica"
SEV_MAYOR = "mayor"
SEV_MENOR = "menor"
SEV_AVISO = "aviso"

# Orden de fuerza de severidad (para desempatar raíces, Req 4.6).
_FUERZA_SEVERIDAD: dict[str, int] = {
    SEV_AVISO: 0,
    SEV_MENOR: 1,
    SEV_MAYOR: 2,
    SEV_CRITICA: 3,
}


# ===========================================================================
# Umbrales del catálogo (design §8.2, con histéresis donde aplica)
# ===========================================================================
# PON-001: utilización de puerto > 60% (disparo); baja del umbral con histéresis
# (cierre a < 55%) para evitar parpadeo alrededor del umbral.
UMBRAL_PON001_ALTA = 0.60
UMBRAL_PON001_BAJA = 0.55
# PON-002: saturación > 90% (disparo); cierre < 85% (histéresis del diseño).
UMBRAL_PON002_ALTA = 0.90
UMBRAL_PON002_BAJA = 0.85
# LAT-001: latencia > 10 ms (disparo); cierre < 8 ms (histéresis del diseño §8.2).
UMBRAL_LAT001_ALTA_MS = 10.0
UMBRAL_LAT001_BAJA_MS = 8.0
# Potencia óptica de referencia para ONT-002 (design §5.2/§7, K6 umbral -28 dBm).
UMBRAL_POTENCIA_DEGRADADA_DBM = -28.0

# Modelo carga→latencia (mismos parámetros que sim.kpis K9, design §7) para que
# LAT-001 sea coherente con el KPI de latencia. Se replican aquí para no crear
# una dependencia de import de kpis (que a su vez no depende de alarms).
LAT_BASE_MS = 2.0
LAT_COLA_MS = 1.5
LAT_U_MAX = 0.98


def _latencia_estimada_ms(util_pico: float) -> float:
    """Modelo carga→latencia (idéntico a K9 en sim.kpis). ``util_pico`` en 0..1+."""
    u = max(0.0, min(util_pico, LAT_U_MAX))
    return LAT_BASE_MS + LAT_COLA_MS * u / (1.0 - u)


# ===========================================================================
# 8.2 Catálogo de las 10 alarmas
# ===========================================================================
@dataclass(frozen=True)
class _CtxOrigen:
    """Contexto de una condición candidata resuelta contra el estado actual.

    Es la unidad que ``evaluar`` produce al recorrer el estado: identifica el
    elemento (``origen_id``) y trae los datos que los predicados de disparo y
    cierre necesitan. Mantener los predicados sobre este contexto evita duplicar
    los recorridos de topología.
    """

    origen_id: str
    # Utilización del puerto (para PON-001/PON-002), fracción 0..1+.
    utilizacion: float = 0.0
    # Estado del elemento (para ONT-001/ONT-002/OLT-001/OLT-002/OLT-003).
    estado: Estado | None = None
    # Potencia óptica (dBm) para ONT-002.
    potencia_dbm: float = 0.0
    # Latencia estimada (ms) para LAT-001.
    latencia_ms: float = 0.0
    # Carga PoE / densidad WiFi normalizadas (0..1+) para PoE-001 / WIFI-001.
    carga_rel: float = 0.0
    # id de la OLT contenedora (para correlación de ONT-001).
    olt_id: str | None = None


@dataclass(frozen=True)
class DefAlarma:
    """Definición de un tipo de alarma del catálogo (design §8.2).

    Atributos:
        codigo: código del catálogo.
        severidad: token de severidad (design §8.1).
        descripcion_disparo: texto legible de la condición de disparo.
        descripcion_cierre: texto legible de la condición de cierre.
        disparo: predicado ``(_CtxOrigen) -> bool`` que evalúa el disparo.
        cierre: predicado ``(_CtxOrigen) -> bool`` que evalúa el cierre. Con
            histéresis, ``cierre`` no es simplemente ``not disparo``: hay una
            banda muerta entre ambos umbrales (design §8.2).
    """

    codigo: str
    severidad: str
    descripcion_disparo: str
    descripcion_cierre: str
    disparo: Callable[[_CtxOrigen], bool]
    cierre: Callable[[_CtxOrigen], bool]


CATALOGO: dict[str, DefAlarma] = {
    "PON-001": DefAlarma(
        codigo="PON-001",
        severidad=SEV_MAYOR,
        descripcion_disparo="Utilización de puerto > 60%",
        descripcion_cierre="Utilización baja del umbral (< 55%, histéresis)",
        disparo=lambda c: c.utilizacion > UMBRAL_PON001_ALTA,
        cierre=lambda c: c.utilizacion < UMBRAL_PON001_BAJA,
    ),
    "PON-002": DefAlarma(
        codigo="PON-002",
        severidad=SEV_CRITICA,
        descripcion_disparo="Utilización de puerto > 90% (saturación)",
        descripcion_cierre="Utilización < 85%",
        disparo=lambda c: c.utilizacion > UMBRAL_PON002_ALTA,
        cierre=lambda c: c.utilizacion < UMBRAL_PON002_BAJA,
    ),
    "ONT-001": DefAlarma(
        codigo="ONT-001",
        severidad=SEV_MAYOR,
        descripcion_disparo="ONT pasa a FUERA",
        descripcion_cierre="ONT vuelve a EN_LINEA",
        disparo=lambda c: c.estado == Estado.FUERA,
        cierre=lambda c: c.estado == Estado.EN_LINEA,
    ),
    "ONT-002": DefAlarma(
        codigo="ONT-002",
        severidad=SEV_MENOR,
        descripcion_disparo="ONT en DEGRADADO (señal baja)",
        descripcion_cierre="Señal se recupera",
        disparo=lambda c: c.estado == Estado.DEGRADADO
        or c.potencia_dbm < UMBRAL_POTENCIA_DEGRADADA_DBM,
        cierre=lambda c: c.estado == Estado.EN_LINEA
        and c.potencia_dbm >= UMBRAL_POTENCIA_DEGRADADA_DBM,
    ),
    "OLT-001": DefAlarma(
        codigo="OLT-001",
        severidad=SEV_CRITICA,
        descripcion_disparo="Chasis OLT caído",
        descripcion_cierre="Chasis restaurado",
        disparo=lambda c: c.estado == Estado.FUERA,
        cierre=lambda c: c.estado != Estado.FUERA,
    ),
    "OLT-002": DefAlarma(
        codigo="OLT-002",
        severidad=SEV_MAYOR,
        descripcion_disparo="Tarjeta caída",
        descripcion_cierre="Tarjeta restaurada (o conmutación)",
        disparo=lambda c: c.estado == Estado.FUERA,
        cierre=lambda c: c.estado != Estado.FUERA,
    ),
    "OLT-003": DefAlarma(
        codigo="OLT-003",
        severidad=SEV_CRITICA,
        descripcion_disparo="Corte de fibra troncal",
        descripcion_cierre="Fibra restaurada / conmutación Type C",
        disparo=lambda c: c.estado == Estado.FUERA,
        cierre=lambda c: c.estado != Estado.FUERA,
    ),
    "PoE-001": DefAlarma(
        codigo="PoE-001",
        severidad=SEV_MENOR,
        descripcion_disparo="Presupuesto PoE excedido en un nodo",
        descripcion_cierre="Carga PoE normaliza",
        disparo=lambda c: c.carga_rel > 1.0,
        cierre=lambda c: c.carga_rel <= 0.95,
    ),
    "WIFI-001": DefAlarma(
        codigo="WIFI-001",
        severidad=SEV_MENOR,
        descripcion_disparo="Densidad de asociaciones WiFi > capacidad AP",
        descripcion_cierre="Densidad baja",
        disparo=lambda c: c.carga_rel > 1.0,
        cierre=lambda c: c.carga_rel <= 0.95,
    ),
    "LAT-001": DefAlarma(
        codigo="LAT-001",
        severidad=SEV_MENOR,
        descripcion_disparo="Latencia estimada > 10 ms",
        descripcion_cierre="Latencia < 8 ms",
        disparo=lambda c: c.latencia_ms > UMBRAL_LAT001_ALTA_MS,
        cierre=lambda c: c.latencia_ms < UMBRAL_LAT001_BAJA_MS,
    ),
}

# Códigos raíz para la correlación (design §8.3): OLT-001 (chasis) y OLT-003
# (corte troncal) colapsan las ONT-001 derivadas bajo ellos.
CODIGOS_RAIZ: frozenset[str] = frozenset({"OLT-001", "OLT-003"})
# Código de las alarmas derivadas que se colapsan bajo una raíz (design §8.3).
CODIGO_DERIVADA: str = "ONT-001"


# ===========================================================================
# Utilidades de topología
# ===========================================================================
def _estadio_activo(state: "SimState") -> Estadio | None:
    """Devuelve el :class:`Estadio` activo del estado, o ``None`` si no hay."""
    estadios = getattr(state, "estadios", None) or {}
    estadio_id = getattr(state, "estadio_activo", None)
    return estadios.get(estadio_id) if estadios else None


def _mensaje(defn: DefAlarma, origen_id: str) -> str:
    """Compone el mensaje legible de una alarma para la UI."""
    return f"[{defn.codigo}] {defn.descripcion_disparo} — {origen_id}"


# ===========================================================================
# Detección: producir el conjunto de condiciones candidatas por tick
# ===========================================================================
def _contextos(state: "SimState") -> dict[tuple[str, str], _CtxOrigen]:
    """Construye el contexto de cada condición candidata del estadio activo.

    Devuelve un dict indexado por la **clave de alarma** ``(codigo, origen_id)``,
    de modo que ``evaluar`` puede casar cada condición con su alarma viva de
    forma directa. Recorre la topología una sola vez.

    Cobertura del catálogo:

    * PON-001 / PON-002 → por puerto (utilización de ``state.util_por_puerto``).
    * OLT-001 → por OLT (estado del chasis).
    * OLT-002 → por tarjeta (estado).
    * OLT-003 → por árbol PON (corte troncal → árbol FUERA).
    * ONT-001 → por ONT en FUERA.
    * ONT-002 → por ONT DEGRADADO o con potencia bajo umbral.
    * LAT-001 → por estadio (latencia estimada de la utilización pico).
    * PoE-001 / WIFI-001 → por puerto (carga relativa; marca de pico opcional).
    """
    ctxs: dict[tuple[str, str], _CtxOrigen] = {}
    estadio = _estadio_activo(state)
    if estadio is None:
        return ctxs

    util_por_puerto: dict[str, float] = getattr(state, "util_por_puerto", None) or {}
    olt = estadio.olt
    olt_id = olt.id

    # --- OLT-001: chasis caído (nivel OLT). ---
    ctxs[("OLT-001", olt_id)] = _CtxOrigen(
        origen_id=olt_id, estado=olt.estado, olt_id=olt_id
    )

    util_pico = 0.0

    for tarjeta in olt.tarjetas:
        # --- OLT-002: tarjeta caída. ---
        ctxs[("OLT-002", tarjeta.id)] = _CtxOrigen(
            origen_id=tarjeta.id, estado=tarjeta.estado, olt_id=olt_id
        )
        for puerto in tarjeta.puertos:
            util = util_por_puerto.get(puerto.id, 0.0)
            # Marca de pico forzada por falla pico_capacidad (sim.faults).
            forzada = getattr(puerto, "utilizacion_forzada", None)
            if forzada is not None:
                util = max(util, float(forzada))
            util_pico = max(util_pico, util)

            # --- PON-001 / PON-002: por puerto (utilización). ---
            ctx_puerto = _CtxOrigen(
                origen_id=puerto.id, utilizacion=util, olt_id=olt_id,
                # Carga relativa PoE/WiFi: se aproxima con la utilización del
                # puerto como proxy de densidad (sin instrumentación dedicada en
                # la PoC). Solo dispara con marca de pico explícita (> 1.0).
                carga_rel=util,
            )
            ctxs[("PON-001", puerto.id)] = ctx_puerto
            ctxs[("PON-002", puerto.id)] = ctx_puerto
            # PoE-001 / WIFI-001 comparten el proxy de carga del puerto.
            ctxs[("PoE-001", puerto.id)] = ctx_puerto
            ctxs[("WIFI-001", puerto.id)] = ctx_puerto

            for arbol in puerto.arboles:
                # --- OLT-003: corte troncal → árbol FUERA. ---
                ctxs[("OLT-003", arbol.id)] = _CtxOrigen(
                    origen_id=arbol.id, estado=arbol.estado, olt_id=olt_id
                )
                for ont in arbol.onts:
                    # --- ONT-001 / ONT-002: por ONT. ---
                    ctx_ont = _CtxOrigen(
                        origen_id=ont.id,
                        estado=ont.estado,
                        potencia_dbm=ont.potencia_optica_dbm,
                        olt_id=olt_id,
                    )
                    ctxs[("ONT-001", ont.id)] = ctx_ont
                    ctxs[("ONT-002", ont.id)] = ctx_ont

    # --- LAT-001: por estadio (latencia estimada de la utilización pico). ---
    ctxs[("LAT-001", estadio.id)] = _CtxOrigen(
        origen_id=estadio.id,
        latencia_ms=_latencia_estimada_ms(util_pico),
        olt_id=olt_id,
    )

    return ctxs


# ===========================================================================
# Ciclo de vida: generar, promover y cerrar (design §8.4, Req 4.1/4.3/4.4)
# ===========================================================================
def evaluar(state: "SimState") -> list[Alarma]:
    """Evalúa el catálogo contra el estado y gestiona el ciclo de vida (Req 4.1).

    Invocada por ``sim.engine.tick`` cada tick (design §4). Algoritmo:

    1. Construye los contextos de todas las condiciones candidatas del estadio
       activo (:func:`_contextos`).
    2. Para cada tipo de alarma del catálogo y cada contexto:

       * Si **no** existe una alarma viva para ``(codigo, origen_id)`` y la
         condición de **disparo** se cumple → crea una alarma ``NUEVA`` sellada
         con ``t_min`` (Req 4.1/4.3: la alarma existe en el tick 1, dentro del
         máximo de 2 ticks).
       * Si existe una alarma viva ``NUEVA`` y la condición sigue cumpliéndose →
         la promueve a ``ACTIVA`` (persiste tras 1 tick, design §8.4).
       * Si existe una alarma viva y la condición de **cierre** se cumple → la
         cierra (``CERRADA``, sella ``t_min_cierre``) automáticamente (Req 4.4).

    3. Correlaciona las alarmas resultantes (:func:`correlacionar`, Req 4.5–4.7).

    Mantiene ``state.alarmas`` como la lista de alarmas de **sesión** (vivas +
    cerradas): las cerradas se conservan como histórico (Req 4.8) y la UI filtra
    por estado. K7/K8 de ``sim.kpis`` solo cuentan las vivas.

    Devuelve la lista de alarmas de sesión (el mismo objeto de ``state.alarmas``).
    """
    t_min = float(getattr(state, "t_min", 0.0))
    alarmas: list[Alarma] = getattr(state, "alarmas", None)
    if not isinstance(alarmas, list):
        alarmas = []
        state.alarmas = alarmas

    # Índice de alarmas VIVAS por clave (codigo, origen_id) para casar rápido.
    vivas: dict[tuple[str, str], Alarma] = {
        (a.codigo, a.origen_id): a for a in alarmas if a.viva
    }

    ctxs = _contextos(state)

    for (codigo, origen_id), ctx in ctxs.items():
        defn = CATALOGO[codigo]
        clave = (codigo, origen_id)
        existente = vivas.get(clave)

        if existente is None:
            # No hay alarma viva: ¿se cumple el disparo? -> crear NUEVA.
            if defn.disparo(ctx):
                nueva = Alarma(
                    codigo=codigo,
                    severidad=defn.severidad,
                    estado=EstadoAlarma.NUEVA,
                    origen_id=origen_id,
                    t_min_apertura=t_min,
                    mensaje=_mensaje(defn, origen_id),
                )
                alarmas.append(nueva)
                vivas[clave] = nueva
            continue

        # Hay alarma viva para esta condición.
        if defn.cierre(ctx):
            # La condición desapareció -> cierre automático (Req 4.4).
            _cerrar(existente, t_min)
        elif existente.estado == EstadoAlarma.NUEVA:
            # Persiste tras 1 tick -> promover NUEVA -> ACTIVA (design §8.4).
            existente.estado = EstadoAlarma.ACTIVA

    # Correlación y cierre en cascada (Req 4.5, 4.6, 4.7).
    correlacionar(alarmas)

    return alarmas


def _cerrar(alarma: Alarma, t_min: float) -> None:
    """Cierra una alarma sellando ``t_min_cierre`` si aún no estaba cerrada."""
    if alarma.estado != EstadoAlarma.CERRADA:
        alarma.estado = EstadoAlarma.CERRADA
        alarma.t_min_cierre = t_min


# ===========================================================================
# 8.3 Correlación y cierre en cascada (Req 4.5, 4.6, 4.7)
# ===========================================================================
def _olt_de_origen(origen_id: str) -> str | None:
    """Extrae el id de la OLT contenedora de un ``origen_id`` jerárquico.

    Los ids siguen la forma ``OLT-<estadio>-T..-P..-A-O..`` (ver
    ``tools/gen_topology.py``); la OLT es el prefijo ``OLT-<estadio>``. Si el id
    no sigue el patrón esperado, devuelve ``None`` (no correlacionable por id).
    """
    partes = origen_id.split("-")
    if len(partes) >= 2 and partes[0] == "OLT":
        return f"{partes[0]}-{partes[1]}"
    return None


def _arbol_de_origen(origen_id: str) -> str | None:
    """Extrae el id del árbol PON contenedor de un ``origen_id`` de ONT.

    Forma ``OLT-<estadio>-T..-P..-A-O..`` → árbol ``OLT-<estadio>-T..-P..-A``.
    Devuelve ``None`` si el id no tiene un sufijo de ONT reconocible.
    """
    partes = origen_id.split("-")
    # ONT: OLT, estadio, Txx, Pxx, A, Oxxx  -> árbol quita el último segmento.
    if len(partes) >= 6 and partes[-1].startswith("O"):
        return "-".join(partes[:-1])
    return None


def _pertenece_a_raiz(derivada: Alarma, raiz: Alarma) -> bool:
    """¿La alarma ``derivada`` (ONT-001) depende del elemento de ``raiz``?

    * Raíz OLT-001 (chasis): toda ONT-001 de la misma OLT es derivada.
    * Raíz OLT-003 (corte troncal de un árbol): solo las ONT-001 de ese árbol.
    """
    if raiz.codigo == "OLT-001":
        # El origen de OLT-001 es la OLT; la ONT pertenece si comparte OLT.
        return _olt_de_origen(derivada.origen_id) == raiz.origen_id
    if raiz.codigo == "OLT-003":
        # El origen de OLT-003 es el árbol; la ONT pertenece si es de ese árbol.
        return _arbol_de_origen(derivada.origen_id) == raiz.origen_id
    return False


def _clave_prioridad_raiz(raiz: Alarma) -> tuple[int, float]:
    """Clave para elegir una sola raíz ante varias candidatas (Req 4.6).

    Se prefiere la de **mayor severidad**; a igualdad, la **primera detectada**
    (menor ``t_min_apertura``). Se devuelve una tupla ordenable donde un valor
    mayor gana: ``(fuerza_severidad, -t_min_apertura)``.
    """
    return (_FUERZA_SEVERIDAD.get(raiz.severidad, 0), -raiz.t_min_apertura)


def correlacionar(alarmas: list[Alarma]) -> list[Alarma]:
    """Correlaciona las ONT-001 derivadas bajo su raíz OLT-001/OLT-003 (§8.3).

    Postcondiciones (Req 4.5, 4.6, 4.7):

    * **Colapso bajo raíz (Req 4.5):** mientras exista una alarma raíz
      (OLT-001 / OLT-003) **viva**, cada alarma ONT-001 viva cuyo origen dependa
      de esa raíz queda marcada como derivada (``raiz_id`` = origen de la raíz).
      La UI lista la raíz una sola vez y no lista las derivadas individualmente
      (``Alarma.derivada`` las identifica).
    * **Raíz única ante múltiples (Req 4.6):** si una ONT-001 puede atribuirse a
      más de una raíz viva (p.ej. OLT-001 y OLT-003 a la vez), se agrupa bajo
      **una sola**, elegida por mayor severidad y, a igualdad, la primera
      detectada (:func:`_clave_prioridad_raiz`).
    * **Cierre en cascada (Req 4.7):** al cerrarse una raíz, todas sus alarmas
      derivadas (las que apuntan a ella por ``raiz_id``) se cierran también.
    * **Desagrupado:** si una derivada deja de pertenecer a cualquier raíz viva
      (p.ej. la raíz se cerró y la derivada sigue viva por otra causa), se limpia
      su ``raiz_id`` para que vuelva a listarse individualmente.

    Opera **in situ** sobre la lista y la devuelve por conveniencia. Es
    idempotente: reejecutarla sobre un estado ya correlacionado no cambia nada.
    """
    # 1) Cierre en cascada: raíz cerrada -> cerrar sus derivadas (Req 4.7).
    #    Se hace primero para que una raíz recién cerrada arrastre a sus
    #    derivadas en el mismo tick que se detecta su cierre.
    raices_cerradas = {
        a.origen_id
        for a in alarmas
        if a.codigo in CODIGOS_RAIZ and not a.viva
    }
    if raices_cerradas:
        # Cierre en el instante de cierre de la raíz (o el más reciente).
        t_cierre_por_raiz: dict[str, float] = {}
        for a in alarmas:
            if a.codigo in CODIGOS_RAIZ and not a.viva:
                t_cierre_por_raiz[a.origen_id] = (
                    a.t_min_cierre if a.t_min_cierre is not None else 0.0
                )
        for a in alarmas:
            if a.derivada and a.viva and a.raiz_id in raices_cerradas:
                _cerrar(a, t_cierre_por_raiz.get(a.raiz_id, a.t_min_apertura))

    # 2) Raíces VIVAS disponibles para agrupar.
    raices_vivas = [
        a for a in alarmas if a.codigo in CODIGOS_RAIZ and a.viva
    ]

    # 3) Re-evaluar cada ONT-001 viva: asignarla a la raíz elegida o desagrupar.
    for a in alarmas:
        if a.codigo != CODIGO_DERIVADA or not a.viva:
            continue
        candidatas = [r for r in raices_vivas if _pertenece_a_raiz(a, r)]
        if candidatas:
            # Elegir una sola raíz (Req 4.6): mayor severidad, luego primera.
            elegida = max(candidatas, key=_clave_prioridad_raiz)
            a.raiz_id = elegida.origen_id
        else:
            # Sin raíz viva aplicable: se lista individualmente.
            a.raiz_id = None

    return alarmas


# ===========================================================================
# Ack del operador (para la tarea 7.8 de UI)
# ===========================================================================
def reconocer(alarma: Alarma) -> Alarma:
    """Mueve una alarma ACTIVA → RECONOCIDA (reconocimiento del operador, §8.4).

    Es la transición de ack que la UI (tarea 7.8) invoca cuando el operador
    reconoce una alarma. Solo aplica desde ``ACTIVA`` (design §8.4); en cualquier
    otro estado es un no-op (una ``NUEVA`` aún no se puede reconocer; una
    ``CERRADA`` ya terminó su ciclo). Devuelve la misma alarma por conveniencia.
    """
    if alarma.estado == EstadoAlarma.ACTIVA:
        alarma.estado = EstadoAlarma.RECONOCIDA
    return alarma


# ===========================================================================
# Consultas de conveniencia para la UI (tabla de activas / histórico, Req 4.8)
# ===========================================================================
def alarmas_activas(state: "SimState") -> list[Alarma]:
    """Alarmas vivas **no derivadas** (lo que la tabla de activas muestra).

    Excluye las CERRADA (histórico) y las derivadas colapsadas bajo una raíz
    (Req 4.5): el operador ve la causa raíz, no cientos de ONT-001.
    """
    alarmas = getattr(state, "alarmas", None) or []
    return [a for a in alarmas if a.viva and not a.derivada]


def historico_alarmas(state: "SimState") -> list[Alarma]:
    """Histórico de sesión: todas las alarmas registradas (vivas + cerradas)."""
    return list(getattr(state, "alarmas", None) or [])
