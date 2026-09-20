"""Prueba de convergencia de SLA — Property 4 (tarea 5.7).

Diseño §6.5 (Acumulación de disponibilidad / Propiedad de convergencia) y §14
(Property 4). Requirement 8.5:

    WHILE una corrida larga (al menos 100.000 ticks) transcurre sin fallas, THE
    Motor_de_Simulacion SHALL mantener la disponibilidad por servicio en
    disponibilidad_servicio ≥ objetivo − tolerancia, con objetivo según su
    protección (TYPE_C → 99.999%, TYPE_B → 99.99%, NINGUNA → 99.9%) y
    tolerancia = 0,5%; una disponibilidad que exceda el objetivo por cualquier
    margen NO constituye incumplimiento (tolerancia de un solo lado).

**Validates: Requirements 8.5**

Enfoque y decisión de modelado
-------------------------------
La propiedad de convergencia es una afirmación sobre el **modelo de acumulación
de disponibilidad** (§6.5), no sobre el tráfico. Ejecutar el motor completo
(`sim.engine.tick`) 100.000 veces sobre la topología real de 1.990 ONTs con
tráfico recalculado cada tick sería innecesariamente lento y no aportaría nada
a lo que la propiedad afirma. Por eso estas pruebas ejercen directamente
`acumular_disponibilidad` sobre conjuntos de ONTs a lo largo de ≥ 100.000 ticks,
agrupadas por esquema de protección, y verifican la cota de un solo lado
`disponibilidad_servicio ≥ objetivo(proteccion) − 0.005`.

Se cubren dos regímenes, ambos deterministas con `semilla = 42`:

1.  **Caso sin fallas (el que exige literalmente el Req 8.5).** Sin fallas todas
    las ONTs permanecen EN_LINEA cada tick, luego `disponibilidad_servicio == 1.0`,
    que satisface trivialmente la cota de un solo lado para *cualquier* objetivo
    (1.0 ≥ objetivo − tolerancia). Esta es exactamente la condición que el
    requisito describe ("transcurre sin fallas").

2.  **Caso modelado por esquema (para hacer la prueba significativa).** Para que
    la aserción no sea trivial, se modela la indisponibilidad *teórica permitida*
    de cada esquema: se inyecta de forma determinista, a lo largo de la corrida,
    una fracción de ticks fuera de línea igual a `(1 − objetivo)` (5.26e-6 para
    TYPE_C, 1e-4 para TYPE_B, 1e-3 para NINGUNA). Con ello la disponibilidad se
    aproxima al objetivo *desde arriba/dentro de la tolerancia* y la cota de un
    solo lado se ejercita de verdad. El sorteo de qué ticks caen usa un RNG
    sembrado con 42 (reproducible).

Ambos regímenes son puros (sin Dash, §3.1) y corren en fracciones de segundo.
"""

from __future__ import annotations

import random

import pytest

from sim.state import (
    ONT,
    Estado,
    Proteccion,
    Servicio,
    TOLERANCIA_SLA,
    acumular_disponibilidad,
    disponibilidad_servicio,
    objetivo_sla,
)

# Número de ticks de la corrida larga (Req 8.5: al menos 100.000).
TICKS = 100_000

# Semilla determinista de la corrida (Req 17.3 / consistencia demo).
SEMILLA = 42

# Esquemas de protección con su objetivo teórico (§6.5).
ESQUEMAS = (Proteccion.TYPE_C, Proteccion.TYPE_B, Proteccion.NINGUNA)


def _hacer_onts(proteccion: Proteccion, n: int = 10) -> list[ONT]:
    """Crea `n` ONTs de un mismo servicio y esquema de protección."""
    return [
        ONT(
            id=f"ont-{proteccion.value}-{i}",
            servicio=Servicio.PALCOS,
            arbol_id="arbol-test",
            proteccion=proteccion,
        )
        for i in range(n)
    ]


def _correr_sin_fallas(onts: list[ONT], ticks: int) -> None:
    """Acumula `ticks` de disponibilidad sin inyectar ninguna falla.

    Todas las ONTs permanecen EN_LINEA (estado por defecto), de modo que cada
    tick incrementa ticks_totales y ticks_en_linea por igual.
    """
    for _ in range(ticks):
        for ont in onts:
            acumular_disponibilidad(ont)


def _correr_con_indisponibilidad_teorica(
    onts: list[ONT], ticks: int, objetivo: float, rng: random.Random
) -> None:
    """Acumula `ticks` inyectando la indisponibilidad *permitida* del esquema.

    Para cada tick, con probabilidad `(1 − objetivo)` se coloca una ONT en
    estado FUERA (indisponible) antes de acumular, y se restaura a EN_LINEA
    después. Así la disponibilidad observada se aproxima al objetivo desde
    dentro de la tolerancia, ejercitando de verdad la cota de un solo lado.
    El sorteo usa `rng` (sembrado con 42) para ser reproducible.
    """
    prob_fuera = 1.0 - objetivo
    for _ in range(ticks):
        for ont in onts:
            caida = rng.random() < prob_fuera
            ont.estado = Estado.FUERA if caida else Estado.EN_LINEA
            acumular_disponibilidad(ont)
    # Restaurar estado nominal al terminar (no dejar ONTs FUERA).
    for ont in onts:
        ont.estado = Estado.EN_LINEA


# ---------------------------------------------------------------------------
# Régimen 1: corrida larga SIN fallas (condición literal del Req 8.5)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("proteccion", ESQUEMAS)
def test_convergencia_sin_fallas_una_via(proteccion: Proteccion):
    """Sin fallas, disponibilidad = 1.0 ≥ objetivo − tolerancia (un solo lado).

    Ejecuta ≥ 100.000 ticks sin inyectar fallas. Como toda ONT permanece
    EN_LINEA, `disponibilidad_servicio == 1.0`, que cumple la cota de un solo
    lado para el objetivo del esquema. Exceder el objetivo (1.0 > objetivo) es
    conforme por definición del requisito.

    **Validates: Requirements 8.5**
    """
    onts = _hacer_onts(proteccion)
    _correr_sin_fallas(onts, TICKS)

    disp = disponibilidad_servicio(onts)
    objetivo = objetivo_sla(proteccion)
    cota_inferior = objetivo - TOLERANCIA_SLA

    # Cada ONT vio exactamente TICKS observaciones (base de la corrida larga).
    assert all(o.ticks_totales == TICKS for o in onts)
    assert disp == pytest.approx(1.0)
    # Cota de un solo lado: conforme si disp ≥ objetivo − tolerancia.
    assert disp >= cota_inferior


# ---------------------------------------------------------------------------
# Régimen 2: corrida larga con la indisponibilidad TEÓRICA del esquema
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("proteccion", ESQUEMAS)
def test_convergencia_al_objetivo_una_via(proteccion: Proteccion):
    """La disponibilidad converge al objetivo del esquema dentro de tolerancia.

    Inyecta de forma determinista (semilla 42) la indisponibilidad permitida
    del esquema `(1 − objetivo)` a lo largo de ≥ 100.000 ticks. La
    disponibilidad resultante debe quedar dentro de la banda de un solo lado:
    `disponibilidad_servicio ≥ objetivo − tolerancia`.

    **Validates: Requirements 8.5**
    """
    rng = random.Random(SEMILLA)
    objetivo = objetivo_sla(proteccion)
    # Más ONTs para TYPE_C: su fracción de caída es diminuta (5.26e-6), así
    # el número esperado de ticks caídos sobre la muestra es representativo.
    n = 50 if proteccion is Proteccion.TYPE_C else 10
    onts = _hacer_onts(proteccion, n=n)

    _correr_con_indisponibilidad_teorica(onts, TICKS, objetivo, rng)

    disp = disponibilidad_servicio(onts)
    cota_inferior = objetivo - TOLERANCIA_SLA

    assert all(o.ticks_totales == TICKS for o in onts)
    # Cota de un solo lado (Req 8.5): conforme si disp ≥ objetivo − tolerancia.
    assert disp >= cota_inferior, (
        f"{proteccion.value}: disp={disp:.6f} < objetivo−tol={cota_inferior:.6f}"
    )


def test_exceder_objetivo_es_conforme():
    """Exceder el objetivo por cualquier margen NO es incumplimiento (una vía).

    Verifica explícitamente la semántica de un solo lado del Req 8.5: una
    disponibilidad perfecta (1.0) es conforme incluso para el objetivo más
    exigente (TYPE_C, 99.999%), porque solo se penaliza caer por debajo de
    `objetivo − tolerancia`.

    **Validates: Requirements 8.5**
    """
    onts = _hacer_onts(Proteccion.TYPE_C)
    _correr_sin_fallas(onts, TICKS)

    disp = disponibilidad_servicio(onts)
    objetivo = objetivo_sla(Proteccion.TYPE_C)

    # disp excede el objetivo: no debe tratarse como violación.
    assert disp > objetivo
    assert disp >= objetivo - TOLERANCIA_SLA
    # La cota superior NO se aplica: no existe aserción disp <= objetivo.
