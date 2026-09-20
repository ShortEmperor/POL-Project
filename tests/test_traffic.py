"""Pruebas de tráfico y determinismo (sim/traffic.py + sim/engine.py, tarea 5.10).

Cubre la tarea 5.10 (design §6.2 tráfico, §6.3 ruido/semilla, Property 10) y los
Requirements 7.4, 7.5, 17.3 y 17.4:

* Funciones puras del modelo de tráfico (§6.2): curva de llegada, factores de
  actividad por fase, evento de gol, fórmulas de bw y utilización de puerto.
* **Utilización pico por escenario** (Req 7.4/7.5): "diseno" dentro del 10% del
  dimensionamiento (~50.6%) en el pico teórico (medio tiempo, t≈50) y "estres"
  en 55–65%.
* **Property 10 — Determinismo por semilla** (Req 17.3/17.4, design §14 y
  "Estrategia de pruebas"): con ``semilla=42`` y la misma secuencia de acciones,
  dos corridas independientes del motor real (``engine.tick``) producen series
  **idénticas**, comparadas por **hash**. Además se comprueba que una semilla
  distinta produce una serie distinta (la semilla realmente importa) y que el
  ruido por tick da textura (no líneas planas).

El paquete ``sim/`` no importa Dash: estas pruebas corren con pytest sin levantar
la Capa de Presentación (design §3.1, Req 17.1).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

import sim.traffic as tr
from sim.engine import tick
from sim.state import SimState
from sim.topology import cargar_topologia


# ---------------------------------------------------------------------------
# Funciones puras (design §6.2)
# ---------------------------------------------------------------------------
def test_f_llegada_tramos():
    assert tr.f_llegada(-180) == pytest.approx(0.05)
    assert tr.f_llegada(-120) == pytest.approx(0.05)
    assert tr.f_llegada(-30) == pytest.approx(0.90)
    assert tr.f_llegada(0) == pytest.approx(1.00)
    assert tr.f_llegada(45) == pytest.approx(1.00)
    assert tr.f_llegada(90) == pytest.approx(1.00)
    assert tr.f_llegada(112.5) == pytest.approx(0.5)
    assert tr.f_llegada(135) == pytest.approx(0.0)
    # Rango acotado a [0, 1].
    for t in range(-200, 200, 5):
        assert 0.0 <= tr.f_llegada(float(t)) <= 1.0


def test_factor_actividad_por_fase():
    assert tr.factor_actividad(-10) == tr.FACTOR_INGRESO
    assert tr.factor_actividad(10) == tr.FACTOR_PRIMER_TIEMPO
    assert tr.factor_actividad(50) == tr.FACTOR_MEDIO_TIEMPO
    assert tr.factor_actividad(70) == tr.FACTOR_SEGUNDO_TIEMPO
    assert tr.factor_actividad(120) == tr.FACTOR_SALIDA


def test_f_gol_pico_y_decaimiento():
    # Antes del gol no hay efecto.
    assert tr.f_gol(9.0, 10.0) == pytest.approx(1.0)
    # En el instante del gol el pico es 3.0 (1 + 2).
    assert tr.f_gol(10.0, 10.0) == pytest.approx(3.0)
    # Decae monótonamente hacia 1.0.
    assert tr.f_gol(11.5, 10.0) < tr.f_gol(10.5, 10.0)
    assert tr.f_gol(100.0, 10.0) == pytest.approx(1.0, abs=1e-6)


def test_utilizacion_y_guarda_capacidad():
    assert tr.utilizacion(4250.0, 8500.0) == pytest.approx(0.5)
    # Guarda de capacidad no positiva → 0.0 (design §6.2).
    assert tr.utilizacion(100.0, 0.0) == 0.0
    assert tr.utilizacion(100.0, -5.0) == 0.0


# ---------------------------------------------------------------------------
# Escenarios: utilización pico en objetivo (Req 7.4 / 7.5)
# ---------------------------------------------------------------------------
def _estado_pico(escenario: str) -> SimpleNamespace:
    """Estado duck-typed en el pico teórico (medio tiempo, t=50)."""
    est = cargar_topologia("azteca")
    return SimpleNamespace(
        t_min=50.0,
        escenario=escenario,
        estadio_activo="azteca",
        estadios={"azteca": est},
        semilla=42,
        goles=[],
    )


def _util_pico(state: SimpleNamespace) -> float:
    tr.actualizar(state)
    activos = [u for u in state.util_por_puerto.values() if u > 0]
    return max(activos)


def test_escenario_diseno_pico_dentro_del_10pct():
    """Escenario 'diseno' → utilización pico ~50.6% (±10%) (Req 7.4).

    Se evalúa en el **pico teórico** de la curva de demanda (medio tiempo,
    t=50, ``f_llegada=1.0`` y ``factor_actividad`` máximo). El objetivo del
    dimensionamiento es 0.506; la tolerancia es ±10% (Req 7.4), es decir el
    intervalo [0.4554, 0.5566].
    """
    peak = _util_pico(_estado_pico("diseno"))
    assert 0.506 * 0.90 <= peak <= 0.506 * 1.10


def test_escenario_estres_pico_en_55_65():
    """Escenario 'estres' → utilización pico en 55–65% (Req 7.5).

    En el pico teórico (medio tiempo), la utilización pico cae en el intervalo
    60% ± 5%; un valor como 63% es conforme (Req 7.5).
    """
    peak = _util_pico(_estado_pico("estres"))
    assert 0.55 <= peak <= 0.65


def test_pico_ocurre_en_medio_tiempo():
    """El pico de utilización 'diseno' se da en medio tiempo, no antes/después.

    Refuerza que la calibración apunta al pico teórico correcto (design §6.2):
    la utilización en medio tiempo (t≈50) domina a la de otras fases (ingreso,
    primer/segundo tiempo, salida) para el mismo escenario y semilla.
    """
    def util_en(t: float) -> float:
        st = _estado_pico("diseno")
        st.t_min = t
        return _util_pico(st)

    pico_medio = util_en(50.0)
    for t_otra in (-10.0, 20.0, 70.0, 120.0):
        assert util_en(t_otra) <= pico_medio + 1e-9


def test_actualizar_agrega_por_puerto_y_estadio():
    """actualizar() expone agregados por puerto y por estadio (Req 7.1/7.2)."""
    state = _estado_pico("diseno")
    tr.actualizar(state)
    assert state.throughput_bajada_mbps > 0
    assert state.throughput_subida_mbps > 0
    assert len(state.throughput_por_puerto) > 0
    assert set(state.throughput_por_servicio) == set(tr.BW_BASE_BAJADA_MBPS)


# ---------------------------------------------------------------------------
# Property 10 — Determinismo por semilla (Req 17.3 / 17.4, design §14)
# ---------------------------------------------------------------------------
# Estas pruebas corren el motor real (``engine.tick``) sobre un ``SimState``
# construido con la topología real (``SimState.crear``) y comparan las series
# resultantes **por hash**, tal como pide la estrategia de pruebas del diseño
# ("fijar semilla=42 y comparar hashes de series").

# Número de ticks de una corrida de comparación (parte del partido a 5x cubre
# ingreso, primer tiempo, medio tiempo y parte del segundo: suficiente textura).
_TICKS_CORRIDA = 60
_VELOCIDAD_CORRIDA = 5.0
# Secuencia fija de goles (acciones) aplicada en ambas corridas para verificar
# determinismo *con* eventos, no solo en el camino base.
_GOLES_FIJOS = (12.0, 55.0)


def _serie_kpis(semilla: int, escenario: str = "diseno") -> list[tuple]:
    """Corre el motor real y devuelve una serie de KPIs por tick.

    Construye un estado fresco con ``SimState.crear(semilla=...)``, inyecta una
    secuencia fija de goles y ejecuta ``engine.tick`` ``_TICKS_CORRIDA`` veces.
    Por cada tick captura una tupla de agregados/KPIs relevantes (redondeados a
    6 decimales para una comparación estable frente a ruido de coma flotante):
    throughput de bajada/subida del estadio y K1 (utilización pico) / K3
    (throughput total). La misma semilla y secuencia deben reproducir la serie.
    """
    state = SimState.crear(semilla=semilla, escenario=escenario)
    state.corriendo = True
    state.velocidad = _VELOCIDAD_CORRIDA
    state.goles = list(_GOLES_FIJOS)

    serie: list[tuple] = []
    for _ in range(_TICKS_CORRIDA):
        tick(state)
        k1 = max(state.util_por_puerto.values()) if state.util_por_puerto else 0.0
        serie.append(
            (
                round(state.t_min, 6),
                round(state.throughput_bajada_mbps, 6),
                round(state.throughput_subida_mbps, 6),
                round(k1, 6),
            )
        )
    return serie


def _hash_serie(serie: list[tuple]) -> str:
    """Hash SHA-256 estable de una serie de tuplas numéricas redondeadas."""
    payload = repr(serie).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_property10_determinismo_por_semilla_hash():
    """Property 10: misma semilla + misma secuencia ⇒ series IDÉNTICAS (por hash).

    Dos corridas independientes del motor real desde ``SimState.crear(42)`` con
    la misma secuencia de goles producen exactamente la misma serie de KPIs; se
    comparan por su hash SHA-256 (Req 17.3, design "regresión de determinismo").

    **Validates: Requirements 17.3, 17.4**
    """
    serie_a = _serie_kpis(42)
    serie_b = _serie_kpis(42)

    # Igualdad exacta de las series y de sus hashes (no solo aproximada).
    assert serie_a == serie_b
    assert _hash_serie(serie_a) == _hash_serie(serie_b)


def test_property10_semilla_distinta_serie_distinta():
    """La semilla realmente importa: otra semilla ⇒ serie distinta (sanity).

    Verifica que el determinismo no es un artefacto de un modelo insensible a la
    semilla: con ``semilla=7`` la serie (y su hash) difieren de la de ``42``. El
    ruido gaussiano del 4% (§6.3) sembrado por ``state.semilla`` garantiza esta
    sensibilidad.

    **Validates: Requirements 17.3, 17.4**
    """
    serie_42 = _serie_kpis(42)
    serie_7 = _serie_kpis(7)

    assert serie_42 != serie_7
    assert _hash_serie(serie_42) != _hash_serie(serie_7)


def test_property10_determinismo_semilla_por_defecto():
    """La semilla por defecto (42) es determinista entre corridas equivalentes.

    Corre dos veces sin especificar semilla explícita (usa ``SEMILLA_POR_DEFECTO
    == 42``, Req 17.3) y comprueba igualdad por hash, cubriendo el caso "pytest
    con la semilla fija por defecto".

    **Validates: Requirements 17.3**
    """
    def corrida_por_defecto() -> list[tuple]:
        state = SimState.crear()  # semilla por defecto = 42
        state.corriendo = True
        state.velocidad = _VELOCIDAD_CORRIDA
        state.goles = list(_GOLES_FIJOS)
        out: list[tuple] = []
        for _ in range(_TICKS_CORRIDA):
            tick(state)
            out.append(round(state.throughput_bajada_mbps, 6))
        return out

    assert _hash_serie(corrida_por_defecto()) == _hash_serie(corrida_por_defecto())


def test_ruido_da_textura_no_linea_plana():
    """El ruido del 4% (§6.3) produce textura: la serie no es constante.

    Sin ruido, dos ticks consecutivos en la misma fase darían el mismo
    throughput. El ruido gaussiano sembrado por tick (semilla, t_min) rompe esa
    planitud, produciendo telemetría realista. Se comprueba que hay al menos dos
    valores distintos de throughput en una corrida corta a velocidad 1x (varios
    ticks dentro de la misma fase).
    """
    state = SimState.crear(semilla=42)
    state.corriendo = True
    state.velocidad = 1.0
    state.t_min = 46.0  # arranca en medio tiempo; varios ticks en la misma fase.

    valores = []
    for _ in range(8):
        tick(state)
        valores.append(state.throughput_bajada_mbps)

    assert len(set(round(v, 6) for v in valores)) > 1
