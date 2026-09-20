"""Bucle en vivo y controles del tablero (design §4, §6.1, §9.1, §9.4).

Este módulo cablea el **único** ``dcc.Interval`` del tablero (``app.live``) al
``tick`` del motor y conecta los controles del operador (play/pausa, velocidad
1x/5x/15x, escenario diseño/estrés y reset) al estado compartido. Es la
frontera de interacción entre la UI y el Motor de Simulación.

Diseño del cableado (design §4, diagrama de secuencia):

* Existe **un solo** ``dcc.Interval`` (vive en ``app.layout.serve_layout`` vía
  ``app.live.intervalo``); aquí **no** se crea otro (Req 16.1).
* Un **único callback maestro** es el dueño del contenedor de indicadores de la
  barra superior (:data:`app.layout.ID_TOPBAR_INDICADORES`). Se dispara con el
  intervalo y con **todos** los controles, identifica el disparador con
  ``dash.ctx`` y:

  1. En un tick del intervalo → llama :func:`app.live.avanzar` (avanza el motor
     un ``tick`` solo si está corriendo, design §4).
  2. En un control → aplica la mutación de estado correspondiente
     (play/pausa, velocidad, escenario, reset, selector de estadio).
  3. Siempre → reconstruye los indicadores de la barra superior y el resumen de
     estado de los controles **leyendo** el estado ya calculado.

  Que un solo callback sea el dueño de esas salidas evita **salidas duplicadas**
  (Dash rechaza dos callbacks que escriban el mismo ``Output``) y garantiza el
  orden "mutar → renderizar" en el mismo ciclo.

Regla de solo lectura de KPIs (Req 2.3): este módulo **no calcula ningún KPI**.
El avance del motor (``tick``) recalcula tráfico/fallas/alarmas/KPIs dentro de
``sim/`` (Req 2.2); aquí solo se **leen** los valores precalculados a través de
``app.live`` para poblar la barra superior. Las gráficas de KPI las refrescan
las vistas (Resumen/Simulación) desde el mismo intervalo, sin recalcular.

Regla de integración visual (Req 9.3): este módulo no declara colores; la
presentación vive en ``app.layout`` / ``app.views`` (que usan ``app.theme``).
"""

from __future__ import annotations

from dash import Input, Output, ctx

from app import live
from app.layout import ID_TOPBAR_INDICADORES, indicadores_topbar
from app.views.simulacion import (
    ID_CTRL_ESCENARIO,
    ID_CTRL_ESTADO,
    ID_CTRL_PLAY,
    ID_CTRL_RESET,
    ID_CTRL_VEL_1,
    ID_CTRL_VEL_5,
    ID_CTRL_VEL_15,
    estado_controles,
)

# Id del selector de estadio de la barra superior (declarado en app.layout).
ID_SELECTOR_ESTADIO = "selector-estadio"


def _aplicar_control(disparador: str, estadio_seleccionado) -> None:
    """Aplica al estado compartido la acción del control que disparó el callback.

    ``disparador`` es el ``ctx.triggered_id`` (id del componente). Cada rama
    delega en la operación de control de ``app.live`` correspondiente, que muta
    la instancia única del estado. No calcula KPIs (Req 2.3).
    """
    if disparador == live.INTERVALO_ID:
        # Tick del reloj: avanza el motor solo si está corriendo (design §4).
        live.avanzar()
    elif disparador == ID_CTRL_PLAY:
        live.alternar_corriendo()          # play/pausa (Req 16.1)
    elif disparador == ID_CTRL_VEL_1:
        live.set_velocidad(1.0)            # velocidad 1x (Req 16.1/16.2)
    elif disparador == ID_CTRL_VEL_5:
        live.set_velocidad(5.0)            # velocidad 5x
    elif disparador == ID_CTRL_VEL_15:
        live.set_velocidad(15.0)           # velocidad 15x
    elif disparador == ID_CTRL_ESCENARIO:
        live.alternar_escenario()          # diseño <-> estrés
    elif disparador == ID_CTRL_RESET:
        live.reset()                       # reinicia el estado compartido
    elif disparador == ID_SELECTOR_ESTADIO:
        live.set_estadio(estadio_seleccionado)


def register_callbacks(app) -> None:
    """Registra el callback maestro del bucle en vivo y los controles.

    Se invoca desde ``app.main`` tras crear la app. Registra un único callback
    dueño de los indicadores de la barra superior y del resumen de estado de los
    controles, disparado por el intervalo y por todos los controles.
    """

    @app.callback(
        Output(ID_TOPBAR_INDICADORES, "children"),
        Output(ID_CTRL_ESTADO, "children"),
        Input(live.INTERVALO_ID, "n_intervals"),
        Input(ID_CTRL_PLAY, "n_clicks"),
        Input(ID_CTRL_VEL_1, "n_clicks"),
        Input(ID_CTRL_VEL_5, "n_clicks"),
        Input(ID_CTRL_VEL_15, "n_clicks"),
        Input(ID_CTRL_ESCENARIO, "n_clicks"),
        Input(ID_CTRL_RESET, "n_clicks"),
        Input(ID_SELECTOR_ESTADIO, "value"),
    )
    def _bucle_y_controles(
        _n_intervals,
        _play,
        _v1,
        _v5,
        _v15,
        _escenario,
        _reset,
        estadio_seleccionado,
    ):
        """Callback maestro: avanza el motor / aplica controles y refresca la UI.

        Identifica el disparador con ``dash.ctx``, aplica la acción sobre el
        estado compartido (avance del motor en el tick, o la mutación del
        control), y devuelve los indicadores de la barra superior y el resumen
        de estado de los controles **leyendo** el estado ya calculado (Req 2.3).
        """
        disparador = ctx.triggered_id
        # En el primer render (sin disparador) no se muta nada: solo se refleja
        # el estado de arranque sembrado por ``app.live`` (Req 15.1).
        if disparador is not None:
            _aplicar_control(disparador, estadio_seleccionado)

        contexto = live.contexto_topbar()
        estado_ctrl = estado_controles(
            contexto["corriendo"], contexto["velocidad"], contexto["escenario"]
        )
        return indicadores_topbar(contexto), estado_ctrl
