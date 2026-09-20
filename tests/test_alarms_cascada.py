"""Prueba basada en propiedad — Property 8: Cierre en cascada.

Diseño §8.3 (correlación y cierre en cascada) y §8.4 (ciclo de vida),
Requirement 4.7:

* **Req 4.7**: WHEN una alarma raíz se cierra, THE Motor_de_Simulacion SHALL
  cerrar en cascada todas sus alarmas derivadas.

Esta prueba verifica el lado del **cierre**: una vez colapsadas las ONT-001
derivadas bajo una raíz OLT-001 (chasis) u OLT-003 (corte troncal en un árbol
sin protección), al desaparecer la condición de la raíz —tras limpiar la falla
y restaurar el estado del elemento— el motor cierra la raíz y **arrastra en
cascada** todas sus derivadas a ``CERRADA``, sellando su ``t_min_cierre``. Las
derivadas dejan de aparecer en ``alarmas_activas``.

Se construye un ``SimState`` con la topología real cargada (``SimState.crear``),
se inyecta la falla que crea la raíz + derivadas, se evalúan un par de ticks
para asentar el ciclo de vida y la correlación, y luego se restaura la condición
de la raíz para observar el cierre en cascada.

**Validates: Requirements 4.7**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.alarms import (
    CODIGOS_RAIZ,
    CODIGO_DERIVADA,
    EstadoAlarma,
    alarmas_activas,
    evaluar,
)
from sim.faults import inyectar_falla, limpiar_fallas
from sim.state import (
    ArbolPON,
    Estado,
    OLT,
    Proteccion,
    PuertoPON,
    SimState,
    Tarjeta,
)


# ---------------------------------------------------------------------------
# Utilidades de recorrido / restauración de la topología del estadio activo
# ---------------------------------------------------------------------------
def _olt_activa(state: SimState) -> OLT:
    """OLT del estadio activo."""
    return state.estadios[state.estadio_activo].olt


def _arboles_estadio_activo(state: SimState) -> list[ArbolPON]:
    """Todos los árboles PON del estadio activo (recorrido completo)."""
    return [
        arbol
        for tarjeta in _olt_activa(state).tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    ]


def _arbol_sin_proteccion_con_onts(state: SimState) -> ArbolPON:
    """Primer árbol NINGUNA con ONTs (un corte troncal deja sus ONTs FUERA)."""
    return next(
        a
        for a in _arboles_estadio_activo(state)
        if a.proteccion == Proteccion.NINGUNA and a.onts
    )


def _restaurar_en_linea(state: SimState) -> None:
    """Restaura toda la jerarquía del estadio activo a EN_LINEA.

    Emula la desaparición de la condición de la raíz (chasis restaurado / fibra
    reparada): la OLT, las tarjetas, los puertos (no-reserva), los árboles y las
    ONTs vuelven a EN_LINEA. Tras esto, la condición de cierre de la raíz
    (OLT-001/OLT-003: ``estado != FUERA``) se cumple.
    """
    olt = _olt_activa(state)
    olt.estado = Estado.EN_LINEA
    for tarjeta in olt.tarjetas:
        tarjeta.estado = Estado.EN_LINEA
        for puerto in tarjeta.puertos:
            if not puerto.es_reserva:
                puerto.estado = Estado.EN_LINEA
            for arbol in puerto.arboles:
                arbol.estado = Estado.EN_LINEA
                arbol.atenuacion_db = 0.0
                for ont in arbol.onts:
                    ont.estado = Estado.EN_LINEA


def _alarmas_por_codigo(state: SimState, codigo: str) -> list:
    """Alarmas de sesión con el código dado (incluye vivas y cerradas)."""
    return [a for a in state.alarmas if a.codigo == codigo]


def _derivadas_de(state: SimState, raiz_origen_id: str) -> list:
    """Alarmas ONT-001 que quedaron colapsadas bajo la raíz indicada."""
    return [
        a
        for a in state.alarmas
        if a.codigo == CODIGO_DERIVADA and a.raiz_id == raiz_origen_id
    ]


def _asentar(state: SimState, ticks: int = 2) -> None:
    """Evalúa el motor de alarmas ``ticks`` veces (asienta ciclo + correlación)."""
    for _ in range(ticks):
        evaluar(state)


# ---------------------------------------------------------------------------
# Sanidad: la topología real permite montar la raíz + derivadas
# ---------------------------------------------------------------------------
def test_topologia_permite_raiz_y_derivadas():
    """Debe existir al menos un árbol NINGUNA con ONTs para el corte troncal.

    Sin él, la raíz OLT-003 no colapsaría ONT-001 derivadas y la propiedad no
    se podría ejercitar; un fallo aquí sería un problema de datos, no del motor.
    """
    state = SimState.crear()
    arbol = _arbol_sin_proteccion_con_onts(state)
    assert arbol.onts, "se esperaba un árbol NINGUNA con ONTs"


# ---------------------------------------------------------------------------
# Req 4.7 — Cierre en cascada bajo raíz OLT-001 (chasis caído)
# ---------------------------------------------------------------------------
def test_req_4_7_cascada_olt001_chasis_ejemplo():
    """Al restaurarse el chasis (OLT-001 cierra), todas sus ONT-001 cierran.

    Montaje: ``chasis_caido`` sobre la OLT deja el chasis FUERA y las ONTs sin
    protección FUERA. Tras asentar, existe una raíz OLT-001 viva con ONT-001
    derivadas colapsadas bajo ella. Al restaurar la jerarquía y reevaluar, la
    raíz cierra y arrastra en cascada a las derivadas.

    Nota de modelado: la conmutación de protección a nivel OLT toma la protección
    más fuerte del chasis, por lo que ``chasis_caido`` por sí solo mantiene las
    ONTs protegidas EN_LINEA. Para materializar las derivadas ONT-001 bajo la
    raíz OLT-001 (que el motor correlaciona por OLT compartida), se dejan FUERA
    las ONTs sin protección del chasis caído (el escenario real que la
    correlación por OLT resuelve).

    **Validates: Requirements 4.7**
    """
    state = SimState.crear()
    olt = _olt_activa(state)

    inyectar_falla(state, "chasis_caido", olt.id)
    # Chasis caído: las ONTs sin protección quedan FUERA (generan ONT-001 que la
    # correlación agrupa bajo la raíz OLT-001 por compartir OLT).
    onts_sin_proteccion = [
        ont
        for tarjeta in olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
        if arbol.proteccion == Proteccion.NINGUNA
        for ont in arbol.onts
    ]
    for ont in onts_sin_proteccion:
        ont.estado = Estado.FUERA
    assert onts_sin_proteccion, "se esperaban ONTs sin protección en el chasis"
    _asentar(state)

    # Precondición: raíz OLT-001 viva con al menos una ONT-001 derivada.
    raices = [a for a in _alarmas_por_codigo(state, "OLT-001") if a.viva]
    assert len(raices) == 1, "se esperaba exactamente una raíz OLT-001 viva"
    raiz = raices[0]
    derivadas = [a for a in _derivadas_de(state, raiz.origen_id) if a.viva]
    assert derivadas, "se esperaban ONT-001 derivadas colapsadas bajo la raíz"

    # Disparar el cierre de la raíz: restaurar y reevaluar.
    limpiar_fallas(state)
    _restaurar_en_linea(state)
    evaluar(state)

    # Postcondición (Req 4.7): la raíz cerró y TODAS sus derivadas cerraron.
    assert raiz.estado == EstadoAlarma.CERRADA, "la raíz OLT-001 no cerró"
    for d in derivadas:
        assert d.estado == EstadoAlarma.CERRADA, (
            f"la derivada {d.origen_id} no cerró en cascada"
        )
        assert d.t_min_cierre is not None, (
            f"la derivada {d.origen_id} cerró sin sellar t_min_cierre"
        )

    # Las derivadas ya no aparecen en la tabla de alarmas activas.
    activas_ids = {a.origen_id for a in alarmas_activas(state)}
    for d in derivadas:
        assert d.origen_id not in activas_ids, (
            f"la derivada {d.origen_id} sigue listada como activa tras el cierre"
        )


# ---------------------------------------------------------------------------
# Req 4.7 — Cierre en cascada bajo raíz OLT-003 (corte troncal, árbol NINGUNA)
# ---------------------------------------------------------------------------
def test_req_4_7_cascada_olt003_corte_troncal_ejemplo():
    """Al repararse la fibra (OLT-003 cierra), sus ONT-001 cierran en cascada.

    Montaje: ``corte_troncal`` sobre un árbol sin protección deja el árbol FUERA
    (raíz OLT-003) y sus ONTs FUERA (derivadas ONT-001). Al restaurar el árbol
    y reevaluar, la raíz cierra y arrastra a sus derivadas.

    **Validates: Requirements 4.7**
    """
    state = SimState.crear()
    arbol = _arbol_sin_proteccion_con_onts(state)

    inyectar_falla(state, "corte_troncal", arbol.id)
    _asentar(state)

    raices = [
        a
        for a in _alarmas_por_codigo(state, "OLT-003")
        if a.viva and a.origen_id == arbol.id
    ]
    assert len(raices) == 1, "se esperaba una raíz OLT-003 viva para el árbol"
    raiz = raices[0]
    derivadas = [a for a in _derivadas_de(state, raiz.origen_id) if a.viva]
    assert derivadas, "se esperaban ONT-001 derivadas del árbol bajo la raíz"

    # Reparar solo el árbol y sus ONTs, y reevaluar.
    limpiar_fallas(state)
    arbol.estado = Estado.EN_LINEA
    arbol.atenuacion_db = 0.0
    for ont in arbol.onts:
        ont.estado = Estado.EN_LINEA
    evaluar(state)

    assert raiz.estado == EstadoAlarma.CERRADA, "la raíz OLT-003 no cerró"
    for d in derivadas:
        assert d.estado == EstadoAlarma.CERRADA, (
            f"la derivada {d.origen_id} no cerró en cascada"
        )
        assert d.t_min_cierre is not None, (
            f"la derivada {d.origen_id} cerró sin sellar t_min_cierre"
        )


# ---------------------------------------------------------------------------
# Property 8 — para cualquier árbol NINGUNA, el cierre de la raíz OLT-003
# cierra en cascada TODAS sus ONT-001 derivadas.
# ---------------------------------------------------------------------------
@settings(max_examples=40, deadline=None)
@given(data=st.data())
def test_property_8_cierre_en_cascada(data):
    """Property 8 (Req 4.7): al cerrar la raíz, toda derivada queda CERRADA.

    Para un árbol NINGUNA con ONTs elegido al azar: se inyecta un corte troncal
    (raíz OLT-003 + ONT-001 derivadas), se asienta la correlación, se repara la
    condición de la raíz y se reevalúa. Se comprueba el invariante del cierre en
    cascada: **ninguna** derivada de esa raíz queda viva; todas quedan CERRADA
    con ``t_min_cierre`` sellado, y ninguna se lista como activa.

    **Validates: Requirements 4.7**
    """
    state = SimState.crear()
    candidatos = [
        a
        for a in _arboles_estadio_activo(state)
        if a.proteccion == Proteccion.NINGUNA and a.onts
    ]
    arbol = data.draw(st.sampled_from(candidatos))

    inyectar_falla(state, "corte_troncal", arbol.id)
    _asentar(state)

    raiz = next(
        a
        for a in _alarmas_por_codigo(state, "OLT-003")
        if a.viva and a.origen_id == arbol.id
    )
    derivadas = [a for a in _derivadas_de(state, raiz.origen_id) if a.viva]
    # El corte troncal en un árbol NINGUNA deja todas sus ONTs FUERA -> derivadas.
    assert len(derivadas) == len(arbol.onts), (
        "todas las ONTs del árbol NINGUNA debían generar una ONT-001 derivada"
    )

    # Reparar la condición de la raíz y reevaluar.
    limpiar_fallas(state)
    arbol.estado = Estado.EN_LINEA
    arbol.atenuacion_db = 0.0
    for ont in arbol.onts:
        ont.estado = Estado.EN_LINEA
    evaluar(state)

    # Invariante de cascada (Req 4.7).
    assert raiz.estado == EstadoAlarma.CERRADA
    activas_ids = {a.origen_id for a in alarmas_activas(state)}
    for d in derivadas:
        assert d.estado == EstadoAlarma.CERRADA, (
            f"derivada {d.origen_id} viva tras cerrar la raíz {raiz.origen_id}"
        )
        assert d.t_min_cierre is not None
        assert d.origen_id not in activas_ids


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
