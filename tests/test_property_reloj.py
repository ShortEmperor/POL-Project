"""Prueba basada en propiedad — Property 2: Monotonía del reloj.

Diseño §6.1 (bucle ``tick``) y §14 (Property 2), Requirement 16.3:

* WHEN el Motor_de_Simulacion ejecuta un tick, SHALL incrementar ``t_min`` sin
  decrecer y sin superar ``135.0``.

A diferencia de una prueba de ejemplo puntual, aquí se usa **Hypothesis** para
generar valores iniciales arbitrarios de ``t_min``, velocidades arbitrarias del
conjunto permitido ``{1x, 5x, 15x}`` y un número arbitrario de ticks del motor
real (``sim.engine.tick``). Tras **cada** tick se comprueba que:

* ``t_min`` no decrece respecto al valor previo (monotonía no decreciente), y
* ``t_min`` nunca supera el tope ``T_MIN_FINAL == 135.0``.

Además, una prueba de frontera verifica que una vez alcanzado ``135.0`` el reloj
permanece exactamente en ``135.0`` a través de ticks posteriores (saturación).

**Validates: Requirements 16.3**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sim.engine import tick
from sim.state import (
    T_MIN_FINAL,
    T_MIN_INICIAL,
    VELOCIDADES_PERMITIDAS,
    SimState,
)

# Estrategia: cualquiera de las velocidades permitidas del motor (Req 16.1).
velocidades = st.sampled_from(list(VELOCIDADES_PERMITIDAS))

# Estrategia: valores iniciales de t_min dentro del rango operativo válido del
# reloj del partido, acotado superiormente por el tope ``T_MIN_FINAL == 135.0``
# (Req 16.3). Un estado válido del motor nunca tiene ``t_min`` por encima del
# tope, de modo que la monotonía se verifica sobre estados alcanzables; la
# saturación exacta en el tope se cubre en ``test_frontera_saturacion_en_135``.
# El extremo inferior se relaja un poco bajo T-180 por robustez del arranque.
t_min_inicial = st.floats(
    min_value=T_MIN_INICIAL - 50.0,
    max_value=T_MIN_FINAL,
    allow_nan=False,
    allow_infinity=False,
)


def _nuevo_state(t_min: float, velocidad: float) -> SimState:
    """Construye un ``SimState`` mínimo, corriendo, con la topología real cargada.

    Se usa ``SimState.crear()`` para cargar la topología real (necesaria para que
    los pasos de tráfico/KPIs del ``tick`` operen sobre un estadio válido) y luego
    se fija ``t_min``/``velocidad`` al caso generado. El motor se marca como
    ``corriendo`` porque ``tick`` avanza el reloj bajo esa precondición (§6.1).
    """
    state = SimState.crear()
    state.corriendo = True
    state.t_min = t_min
    state.set_velocidad(velocidad)
    return state


@settings(max_examples=100, deadline=None)
@given(
    t_min0=t_min_inicial,
    velocidad=velocidades,
    num_ticks=st.integers(min_value=1, max_value=60),
)
def test_monotonia_y_tope_bajo_ticks_arbitrarios(t_min0, velocidad, num_ticks):
    """Property 2: tras cada tick, ``t_min`` no decrece y no supera ``135.0``.

    Para un ``t_min`` inicial arbitrario, una velocidad arbitraria del conjunto
    permitido y un número arbitrario de ticks, se corre ``engine.tick`` en bucle
    y **después de cada tick** se verifica la invariante de monotonía no
    decreciente y el tope de ``135.0``.

    **Validates: Requirements 16.3**
    """
    state = _nuevo_state(t_min0, velocidad)

    previo = state.t_min
    for _ in range(num_ticks):
        tick(state)

        # No decrece respecto al valor previo (monotonía no decreciente).
        assert state.t_min >= previo
        # Nunca supera el tope del reloj del partido (Req 16.3).
        assert state.t_min <= T_MIN_FINAL

        previo = state.t_min


@settings(max_examples=100, deadline=None)
@given(t_min0=t_min_inicial, velocidad=velocidades)
def test_incremento_por_velocidad_hasta_el_tope(t_min0, velocidad):
    """Cada tick incrementa exactamente ``velocidad`` minutos hasta topar en 135.0.

    Confirma la semántica de avance del reloj (§6.1): mientras haya margen antes
    del tope, ``t_min`` crece en ``velocidad``; al alcanzar el tope, queda topado
    en ``135.0`` sin superarlo.

    **Validates: Requirements 16.3**
    """
    state = _nuevo_state(t_min0, velocidad)

    antes = state.t_min
    tick(state)
    esperado = min(antes + velocidad, T_MIN_FINAL)

    assert state.t_min == esperado
    assert state.t_min <= T_MIN_FINAL
    assert state.t_min >= antes


@settings(max_examples=50, deadline=None)
@given(velocidad=velocidades, num_ticks=st.integers(min_value=1, max_value=30))
def test_frontera_saturacion_en_135(velocidad, num_ticks):
    """Frontera: una vez en 135.0, el reloj permanece exactamente en 135.0.

    Se arranca el estado justo en el tope ``T_MIN_FINAL`` y se corren varios
    ticks a cualquier velocidad permitida; ``t_min`` debe quedarse clavado en
    ``135.0`` (ni crece ni decrece), demostrando la saturación del reloj.

    **Validates: Requirements 16.3**
    """
    state = _nuevo_state(T_MIN_FINAL, velocidad)

    for _ in range(num_ticks):
        tick(state)
        assert state.t_min == T_MIN_FINAL
