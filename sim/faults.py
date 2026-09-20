"""Modelo de fallas y conmutación de protección (design §6.4, Requirement 6).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). Modela la inyección manual de fallas, la conmutación de
protección (TYPE_B/TYPE_C en el mismo tick, NINGUNA cae) y la reaplicación
determinista de las fallas activas cada tick.

Contenido (design §6.4 y "Manejo de fallas de red"):

* :func:`inyectar_falla` — registra una falla sobre un objetivo de la topología
  del estadio activo y **resuelve la conmutación de protección en el mismo
  tick** (postcondición del diseño, Req 6.4/6.5/6.6).
* :func:`conmutar_proteccion` — resuelve la protección de un elemento:
  TYPE_B/TYPE_C conmutan a la reserva en el mismo tick; si la reserva no está
  disponible/operativa ese tick, la ONT protegida pasa **de inmediato** a
  FUERA/DEGRADADO **sin reintentar** (Req 6.6); NINGUNA deja a las ONTs
  dependientes en FUERA (Req 6.4). Devuelve ``True`` solo si mantuvo servicio.
* :func:`aplicar` — punto de entrada que ``sim.engine.tick`` invoca cada tick
  (import protegido, ver `engine._aplicar_fallas`). Reaplica el efecto de todas
  las fallas activas de forma idempotente, de modo que persisten hasta que se
  limpien con :func:`limpiar_fallas` (reset de la simulación). Además invoca el
  paso estocástico opcional (:func:`paso_estocastico`), que es un **no-op salvo
  que se active explícitamente** (``state.fallas_estocasticas_activas``).
* :func:`p_falla_por_tick` / :func:`paso_estocastico` — fallas estocásticas
  (tarea 7.2, design §6.4): modelo exponencial de MTBF, **desactivado por
  defecto**. Solo se ejecuta si se opta explícitamente por él.

Los 5 tipos de falla (design §6.4) se mapean al nivel de jerarquía afectado:

* ``tarjeta_caida``      → la tarjeta y todos sus puertos/árboles/ONTs.
* ``corte_troncal``      → un árbol PON (troncal/splitter) y sus ONTs.
* ``chasis_caido``       → la OLT completa (todo el estadio).
* ``degradacion_optica`` → eleva la atenuación de un árbol; ONTs → DEGRADADO.
* ``pico_capacidad``     → fuerza utilización alta en un puerto (marca de pico).

Tiempo de conmutación (design §7, K12): al conmutar con éxito se registra
``state.ultimo_tiempo_conmutacion_ms`` con un valor < 50 ms para que K12 lo lea.

Nota de alcance: ``SimState`` (tarea 5.3) se referencia por ``TYPE_CHECKING``
para no crear una dependencia real (permite importar este módulo aislado). Se
asume que ``state`` expone: ``estadios``, ``estadio_activo`` y — opcionalmente —
``eventos``. Los atributos de fallas (``fallas_activas``,
``ultimo_tiempo_conmutacion_ms``) se crean por duck typing si no existen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sim.state import (
    ONT,
    ArbolPON,
    Estadio,
    Estado,
    OLT,
    Proteccion,
    PuertoPON,
    Tarjeta,
)

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones de tipo
    from sim.state import SimState


# ---------------------------------------------------------------------------
# Constantes del modelo de fallas (design §6.4 y §7)
# ---------------------------------------------------------------------------
# Tipos de falla soportados por `inyectar_falla` (design §6.4).
TIPOS_FALLA: frozenset[str] = frozenset(
    {
        "tarjeta_caida",
        "corte_troncal",
        "chasis_caido",
        "degradacion_optica",
        "pico_capacidad",
    }
)

# Tiempo de conmutación reportado en una conmutación TYPE_B/TYPE_C exitosa
# (design §7, K12: umbral <= 50 ms; §6.4 tabla: "< 1 tick, < 50 ms"). Se elige
# un valor por debajo del umbral, plausible para una conmutación óptica.
TIEMPO_CONMUTACION_MS: float = 8.0

# Atenuación (dB) que añade una `degradacion_optica`. Con la potencia nominal de
# -22 dBm y el umbral de -28 dBm (K6), 8 dB deja la señal por debajo del umbral
# y justifica el estado DEGRADADO del árbol y sus ONTs.
ATENUACION_DEGRADACION_DB: float = 8.0

# Utilización objetivo que fuerza un `pico_capacidad` (fracción, > 90% dispara
# PON-002 crítica en el motor de alarmas). Se expone como marca en el puerto.
UTILIZACION_PICO: float = 0.95


# ---------------------------------------------------------------------------
# Registro de fallas activas
# ---------------------------------------------------------------------------
@dataclass
class Falla:
    """Una falla inyectada y activa sobre un objetivo de la topología.

    Persiste en ``state.fallas_activas`` hasta que se limpie (reset). Su efecto
    se reaplica de forma idempotente cada tick por :func:`aplicar`.
    """

    tipo: str
    objetivo_id: str
    t_min: float = 0.0  # minuto de partido en que se inyectó (para el log)


def _fallas_activas(state: "SimState") -> list[Falla]:
    """Devuelve (creando si hace falta) la lista de fallas activas del estado."""
    fallas = getattr(state, "fallas_activas", None)
    if fallas is None:
        fallas = []
        state.fallas_activas = fallas  # type: ignore[attr-defined]
    return fallas


def limpiar_fallas(state: "SimState") -> None:
    """Elimina todas las fallas activas (usado por el reset de la simulación).

    No restaura el estado de los elementos ya afectados; el reset recrea la
    topología. Se ofrece para que la capa de presentación pueda limpiar el
    registro de fallas al reiniciar sin recrear el estado completo.
    """
    _fallas_activas(state).clear()


# ---------------------------------------------------------------------------
# Localización del objetivo en la topología del estadio activo
# ---------------------------------------------------------------------------
def _estadio_activo(state: "SimState") -> Estadio | None:
    """Devuelve el estadio activo del estado, o None si no hay topología."""
    estadios = getattr(state, "estadios", None) or {}
    return estadios.get(getattr(state, "estadio_activo", None))


def _buscar_objetivo(
    estadio: Estadio, objetivo_id: str
) -> OLT | Tarjeta | PuertoPON | ArbolPON | ONT | None:
    """Localiza un elemento por id en la jerarquía del estadio (o None).

    Recorre OLT → Tarjeta → PuertoPON → ArbolPON → ONT. La OLT se identifica
    tanto por ``olt.id`` como por ``chasis`` implícito del estadio.
    """
    olt = estadio.olt
    if olt.id == objetivo_id:
        return olt
    for tarjeta in olt.tarjetas:
        if tarjeta.id == objetivo_id:
            return tarjeta
        for puerto in tarjeta.puertos:
            if puerto.id == objetivo_id:
                return puerto
            for arbol in puerto.arboles:
                if arbol.id == objetivo_id:
                    return arbol
                for ont in arbol.onts:
                    if ont.id == objetivo_id:
                        return ont
    return None


# ---------------------------------------------------------------------------
# Recolección de ONTs y protección efectiva por nivel
# ---------------------------------------------------------------------------
def _onts_de(elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT) -> list[ONT]:
    """Devuelve todas las ONTs que cuelgan del elemento dado (recursivo)."""
    if isinstance(elemento, ONT):
        return [elemento]
    if isinstance(elemento, ArbolPON):
        return list(elemento.onts)
    if isinstance(elemento, PuertoPON):
        return [o for a in elemento.arboles for o in a.onts]
    if isinstance(elemento, Tarjeta):
        return [o for p in elemento.puertos for a in p.arboles for o in a.onts]
    if isinstance(elemento, OLT):
        return [
            o
            for t in elemento.tarjetas
            for p in t.puertos
            for a in p.arboles
            for o in a.onts
        ]
    return []


def _puertos_de(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT,
) -> list[PuertoPON]:
    """Devuelve los puertos PON que cuelgan del elemento (para localizar reserva)."""
    if isinstance(elemento, PuertoPON):
        return [elemento]
    if isinstance(elemento, Tarjeta):
        return list(elemento.puertos)
    if isinstance(elemento, OLT):
        return [p for t in elemento.tarjetas for p in t.puertos]
    return []


def _proteccion_efectiva(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT,
) -> Proteccion:
    """Determina el esquema de protección aplicable a las ONTs del elemento.

    La protección vive en ArbolPON y en ONT (design §5.2). Para un elemento de
    nivel superior (puerto/tarjeta/OLT) se toma la protección **más fuerte**
    presente entre sus ONTs/árboles: si algún elemento protegido tiene TYPE_C o
    TYPE_B, la conmutación aplica; si todos son NINGUNA, caen.

    Orden de fuerza: TYPE_C > TYPE_B > NINGUNA.
    """
    if isinstance(elemento, (ArbolPON, ONT)):
        return elemento.proteccion

    fuerza = {Proteccion.NINGUNA: 0, Proteccion.TYPE_B: 1, Proteccion.TYPE_C: 2}
    mejor = Proteccion.NINGUNA
    arboles: list[ArbolPON]
    if isinstance(elemento, PuertoPON):
        arboles = list(elemento.arboles)
    elif isinstance(elemento, Tarjeta):
        arboles = [a for p in elemento.puertos for a in p.arboles]
    else:  # OLT
        arboles = [a for t in elemento.tarjetas for p in t.puertos for a in p.arboles]
    for arbol in arboles:
        if fuerza[arbol.proteccion] > fuerza[mejor]:
            mejor = arbol.proteccion
    return mejor


def _reserva_disponible(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT,
) -> bool:
    """Indica si existe un recurso de reserva operativo para la conmutación.

    Modelo (design §6.4): la conmutación TYPE_B activa un ``PuertoPON`` con
    ``es_reserva=True``. La reserva está disponible si existe al menos un puerto
    de reserva **no** FUERA asociado al mismo alcance del elemento.

    Para elementos por debajo del puerto (árbol/ONT) se busca la reserva en el
    puerto que los contiene (localizado por el llamador vía ``state``); aquí, a
    falta de ese contexto, se considera disponible si el propio elemento o su
    ámbito expone un puerto de reserva operativo. La búsqueda por estadio se
    hace en :func:`conmutar_proteccion` cuando el elemento no tiene puertos.
    """
    puertos = _puertos_de(elemento)
    return any(p.es_reserva and p.estado != Estado.FUERA for p in puertos)


def _reserva_disponible_en_estadio(estadio: Estadio | None) -> bool:
    """¿Hay algún puerto de reserva operativo en el estadio? (fallback).

    Se usa para elementos por debajo del puerto (ArbolPON/ONT), cuyo recurso de
    reserva es un puerto de la misma tarjeta marcado ``es_reserva``.
    """
    if estadio is None:
        return False
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if puerto.es_reserva and puerto.estado != Estado.FUERA:
                return True
    return False


# ---------------------------------------------------------------------------
# 6.4 Conmutación de protección
# ---------------------------------------------------------------------------
def conmutar_proteccion(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT,
    state: "SimState",
    *,
    degradar_si_falla: bool = False,
) -> bool:
    """Resuelve la protección del elemento **dentro del mismo tick** (design §6.4).

    Comportamiento (Req 6.4/6.5/6.6, tabla de conmutación del diseño):

    * ``TYPE_B`` / ``TYPE_C``: si hay una reserva disponible y operativa **este
      mismo tick**, se conmuta: la reserva pasa a ``EN_LINEA`` y las ONTs
      protegidas se mantienen ``EN_LINEA``. Se registra el tiempo de
      conmutación (``state.ultimo_tiempo_conmutacion_ms`` < 50 ms, K12) y se
      devuelve ``True``. Si la reserva **no** está disponible/operativa ese
      tick, la ONT protegida pasa **de inmediato** a ``FUERA`` (o ``DEGRADADO``
      si ``degradar_si_falla``) **sin reintentar** en ticks posteriores
      (Req 6.6) y se devuelve ``False``.
    * ``NINGUNA``: las ONTs dependientes pasan a ``FUERA`` (Req 6.4). Devuelve
      ``False`` (no se mantuvo el servicio).

    Devuelve ``True`` solo si la conmutación mantuvo el servicio.
    """
    proteccion = _proteccion_efectiva(elemento)
    onts = _onts_de(elemento)

    if proteccion == Proteccion.NINGUNA:
        # Sin redundancia: las ONTs dependientes caen (Req 6.4).
        for ont in onts:
            ont.estado = Estado.FUERA
        return False

    # TYPE_B / TYPE_C: intentar conmutar a la reserva en el mismo tick.
    estadio = _estadio_activo(state)
    disponible = _reserva_disponible(elemento) or _reserva_disponible_en_estadio(
        estadio
    )

    if not disponible:
        # Req 6.6: reserva no disponible este tick -> la ONT protegida cae de
        # inmediato (FUERA o DEGRADADO) SIN reintentar en ticks posteriores.
        estado_caida = Estado.DEGRADADO if degradar_si_falla else Estado.FUERA
        for ont in onts:
            ont.estado = estado_caida
        return False

    # Conmutación exitosa: activar la reserva y mantener EN_LINEA las ONTs.
    _activar_reserva(elemento, estadio)
    for ont in onts:
        ont.estado = Estado.EN_LINEA
    # Registrar el tiempo de conmutación para K12 (< 50 ms, design §7).
    state.ultimo_tiempo_conmutacion_ms = TIEMPO_CONMUTACION_MS  # type: ignore[attr-defined]
    return True


def _activar_reserva(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT,
    estadio: Estadio | None,
) -> None:
    """Pone EN_LINEA el primer puerto de reserva operativo del ámbito del elemento.

    Busca primero entre los puertos del propio elemento; si no hay (árbol/ONT),
    recae en cualquier puerto de reserva del estadio (misma tarjeta lógica).
    """
    for puerto in _puertos_de(elemento):
        if puerto.es_reserva and puerto.estado != Estado.FUERA:
            puerto.estado = Estado.EN_LINEA
            return
    if estadio is not None:
        for tarjeta in estadio.olt.tarjetas:
            for puerto in tarjeta.puertos:
                if puerto.es_reserva and puerto.estado != Estado.FUERA:
                    puerto.estado = Estado.EN_LINEA
                    return


# ---------------------------------------------------------------------------
# 6.4 Efecto de cada tipo de falla sobre la jerarquía
# ---------------------------------------------------------------------------
def _aplicar_tarjeta_caida(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT, state: "SimState"
) -> None:
    """`tarjeta_caida`: marca la tarjeta/puertos FUERA y resuelve la protección.

    El objetivo puede ser una tarjeta (o cualquier elemento por debajo). Marca
    el elemento y sus puertos como FUERA y delega la resolución de las ONTs a
    :func:`conmutar_proteccion` (TYPE_B/TYPE_C mantienen servicio, NINGUNA cae).
    """
    if isinstance(elemento, Tarjeta):
        elemento.estado = Estado.FUERA
        for puerto in elemento.puertos:
            if not puerto.es_reserva:
                puerto.estado = Estado.FUERA
    elif isinstance(elemento, PuertoPON):
        if not elemento.es_reserva:
            elemento.estado = Estado.FUERA
    conmutar_proteccion(elemento, state)


def _aplicar_corte_troncal(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT, state: "SimState"
) -> None:
    """`corte_troncal`: corte de fibra troncal/splitter de un árbol PON.

    Marca el árbol FUERA. Con TYPE_C sobrevive por redundancia completa (la
    conmutación mantiene EN_LINEA); sin protección suficiente, las ONTs caen.
    """
    if isinstance(elemento, ArbolPON):
        elemento.estado = Estado.FUERA
    conmutar_proteccion(elemento, state)


def _aplicar_chasis_caido(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT, state: "SimState"
) -> None:
    """`chasis_caido`: caída de la OLT completa (nivel estadio).

    Marca la OLT y sus tarjetas FUERA y resuelve la protección de todas sus
    ONTs. Sin redundancia completa, las ONTs dependientes caen a FUERA.
    """
    if isinstance(elemento, OLT):
        elemento.estado = Estado.FUERA
        for tarjeta in elemento.tarjetas:
            tarjeta.estado = Estado.FUERA
            for puerto in tarjeta.puertos:
                if not puerto.es_reserva:
                    puerto.estado = Estado.FUERA
    conmutar_proteccion(elemento, state)


def _aplicar_degradacion_optica(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT, state: "SimState"
) -> None:
    """`degradacion_optica`: eleva la atenuación del árbol; ONTs → DEGRADADO.

    A diferencia de las fallas duras, la degradación no deja las ONTs FUERA:
    quedan DEGRADADO (siguen dando servicio con señal reducida). Eleva
    ``atenuacion_db`` del/los árboles afectados para que el modelo de tráfico
    baje la potencia óptica Rx (dispara ONT-002 en el motor de alarmas).
    """
    arboles: list[ArbolPON]
    if isinstance(elemento, ArbolPON):
        arboles = [elemento]
    elif isinstance(elemento, PuertoPON):
        arboles = list(elemento.arboles)
    elif isinstance(elemento, Tarjeta):
        arboles = [a for p in elemento.puertos for a in p.arboles]
    elif isinstance(elemento, OLT):
        arboles = [a for t in elemento.tarjetas for p in t.puertos for a in p.arboles]
    else:  # ONT: degradar su árbol contenedor no es localizable aquí; degrada la ONT.
        elemento.estado = Estado.DEGRADADO
        return

    for arbol in arboles:
        arbol.atenuacion_db = max(arbol.atenuacion_db, ATENUACION_DEGRADACION_DB)
        for ont in arbol.onts:
            if ont.estado == Estado.EN_LINEA:
                ont.estado = Estado.DEGRADADO


def _aplicar_pico_capacidad(
    elemento: OLT | Tarjeta | PuertoPON | ArbolPON | ONT, state: "SimState"
) -> None:
    """`pico_capacidad`: fuerza una utilización alta en el/los puertos afectados.

    Marca un ``pico`` sobre el puerto (``utilizacion_forzada``) que el motor de
    tráfico/alarmas lee para elevar la utilización por encima del umbral
    (PON-001 > 60%, PON-002 > 90%). No cambia el estado de servicio de las ONTs
    (es una falla de congestión, no de conectividad).
    """
    puertos = _puertos_de(elemento)
    if not puertos and isinstance(elemento, (ArbolPON, ONT)):
        # Para árbol/ONT no hay puerto directo accesible; se marca el pico en el
        # estado global como fallback (el motor de alarmas puede leerlo).
        state.pico_capacidad_forzado = True  # type: ignore[attr-defined]
        return
    for puerto in puertos:
        puerto.utilizacion_forzada = UTILIZACION_PICO  # type: ignore[attr-defined]


# Despacho por tipo de falla.
_APLICADORES = {
    "tarjeta_caida": _aplicar_tarjeta_caida,
    "corte_troncal": _aplicar_corte_troncal,
    "chasis_caido": _aplicar_chasis_caido,
    "degradacion_optica": _aplicar_degradacion_optica,
    "pico_capacidad": _aplicar_pico_capacidad,
}


def _aplicar_una_falla(falla: Falla, state: "SimState") -> None:
    """Reaplica el efecto de una falla sobre la topología actual (idempotente)."""
    estadio = _estadio_activo(state)
    if estadio is None:
        return
    elemento = _buscar_objetivo(estadio, falla.objetivo_id)
    if elemento is None:
        return
    aplicador = _APLICADORES.get(falla.tipo)
    if aplicador is not None:
        aplicador(elemento, state)


# ---------------------------------------------------------------------------
# 6.4 Inyección manual de fallas
# ---------------------------------------------------------------------------
def inyectar_falla(state: "SimState", tipo: str, objetivo_id: str) -> None:
    """Inyecta una falla y resuelve la conmutación **en el mismo tick** (design §6.4).

    Precondición: ``objetivo_id`` existe en la topología del estadio activo.
    Postcondición: el estado del objetivo se marca y la conmutación de
    protección se resuelve en el mismo tick (Req 6.4/6.5/6.6). La falla queda
    registrada en ``state.fallas_activas`` y su efecto se reaplica cada tick por
    :func:`aplicar` hasta que se limpie (reset).

    Args:
        state: estado de la simulación (con topología del estadio activo).
        tipo: uno de :data:`TIPOS_FALLA`.
        objetivo_id: id de un elemento de la topología del estadio activo.

    Raises:
        ValueError: si ``tipo`` no es un tipo de falla soportado, o si
            ``objetivo_id`` no existe en la topología del estadio activo
            (violación de la precondición del diseño).
    """
    if tipo not in TIPOS_FALLA:
        raise ValueError(
            f"Tipo de falla no soportado: {tipo!r}. "
            f"Válidos: {sorted(TIPOS_FALLA)}"
        )

    estadio = _estadio_activo(state)
    if estadio is None:
        raise ValueError("No hay estadio activo cargado en el estado.")

    if _buscar_objetivo(estadio, objetivo_id) is None:
        raise ValueError(
            f"objetivo_id {objetivo_id!r} no existe en la topología del "
            f"estadio activo {estadio.id!r}."
        )

    # Registrar la falla como activa (persiste hasta el reset).
    falla = Falla(
        tipo=tipo,
        objetivo_id=objetivo_id,
        t_min=float(getattr(state, "t_min", 0.0)),
    )
    _fallas_activas(state).append(falla)

    # Resolver el efecto y la conmutación de protección en el mismo tick.
    _aplicar_una_falla(falla, state)


# ---------------------------------------------------------------------------
# 6.1 Punto de entrada del bucle: aplicar (invocado por engine.tick)
# ---------------------------------------------------------------------------
def aplicar(state: "SimState") -> None:
    """Reaplica todas las fallas activas del estado (design §6.1, invocado por tick).

    ``sim.engine.tick`` llama a esta función cada tick (import protegido). Es
    **idempotente**: reaplicar una falla que ya dejó el objetivo en su estado
    final no lo cambia. Así las fallas inyectadas **persisten** de tick en tick
    hasta que se limpian con :func:`limpiar_fallas` (o se recrea el estado en un
    reset), tal como pide el diseño ("las fallas persisten hasta cerrarse").
    """
    for falla in _fallas_activas(state):
        _aplicar_una_falla(falla, state)
    # Fallas estocásticas (tarea 7.2): NO-OP salvo que se activen explícitamente.
    paso_estocastico(state)


# ---------------------------------------------------------------------------
# Falla estocástica (tarea 7.2, opcional): modelo exponencial de MTBF
# ---------------------------------------------------------------------------
def p_falla_por_tick(delta_t_min: float, mtbf_min: float) -> float:
    """Probabilidad de falla en un tick dado el MTBF (modelo exponencial, §6.4).

    Se incluye aquí por cohesión del modelo de fallas; permanece **desactivada
    por defecto** (no la invoca ``aplicar`` a menos que se active explícitamente,
    ver :func:`paso_estocastico`). Con ``mtbf_min <= 0`` devuelve 0.0 (sin
    fallas). La activación de fallas estocásticas es la tarea 7.2.
    """
    if mtbf_min <= 0:
        return 0.0
    return 1.0 - math.exp(-delta_t_min / mtbf_min)


# MTBF por defecto (minutos) para las fallas estocásticas cuando se activan sin
# especificar uno. Se elige un valor grande (fallas raras) para que la demo, al
# activarse, no se sature de fallas. Solo se usa si la activación no fija otro.
MTBF_POR_DEFECTO_MIN: float = 100_000.0


def _mtbf_estocastico(state: "SimState") -> float:
    """MTBF (min) configurado para el paso estocástico; por defecto grande."""
    return float(getattr(state, "mtbf_estocastico_min", MTBF_POR_DEFECTO_MIN))


def _objetivos_para_falla_estocastica(estadio: Estadio) -> list[str]:
    """Ids de árboles PON del estadio, candidatos a un corte troncal estocástico.

    El paso estocástico simula cortes de troncal (``corte_troncal``), la falla
    de red más representativa a nivel de árbol PON. Devuelve los ids de todos
    los árboles del estadio activo (deterministamente ordenados por recorrido).
    """
    return [
        arbol.id
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    ]


def paso_estocastico(state: "SimState") -> bool:
    """Inyecta (opcionalmente) una falla estocástica en el tick actual (tarea 7.2).

    **Desactivado por defecto.** Solo actúa si ``state.fallas_estocasticas_activas``
    es verdadero (atributo opt-in; si no existe se trata como ``False``). Cuando
    está activo, calcula la probabilidad de falla del tick con
    :func:`p_falla_por_tick` (modelo exponencial, design §6.4) usando el paso de
    tiempo ``state.velocidad`` y el MTBF configurado, y decide **de forma
    determinista** —sembrando el generador con ``state.semilla`` y el minuto de
    partido, igual que ``sim.traffic``— si inyecta un ``corte_troncal`` sobre un
    árbol PON del estadio activo.

    Devuelve ``True`` si inyectó una falla en este tick, ``False`` en caso
    contrario (incluido el caso —el habitual— en que está desactivado). No lanza
    excepciones: si no hay estadio/objetivos, es un no-op.

    Determinismo (Req 17.3): dos corridas con la misma semilla, escenario y
    activación producen exactamente la misma secuencia de fallas estocásticas.
    """
    if not getattr(state, "fallas_estocasticas_activas", False):
        return False  # opt-in: comportamiento por defecto sin cambios.

    mtbf_min = _mtbf_estocastico(state)
    delta_t_min = float(getattr(state, "velocidad", 1.0))
    p = p_falla_por_tick(delta_t_min, mtbf_min)
    if p <= 0.0:
        return False

    estadio = _estadio_activo(state)
    if estadio is None:
        return False
    objetivos = _objetivos_para_falla_estocastica(estadio)
    if not objetivos:
        return False

    rng = _rng_estocastico(state)
    if rng.random() >= p:
        return False  # este tick no ocurre falla.

    # Ocurre una falla: elegir un objetivo de forma determinista.
    objetivo_id = objetivos[int(rng.integers(len(objetivos)))]
    inyectar_falla(state, "corte_troncal", objetivo_id)
    return True


def _rng_estocastico(state: "SimState"):
    """Generador determinista para el paso estocástico (siembra semilla + t_min).

    Usa un ``SeedSequence`` con ``(state.semilla, t_min discretizado)`` para que
    la decisión sea reproducible con la misma semilla y distinta cada tick, sin
    depender del orden de otras llamadas al RNG (mismo patrón que ``sim.traffic``).
    """
    import numpy as np  # import local: mantiene faults importable sin numpy.

    semilla = int(getattr(state, "semilla", 42))
    t_clave = int(round(float(getattr(state, "t_min", 0.0)) * 100))
    # Sub-flujo distinto al de tráfico (offset) para no correlacionar decisiones.
    seq = np.random.SeedSequence([semilla, t_clave & 0xFFFFFFFF, 0xFA11])
    return np.random.default_rng(seq)
