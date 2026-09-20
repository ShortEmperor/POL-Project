"""Fallas estocásticas opcionales (tarea 7.2, design §6.4, Requirement 6.3).

Cubre dos cosas:

1. La matemática de :func:`p_falla_por_tick` (modelo exponencial de MTBF):
   ``p = 1 - exp(-delta_t / mtbf)``. Casos: MTBF enorme → ~0, MTBF <= 0 → 0.0,
   delta_t pequeño → probabilidad pequeña, y monotonía creciente en ``delta_t``.

2. Que las fallas estocásticas están **desactivadas por defecto**: un tick
   normal (sin activar el opt-in) NO inyecta ninguna falla. Solo cuando se
   activa ``state.fallas_estocasticas_activas`` puede llegar a inyectar, y aun
   así de forma **determinista** con la misma semilla (Req 17.3).

**Validates: Requirements 6.3**

Python puro: no se levanta la Capa_de_Presentacion (design §3.1).
"""

from __future__ import annotations

import math

import pytest

from sim.faults import (
    aplicar,
    p_falla_por_tick,
    paso_estocastico,
)
from sim.state import SimState


# ---------------------------------------------------------------------------
# 1) Matemática del modelo exponencial: p_falla_por_tick
# ---------------------------------------------------------------------------
def test_mtbf_no_positivo_devuelve_cero():
    """Con MTBF <= 0 la probabilidad es exactamente 0.0 (guarda contra div/0)."""
    assert p_falla_por_tick(1.0, 0.0) == 0.0
    assert p_falla_por_tick(15.0, -5.0) == 0.0


def test_mtbf_enorme_probabilidad_casi_cero():
    """Con un MTBF muchísimo mayor que delta_t la falla es prácticamente nula."""
    p = p_falla_por_tick(1.0, 1_000_000_000.0)
    assert 0.0 <= p < 1e-6


def test_delta_pequeno_probabilidad_pequena_y_formula_exacta():
    """delta_t pequeño ⇒ probabilidad pequeña, y coincide con la fórmula exacta."""
    delta_t, mtbf = 1.0, 1000.0
    esperado = 1.0 - math.exp(-delta_t / mtbf)
    assert p_falla_por_tick(delta_t, mtbf) == pytest.approx(esperado)
    assert 0.0 < esperado < 0.01  # ~0.0999% de falla por minuto


def test_probabilidad_en_rango_0_1():
    """La probabilidad siempre cae en [0, 1] para MTBF positivo y delta_t >= 0.

    Con delta_t >> mtbf la exponencial tiende a 0 y la probabilidad satura a 1.0
    (comportamiento correcto del modelo), por eso el extremo superior es
    inclusivo.
    """
    for delta_t in (0.0, 1.0, 5.0, 15.0, 1e6):
        for mtbf in (0.5, 10.0, 1000.0, 1e9):
            p = p_falla_por_tick(delta_t, mtbf)
            assert 0.0 <= p <= 1.0


def test_monotonia_creciente_en_delta_t():
    """A MTBF fijo, la probabilidad crece con delta_t (más tiempo, más riesgo)."""
    mtbf = 500.0
    deltas = [0.5, 1.0, 5.0, 15.0, 60.0]
    probs = [p_falla_por_tick(d, mtbf) for d in deltas]
    for anterior, siguiente in zip(probs, probs[1:]):
        assert siguiente > anterior


def test_delta_cero_probabilidad_cero():
    """Sin avance de tiempo (delta_t = 0) no hay probabilidad de falla."""
    assert p_falla_por_tick(0.0, 1000.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2) Desactivado por defecto: un tick normal no inyecta nada
# ---------------------------------------------------------------------------
def _num_fallas(state: SimState) -> int:
    return len(getattr(state, "fallas_activas", []) or [])


def test_estocastica_desactivada_por_defecto_no_inyecta():
    """Por defecto, ``aplicar`` (el paso del tick) NO inyecta fallas estocásticas.

    Se ejecuta ``aplicar`` muchas veces avanzando el reloj; como el opt-in no
    está activo, el número de fallas activas permanece en cero. Esto garantiza
    que el comportamiento existente no cambia (tarea opcional no disruptiva).
    """
    state = SimState.crear()
    # El flag no existe / es falso por defecto.
    assert not getattr(state, "fallas_estocasticas_activas", False)

    for i in range(200):
        state.t_min = -180.0 + i  # avanzar el reloj para variar la siembra
        aplicar(state)

    assert _num_fallas(state) == 0


def test_paso_estocastico_devuelve_false_si_desactivado():
    """``paso_estocastico`` es un no-op explícito cuando el opt-in está apagado."""
    state = SimState.crear()
    assert paso_estocastico(state) is False
    assert _num_fallas(state) == 0


# ---------------------------------------------------------------------------
# 3) Opt-in: activado, puede inyectar y es determinista con la misma semilla
# ---------------------------------------------------------------------------
def _correr_estocastico(semilla: int, mtbf_min: float, pasos: int) -> list[str]:
    """Corre ``pasos`` ticks con estocástica activa y devuelve tipos inyectados."""
    state = SimState.crear(semilla=semilla)
    state.fallas_estocasticas_activas = True  # opt-in explícito
    state.mtbf_estocastico_min = mtbf_min
    state.velocidad = 15.0  # delta_t grande para observar fallas en pocos pasos
    for i in range(pasos):
        state.t_min = -180.0 + i * state.velocidad
        aplicar(state)
    return [f.tipo for f in getattr(state, "fallas_activas", [])]


def test_activado_puede_inyectar_con_mtbf_agresivo():
    """Con el opt-in activo y un MTBF pequeño se llegan a inyectar fallas."""
    inyectadas = _correr_estocastico(semilla=42, mtbf_min=30.0, pasos=60)
    assert inyectadas, "con MTBF agresivo y opt-in activo debería inyectar alguna falla"
    assert all(t == "corte_troncal" for t in inyectadas)


def test_estocastica_determinista_misma_semilla():
    """Misma semilla ⇒ misma secuencia de fallas estocásticas (Req 17.3)."""
    a = _correr_estocastico(semilla=7, mtbf_min=30.0, pasos=60)
    b = _correr_estocastico(semilla=7, mtbf_min=30.0, pasos=60)
    assert a == b
