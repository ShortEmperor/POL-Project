"""Vista 3: Simulación (design §9.4, Requirements 2, 9 y 15).

Compone tres regiones sobre la retícula de 12 columnas:

* **Controles** (play/pausa, velocidad, escenario, reset) — reales; la
  interacción de reloj/escenario se cablea en ``app/callbacks.py`` (tarea 5.8).
  El **botón de gol** es real y se cablea aquí (tarea 7.8).
* **Cuadrícula 2x2 de gráficas en vivo** (design §9.4): throughput total
  (Gbps), utilización de puerto pico (%), ONTs en línea (conteo) y potencia
  óptica media (dBm). Las cuatro comparten el **mismo eje de tiempo** (índice de
  tick) para comparación visual (design §9.6). Cada eje lleva su **unidad en el
  título** (Gbps, %, conteo, dBm) — Req 9.6.
* **Inyección de fallas y tabla de alarmas** (tarea 7.8, Req 4.8): botones de
  inyección de fallas (uno por tipo del catálogo, design §6.4) y botón de gol,
  todos habilitados con id; una **tabla de alarmas activas** y un **histórico de
  alarmas de la sesión**. El cableado (inyección + refresco de la tabla) vive en
  un callback de este módulo dueño del contenedor de alarmas, disparado por el
  intervalo y por los botones. La severidad/estado usa color + ícono + texto (el
  color nunca es el único canal, design §9.6).

Integración en vivo (design §4): las cuatro gráficas se refrescan desde el
único ``dcc.Interval`` del tablero (``app.live``) mediante un callback
registrado en este módulo con ``@callback``, sin crear un segundo intervalo.
Las series se leen del historial ya empujado por ``sim.kpis`` (Req 2.2); ningún
KPI se calcula aquí. La vista carga poblada desde el primer render porque
``app.live`` siembra el historial al arrancar (Req 15.1/15.2).

Regla de integración visual (Req 9.3): este módulo **no declara ningún color
literal**; todo estilo proviene de :data:`app.theme.TOKENS`. El orden de
servicios de cualquier leyenda es fijo (:data:`app.components.ORDEN_SERVICIOS`,
Req 9.5).
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, callback, ctx, dcc, html

from app import live
from app.components import ORDEN_SERVICIOS, panel, placeholder_texto
from app.theme import TOKENS

# ---------------------------------------------------------------------------
# Definición de las cuatro gráficas de la cuadrícula 2x2 (design §9.4).
# (código de KPI en el historial, título, unidad para el eje Y, token de color)
# El orden es fijo (design §9.6).
# ---------------------------------------------------------------------------
_GRAFICAS = [
    ("K3", "Throughput total", "Gbps", "acento"),
    ("K1", "Utilización de puerto pico", "%", "util_alto"),
    ("K4", "ONTs en línea", "conteo", "en_linea"),
    ("K6", "Potencia óptica media", "dBm", "aviso"),
]

# Ids de los cuatro dcc.Graph que refresca el callback del intervalo.
_ID_GRAFICA = {codigo: f"sim-grafica-{codigo}" for codigo, *_ in _GRAFICAS}

# Título del eje X compartido por las cuatro gráficas (design §9.6).
_EJE_X_TITULO = "Tiempo (ticks de simulación)"

# ---------------------------------------------------------------------------
# Inyección de fallas y evento de gol (design §9.4). Ahora reales (tarea 7.8):
# cada botón lleva un id y el cableado (mutación del estado) vive en el callback
# de este módulo, que delega en ``app.live.inyectar_falla`` / ``app.live.gol``.
# ---------------------------------------------------------------------------
# Id de cada botón de inyección de falla, indexado por tipo (app.live.TIPOS_FALLA_UI).
ID_FALLA = {tipo: f"sim-falla-{tipo}" for tipo in live.TIPOS_FALLA_UI}
# Id del botón de gol (evento de pico transitorio de actividad, design §6.2).
ID_GOL = "sim-gol"
# Contenedor de alarmas (tabla de activas + histórico de sesión) que refresca el
# callback del intervalo y tras cada inyección (Req 4.8).
ID_ALARMAS = "sim-alarmas"

# ---------------------------------------------------------------------------
# Presentación de severidad de alarma (design §8.1, §9.6).
# El color NUNCA es el único canal: cada severidad lleva ícono + texto además
# del token de color (accesibilidad para daltonismo, design §9.6). El token de
# color proviene de app.theme.TOKENS (Req 9.3).
# (token_color, ícono, etiqueta)
_SEVERIDAD_PRESENTACION: dict[str, tuple[str, str, str]] = {
    "critica": ("critica", "✕", "Crítica"),
    "mayor": ("mayor", "▲", "Mayor"),
    "menor": ("menor", "●", "Menor"),
    "aviso": ("aviso", "ⓘ", "Aviso"),
}

# Presentación del estado del ciclo de vida de la alarma (design §8.4) para el
# histórico: ícono + texto (el color acompaña, nunca es único canal, §9.6).
_ESTADO_ALARMA_PRESENTACION: dict[str, tuple[str, str, str]] = {
    "nueva": ("aviso", "◆", "Nueva"),
    "activa": ("mayor", "▶", "Activa"),
    "reconocida": ("acento", "✓", "Reconocida"),
    "cerrada": ("texto_muted", "○", "Cerrada"),
}


# ---------------------------------------------------------------------------
# Construcción de figuras Plotly con el tema oscuro (design §9.6)
# ---------------------------------------------------------------------------
def _figura(codigo: str, titulo: str, unidad: str, token_color: str, serie: list[float]) -> go.Figure:
    """Construye la figura de una serie con eje de tiempo compartido y unidad.

    * El eje X es el índice de tick (tiempo de simulación), compartido por las
      cuatro gráficas (design §9.6).
    * El eje Y lleva la unidad en su título (Req 9.6).
    * Si la serie está vacía (antes del primer tick) se dibuja una figura con
      una anotación de "sin datos aún" en vez de un panel vacío (Req 15.3).
    """
    color = TOKENS[token_color]
    fig = go.Figure()

    if serie:
        x = list(range(len(serie)))
        fig.add_trace(
            go.Scatter(
                x=x,
                y=serie,
                mode="lines",
                line={"color": color, "width": 2},
                fill="tozeroy" if unidad in ("Gbps", "%", "conteo") else None,
                fillcolor=color if unidad in ("Gbps", "%", "conteo") else None,
                opacity=0.9,
                name=titulo,
                hovertemplate=f"tick %{{x}}<br>%{{y:.2f}} {unidad}<extra></extra>",
            )
        )
    else:
        fig.add_annotation(
            text="Sin datos aún — pulse play",
            showarrow=False,
            font={"color": TOKENS["texto_muted"], "size": 12},
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
        )

    fig.update_layout(
        title={
            "text": f"{titulo} ({unidad})",
            "font": {"color": TOKENS["texto"], "size": 14},
            "x": 0.02,
        },
        paper_bgcolor=TOKENS["bg_panel"],
        plot_bgcolor=TOKENS["bg_elevated"],
        font={"color": TOKENS["texto"], "family": TOKENS["fuente"]},
        margin={"l": 56, "r": 16, "t": 40, "b": 40},
        height=260,
        showlegend=False,
    )
    # Eje X compartido: mismo título/rango conceptual en las cuatro gráficas.
    fig.update_xaxes(
        title={"text": _EJE_X_TITULO, "font": {"size": 11, "color": TOKENS["texto_muted"]}},
        gridcolor=TOKENS["borde"],
        zerolinecolor=TOKENS["borde"],
        color=TOKENS["texto_muted"],
    )
    # Eje Y: unidad SIEMPRE presente en el título (Req 9.6).
    fig.update_yaxes(
        title={"text": unidad, "font": {"size": 11, "color": TOKENS["texto_muted"]}},
        gridcolor=TOKENS["borde"],
        zerolinecolor=TOKENS["borde"],
        color=TOKENS["texto_muted"],
    )
    return fig


def _grafica(codigo: str, titulo: str, unidad: str, token_color: str, serie: list[float]) -> dcc.Graph:
    return dcc.Graph(
        id=_ID_GRAFICA[codigo],
        figure=_figura(codigo, titulo, unidad, token_color, serie),
        config={"displayModeBar": False},
        style={"backgroundColor": TOKENS["bg_panel"]},
    )


def _cuadricula(historial: dict) -> dbc.Row:
    """Cuadrícula 2x2 de las cuatro gráficas con eje de tiempo compartido."""
    cols = [
        dbc.Col(
            _grafica(codigo, titulo, unidad, token, historial.get(codigo, [])),
            width=6,
        )
        for codigo, titulo, unidad, token in _GRAFICAS
    ]
    return dbc.Row(cols, className="g-3")


# ---------------------------------------------------------------------------
# Controles (interacción cableada en app/callbacks.py, tarea 5.8) y
# fallas (maqueta; tarea 7.8).
# ---------------------------------------------------------------------------
# Ids de los controles reales. El cableado (mutación del estado en vivo) vive en
# ``app/callbacks.py``: aquí solo se declaran los componentes con su id, sin
# lógica de negocio (separación de capas, design §3.1). Se exportan para que el
# módulo de callbacks los referencie sin duplicar literales.
ID_CTRL_PLAY = "ctrl-play"
ID_CTRL_VEL_1 = "ctrl-vel-1"
ID_CTRL_VEL_5 = "ctrl-vel-5"
ID_CTRL_VEL_15 = "ctrl-vel-15"
ID_CTRL_ESCENARIO = "ctrl-escenario"
ID_CTRL_RESET = "ctrl-reset"
# Contenedor de estado de los controles (lo refresca el callback en vivo con la
# lectura del estado del motor: corriendo/pausado, velocidad y escenario).
ID_CTRL_ESTADO = "ctrl-estado"

_ESTILO_BOTON_BASE = {
    "border": f"1px solid {TOKENS['borde']}",
    "borderRadius": TOKENS["radio"],
    "padding": f"{TOKENS['espacio_sm']} {TOKENS['espacio_md']}",
    "fontFamily": TOKENS["fuente"],
    "fontSize": "13px",
}


def _boton_control(texto: str, id_: str, color: str) -> html.Button:
    """Botón de control real (habilitado) con id para el cableado de la tarea 5.8."""
    return html.Button(
        texto,
        id=id_,
        n_clicks=0,
        style={
            **_ESTILO_BOTON_BASE,
            "backgroundColor": TOKENS["bg_elevated"],
            "color": color,
            "cursor": "pointer",
        },
    )


def _boton_falla(tipo: str) -> html.Button:
    """Botón de inyección de falla real (habilitado) con id para el cableado (tarea 7.8).

    El texto es la etiqueta legible del tipo (``app.live.ETIQUETA_FALLA``) y el
    id (``ID_FALLA[tipo]``) lo referencia el callback de este módulo, que delega
    la inyección en ``app.live.inyectar_falla`` sobre un objetivo por defecto.
    """
    return html.Button(
        f"⚡ {live.ETIQUETA_FALLA.get(tipo, tipo)}",
        id=ID_FALLA[tipo],
        n_clicks=0,
        style={
            **_ESTILO_BOTON_BASE,
            "backgroundColor": TOKENS["bg_elevated"],
            "color": TOKENS["mayor"],
            "cursor": "pointer",
            "textAlign": "left",
        },
    )


def estado_controles(corriendo: bool, velocidad: float, escenario: str) -> html.Div:
    """Resumen legible del estado de los controles (lo refresca el callback en vivo).

    Muestra corriendo/pausado, la velocidad activa y el escenario, con color e
    ícono (el color nunca es el único canal, design §9.6). Es una lectura del
    estado del motor; no calcula nada.
    """
    return html.Div(
        [
            html.Span(
                ("▶ Corriendo" if corriendo else "⏸ Pausado"),
                style={
                    "color": TOKENS["en_linea"] if corriendo else TOKENS["texto_muted"],
                    "fontWeight": 600,
                    "marginRight": TOKENS["espacio_md"],
                },
            ),
            html.Span(
                f"Velocidad {velocidad:.0f}x",
                style={"color": TOKENS["acento"], "marginRight": TOKENS["espacio_md"]},
            ),
            html.Span(
                f"Escenario {str(escenario).capitalize()}",
                style={"color": TOKENS["acento"]},
            ),
        ],
        style={"fontSize": "13px", "marginTop": TOKENS["espacio_sm"]},
    )


def _controles() -> html.Div:
    datos = live.snapshot()
    return html.Div(
        [
            html.Div(
                [
                    _boton_control("▶ Play / ⏸ Pausa", ID_CTRL_PLAY, TOKENS["en_linea"]),
                    _boton_control("1x", ID_CTRL_VEL_1, TOKENS["acento"]),
                    _boton_control("5x", ID_CTRL_VEL_5, TOKENS["acento"]),
                    _boton_control("15x", ID_CTRL_VEL_15, TOKENS["acento"]),
                    _boton_control("Escenario diseño / estrés", ID_CTRL_ESCENARIO, TOKENS["acento"]),
                    _boton_control("⟲ Reset", ID_CTRL_RESET, TOKENS["texto_muted"]),
                    _boton_control("⚽ Gol", ID_GOL, TOKENS["menor"]),
                ],
                style={"display": "flex", "gap": TOKENS["espacio_sm"], "flexWrap": "wrap"},
            ),
            html.Div(
                estado_controles(
                    datos["corriendo"],
                    float(datos.get("velocidad", 1.0)),
                    datos.get("escenario", "diseno"),
                ),
                id=ID_CTRL_ESTADO,
            ),
        ]
    )


def _leyenda_servicios() -> html.Div:
    """Nota de orden de servicios fijo para las leyendas por servicio (Req 9.5).

    Aún no hay gráficas por servicio en la cuadrícula (las cuatro son
    agregadas), pero el orden queda fijado aquí para cualquier leyenda de
    servicios que se añada, cumpliendo el orden fijo de servicios (Req 9.5).
    """
    items = [
        html.Span(
            s,
            style={
                "fontSize": "10px",
                "color": TOKENS["texto_muted"],
                "fontFamily": TOKENS["fuente_mono"],
                "marginRight": TOKENS["espacio_sm"],
            },
        )
        for s in ORDEN_SERVICIOS
    ]
    return html.Div(
        [html.Span("Orden de servicios: ", style={"fontSize": "10px", "color": TOKENS["texto_muted"]})] + items,
        style={"marginTop": TOKENS["espacio_sm"], "display": "flex", "flexWrap": "wrap"},
    )


# ---------------------------------------------------------------------------
# Panel de inyección de fallas (botones reales; tarea 7.8)
# ---------------------------------------------------------------------------
def _panel_fallas() -> html.Div:
    """Columna de botones de inyección de fallas (uno por tipo, design §9.4).

    Los botones están **habilitados** y llevan id (``ID_FALLA``); su cableado
    (mutación del estado en vivo) vive en el callback de alarmas de este módulo,
    que delega en ``app.live.inyectar_falla``. Bajo los botones se añade una nota
    de que la falla se aplica a un objetivo por defecto del estadio activo.
    """
    return html.Div(
        [
            html.Div(
                [_boton_falla(tipo) for tipo in live.TIPOS_FALLA_UI],
                style={
                    "display": "flex",
                    "flexDirection": "column",
                    "gap": TOKENS["espacio_sm"],
                },
            ),
            html.Div(
                "La falla se inyecta sobre un objetivo por defecto del estadio activo.",
                style={
                    "fontSize": "11px",
                    "color": TOKENS["texto_muted"],
                    "marginTop": TOKENS["espacio_sm"],
                },
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Tabla de alarmas activas e histórico de sesión (Req 4.8)
# ---------------------------------------------------------------------------
def _severidad_celda(severidad: str) -> html.Span:
    """Celda de severidad con ícono + texto + color (color no es único canal, §9.6)."""
    token, icono, etiqueta = _SEVERIDAD_PRESENTACION.get(
        str(severidad), ("texto_muted", "•", str(severidad))
    )
    return html.Span(
        [
            html.Span(f"{icono} ", style={"fontWeight": 700}),
            html.Span(etiqueta),
        ],
        style={"color": TOKENS[token], "fontSize": "12px", "fontWeight": 600},
    )


def _estado_celda(estado_valor: str) -> html.Span:
    """Celda del estado del ciclo de vida con ícono + texto + color (§9.6)."""
    token, icono, etiqueta = _ESTADO_ALARMA_PRESENTACION.get(
        str(estado_valor), ("texto_muted", "•", str(estado_valor))
    )
    return html.Span(
        [html.Span(f"{icono} ", style={"fontWeight": 700}), html.Span(etiqueta)],
        style={"color": TOKENS[token], "fontSize": "12px"},
    )


def _valor_estado(alarma) -> str:
    """Extrae el valor de texto del estado de la alarma (Enum o str)."""
    estado = getattr(alarma, "estado", "")
    return getattr(estado, "value", estado)


def _fmt_t(t_min) -> str:
    """Formatea un minuto de partido (float) como texto, o '—' si es None."""
    if t_min is None:
        return "—"
    try:
        return f"T{float(t_min):+.0f}"
    except (TypeError, ValueError):
        return "—"


_ESTILO_TH = {
    "textAlign": "left",
    "padding": TOKENS["espacio_xs"],
    "fontSize": "11px",
    "color": TOKENS["texto_muted"],
    "textTransform": "uppercase",
    "letterSpacing": "0.04em",
    "borderBottom": f"1px solid {TOKENS['borde']}",
}
_ESTILO_TD = {
    "padding": TOKENS["espacio_xs"],
    "fontSize": "12px",
    "color": TOKENS["texto"],
    "borderBottom": f"1px solid {TOKENS['borde']}",
    "verticalAlign": "top",
}


def _tabla_activas(activas: list) -> html.Div:
    """Tabla de alarmas activas (vivas no derivadas) — Req 4.8.

    Columnas: código, severidad (color + ícono + texto, §9.6), origen, apertura
    (t_min) y mensaje. Si no hay activas, muestra un indicador en lugar de una
    tabla vacía (Req 15.3).
    """
    if not activas:
        return placeholder_texto("Sin alarmas activas.")

    encabezado = html.Thead(
        html.Tr(
            [
                html.Th("Código", style=_ESTILO_TH),
                html.Th("Severidad", style=_ESTILO_TH),
                html.Th("Origen", style=_ESTILO_TH),
                html.Th("Apertura", style=_ESTILO_TH),
                html.Th("Mensaje", style=_ESTILO_TH),
            ]
        )
    )
    filas = [
        html.Tr(
            [
                html.Td(
                    getattr(a, "codigo", ""),
                    style={**_ESTILO_TD, "fontFamily": TOKENS["fuente_mono"]},
                ),
                html.Td(_severidad_celda(getattr(a, "severidad", "")), style=_ESTILO_TD),
                html.Td(
                    getattr(a, "origen_id", ""),
                    style={**_ESTILO_TD, "fontFamily": TOKENS["fuente_mono"], "color": TOKENS["texto_muted"]},
                ),
                html.Td(_fmt_t(getattr(a, "t_min_apertura", None)), style=_ESTILO_TD),
                html.Td(getattr(a, "mensaje", ""), style=_ESTILO_TD),
            ]
        )
        for a in activas
    ]
    return html.Table(
        [encabezado, html.Tbody(filas)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )


def _tabla_historico(historico: list) -> html.Div:
    """Histórico de sesión: todas las alarmas registradas (vivas + cerradas) — Req 4.8.

    Columnas: código, estado del ciclo de vida (color + ícono + texto, §9.6),
    origen, apertura y cierre (t_min). Se muestra en orden de apertura (las más
    recientes al final, tal como se registran). Si está vacío, muestra un
    indicador (Req 15.3).
    """
    if not historico:
        return placeholder_texto("Sin alarmas en la sesión todavía.")

    encabezado = html.Thead(
        html.Tr(
            [
                html.Th("Código", style=_ESTILO_TH),
                html.Th("Estado", style=_ESTILO_TH),
                html.Th("Origen", style=_ESTILO_TH),
                html.Th("Apertura", style=_ESTILO_TH),
                html.Th("Cierre", style=_ESTILO_TH),
            ]
        )
    )
    filas = [
        html.Tr(
            [
                html.Td(
                    getattr(a, "codigo", ""),
                    style={**_ESTILO_TD, "fontFamily": TOKENS["fuente_mono"]},
                ),
                html.Td(_estado_celda(_valor_estado(a)), style=_ESTILO_TD),
                html.Td(
                    getattr(a, "origen_id", ""),
                    style={**_ESTILO_TD, "fontFamily": TOKENS["fuente_mono"], "color": TOKENS["texto_muted"]},
                ),
                html.Td(_fmt_t(getattr(a, "t_min_apertura", None)), style=_ESTILO_TD),
                html.Td(_fmt_t(getattr(a, "t_min_cierre", None)), style=_ESTILO_TD),
            ]
        )
        for a in historico
    ]
    return html.Table(
        [encabezado, html.Tbody(filas)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )


def _contenido_alarmas(activas: list, historico: list) -> html.Div:
    """Contenido del contenedor de alarmas: tabla de activas + histórico (Req 4.8).

    Es lo que devuelve el callback en vivo para ``ID_ALARMAS``. Muestra SIEMPRE
    ambas secciones (una tabla de activas y un histórico de sesión), poblando
    con indicadores cuando alguna esté vacía (Req 15.3).
    """
    return html.Div(
        [
            html.Div(
                f"Activas ({len(activas)})",
                style={
                    "fontSize": "12px",
                    "fontWeight": 600,
                    "color": TOKENS["texto"],
                    "marginBottom": TOKENS["espacio_xs"],
                },
            ),
            _tabla_activas(activas),
            html.Div(
                f"Histórico de sesión ({len(historico)})",
                style={
                    "fontSize": "12px",
                    "fontWeight": 600,
                    "color": TOKENS["texto"],
                    "marginTop": TOKENS["espacio_md"],
                    "marginBottom": TOKENS["espacio_xs"],
                    "borderTop": f"1px solid {TOKENS['borde']}",
                    "paddingTop": TOKENS["espacio_sm"],
                },
            ),
            _tabla_historico(historico),
        ]
    )


def _alarmas() -> html.Div:
    """Contenedor de alarmas poblado desde el estado en vivo (Req 4.8/15.1)."""
    datos = live.alarmas_snapshot()
    return html.Div(
        _contenido_alarmas(datos["activas"], datos["historico"]),
        id=ID_ALARMAS,
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def layout() -> html.Div:
    """Retorna la vista de Simulación con la cuadrícula 2x2 poblada en vivo.

    Las cuatro gráficas se pueblan desde ``app.live.snapshot`` (Req 15.1/15.2);
    si una serie aún no tiene puntos, la figura muestra su anotación en lugar de
    quedar vacía (Req 15.3).
    """
    datos = live.snapshot()
    historial = datos["historial"]

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(panel("Controles de simulación", _controles()), width=8),
                    dbc.Col(
                        panel(
                            "Inyección de fallas",
                            _panel_fallas(),
                            subtitulo="Inyecta una falla del catálogo (design §6.4)",
                        ),
                        width=4,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "Gráficas en vivo",
                            html.Div(_cuadricula(historial), id="sim-cuadricula"),
                            _leyenda_servicios(),
                            subtitulo="Cuadrícula 2x2 · eje de tiempo compartido · unidad por eje",
                        ),
                        width=8,
                    ),
                    dbc.Col(
                        panel(
                            "Alarmas",
                            _alarmas(),
                            subtitulo="Tabla de activas e histórico de la sesión",
                        ),
                        width=4,
                    ),
                ],
                className="g-3",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Callback en vivo (registrado aquí; se cuelga del único intervalo del tablero)
# ---------------------------------------------------------------------------
@callback(
    [Output(_ID_GRAFICA[codigo], "figure") for codigo, *_ in _GRAFICAS],
    Input(live.INTERVALO_ID, "n_intervals"),
)
def _refrescar_graficas(_n):
    """Refresca las cuatro figuras de la cuadrícula desde el estado en vivo.

    Lee las series del historial ya empujadas por ``sim.kpis`` (Req 2.2) y
    reconstruye cada figura conservando el eje de tiempo compartido y la unidad
    en el título de eje (Req 9.6). No avanza el motor.
    """
    datos = live.snapshot()
    historial = datos["historial"]
    return [
        _figura(codigo, titulo, unidad, token, historial.get(codigo, []))
        for codigo, titulo, unidad, token in _GRAFICAS
    ]


# ---------------------------------------------------------------------------
# Callback de inyección de fallas + tabla de alarmas (tarea 7.8, Req 4.8)
#
# Un ÚNICO callback es dueño del contenedor de alarmas (``ID_ALARMAS``). Se
# dispara con el intervalo del tablero (para refrescar la tabla cada tick) y con
# los botones de inyección de fallas y el botón de gol. Identifica el disparador
# con ``dash.ctx``:
#
#   * Botón de falla  -> ``app.live.inyectar_falla(tipo)`` (mismo tick, §6.4).
#   * Botón de gol    -> ``app.live.gol()`` (pico de actividad f_gol, §6.2).
#   * Tick del intervalo -> no muta nada; solo relee y refresca la tabla.
#
# Siempre devuelve el contenido reconstruido leyendo las alarmas ya calculadas
# por ``sim.alarms`` (Req 2.3): la inyección y el refresco de la tabla coexisten
# en un mismo callback, evitando salidas duplicadas (el intervalo ya alimenta
# otros Outputs en otros callbacks; aquí ``ID_ALARMAS`` es exclusivo de este
# callback).
# ---------------------------------------------------------------------------
# Mapa inverso id_boton -> tipo de falla, para resolver el disparador.
_TIPO_POR_ID: dict[str, str] = {ID_FALLA[tipo]: tipo for tipo in live.TIPOS_FALLA_UI}


@callback(
    Output(ID_ALARMAS, "children"),
    Input(live.INTERVALO_ID, "n_intervals"),
    [Input(ID_FALLA[tipo], "n_clicks") for tipo in live.TIPOS_FALLA_UI],
    Input(ID_GOL, "n_clicks"),
)
def _fallas_y_alarmas(_n, *_clicks):
    """Inyecta fallas/gol según el botón disparado y refresca la tabla de alarmas.

    Usa ``ctx.triggered_id`` para decidir la acción. En un tick del intervalo no
    inyecta nada (solo refresca). Tras cualquier acción, relee las alarmas del
    estado (``app.live.alarmas_snapshot``) y reconstruye el contenedor con la
    tabla de activas y el histórico de sesión (Req 4.8). No calcula alarmas ni
    avanza el motor (el motor avanza en el callback maestro del intervalo).
    """
    disparador = ctx.triggered_id
    if disparador == ID_GOL:
        live.gol()
    elif disparador in _TIPO_POR_ID:
        live.inyectar_falla(_TIPO_POR_ID[disparador])

    datos = live.alarmas_snapshot()
    return _contenido_alarmas(datos["activas"], datos["historico"])
