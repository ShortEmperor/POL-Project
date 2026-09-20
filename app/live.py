"""Estado en vivo compartido de la Capa de Presentación (design §3.2, §4, §6.1).

Este módulo es la frontera entre el Motor de Simulación (Python puro, ``sim/``)
y las vistas de Dash. Mantiene **una única** instancia de :class:`SimState` a
nivel de módulo (design §3.2): todos los navegadores comparten la misma
simulación, tal como exige la demo de sala.

Contenido:

* :data:`ESTADO` — la instancia única de ``SimState`` con la topología real
  cargada. Se siembra con unos pocos ticks al importar el módulo para que las
  gráficas y tarjetas nunca arranquen vacías (Req 15.1/15.2).
* :data:`INTERVALO_ID` / :func:`intervalo` — el **único** ``dcc.Interval`` del
  tablero (design §4). El período se toma de ``POL_TICK_MS`` (por defecto
  1000 ms). Las vistas cuelgan sus callbacks de gráficas de este intervalo; no
  se crea un segundo intervalo en ninguna vista.
* :func:`avanzar` — invocado por el callback del intervalo: avanza el motor un
  ``tick`` cuando la simulación está corriendo (design §4).
* :func:`snapshot` — lectura de solo lectura del estado para las vistas:
  devuelve los KPIs ya calculados por ``sim.kpis`` (nunca recalcula en la capa
  de presentación, Req 2.2) y las series de historial para graficar.

Regla de separación (design §3.1): las vistas leen de aquí; el cálculo vive en
``sim/``. Este módulo importa de ``sim/`` pero ``sim/`` nunca importa de ``app/``.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any

from dash import dcc

from sim import engine, kpis
from sim.state import SimState

# ---------------------------------------------------------------------------
# Parámetros del reloj (design §6.1). Configurables por entorno para la demo.
# ---------------------------------------------------------------------------
def _tick_ms() -> int:
    """Período del intervalo en ms (``POL_TICK_MS``, por defecto 1000)."""
    try:
        valor = int(os.environ.get("POL_TICK_MS", "1000"))
    except (TypeError, ValueError):
        valor = 1000
    return max(200, valor)


# Identificador del único intervalo del tablero (design §4).
INTERVALO_ID = "reloj-simulacion"

# Número de ticks de precalentamiento al arrancar, para que el historial tenga
# puntos y las gráficas/tarjetas nunca se vean vacías al abrir la app
# (Req 15.1/15.2). Es un arranque en fase de ingreso (T-180), no una corrida.
_TICKS_SEMILLA = 30


# ---------------------------------------------------------------------------
# Instancia única del estado (design §3.2)
# ---------------------------------------------------------------------------
def _crear_estado_sembrado() -> SimState:
    """Crea el ``SimState`` único y lo siembra con algunos ticks.

    La siembra corre el motor en modo ``corriendo`` unos pocos ticks para
    poblar ``state.historial`` y los agregados de tráfico, de modo que la vista
    de Resumen y la cuadrícula de Simulación tengan datos desde el primer render
    (Req 15.1/15.2). Tras sembrar, el reloj queda **pausado** en el estado de
    arranque de la demo (el operador pulsa play cuando quiera, tarea 5.8).
    """
    estado = SimState.crear()
    # Correr la siembra sin mover el reloj de forma perceptible: avanzamos a 1x.
    estado.corriendo = True
    for _ in range(_TICKS_SEMILLA):
        engine.tick(estado)
    estado.corriendo = False
    return estado


# Única instancia compartida. Si la topología no puede cargarse (entorno de
# ejecución acotado), se degrada a un estado vacío para que la app siga
# levantando y las vistas muestren indicadores en lugar de fallar (Req 15.3).
try:
    ESTADO: SimState = _crear_estado_sembrado()
except Exception:  # pragma: no cover - fail-soft de arranque
    ESTADO = SimState()


# ---------------------------------------------------------------------------
# Componente de intervalo (único en el tablero)
# ---------------------------------------------------------------------------
def intervalo() -> dcc.Interval:
    """Devuelve el único ``dcc.Interval`` del tablero (design §4)."""
    return dcc.Interval(id=INTERVALO_ID, interval=_tick_ms(), n_intervals=0)


# ---------------------------------------------------------------------------
# Avance del motor (invocado por el callback del intervalo)
# ---------------------------------------------------------------------------
def avanzar() -> None:
    """Avanza el motor un ``tick`` si la simulación está corriendo (design §4).

    Es el punto que el callback del intervalo llama en cada disparo. Si el
    estado está pausado, no mueve el reloj (design §4, rama "pausado").
    """
    if getattr(ESTADO, "corriendo", False):
        engine.tick(ESTADO)


# ---------------------------------------------------------------------------
# Controles (mutan el estado compartido; invocados por app/callbacks.py)
#
# Viven aquí, junto a la instancia única ESTADO, para que el módulo de callbacks
# solo cablee eventos de UI a estas operaciones (separación de capas, §3.1). El
# cálculo (tick, KPIs, alarmas) vive en sim/; estas funciones solo ajustan los
# campos de control del estado.
# ---------------------------------------------------------------------------
def alternar_corriendo() -> bool:
    """Play/Pausa: invierte ``ESTADO.corriendo`` y devuelve el nuevo valor (Req 16.1)."""
    ESTADO.corriendo = not getattr(ESTADO, "corriendo", False)
    return ESTADO.corriendo


def set_velocidad(velocidad: float) -> float:
    """Fija la velocidad restringiéndola a {1x, 5x, 15x} (Req 16.1/16.2).

    Delega en ``SimState.set_velocidad``, que normaliza cualquier valor al
    conjunto permitido de forma continua. Devuelve la velocidad efectiva.
    """
    return ESTADO.set_velocidad(velocidad)


def alternar_escenario() -> str:
    """Conmuta el escenario entre "diseno" y "estres"; devuelve el nuevo valor."""
    ESTADO.escenario = "estres" if getattr(ESTADO, "escenario", "diseno") == "diseno" else "diseno"
    return ESTADO.escenario


def set_escenario(escenario: str) -> str:
    """Fija el escenario a "diseno" o "estres" (ignora valores no soportados)."""
    if escenario in ("diseno", "estres"):
        ESTADO.escenario = escenario
    return ESTADO.escenario


def reset() -> None:
    """Reinicia la simulación reconstruyendo el estado compartido (design §4).

    Recrea ``ESTADO`` con ``SimState.crear`` conservando el estadio y la semilla
    actuales, lo que descarta fallas y alarmas de la sesión (el nuevo estado
    arranca con listas vacías) y vuelve el reloj a T-180, pausado y a 1x. Como
    otras vistas cierran sus callbacks sobre ``live.ESTADO`` por referencia de
    módulo (``app.live.ESTADO``), se **muta la referencia del módulo** y se
    resiembra el historial para que las gráficas no queden vacías (Req 15.1).

    Es fail-soft: si la topología no puede cargarse (entorno acotado), degrada
    a un ``SimState`` vacío como en el arranque.
    """
    global ESTADO
    estadio = getattr(ESTADO, "estadio_activo", "azteca")
    semilla = getattr(ESTADO, "semilla", None)
    try:
        if semilla is not None:
            nuevo = SimState.crear(estadio_activo=estadio, semilla=semilla)
        else:
            nuevo = SimState.crear(estadio_activo=estadio)
        nuevo.corriendo = True
        for _ in range(_TICKS_SEMILLA):
            engine.tick(nuevo)
        nuevo.corriendo = False
    except Exception:  # pragma: no cover - fail-soft de reset
        nuevo = SimState()
    ESTADO = nuevo


def set_estadio(estadio_id: str) -> None:
    """Cambia el estadio activo si existe en la topología cargada."""
    estadios = getattr(ESTADO, "estadios", None) or {}
    if estadio_id in estadios:
        ESTADO.estadio_activo = estadio_id


# ---------------------------------------------------------------------------
# Inyección de fallas y evento de gol (design §6.4, §9.4; Req 4.8/6.x)
#
# La vista de Simulación (tarea 7.8) ofrece botones de inyección de fallas y un
# botón de gol. El motor (``sim.faults.inyectar_falla``) exige un ``objetivo_id``
# de la topología del estadio activo; aquí se elige un **objetivo por defecto
# sensato por tipo de falla** a partir de la topología cargada, de modo que la
# UI solo necesite indicar el *tipo* de falla. El cálculo de la falla y la
# conmutación de protección viven en ``sim/`` (separación de capas, §3.1); estas
# funciones solo localizan el objetivo y delegan.
# ---------------------------------------------------------------------------
# Tipos de falla soportados por la UI (mismo conjunto que sim.faults.TIPOS_FALLA,
# design §6.4). Se listan aquí en un orden estable para los botones.
TIPOS_FALLA_UI: tuple[str, ...] = (
    "tarjeta_caida",
    "corte_troncal",
    "chasis_caido",
    "degradacion_optica",
    "pico_capacidad",
)

# Etiquetas legibles de cada tipo de falla para los botones de la vista.
ETIQUETA_FALLA: dict[str, str] = {
    "tarjeta_caida": "Tarjeta caída",
    "corte_troncal": "Corte troncal",
    "chasis_caido": "Chasis caído",
    "degradacion_optica": "Degradación óptica",
    "pico_capacidad": "Pico de capacidad",
}


def _estadio_activo():
    """Devuelve el :class:`Estadio` activo del estado, o ``None`` si no hay."""
    estadios = getattr(ESTADO, "estadios", None) or {}
    return estadios.get(getattr(ESTADO, "estadio_activo", None))


def _primer_arbol_con_proteccion(estadio, proteccion) -> str | None:
    """Id del primer árbol PON del estadio con la protección dada (o ``None``).

    ``proteccion`` es un valor de ``sim.state.Proteccion``. Recorre la jerarquía
    en orden determinista (tarjeta → puerto → árbol) y devuelve el primer árbol
    cuyo esquema de protección coincide, para elegir un objetivo representativo.
    """
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            for arbol in puerto.arboles:
                if arbol.proteccion == proteccion:
                    return arbol.id
    return None


def _primer_arbol(estadio) -> str | None:
    """Id del primer árbol PON activo del estadio (o ``None`` si no hay)."""
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            for arbol in puerto.arboles:
                return arbol.id
    return None


def _primera_tarjeta(estadio) -> str | None:
    """Id de la primera tarjeta del estadio (o ``None``)."""
    for tarjeta in estadio.olt.tarjetas:
        return tarjeta.id
    return None


def _primer_puerto_activo(estadio) -> str | None:
    """Id del primer puerto PON con árbol (no de reserva) del estadio (o ``None``)."""
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if not getattr(puerto, "es_reserva", False) and puerto.arboles:
                return puerto.id
    return None


def objetivo_por_defecto(tipo: str) -> str | None:
    """Elige un objetivo de topología sensato para el ``tipo`` de falla (design §6.4).

    Mapea cada tipo de falla al nivel de jerarquía que afecta, tomando el primer
    elemento representativo del estadio activo:

    * ``tarjeta_caida``      → la primera tarjeta de la OLT.
    * ``corte_troncal``      → el primer árbol PON **sin protección** (NINGUNA),
      para que el corte sea observable (con TYPE_C conmutaría y no caería).
    * ``chasis_caido``       → la OLT completa del estadio.
    * ``degradacion_optica`` → el primer árbol PON del estadio.
    * ``pico_capacidad``     → el primer puerto PON activo del estadio.

    Devuelve ``None`` si no hay estadio/topología o no se encuentra un objetivo
    para ese tipo (la UI entonces no inyecta nada).
    """
    from sim.state import Proteccion

    estadio = _estadio_activo()
    if estadio is None:
        return None

    if tipo == "chasis_caido":
        return estadio.olt.id
    if tipo == "tarjeta_caida":
        return _primera_tarjeta(estadio)
    if tipo == "corte_troncal":
        # Preferir un árbol sin protección para que el corte sea observable
        # (un árbol TYPE_C conmutaría y mantendría servicio, Req 6.5).
        return _primer_arbol_con_proteccion(
            estadio, Proteccion.NINGUNA
        ) or _primer_arbol(estadio)
    if tipo == "degradacion_optica":
        return _primer_arbol(estadio)
    if tipo == "pico_capacidad":
        return _primer_puerto_activo(estadio)
    return None


def inyectar_falla(tipo: str) -> bool:
    """Inyecta una falla del ``tipo`` dado sobre un objetivo por defecto (design §6.4).

    Elige el objetivo con :func:`objetivo_por_defecto` y delega en
    ``sim.faults.inyectar_falla`` sobre el estado compartido. La falla y su
    conmutación de protección se resuelven en el mismo tick en ``sim/`` (Req
    6.4/6.5/6.6); aquí no se calcula nada. Es fail-soft: si no hay objetivo o el
    motor rechaza la inyección, devuelve ``False`` sin propagar la excepción.

    Devuelve ``True`` si la falla se inyectó.
    """
    if tipo not in TIPOS_FALLA_UI:
        return False
    objetivo = objetivo_por_defecto(tipo)
    if objetivo is None:
        return False
    try:
        from sim import faults

        faults.inyectar_falla(ESTADO, tipo, objetivo)
    except Exception:  # pragma: no cover - fail-soft de inyección
        return False
    return True


def gol() -> float:
    """Registra un gol en el minuto de partido actual (design §6.2, §9.4).

    Añade ``ESTADO.t_min`` a ``ESTADO.goles`` para que ``sim.traffic`` aplique el
    pico transitorio de actividad ``f_gol`` desde el próximo tick. Devuelve el
    minuto registrado. Crea la lista ``goles`` por duck typing si no existiera.
    """
    goles = getattr(ESTADO, "goles", None)
    if not isinstance(goles, list):
        goles = []
        ESTADO.goles = goles  # type: ignore[attr-defined]
    t = float(getattr(ESTADO, "t_min", 0.0))
    goles.append(t)
    return t


# ---------------------------------------------------------------------------
# Lectura de alarmas para la UI (tabla de activas e histórico de sesión, Req 4.8)
# ---------------------------------------------------------------------------
def alarmas_snapshot() -> dict[str, list]:
    """Lectura de solo lectura de alarmas para la vista de Simulación (Req 4.8).

    Devuelve dos listas de objetos ``sim.alarms.Alarma``:

    * ``activas``: alarmas **vivas no derivadas** (``sim.alarms.alarmas_activas``),
      lo que la tabla de activas muestra (la correlación colapsa las derivadas
      bajo su raíz, Req 4.5).
    * ``historico``: **todas** las alarmas de la sesión (vivas + cerradas),
      ``sim.alarms.historico_alarmas`` (Req 4.8).

    La lógica de alarmas vive en ``sim/`` (Req 2.3); aquí solo se lee. Es
    tolerante: si el módulo o las alarmas no existen aún, devuelve listas vacías.
    """
    try:
        from sim import alarms
    except Exception:  # pragma: no cover - fail-soft
        return {"activas": [], "historico": []}
    try:
        activas = list(alarms.alarmas_activas(ESTADO))
        historico = list(alarms.historico_alarmas(ESTADO))
    except Exception:  # pragma: no cover - fail-soft
        return {"activas": [], "historico": []}
    return {"activas": activas, "historico": historico}


# ---------------------------------------------------------------------------
# Lectura de solo lectura para las vistas
# ---------------------------------------------------------------------------
def _historial_lista(clave: str) -> list[float]:
    """Serie de historial ``clave`` como lista de floats (vacía si no existe)."""
    historial = getattr(ESTADO, "historial", None) or {}
    serie = historial.get(clave)
    if isinstance(serie, (deque, list, tuple)):
        return [float(v) for v in serie]
    return []


def snapshot() -> dict[str, Any]:
    """Lectura consistente del estado para las vistas (design §7, Req 2.2).

    Devuelve:

    * ``kpis``: dict K1..K12 con los valores **ya calculados** por
      ``sim.kpis.calcular`` (la capa de presentación no recalcula, Req 2.2).
    * ``historial``: dict serie → lista de floats (copiada para lectura segura).
    * ``t_min`` / ``corriendo`` / ``estadio`` / ``total_onts``: metadatos del
      reloj y del estadio activo para poblar tarjetas y ejes.

    Nunca lanza: si faltan agregados (antes del primer tick) los KPIs vienen en
    0.0 desde ``sim.kpis`` y las series vienen vacías, de modo que las vistas
    caen a sus indicadores por defecto (Req 15.3).
    """
    valores = kpis.calcular(ESTADO)

    estadios = getattr(ESTADO, "estadios", None) or {}
    estadio = estadios.get(getattr(ESTADO, "estadio_activo", None))
    total_onts = estadio.num_onts if estadio is not None else 0

    series_kpi = {codigo: _historial_lista(codigo) for codigo in kpis.CODIGOS_KPI}

    return {
        "kpis": valores,
        "historial": series_kpi,
        "t_min": float(getattr(ESTADO, "t_min", 0.0)),
        "corriendo": bool(getattr(ESTADO, "corriendo", False)),
        "velocidad": float(getattr(ESTADO, "velocidad", 1.0)),
        "escenario": getattr(ESTADO, "escenario", "diseno"),
        "estadio": getattr(ESTADO, "estadio_activo", None),
        "total_onts": total_onts,
    }


# ---------------------------------------------------------------------------
# Contexto de la barra superior (design §9.1, Req 1.3)
# ---------------------------------------------------------------------------
def _conteo_alarmas() -> tuple[int, int]:
    """Cuenta alarmas activas y críticas del estado (lectura de solo lectura).

    Usa ``sim.alarms`` para contar las alarmas **vivas no derivadas** (lo que la
    barra superior muestra al operador, Req 4.5) y, de esas, las de severidad
    crítica. La lógica de alarmas vive en ``sim/`` (Req 2.3): aquí solo se lee.
    Es tolerante: si el módulo o las alarmas no existen aún, devuelve ``(0, 0)``.
    """
    try:
        from sim import alarms
    except Exception:  # pragma: no cover - fail-soft
        return (0, 0)
    try:
        activas = alarms.alarmas_activas(ESTADO)
    except Exception:  # pragma: no cover - fail-soft
        return (0, 0)
    criticas = sum(
        1 for a in activas if getattr(getattr(a, "severidad", None), "value", getattr(a, "severidad", "")) == "critica"
    )
    return (len(activas), criticas)


def contexto_topbar() -> dict[str, Any]:
    """Contexto en vivo para los indicadores de la barra superior (Req 1.3).

    Devuelve un dict con el reloj (``t_min``), ``corriendo``, ``velocidad``,
    ``escenario``, el estadio activo y los contadores de alarmas activas y
    críticas. Todos los valores son **lecturas** del estado ya calculado por
    ``sim/`` (Req 2.3); esta función no avanza el motor ni calcula KPIs/alarmas.
    """
    activas, criticas = _conteo_alarmas()
    return {
        "estadio": getattr(ESTADO, "estadio_activo", None),
        "t_min": float(getattr(ESTADO, "t_min", 0.0)),
        "corriendo": bool(getattr(ESTADO, "corriendo", False)),
        "velocidad": float(getattr(ESTADO, "velocidad", 1.0)),
        "escenario": getattr(ESTADO, "escenario", "diseno"),
        "alarmas_activas": activas,
        "alarmas_criticas": criticas,
    }
