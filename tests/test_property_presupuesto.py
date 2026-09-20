"""Prueba basada en propiedad — Property 12: Presupuesto de indisponibilidad.

Diseño §10.2 (Property 12) y Requirement 13.3:

El presupuesto anual de indisponibilidad permitido por un objetivo de
disponibilidad ``objetivo`` (fracción 0..1) es ``(1 - objetivo) *
MINUTOS_POR_ANIO`` minutos/año, con ``MINUTOS_POR_ANIO = 365 * 24 * 60 =
525 600``. De ahí las cifras de referencia del criterio de aceptación:

* 99.999% (cinco nueves) ≈ 5.256  min/año
* 99.99%  (cuatro nueves) ≈ 52.56 min/año
* 99.9%   (tres nueves)   ≈ 525.6 min/año

Se combinan pruebas de ejemplo (las cifras estándar del requisito) con una
propiedad de Hypothesis sobre todo el rango ``objetivo ∈ (0, 1)`` (fórmula
exacta, monotonía decreciente y no-negatividad), más la verificación del helper
por esquema de protección y un pequeño saneo de ``presupuesto_consumido``.

**Validates: Requirements 13.3**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.economics import (
    MINUTOS_POR_ANIO,
    presupuesto_consumido,
    presupuesto_indisponibilidad_min,
    presupuesto_indisponibilidad_min_proteccion,
)
from sim.state import ONT, Proteccion, Servicio, objetivo_sla


# ---------------------------------------------------------------------------
# Ejemplos del criterio de aceptación (Req 13.3): cifras estándar de "nueves".
#
# Valores exactos = (1 - objetivo) * 525600:
#   0.99999 -> 5.256   0.9999 -> 52.56   0.999 -> 525.6
# El requisito los cita redondeados (≈ 5.26 / 52.6 / 525.6); se afirma contra
# el valor exacto con pytest.approx, lo que también cubre el redondeo del
# enunciado dentro de una tolerancia razonable.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("objetivo", "esperado_min_anio"),
    [
        (0.99999, 5.256),   # cinco nueves  ≈ 5.26
        (0.9999, 52.56),    # cuatro nueves ≈ 52.6
        (0.999, 525.6),     # tres nueves   ≈ 525.6
    ],
)
def test_presupuesto_cifras_estandar(objetivo: float, esperado_min_anio: float) -> None:
    """Los minutos/año coinciden con el cálculo estándar de disponibilidad (Req 13.3)."""
    assert presupuesto_indisponibilidad_min(objetivo) == pytest.approx(
        esperado_min_anio, rel=1e-9
    )


def test_minutos_por_anio_es_525600() -> None:
    """La base anual es 365 * 24 * 60 = 525 600 minutos (design §10.2)."""
    assert MINUTOS_POR_ANIO == pytest.approx(525600.0)


# ---------------------------------------------------------------------------
# Property 12 (Hypothesis): fórmula exacta, monotonía y no-negatividad.
# ---------------------------------------------------------------------------
# Objetivos en el interior abierto (0, 1). Se excluyen 0 y 1 exactos para
# centrar la propiedad en el rango contractual útil; los extremos se cubren en
# los ejemplos de borde más abajo.
objetivos = st.floats(
    min_value=0.0,
    max_value=1.0,
    exclude_min=True,
    exclude_max=True,
    allow_nan=False,
    allow_infinity=False,
)


@given(objetivo=objetivos)
@settings(max_examples=300)
def test_presupuesto_formula_y_no_negatividad(objetivo: float) -> None:
    """presupuesto(objetivo) == (1 - objetivo) * 525600 y es >= 0 (Req 13.3)."""
    resultado = presupuesto_indisponibilidad_min(objetivo)
    assert resultado == pytest.approx((1.0 - objetivo) * MINUTOS_POR_ANIO)
    assert resultado >= 0.0


@given(a=objetivos, b=objetivos)
@settings(max_examples=300)
def test_presupuesto_monotono_decreciente(a: float, b: float) -> None:
    """A mayor objetivo de disponibilidad, menor presupuesto de indisponibilidad."""
    if a < b:
        # Un objetivo más exigente (mayor) deja MENOS minutos de indisponibilidad.
        assert presupuesto_indisponibilidad_min(a) >= presupuesto_indisponibilidad_min(b)


def test_presupuesto_bordes() -> None:
    """Bordes 0 y 1: disponibilidad nula -> año entero; perfecta -> cero minutos."""
    assert presupuesto_indisponibilidad_min(0.0) == pytest.approx(MINUTOS_POR_ANIO)
    assert presupuesto_indisponibilidad_min(1.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Helper por esquema de protección: reutiliza objetivo_sla (§6.5).
#   TYPE_C -> 0.99999 -> 5.256 ; TYPE_B -> 0.9999 -> 52.56 ; NINGUNA -> 0.999 -> 525.6
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("proteccion", "esperado_min_anio"),
    [
        (Proteccion.TYPE_C, 5.256),
        (Proteccion.TYPE_B, 52.56),
        (Proteccion.NINGUNA, 525.6),
    ],
)
def test_presupuesto_por_proteccion(
    proteccion: Proteccion, esperado_min_anio: float
) -> None:
    """El helper por protección mapea vía objetivo_sla a las cifras estándar (Req 13.3)."""
    assert presupuesto_indisponibilidad_min_proteccion(proteccion) == pytest.approx(
        esperado_min_anio, rel=1e-9
    )
    # Y equivale a evaluar el presupuesto sobre el objetivo teórico de la protección.
    assert presupuesto_indisponibilidad_min_proteccion(
        proteccion
    ) == pytest.approx(presupuesto_indisponibilidad_min(objetivo_sla(proteccion)))


# ---------------------------------------------------------------------------
# Saneo de presupuesto_consumido (Req 13.4): consumido = (1 - d) / (1 - objetivo).
# ---------------------------------------------------------------------------
def _ont(ticks_totales: int, ticks_en_linea: int) -> ONT:
    """ONT mínima con acumuladores de disponibilidad prefijados."""
    ont = ONT(id="ont-x", servicio=Servicio.PALCOS, arbol_id="arbol-x")
    ont.ticks_totales = ticks_totales
    ont.ticks_en_linea = ticks_en_linea
    return ont


def test_consumido_disponibilidad_perfecta_es_cero() -> None:
    """Sin indisponibilidad observada no hay consumo, para cualquier objetivo."""
    onts = [_ont(ticks_totales=1000, ticks_en_linea=1000)]
    assert presupuesto_consumido(onts, objetivo=0.999) == pytest.approx(0.0)


def test_consumido_presupuesto_justo_agotado() -> None:
    """Indisponibilidad observada igual a la permitida -> consumo == 1.0."""
    # objetivo 0.999 permite 0.001 de indisponibilidad; d = 0.999 la agota justa.
    onts = [_ont(ticks_totales=1000, ticks_en_linea=999)]
    assert presupuesto_consumido(onts, objetivo=0.999) == pytest.approx(1.0)


def test_consumido_presupuesto_excedido() -> None:
    """Indisponibilidad observada mayor que la permitida -> consumo > 1.0."""
    # d = 0.998 (2/1000 fuera) frente a objetivo 0.999 (permite 1/1000): doble.
    onts = [_ont(ticks_totales=1000, ticks_en_linea=998)]
    assert presupuesto_consumido(onts, objetivo=0.999) == pytest.approx(2.0)
