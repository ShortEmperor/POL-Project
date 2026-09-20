"""Pruebas de KPIs y completitud del catálogo (tarea 9.9).

Cobertura del Requirement 2 (catálogo K1–K12 y completitud, Req 2.4) y del
Requirement 17.1/17.2 (cobertura pytest de los KPIs y de la conmutación de
protección **sin levantar la Capa de Presentación**: este módulo importa **solo**
de ``sim`` y del stdlib, nunca de ``app`` ni de Dash).

Alcance:

* Req 2.4 — completitud del catálogo: ``CATALOGO`` contiene exactamente K1..K12,
  ``verificar_completitud()`` pasa, y una prueba que **falla** si un KPI del
  catálogo estuviera sin implementar o fuera de rango. Cada KPI expone metadatos
  (nombre, fórmula, unidad, umbral).
* K1–K12 — cálculo real: se construye ``SimState.crear()`` + ``traffic.actualizar``
  y se afirma que ``calcular(state)`` devuelve exactamente las claves K1..K12,
  todas numéricas, con saneos por KPI (K1 %, K3 Gbps > 0, K4 conteo, K6 dBm
  negativo, K11 = 100 - K1, etc.).
* Conmutación de protección (Req 17.2) a nivel de simulación:
  TYPE_B/TYPE_C con reserva mantiene servicio; NINGUNA cae. Complementa
  ``tests/test_faults_proteccion.py`` / ``tests/test_faults_sin_proteccion.py``
  con una aserción enfocada (sin duplicar las pruebas de propiedad).
"""

from __future__ import annotations

import math

import pytest

from sim.faults import conmutar_proteccion, inyectar_falla
from sim.kpis import (
    CATALOGO,
    CODIGOS_KPI,
    KPI,
    calcular,
    verificar_completitud,
)
from sim.state import Estado, Proteccion, SimState
from sim.traffic import actualizar

UNIDADES_VALIDAS = {"%", "Gbps", "dBm", "ms", "conteo"}
COMPARACIONES_VALIDAS = {"<=", ">=", "==", None}


# ===========================================================================
# Req 2.4 — Completitud del catálogo K1..K12
# ===========================================================================
def test_catalogo_tiene_exactamente_k1_a_k12():
    """El catálogo cubre exactamente K1..K12 (ni falta ni sobra, Req 2.1/2.4)."""
    assert set(CATALOGO) == set(CODIGOS_KPI)
    assert CODIGOS_KPI == tuple(f"K{i}" for i in range(1, 13))
    assert len(CATALOGO) == 12


def test_verificar_completitud_pasa():
    """`verificar_completitud()` no lanza con el catálogo completo (Req 2.4)."""
    verificar_completitud()  # AssertionError si faltara/ sobrara algún KPI.


def test_cada_kpi_tiene_metadatos():
    """Cada KPI del catálogo expone nombre, fórmula, unidad y umbral (Req 2.1).

    FALLA si un KPI del catálogo careciera de metadatos o usara una unidad
    fuera del conjunto soportado (haciendo cumplir Req 2.4: ningún KPI del
    catálogo queda mal definido).
    """
    for codigo, kpi in CATALOGO.items():
        assert isinstance(kpi, KPI)
        assert kpi.codigo == codigo
        assert isinstance(kpi.nombre, str) and kpi.nombre.strip()
        assert isinstance(kpi.formula, str) and kpi.formula.strip()
        assert kpi.unidad in UNIDADES_VALIDAS, f"{codigo}: unidad inválida {kpi.unidad!r}"
        assert kpi.umbral is None or isinstance(kpi.umbral, (int, float))
        assert isinstance(kpi.umbral_texto, str) and kpi.umbral_texto.strip()
        assert kpi.comparacion in COMPARACIONES_VALIDAS


def test_completitud_falla_si_falta_un_kpi(monkeypatch):
    """Si un KPI del catálogo desapareciera, la verificación DEBE fallar (Req 2.4).

    Se elimina K12 de una copia del catálogo y se comprueba que
    ``verificar_completitud`` lo detecta con ``AssertionError``. No muta el
    catálogo real: se parchea sobre una copia mediante ``monkeypatch``.
    """
    catalogo_incompleto = dict(CATALOGO)
    del catalogo_incompleto["K12"]
    monkeypatch.setattr("sim.kpis.CATALOGO", catalogo_incompleto)
    with pytest.raises(AssertionError):
        verificar_completitud()


# ===========================================================================
# K1–K12 — cálculo real sobre un SimState con tráfico
# ===========================================================================
@pytest.fixture()
def estado_con_trafico():
    """SimState real de Azteca con un tick de tráfico aplicado (escenario diseño)."""
    state = SimState.crear(estadio_activo="azteca", escenario="diseno")
    state.t_min = 0.0  # medio partido: hay demanda de tráfico.
    actualizar(state)
    return state


def test_calcular_devuelve_exactamente_k1_a_k12(estado_con_trafico):
    """`calcular` devuelve exactamente las claves K1..K12, todas numéricas (Req 2.1)."""
    valores = calcular(estado_con_trafico)
    assert set(valores) == set(CODIGOS_KPI)
    for codigo, v in valores.items():
        assert isinstance(v, (int, float)), f"{codigo} no es numérico: {v!r}"
        assert not isinstance(v, bool)
        assert math.isfinite(v), f"{codigo} no es finito: {v!r}"


def test_kpis_saneo_por_metrica(estado_con_trafico):
    """Saneos por KPI: rangos y relaciones esperadas (design §7)."""
    v = calcular(estado_con_trafico)

    # K1/K2 utilización en % dentro de un rango razonable (>= 0).
    assert v["K1"] >= 0.0
    assert v["K2"] >= 0.0
    assert v["K2"] <= v["K1"] + 1e-9  # la media no supera el pico.

    # K3 throughput total en Gbps > 0 (hay tráfico en curso).
    assert v["K3"] > 0.0

    # K4 ONTs en línea: conteo entero >= 0 y <= total de ONTs del estadio.
    estadio = estado_con_trafico.estadios[estado_con_trafico.estadio_activo]
    assert v["K4"] == int(v["K4"])
    assert 0 <= v["K4"] <= estadio.num_onts

    # K5 disponibilidad por servicio en % (0..100). Sin fallas, alta.
    assert 0.0 <= v["K5"] <= 100.0

    # K6 potencia óptica media en dBm: negativa (nivel óptico Rx típico).
    assert v["K6"] < 0.0

    # K7/K8/K10 conteos enteros no negativos; sin fallas inyectadas, cero.
    for codigo in ("K7", "K8", "K10"):
        assert v[codigo] == int(v[codigo])
        assert v[codigo] >= 0
    assert v["K8"] <= v["K7"]  # las críticas son subconjunto de las activas.

    # K9 latencia estimada en ms > 0.
    assert v["K9"] > 0.0

    # K11 margen de capacidad = 100 - K1 (acotado a >= 0).
    assert v["K11"] == pytest.approx(max(0.0, 100.0 - v["K1"]))

    # K12 tiempo de conmutación en ms >= 0 (sin conmutaciones aún, 0).
    assert v["K12"] >= 0.0


def test_calcular_empuja_historial(estado_con_trafico):
    """Cada llamada a `calcular` añade un punto por serie K1..K12 (design §6.1)."""
    calcular(estado_con_trafico)
    calcular(estado_con_trafico)
    for codigo in CODIGOS_KPI:
        assert codigo in estado_con_trafico.historial
        # Dos ticks -> al menos dos puntos en cada serie.
        assert len(estado_con_trafico.historial[codigo]) >= 2


def test_k12_refleja_conmutacion_de_proteccion(estado_con_trafico):
    """Tras una conmutación exitosa, K12 reporta el tiempo de switch (< 50 ms)."""
    state = estado_con_trafico
    estadio = state.estadios[state.estadio_activo]

    # Buscar un árbol protegido y asegurar reserva operativa.
    arbol = None
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if puerto.es_reserva and puerto.estado == Estado.FUERA:
                puerto.estado = Estado.RESERVA
            for a in puerto.arboles:
                if a.proteccion in (Proteccion.TYPE_B, Proteccion.TYPE_C) and arbol is None:
                    arbol = a
    assert arbol is not None, "la topología debe tener un árbol protegido"

    ok = conmutar_proteccion(arbol, state)
    assert ok is True
    v = calcular(state)
    assert 0 < v["K12"] <= 50.0


# ===========================================================================
# Conmutación de protección a nivel de simulación (Req 17.2) — sin UI
# ===========================================================================
def test_type_bc_con_reserva_mantiene_servicio():
    """TYPE_B/TYPE_C con reserva operativa mantiene EN_LINEA las ONTs (Req 6.5)."""
    state = SimState.crear(estadio_activo="azteca")
    estadio = state.estadios[state.estadio_activo]

    # Asegurar reserva operativa en el estadio.
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if puerto.es_reserva and puerto.estado == Estado.FUERA:
                puerto.estado = Estado.RESERVA

    arbol = next(
        a
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for a in puerto.arboles
        if a.proteccion in (Proteccion.TYPE_B, Proteccion.TYPE_C)
    )
    onts_prot = [o for o in arbol.onts if o.proteccion in (Proteccion.TYPE_B, Proteccion.TYPE_C)]
    assert onts_prot

    inyectar_falla(state, "corte_troncal", arbol.id)

    assert all(o.estado == Estado.EN_LINEA for o in onts_prot)


def test_ninguna_cae_a_fuera():
    """Sin protección (NINGUNA) las ONTs dependientes caen a FUERA (Req 6.4)."""
    state = SimState.crear(estadio_activo="azteca")
    estadio = state.estadios[state.estadio_activo]

    arbol = next(
        a
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for a in puerto.arboles
        if a.proteccion == Proteccion.NINGUNA and a.onts
    )
    inyectar_falla(state, "corte_troncal", arbol.id)

    assert all(o.estado == Estado.FUERA for o in arbol.onts)
