"""Pruebas del bucle en vivo y los controles (tarea 5.8, app/callbacks.py).

Verifican la **lógica de control** que el callback maestro cablea al estado
compartido, sin levantar el servidor Dash:

* Play/Pausa alterna ``ESTADO.corriendo`` y ``avanzar`` avanza el reloj solo
  cuando está corriendo (Req 16.1, design §4).
* La velocidad se restringe al conjunto {1x, 5x, 15x} de forma continua, incluso
  ante valores arbitrarios (Req 16.1/16.2).
* El escenario conmuta entre "diseno" y "estres" (design §9.4).
* El reset reconstruye el estado compartido: reloj a T-180 (tras resiembra) y
  sin fallas/alarmas de la sesión previa.
* El contexto de la barra superior es una **lectura** (no recalcula KPIs,
  Req 2.3) y expone reloj/estado/velocidad/escenario/alarmas.

También se comprueba, importando ``app.main``, que todos los callbacks se
registran sin salidas duplicadas (Req 1.4) usando la validación de Dash.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def live():
    """Recarga ``app.live`` en cada prueba para partir de un estado limpio.

    El módulo mantiene una instancia única (``ESTADO``) a nivel de módulo; al
    recargarlo se re-siembra desde la topología, aislando las pruebas entre sí.
    """
    import app.live as live_mod

    importlib.reload(live_mod)
    return live_mod


# ---------------------------------------------------------------------------
# Play / Pausa y avance del motor (Req 16.1, design §4)
# ---------------------------------------------------------------------------
def test_play_pausa_alterna_corriendo(live):
    """El toggle de play/pausa invierte ``corriendo`` (arranca pausado)."""
    assert live.ESTADO.corriendo is False
    assert live.alternar_corriendo() is True
    assert live.ESTADO.corriendo is True
    assert live.alternar_corriendo() is False
    assert live.ESTADO.corriendo is False


def test_avanzar_solo_corre_con_play(live):
    """``avanzar`` mueve el reloj solo si está corriendo (design §4)."""
    t0 = live.ESTADO.t_min
    # Pausado: no avanza.
    live.avanzar()
    assert live.ESTADO.t_min == t0
    # Play + avanzar: el reloj avanza en `velocidad` minutos.
    live.alternar_corriendo()
    live.set_velocidad(5.0)
    live.avanzar()
    assert live.ESTADO.t_min == pytest.approx(t0 + 5.0)


def test_avanzar_monotono_no_supera_final(live):
    """El reloj es monótono y no supera T+135 al avanzar (Req 16.3)."""
    live.alternar_corriendo()
    live.set_velocidad(15.0)
    previo = live.ESTADO.t_min
    for _ in range(100):
        live.avanzar()
        assert live.ESTADO.t_min >= previo
        assert live.ESTADO.t_min <= 135.0
        previo = live.ESTADO.t_min
    assert live.ESTADO.t_min == 135.0


# ---------------------------------------------------------------------------
# Velocidad restringida a {1x, 5x, 15x} de forma continua (Req 16.1/16.2)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (1.0, 1.0),
        (5.0, 5.0),
        (15.0, 15.0),
        (3.0, 1.0),     # equidistante 1<->5: desempata al menor (1x)
        (2.0, 1.0),     # 2 está más cerca de 1
        (9.0, 5.0),     # 9 está más cerca de 5 que de 15
        (10.0, 5.0),    # equidistante 5<->15: desempata al menor (5x)
        (12.0, 15.0),   # 12 está más cerca de 15
        (100.0, 15.0),  # se topa al máximo permitido
        (0.0, 1.0),     # se topa al mínimo permitido
    ],
)
def test_set_velocidad_snap_al_conjunto(live, entrada, esperado):
    """Cualquier velocidad se normaliza al conjunto {1, 5, 15} (Req 16.2)."""
    efectiva = live.set_velocidad(entrada)
    assert efectiva == esperado
    assert live.ESTADO.velocidad == esperado
    assert live.ESTADO.velocidad in (1.0, 5.0, 15.0)


def test_botones_velocidad_fijan_valor_exacto(live):
    """Los tres botones de velocidad fijan exactamente 1x/5x/15x (Req 16.1)."""
    assert live.set_velocidad(1.0) == 1.0
    assert live.set_velocidad(5.0) == 5.0
    assert live.set_velocidad(15.0) == 15.0


# ---------------------------------------------------------------------------
# Escenario diseño / estrés (design §9.4)
# ---------------------------------------------------------------------------
def test_alternar_escenario(live):
    """El toggle de escenario conmuta diseno <-> estres."""
    assert live.ESTADO.escenario == "diseno"
    assert live.alternar_escenario() == "estres"
    assert live.ESTADO.escenario == "estres"
    assert live.alternar_escenario() == "diseno"


def test_set_escenario_ignora_valores_invalidos(live):
    """``set_escenario`` solo acepta valores soportados."""
    live.set_escenario("estres")
    assert live.ESTADO.escenario == "estres"
    live.set_escenario("valor_invalido")
    assert live.ESTADO.escenario == "estres"


# ---------------------------------------------------------------------------
# Reset reconstruye el estado compartido (design §4)
# ---------------------------------------------------------------------------
def test_reset_reconstruye_estado(live):
    """El reset vuelve el reloj a T-180 y descarta fallas/alarmas de sesión."""
    # Ensuciar el estado: correr un rato e inyectar una falla.
    live.alternar_corriendo()
    live.set_velocidad(15.0)
    for _ in range(10):
        live.avanzar()
    assert live.ESTADO.t_min > -180.0

    from sim import faults

    objetivo = live.ESTADO.estadios[live.ESTADO.estadio_activo].olt.tarjetas[0].id
    faults.inyectar_falla(live.ESTADO, "tarjeta_caida", objetivo)
    assert getattr(live.ESTADO, "fallas_activas", [])

    live.reset()

    # Tras el reset (con resiembra a T-180 + _TICKS_SEMILLA a 1x): reloj
    # reiniciado por debajo de 0 y sin fallas/alarmas de la sesión previa.
    assert live.ESTADO.t_min < 0.0
    assert live.ESTADO.corriendo is False
    assert live.ESTADO.velocidad == 1.0
    assert not getattr(live.ESTADO, "fallas_activas", [])
    assert live.ESTADO.alarmas == []


# ---------------------------------------------------------------------------
# Contexto de la barra superior: lectura de solo lectura (Req 2.3)
# ---------------------------------------------------------------------------
def test_contexto_topbar_es_lectura(live):
    """``contexto_topbar`` expone el estado sin avanzar el motor (Req 2.3)."""
    t0 = live.ESTADO.t_min
    ctx = live.contexto_topbar()
    # No avanzó el reloj (solo lectura).
    assert live.ESTADO.t_min == t0
    assert set(ctx) >= {
        "t_min",
        "corriendo",
        "velocidad",
        "escenario",
        "alarmas_activas",
        "alarmas_criticas",
    }
    assert ctx["alarmas_activas"] >= 0
    assert ctx["alarmas_criticas"] >= 0


# ---------------------------------------------------------------------------
# Integración: la app registra todos los callbacks sin salidas duplicadas
# ---------------------------------------------------------------------------
def test_app_registra_callbacks_sin_salidas_duplicadas():
    """``app.main`` levanta con la validación de Dash y sin Outputs duplicados."""
    import app.main as m

    m.app._setup_server()  # ejecuta la validación de layout/callbacks de Dash

    salidas: list[str] = []
    for spec in m.app.callback_map.values():
        salida = spec["output"]
        salida_lista = salida if isinstance(salida, list) else [salida]
        salidas.extend(str(o) for o in salida_lista)

    assert len(salidas) == len(set(salidas)), "Hay callbacks con Outputs duplicados"
    # El callback maestro del bucle en vivo debe estar registrado.
    assert "..top-bar-indicadores.children...ctrl-estado.children.." in m.app.callback_map
