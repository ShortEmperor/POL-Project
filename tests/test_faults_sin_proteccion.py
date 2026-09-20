"""Prueba basada en propiedad — Property 6: Sin protección cae.

Diseño §14 (Property 6) y §6.4, Requirement 6.4 y 6.6:

* **Req 6.4**: WHEN se inyecta una falla en un elemento con protección NINGUNA,
  THE Motor_de_Simulacion SHALL cambiar a FUERA las ONTs que cuelgan de ese
  elemento (sin redundancia, sin recuperación).
* **Req 6.6**: IF se conmuta una protección TYPE_B o TYPE_C y el recurso de
  reserva NO está disponible u operativo en ese mismo tick, THEN THE
  Motor_de_Simulacion SHALL pasar inmediatamente en ese mismo tick la ONT
  protegida a FUERA (o DEGRADADO), SIN reintentar la conmutación en ticks
  posteriores.

A diferencia de la Property 5 (protección disponible mantiene servicio), aquí se
verifica el lado negativo: cuando no hay redundancia (NINGUNA) o cuando la
reserva no está operativa, las ONTs afectadas caen y **permanecen** caídas en
ticks posteriores (no hay reintento de conmutación).

Se construye un ``SimState`` con la topología real cargada (``SimState.crear``)
y se parametriza/propaga sobre los elementos elegibles del estadio activo.

**Validates: Requirements 6.4, 6.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.engine import tick
from sim.faults import inyectar_falla
from sim.state import (
    ArbolPON,
    Estado,
    Proteccion,
    SimState,
)


# ---------------------------------------------------------------------------
# Utilidades de recorrido de la topología del estadio activo
# ---------------------------------------------------------------------------
def _arboles_estadio_activo(state: SimState) -> list[ArbolPON]:
    """Todos los árboles PON del estadio activo (recorrido completo)."""
    estadio = state.estadios[state.estadio_activo]
    return [
        arbol
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    ]


def _arboles_por_proteccion(
    state: SimState, proteccion: Proteccion
) -> list[ArbolPON]:
    """Árboles del estadio activo con el esquema de protección indicado."""
    return [
        a for a in _arboles_estadio_activo(state) if a.proteccion == proteccion
    ]


def _deshabilitar_todas_las_reservas(state: SimState) -> int:
    """Pone FUERA todos los puertos de reserva del estadio activo.

    ``conmutar_proteccion`` considera disponible la reserva si existe algún
    puerto ``es_reserva`` no-FUERA en el ámbito del elemento **o** en cualquier
    tarjeta del estadio (fallback ``_reserva_disponible_en_estadio``). Para
    ejercitar Req 6.6 (reserva NO disponible este tick) hay que dejar todos los
    puertos de reserva del estadio en FUERA. Devuelve cuántos se deshabilitaron.
    """
    estadio = state.estadios[state.estadio_activo]
    n = 0
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if puerto.es_reserva:
                puerto.estado = Estado.FUERA
                n += 1
    return n


# Estados que NO cuentan como servicio (Req 6.4/6.6: la ONT cayó).
ESTADOS_CAIDA = (Estado.FUERA, Estado.DEGRADADO)

# Tipos de falla "dura" que dejan un árbol sin conectividad (no de congestión).
FALLAS_DURAS = ("corte_troncal", "tarjeta_caida", "chasis_caido")


# ---------------------------------------------------------------------------
# Sanidad: la topología real contiene los elementos que necesita la prueba
# ---------------------------------------------------------------------------
def test_topologia_tiene_elementos_de_cada_proteccion():
    """La topología real debe tener árboles NINGUNA y TYPE_B/TYPE_C.

    Sin estos elementos las propiedades 6.4/6.6 no se podrían ejercitar; el
    fallo aquí sería un problema de datos, no del motor.
    """
    state = SimState.crear()
    ninguna = _arboles_por_proteccion(state, Proteccion.NINGUNA)
    type_b = _arboles_por_proteccion(state, Proteccion.TYPE_B)
    type_c = _arboles_por_proteccion(state, Proteccion.TYPE_C)

    assert ninguna, "se esperaban árboles con protección NINGUNA"
    assert type_b or type_c, "se esperaban árboles TYPE_B/TYPE_C"
    # Debe haber al menos un puerto de reserva para deshabilitar en Req 6.6.
    assert _deshabilitar_todas_las_reservas(SimState.crear()) > 0


# ---------------------------------------------------------------------------
# Req 6.4 — Sin protección (NINGUNA) las ONTs dependientes caen a FUERA
# ---------------------------------------------------------------------------
def test_req_6_4_ninguna_cae_a_fuera_ejemplo():
    """Ejemplo puntual: un corte troncal en un árbol NINGUNA deja sus ONTs FUERA.

    **Validates: Requirements 6.4**
    """
    state = SimState.crear()
    ninguna = _arboles_por_proteccion(state, Proteccion.NINGUNA)
    arbol = next(a for a in ninguna if a.onts)

    # Precondición: las ONTs empiezan EN_LINEA (topología semilla).
    assert all(o.estado == Estado.EN_LINEA for o in arbol.onts)

    inyectar_falla(state, "corte_troncal", arbol.id)

    # Postcondición (Req 6.4): todas las ONTs del árbol NINGUNA caen a FUERA.
    assert all(o.estado == Estado.FUERA for o in arbol.onts)


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_req_6_4_ninguna_cae_property(data):
    """Property 6 (Req 6.4): cualquier falla dura en un árbol NINGUNA lo deja FUERA.

    Se elige aleatoriamente un árbol NINGUNA (con ONTs) y un tipo de falla dura;
    tras inyectarla, todas las ONTs dependientes deben quedar en FUERA. Como no
    hay redundancia, el resultado no depende de la disponibilidad de reservas.

    **Validates: Requirements 6.4**
    """
    state = SimState.crear()
    candidatos = [a for a in _arboles_por_proteccion(state, Proteccion.NINGUNA) if a.onts]
    arbol = data.draw(st.sampled_from(candidatos))
    tipo = data.draw(st.sampled_from(("corte_troncal", "tarjeta_caida")))

    inyectar_falla(state, tipo, arbol.id)

    assert all(o.estado == Estado.FUERA for o in arbol.onts), (
        f"árbol NINGUNA {arbol.id} bajo {tipo}: se esperaban todas las ONTs FUERA"
    )


def test_req_6_4_ninguna_no_se_recupera_en_ticks_posteriores():
    """Sin protección no hay recuperación: las ONTs siguen FUERA tras más ticks.

    La falla persiste en ``state.fallas_activas`` y se reaplica cada tick; NINGUNA
    nunca reintenta conmutación (no hay reserva), así que las ONTs permanecen FUERA.

    **Validates: Requirements 6.4**
    """
    state = SimState.crear()
    state.corriendo = True
    arbol = next(a for a in _arboles_por_proteccion(state, Proteccion.NINGUNA) if a.onts)

    inyectar_falla(state, "corte_troncal", arbol.id)
    assert all(o.estado == Estado.FUERA for o in arbol.onts)

    for _ in range(5):
        tick(state)
        assert all(o.estado == Estado.FUERA for o in arbol.onts), (
            "una ONT NINGUNA se recuperó indebidamente tras la falla"
        )


# ---------------------------------------------------------------------------
# Req 6.6 — TYPE_B/TYPE_C sin reserva disponible: la ONT protegida cae de
# inmediato en el mismo tick y NO se recupera en ticks posteriores.
# ---------------------------------------------------------------------------
def test_req_6_6_sin_reserva_cae_en_el_mismo_tick_ejemplo():
    """Ejemplo: TYPE_B/TYPE_C sin reserva operativa cae en el mismo tick.

    Se deshabilitan todas las reservas del estadio antes de inyectar la falla,
    de modo que la conmutación no encuentra recurso disponible ese tick.

    **Validates: Requirements 6.6**
    """
    state = SimState.crear()
    protegidos = _arboles_por_proteccion(state, Proteccion.TYPE_B) + \
        _arboles_por_proteccion(state, Proteccion.TYPE_C)
    arbol = next(a for a in protegidos if a.onts)

    # Precondición: ONTs EN_LINEA y reservas deshabilitadas (no operativas).
    assert all(o.estado == Estado.EN_LINEA for o in arbol.onts)
    assert _deshabilitar_todas_las_reservas(state) > 0

    inyectar_falla(state, "corte_troncal", arbol.id)

    # Postcondición (Req 6.6): la ONT protegida cae de inmediato (FUERA/DEGRADADO).
    assert all(o.estado in ESTADOS_CAIDA for o in arbol.onts), (
        "sin reserva disponible, la ONT protegida debía caer en el mismo tick"
    )


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_req_6_6_sin_reserva_cae_y_no_reintenta_property(data):
    """Property 6 (Req 6.6): sin reserva, la ONT protegida cae y NO reintenta.

    Para un árbol TYPE_B/TYPE_C elegido al azar, con todas las reservas del
    estadio deshabilitadas, tras inyectar una falla dura la ONT protegida debe:

    1. caer en el mismo tick (FUERA o DEGRADADO), y
    2. permanecer caída en ticks posteriores (sin reintentar la conmutación,
       ya que la reserva sigue no disponible).

    **Validates: Requirements 6.6**
    """
    state = SimState.crear()
    state.corriendo = True
    protegidos = [
        a
        for a in (
            _arboles_por_proteccion(state, Proteccion.TYPE_B)
            + _arboles_por_proteccion(state, Proteccion.TYPE_C)
        )
        if a.onts
    ]
    arbol = data.draw(st.sampled_from(protegidos))
    tipo = data.draw(st.sampled_from(("corte_troncal", "tarjeta_caida")))

    # Reserva NO disponible este tick (ni en ticks posteriores).
    assert _deshabilitar_todas_las_reservas(state) > 0

    inyectar_falla(state, tipo, arbol.id)

    # 1) Caída inmediata en el mismo tick de la falla (Req 6.6).
    assert all(o.estado in ESTADOS_CAIDA for o in arbol.onts), (
        f"árbol {arbol.proteccion.value} {arbol.id} sin reserva bajo {tipo}: "
        "se esperaba caída inmediata"
    )

    # 2) Sin reintento: las reservas siguen FUERA, la ONT no se recupera.
    for _ in range(5):
        # Reafirmar que las reservas continúan no disponibles cada tick.
        _deshabilitar_todas_las_reservas(state)
        tick(state)
        assert all(o.estado in ESTADOS_CAIDA for o in arbol.onts), (
            "la ONT protegida se recuperó pese a que la reserva nunca estuvo "
            "disponible (Req 6.6 prohíbe el reintento)"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
