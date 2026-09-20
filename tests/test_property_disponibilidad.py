"""Prueba basada en propiedad — Property 3: Consistencia de disponibilidad.

Diseño §14 (Property 3) y §6.5, Requirement 8.3:

* Para toda ONT, en todo momento, se cumple ``0 ≤ ticks_en_linea ≤ ticks_totales``.

A diferencia de una prueba de ejemplo puntual, aquí se usa **Hypothesis** para
generar secuencias arbitrarias de transiciones de estado de una ONT y un número
arbitrario de ticks del motor real, comprobando que la invariante de
consistencia de disponibilidad se mantiene a lo largo de toda la corrida.

**Validates: Requirements 8.3**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sim.engine import tick
from sim.state import (
    ONT,
    Estado,
    Proteccion,
    Servicio,
    SimState,
    acumular_disponibilidad,
)

# Estados disponibles (cuentan como "en línea") vs no disponibles (Req 8.1).
ESTADOS_EN_LINEA = (Estado.EN_LINEA, Estado.DEGRADADO)
ESTADOS_FUERA = (Estado.FUERA, Estado.RESERVA)

# Estrategia: cualquiera de los cuatro estados posibles de una ONT.
estados = st.sampled_from(list(Estado))


def _onts_estadio_activo(state: SimState) -> list[ONT]:
    """Todas las ONTs del estadio activo, recorriendo la jerarquía completa."""
    estadio = state.estadios[state.estadio_activo]
    return [
        ont
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
        for ont in arbol.onts
    ]


@settings(max_examples=200, deadline=None)
@given(secuencia=st.lists(estados, min_size=0, max_size=200))
def test_invariante_bajo_secuencia_arbitraria_de_estados(secuencia):
    """Property 3: 0 ≤ ticks_en_linea ≤ ticks_totales tras cada transición.

    Se aplica ``acumular_disponibilidad`` sobre una ONT para una secuencia
    arbitraria de estados y se verifica la invariante **después de cada tick**,
    no solo al final. Así se comprueba que la invariante se mantiene "en todo
    momento" (design §14, Property 3), independientemente del orden y la mezcla
    de estados en línea / fuera de línea.

    **Validates: Requirements 8.3**
    """
    ont = ONT(id="ONT-test", servicio=Servicio.WIFI, arbol_id="ARB-1")

    # Invariante en el arranque (0 == 0).
    assert 0 <= ont.ticks_en_linea <= ont.ticks_totales

    en_linea_esperados = 0
    for i, estado in enumerate(secuencia, start=1):
        ont.estado = estado
        acumular_disponibilidad(ont)

        # La invariante debe mantenerse tras cada tick individual.
        assert 0 <= ont.ticks_en_linea <= ont.ticks_totales

        # ticks_totales cuenta cada tick sin excepción.
        assert ont.ticks_totales == i

        # Verificación cruzada de la semántica en línea / fuera (Req 8.1).
        if estado in ESTADOS_EN_LINEA:
            en_linea_esperados += 1
        assert ont.ticks_en_linea == en_linea_esperados


@given(estado=estados)
def test_degradado_y_en_linea_cuentan_como_disponible(estado):
    """DEGRADADO y EN_LINEA cuentan como en línea; FUERA y RESERVA no (Req 8.1).

    **Validates: Requirements 8.3**
    """
    ont = ONT(id="ONT-sem", servicio=Servicio.CCTV, arbol_id="ARB-1")
    ont.estado = estado
    acumular_disponibilidad(ont)

    assert ont.ticks_totales == 1
    if estado in ESTADOS_EN_LINEA:
        assert ont.ticks_en_linea == 1
    else:
        assert estado in ESTADOS_FUERA
        assert ont.ticks_en_linea == 0

    # La invariante se mantiene en cualquier caso.
    assert 0 <= ont.ticks_en_linea <= ont.ticks_totales


@settings(max_examples=50, deadline=None)
@given(
    num_ticks=st.integers(min_value=0, max_value=60),
    semilla_estados=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_invariante_bajo_motor_real(num_ticks, semilla_estados):
    """Property 3 sobre el motor real: tras N ticks de ``engine.tick``, toda ONT
    del estadio activo satisface 0 ≤ ticks_en_linea ≤ ticks_totales.

    Se construye un ``SimState`` con la topología real cargada y se corre el
    bucle ``tick`` un número arbitrario de veces. Antes de cada tick se aleatoriza
    el estado de las ONTs (usando la semilla generada) para ejercitar la mezcla
    de estados en línea / fuera de línea que el acumulador debe manejar. Tras cada
    tick se verifica la invariante para **todas** las ONTs del estadio activo.

    **Validates: Requirements 8.3**
    """
    import random

    rng = random.Random(semilla_estados)
    state = SimState.crear()
    state.corriendo = True

    onts = _onts_estadio_activo(state)
    todos_los_estados = list(Estado)

    # Invariante inicial (todos los acumuladores en 0).
    for ont in onts:
        assert 0 <= ont.ticks_en_linea <= ont.ticks_totales

    for _ in range(num_ticks):
        # Aleatoriza los estados de las ONTs para variar disponibilidad.
        for ont in onts:
            ont.estado = rng.choice(todos_los_estados)

        tick(state)

        # La invariante debe cumplirse para toda ONT tras cada tick.
        for ont in onts:
            assert 0 <= ont.ticks_en_linea <= ont.ticks_totales

    # Tras N ticks, ticks_totales == N para cada ONT del estadio activo.
    for ont in onts:
        assert ont.ticks_totales == num_ticks
