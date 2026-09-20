"""Vista 1: Resumen (design §9.2, Requirements 2, 9 y 15).

Panorama de un vistazo para el operador:

* **Tarjetas de KPI** (K1, K3, K4, K7, K8, K10) pobladas con los valores en
  vivo ya calculados por ``sim.kpis`` (Req 2.2/2.3). Cada tarjeta con umbral
  indica visualmente si el valor cumple o lo excede, con color **y** un canal
  no cromático (ícono + texto) por accesibilidad (Req 2.5, design §9.6).
* **Semáforo de los tres estadios** (Azteca, BBVA, Akron) con estado + ícono.
* **Línea de tiempo del partido** (T-180 → T+135) que ubica el minuto actual.

Integración en vivo (design §4): las tarjetas se refrescan desde el único
``dcc.Interval`` del tablero (``app.live``) mediante un callback registrado en
este módulo con ``@callback`` (mismo patrón que la vista de Monitoreo, para no
colisionar). Ningún KPI se calcula aquí; solo se leen valores precalculados
(Req 2.2). La vista carga poblada desde el primer render (Req 15.1/15.2) porque
``app.live`` siembra el historial al arrancar.

Regla de integración visual (Req 9.3): este módulo **no declara ningún color
literal**; todo estilo proviene de :data:`app.theme.TOKENS`.
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, callback, html

from app import live
from app.components import badge_umbral, color_umbral, evaluar_umbral, panel
from app.theme import TOKENS
from sim.kpis import CATALOGO

# Los seis KPIs destacados de la vista de Resumen (design §7 columna "Panel",
# design §9.2). El orden es fijo (design §9.6).
_KPIS_RESUMEN: tuple[str, ...] = ("K1", "K3", "K4", "K7", "K8", "K10")

_ESTADIOS = [
    ("azteca", "Estadio Azteca"),
    ("bbva", "Estadio BBVA"),
    ("akron", "Estadio Akron"),
]

# Contenedor de las tarjetas que refresca el callback del intervalo.
_ID_TARJETAS = "resumen-tarjetas-kpi"
_ID_TIMELINE = "resumen-timeline"

# Indicador por defecto para un dato ausente (Req 15.3).
_SIN_DATO = "—"


# ---------------------------------------------------------------------------
# Formato de valores de KPI según su unidad
# ---------------------------------------------------------------------------
def _formato_valor(codigo: str, valor: float | None) -> str:
    """Formatea el valor de un KPI para mostrarlo (sin la unidad).

    Conteos (K4/K7/K8/K10) se muestran como enteros; el resto con un decimal.
    Devuelve el indicador de dato ausente si ``valor`` es ``None`` (Req 15.3).
    """
    if valor is None:
        return _SIN_DATO
    unidad = CATALOGO[codigo].unidad
    if unidad == "conteo":
        return f"{valor:.0f}"
    if codigo == "K3":  # throughput total en Gbps con dos decimales
        return f"{valor:.2f}"
    return f"{valor:.1f}"


def _tarjeta_kpi(codigo: str, valor: float | None, total_onts: int = 0) -> html.Div:
    """Tarjeta de un KPI con etiqueta, valor+unidad y badge de umbral (Req 2.5).

    K4 (ONTs en línea) se expone como conteo en ``sim.kpis`` pero su umbral es
    porcentual (≥ 99.9%). Su cumplimiento se evalúa aquí contra el total de ONTs
    del estadio (design §7), no comparando el conteo con 99.9 directamente.
    """
    meta = CATALOGO[codigo]
    if valor is None:
        estado_umbral = "sin_umbral"
    elif codigo == "K4" and total_onts > 0:
        pct_en_linea = valor / total_onts * 100.0
        estado_umbral = evaluar_umbral(pct_en_linea, meta.umbral, meta.comparacion)
    else:
        estado_umbral = evaluar_umbral(valor, meta.umbral, meta.comparacion)
    color_valor = color_umbral(estado_umbral) if estado_umbral != "sin_umbral" else TOKENS["texto"]

    return html.Div(
        [
            html.Div(
                f"{codigo} · {meta.nombre}",
                style={
                    "fontSize": "12px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                    "minHeight": "30px",
                },
            ),
            html.Div(
                [
                    html.Span(
                        _formato_valor(codigo, valor),
                        style={
                            "fontSize": "28px",
                            "fontWeight": 700,
                            "fontFamily": TOKENS["fuente_mono"],
                            "color": color_valor,
                        },
                    ),
                    html.Span(
                        f" {meta.unidad}" if meta.unidad and meta.unidad != "conteo" else "",
                        style={"fontSize": "14px", "color": TOKENS["texto_muted"]},
                    ),
                ]
            ),
            badge_umbral(estado_umbral, meta.umbral_texto),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderLeft": f"3px solid {color_valor}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_md"],
            "height": "100%",
            "boxSizing": "border-box",
        },
    )


def _tarjetas(kpis_valores: dict | None, total_onts: int = 0) -> dbc.Row:
    """Fila de las seis tarjetas de KPI destacadas (design §9.2)."""
    valores = kpis_valores or {}
    cols = [
        dbc.Col(_tarjeta_kpi(codigo, valores.get(codigo), total_onts), width=2)
        for codigo in _KPIS_RESUMEN
    ]
    return dbc.Row(cols, className="g-3")


# ---------------------------------------------------------------------------
# Semáforo de los tres estadios (design §9.2)
# ---------------------------------------------------------------------------
def _semaforo(estadio_id: str, nombre: str, activo: bool) -> html.Div:
    """Tarjeta de semáforo de un estadio.

    El color nunca es el único canal: acompaña un ícono y el texto de estado
    (design §9.6). El estadio activo se resalta con un borde de acento.
    """
    # En reposo (sin fallas, tarea 7.x) los tres estadios están EN LÍNEA.
    return html.Div(
        [
            html.Span(
                "● ",
                style={"color": TOKENS["en_linea"], "fontSize": "18px"},
            ),
            html.Span(
                "EN LÍNEA",
                style={"fontFamily": TOKENS["fuente_mono"], "fontSize": "12px", "color": TOKENS["en_linea"]},
            ),
            html.Div(
                nombre,
                style={"color": TOKENS["texto"], "fontSize": "13px", "marginTop": TOKENS["espacio_xs"], "fontWeight": 600},
            ),
            html.Div(
                "Activo" if activo else "En espera",
                style={"color": TOKENS["texto_muted"], "fontSize": "11px"},
            ),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['acento'] if activo else TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_md"],
            "textAlign": "center",
        },
    )


def _panel_semaforo(estadio_activo: str | None) -> html.Div:
    return dbc.Row(
        [
            dbc.Col(_semaforo(eid, nombre, eid == estadio_activo), width=4)
            for eid, nombre in _ESTADIOS
        ],
        className="g-3",
    )


# ---------------------------------------------------------------------------
# Línea de tiempo del partido (design §9.2)
# ---------------------------------------------------------------------------
_T_INICIAL = -180.0
_T_FINAL = 135.0
_FASES = [
    (-180.0, 0.0, "Ingreso"),
    (0.0, 45.0, "1er tiempo"),
    (45.0, 60.0, "Medio tiempo"),
    (60.0, 90.0, "2do tiempo"),
    (90.0, 135.0, "Salida"),
]


def _timeline(t_min: float | None) -> html.Div:
    """Barra de progreso T-180 → T+135 con marcador del minuto actual (§9.2)."""
    t = _T_INICIAL if t_min is None else max(_T_INICIAL, min(t_min, _T_FINAL))
    total = _T_FINAL - _T_INICIAL
    pct = (t - _T_INICIAL) / total * 100.0

    # Segmentos de fase, proporcionales a su duración.
    segmentos = []
    for ini, fin, etiqueta in _FASES:
        ancho = (fin - ini) / total * 100.0
        activo = ini <= t < fin or (fin == _T_FINAL and t >= fin)
        segmentos.append(
            html.Div(
                etiqueta,
                style={
                    "width": f"{ancho}%",
                    "backgroundColor": TOKENS["acento"] if activo else TOKENS["bg_elevated"],
                    "color": TOKENS["texto"] if activo else TOKENS["texto_muted"],
                    "borderRight": f"1px solid {TOKENS['borde']}",
                    "fontSize": "11px",
                    "textAlign": "center",
                    "padding": f"{TOKENS['espacio_xs']} 0",
                    "boxSizing": "border-box",
                    "overflow": "hidden",
                    "whiteSpace": "nowrap",
                },
            )
        )

    signo = "+" if t >= 0 else "-"
    reloj = f"T{signo}{abs(t):.0f} min"

    return html.Div(
        [
            html.Div(
                [
                    html.Span("Minuto actual: ", style={"color": TOKENS["texto_muted"], "fontSize": "13px"}),
                    html.Span(
                        reloj,
                        style={"fontFamily": TOKENS["fuente_mono"], "color": TOKENS["texto"], "fontWeight": 600},
                    ),
                ],
                style={"marginBottom": TOKENS["espacio_sm"]},
            ),
            html.Div(
                segmentos,
                style={
                    "display": "flex",
                    "width": "100%",
                    "border": f"1px solid {TOKENS['borde']}",
                    "borderRadius": TOKENS["radio"],
                    "overflow": "hidden",
                },
            ),
            # Riel + marcador de posición.
            html.Div(
                html.Div(
                    style={
                        "position": "absolute",
                        "left": f"{pct}%",
                        "top": 0,
                        "width": "2px",
                        "height": "14px",
                        "backgroundColor": TOKENS["en_linea"],
                    }
                ),
                style={
                    "position": "relative",
                    "height": "14px",
                    "marginTop": TOKENS["espacio_xs"],
                    "backgroundColor": TOKENS["bg_elevated"],
                    "borderRadius": TOKENS["radio"],
                },
            ),
            html.Div(
                [
                    html.Span("T-180", style={"fontSize": "10px", "color": TOKENS["texto_muted"]}),
                    html.Span("T+135", style={"fontSize": "10px", "color": TOKENS["texto_muted"], "float": "right"}),
                ],
                style={"marginTop": TOKENS["espacio_xs"]},
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def layout() -> html.Div:
    """Retorna la vista de Resumen poblada con el estado en vivo actual.

    Todos los paneles se pueblan desde ``app.live.snapshot`` (Req 15.1/15.2);
    donde un dato falte, las tarjetas caen al indicador por defecto (Req 15.3).
    """
    datos = live.snapshot()
    kpis_valores = datos["kpis"]

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "KPIs destacados",
                            html.Div(_tarjetas(kpis_valores, datos["total_onts"]), id=_ID_TARJETAS),
                            subtitulo="K1, K3, K4, K7, K8, K10 · cumplimiento de umbral por tarjeta",
                        ),
                        width=12,
                    )
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "Semáforo de estadios",
                            _panel_semaforo(datos["estadio"]),
                        ),
                        width=6,
                    ),
                    dbc.Col(
                        panel(
                            "Línea de tiempo del partido",
                            html.Div(_timeline(datos["t_min"]), id=_ID_TIMELINE),
                            subtitulo="T-180 a T+135 minutos",
                        ),
                        width=6,
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
    Output(_ID_TARJETAS, "children"),
    Output(_ID_TIMELINE, "children"),
    Input(live.INTERVALO_ID, "n_intervals"),
)
def _refrescar_resumen(_n):
    """Refresca las tarjetas de KPI y la línea de tiempo desde el estado en vivo.

    Lee ``app.live.snapshot`` (valores ya calculados por ``sim.kpis``, Req 2.2)
    y reconstruye las tarjetas con su indicación de umbral y el marcador de la
    línea de tiempo. No avanza el motor: eso lo hace el callback del intervalo
    en ``app.callbacks`` (tarea 5.8).
    """
    datos = live.snapshot()
    return _tarjetas(datos["kpis"], datos["total_onts"]), _timeline(datos["t_min"])
