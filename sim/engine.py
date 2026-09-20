"""Reloj y bucle de simulación del motor (design §6.1, tarea 5.3).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). Expone `tick(state)`, que avanza la simulación un paso: mueve el
reloj, actualiza tráfico, aplica fallas y alarmas, recalcula KPIs y acumula la
disponibilidad de las ONTs del estadio activo.

Orden del ciclo (design §4, diagrama de secuencia):

    t_min += velocidad  ->  traffic.actualizar  ->  faults.aplicar
    ->  alarms.evaluar  ->  kpis.calcular (empuja historial)  ->  disponibilidad

Módulos aún inexistentes (`sim.faults`, tarea 7.1; `sim.alarms`, tarea 7.5) se
invocan de forma **protegida** (`try/except ImportError` + `hasattr`): `tick`
funciona hoy y recogerá esos pasos automáticamente en cuanto los módulos
existan, sin tocar este archivo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sim import kpis, traffic
from sim.state import (
    T_MIN_FINAL,
    acumular_disponibilidad,
)

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones de tipo
    from sim.state import SimState


# ---------------------------------------------------------------------------
# Pasos opcionales (módulos de fallas/alarmas aún no implementados)
# ---------------------------------------------------------------------------
def _aplicar_fallas(state: "SimState") -> None:
    """Invoca `sim.faults.aplicar(state)` si el módulo existe (tarea 7.1).

    Import protegido: mientras `sim.faults` no exista, este paso es un no-op y
    `tick` sigue funcionando. Cuando el módulo aparezca con una función
    `aplicar`, se ejecutará automáticamente sin cambios aquí.
    """
    try:
        from sim import faults  # type: ignore
    except ImportError:
        return
    if hasattr(faults, "aplicar"):
        faults.aplicar(state)


def _evaluar_alarmas(state: "SimState") -> None:
    """Invoca `sim.alarms.evaluar(state)` si el módulo existe (tarea 7.5).

    Import protegido, análogo a `_aplicar_fallas`.
    """
    try:
        from sim import alarms  # type: ignore
    except ImportError:
        return
    if hasattr(alarms, "evaluar"):
        alarms.evaluar(state)


def _acumular_disponibilidad_estadio(state: "SimState") -> None:
    """Acumula un tick de disponibilidad en cada ONT del estadio activo (Req 8.1).

    Recorre la topología del estadio activo y llama `acumular_disponibilidad`
    por ONT. Es la base de la evidencia de convergencia SLA (Req 8, tests
    5.6/5.7). Tolerante: si no hay estadio activo cargado, no hace nada.
    """
    estadios = getattr(state, "estadios", None) or {}
    estadio = estadios.get(getattr(state, "estadio_activo", None))
    if estadio is None:
        return
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            for arbol in puerto.arboles:
                for ont in arbol.onts:
                    acumular_disponibilidad(ont)


# ---------------------------------------------------------------------------
# Bucle principal (design §6.1)
# ---------------------------------------------------------------------------
def tick(state: "SimState") -> "SimState":
    """Avanza la simulación un paso (design §6.1).

    Precondiciones:
      - `state.corriendo == True` (si es False, el llamador no invoca `tick`).
      - `state.velocidad in {1.0, 5.0, 15.0}` (garantizado por `SimState`).

    Postcondiciones:
      - `state.t_min` incrementado en `state.velocidad`, monótono y topado en
        `+135.0` (Req 16.3). Nunca decrece ni supera 135.0.
      - Tráfico, fallas, alarmas y KPIs recalculados y consistentes entre sí.
      - Disponibilidad acumulada un tick por cada ONT del estadio activo (Req 8.1).
      - Cada serie de historial creció en <= 1 punto y respeta maxlen=600
        (empujado por `kpis.calcular`, Req 16.4).

    Devuelve el mismo `state` mutado, por conveniencia del llamador.
    """
    # Reloj: monótono no decreciente y topado en +135 (Req 16.3).
    state.t_min = min(state.t_min + state.velocidad, T_MIN_FINAL)

    # Recalcular el estado del tick en el orden del diagrama de secuencia (§4).
    traffic.actualizar(state)
    _aplicar_fallas(state)      # sim.faults.aplicar (tarea 7.1, protegido)
    _evaluar_alarmas(state)     # sim.alarms.evaluar (tarea 7.5, protegido)
    kpis.calcular(state)        # calcula K1..K12 y empuja el historial acotado.

    # Acumular disponibilidad tras resolver fallas/protecciones del tick (Req 8.1).
    _acumular_disponibilidad_estadio(state)

    return state
