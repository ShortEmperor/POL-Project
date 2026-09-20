"""Prueba basada en propiedad — Property 11: Break-even bien definido.

Diseño §10.1 y §14 (Property 11), Requirements 11.3 / 11.4:

* WHEN el TCO acumulado de POL es menor o igual (≤) al de cobre en un año
  dentro del horizonte, `break_even` SHALL devolver ese año (Req 11.3). La
  **igualdad exacta** entre ambos TCO cuenta como cruce.
* IF el TCO de POL nunca cruza por debajo del de cobre dentro del horizonte,
  THEN `break_even` SHALL devolver ``None`` (Req 11.4).

Se usa **Hypothesis** para generar CAPEX/OPEX arbitrarios de POL y de cobre más
un horizonte, y se contrasta el resultado de :func:`sim.economics.break_even`
contra una referencia por fuerza bruta calculada con :func:`sim.economics.tco`:

* Si existe un año de cruce en ``[1, horizonte]``, ``break_even`` devuelve el
  **primer** año y en ese año ``TCO_POL(año) <= TCO_cobre(año)``.
* Si ningún año del horizonte satisface el cruce, ``break_even`` devuelve
  ``None``.

Además, se incluyen casos concretos: cruce inmediato (año 1), sin cruce
(``None``) y cruce por igualdad exacta.

**Validates: Requirements 11.3, 11.4**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sim.economics import break_even, tco


def _primer_cruce_referencia(
    capex_pol: float,
    opex_pol: float,
    capex_cobre: float,
    opex_cobre: float,
    horizonte: int,
) -> int | None:
    """Referencia por fuerza bruta del año de break-even (Req 11.3/11.4).

    Recorre ``1..horizonte`` con la misma semántica que la especificación:
    devuelve el primer año en que ``TCO_POL <= TCO_cobre`` (igualdad = cruce) o
    ``None`` si ningún año del horizonte lo cumple. Se calcula de forma
    independiente a ``break_even`` (solo reutiliza ``tco``) para servir de
    oráculo de la prueba.
    """
    for anio in range(1, horizonte + 1):
        if tco(capex_pol, opex_pol, anio) <= tco(capex_cobre, opex_cobre, anio):
            return anio
    return None


@st.composite
def escenarios_economicos(draw) -> dict:
    """Genera escenarios económicos POL vs cobre dentro del dominio válido.

    Constriñe la entrada a valores no negativos y realistas:
      * CAPEX y OPEX de ambas arquitecturas en ``[0, 1e6]`` (enteros para que la
        igualdad exacta de TCO sea alcanzable sin ruido de coma flotante).
      * Horizonte entero positivo en ``[1, 30]`` años.

    El espacio incluye deliberadamente casos donde POL cruza pronto, tarde o
    nunca, de modo que la propiedad se ejerza en ambas ramas (año y ``None``).
    """
    capex_pol = draw(st.integers(min_value=0, max_value=1_000_000))
    opex_pol = draw(st.integers(min_value=0, max_value=1_000_000))
    capex_cobre = draw(st.integers(min_value=0, max_value=1_000_000))
    opex_cobre = draw(st.integers(min_value=0, max_value=1_000_000))
    horizonte = draw(st.integers(min_value=1, max_value=30))
    return {
        "capex_pol": capex_pol,
        "opex_pol": opex_pol,
        "capex_cobre": capex_cobre,
        "opex_cobre": opex_cobre,
        "horizonte": horizonte,
    }


@settings(max_examples=300, deadline=None)
@given(esc=escenarios_economicos())
def test_break_even_bien_definido(esc):
    """Property 11: break_even devuelve el primer año de cruce, o None.

    Contrasta ``break_even`` contra la referencia por fuerza bruta y verifica la
    condición de cruce en el año devuelto.

    **Validates: Requirements 11.3, 11.4**
    """
    capex_pol = esc["capex_pol"]
    opex_pol = esc["opex_pol"]
    capex_cobre = esc["capex_cobre"]
    opex_cobre = esc["opex_cobre"]
    horizonte = esc["horizonte"]

    resultado = break_even(capex_pol, opex_pol, capex_cobre, opex_cobre, horizonte)
    esperado = _primer_cruce_referencia(
        capex_pol, opex_pol, capex_cobre, opex_cobre, horizonte
    )

    # 1) Coincide con la referencia (mismo año o ambos None).
    assert resultado == esperado

    if resultado is None:
        # 2a) Sin cruce: ningún año del horizonte cumple TCO_POL <= TCO_cobre.
        for anio in range(1, horizonte + 1):
            assert tco(capex_pol, opex_pol, anio) > tco(capex_cobre, opex_cobre, anio)
    else:
        # 2b) Con cruce: el año está en el horizonte, es el PRIMERO en cruzar y
        #     en él TCO_POL <= TCO_cobre (igualdad cuenta como cruce, Req 11.3).
        assert 1 <= resultado <= horizonte
        assert tco(capex_pol, opex_pol, resultado) <= tco(
            capex_cobre, opex_cobre, resultado
        )
        for anio in range(1, resultado):
            assert tco(capex_pol, opex_pol, anio) > tco(capex_cobre, opex_cobre, anio)


# ---------------------------------------------------------------------------
# Casos concretos (ejemplos) que fijan la semántica de la propiedad.
# ---------------------------------------------------------------------------
def test_cruce_inmediato_anio_1():
    """Req 11.3: POL ya es más barato desde el primer año -> break-even = 1.

    CAPEX POL menor y OPEX POL menor: en el año 1 el TCO de POL ya es < cobre.
    """
    # TCO_POL(1) = 100 + 10 = 110 ; TCO_cobre(1) = 500 + 50 = 550 -> cruza en 1.
    assert break_even(100, 10, 500, 50, 10) == 1


def test_nunca_cruza_devuelve_none():
    """Req 11.4: si POL siempre cuesta más en el horizonte -> None.

    CAPEX y OPEX de POL estrictamente mayores que los de cobre: el TCO de POL
    nunca alcanza al de cobre en ningún año del horizonte.
    """
    # TCO_POL(anio) = 1000 + 200*anio siempre > TCO_cobre = 100 + 10*anio.
    assert break_even(1000, 200, 100, 10, 10) is None


def test_cruce_por_igualdad_exacta():
    """Req 11.3: la igualdad exacta de TCO cuenta como cruce.

    Se elige un escenario donde el TCO de POL es estrictamente mayor en el año 1
    y **exactamente igual** al de cobre en el año 2; ese año de igualdad debe
    devolverse como punto de equilibrio.
    """
    # POL: capex 300, opex 100 -> TCO(1)=400, TCO(2)=500.
    # Cobre: capex 100, opex 200 -> TCO(1)=300, TCO(2)=500.
    # Año 1: 400 > 300 (no cruza). Año 2: 500 == 500 (igualdad = cruce).
    assert break_even(300, 100, 100, 200, 10) == 2
    assert tco(300, 100, 2) == tco(100, 200, 2)


def test_cruce_fuera_del_horizonte_devuelve_none():
    """Req 11.4: un cruce que ocurriría más allá del horizonte no cuenta.

    POL cruza al cobre en el año 5, pero con horizonte 3 no hay break-even.
    """
    # POL: capex 300, opex 50 ; Cobre: capex 100, opex 100.
    # TCO_POL <= TCO_cobre cuando 300+50a <= 100+100a -> a >= 4 -> cruza en 4.
    assert break_even(300, 50, 100, 100, 3) is None
    assert break_even(300, 50, 100, 100, 5) == 4
