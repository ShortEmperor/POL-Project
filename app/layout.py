"""Layout raíz del POL Command Center (design §9.1).

Define la retícula de 12 columnas orientada a 1920x1080 (no responsiva), la
**barra superior persistente** presente en las cuatro vistas y la navegación
por pestañas. El ruteo de vistas se resuelve en :func:`render_view`, invocado
por el callback de ``app/main.py``.

Regla de integración visual (Requirement 9.3): este módulo no declara ningún
color literal; todo estilo proviene de :data:`app.theme.TOKENS`.

Nota de alcance: ``SimState`` y el reloj en vivo se implementan en la tarea 5.3
y se cablean a la barra superior en la tarea 5.8. Mientras tanto, la barra usa
:data:`CONTEXTO_INICIAL` (valores por defecto del estado de arranque) para
poblar sus indicadores sin dejarlos vacíos (Requirement 15.3).
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html

from app import live
from app.theme import TOKENS
from app.views import monitoreo, negocio, resumen, simulacion

# Identificadores de las cuatro pestañas (Requirement 1.1).
TAB_RESUMEN = "resumen"
TAB_MONITOREO = "monitoreo"
TAB_SIMULACION = "simulacion"
TAB_NEGOCIO = "negocio"
TAB_INICIAL = TAB_RESUMEN

# Id del contenedor de indicadores de la barra superior. El bucle en vivo
# (``app/callbacks.py``, tarea 5.8) reescribe este contenedor en cada tick para
# que la barra refleje el estado real del motor (reloj, corriendo/pausado,
# velocidad, escenario, alarmas). Requirement 1.3/1.4.
ID_TOPBAR_INDICADORES = "top-bar-indicadores"

_ESTADIOS = [
    {"label": "Estadio Azteca", "value": "azteca"},
    {"label": "Estadio BBVA", "value": "bbva"},
    {"label": "Estadio Akron", "value": "akron"},
]

# Contexto de arranque para la barra superior (previo al motor en vivo, tarea 5.3).
CONTEXTO_INICIAL = {
    "estadio": "azteca",
    "t_min": -180.0,
    "corriendo": False,
    "velocidad": 1.0,
    "escenario": "diseno",
    "alarmas_activas": 0,
    "alarmas_criticas": 0,
}


def fase_partido(t_min: float) -> str:
    """Etiqueta de fase del partido según el minuto ``t_min`` (design §6.2).

    Los tramos siguen la curva de llegada ``f_llegada`` y el factor de
    actividad por fase del modelo de tráfico.
    """
    if t_min < 0:
        return "Ingreso"
    if t_min < 45:
        return "Primer tiempo"
    if t_min < 60:
        return "Medio tiempo"
    if t_min < 90:
        return "Segundo tiempo"
    return "Salida"


def _formato_reloj(t_min: float) -> str:
    signo = "+" if t_min >= 0 else "-"
    return f"T{signo}{abs(t_min):.0f} min"


def _indicador(etiqueta: str, valor, *, color: str | None = None, icono: str = "") -> html.Div:
    """Bloque etiqueta/valor de la barra superior.

    El color nunca es el único canal: se acompaña de ícono o texto
    (accesibilidad para daltonismo, design §9.6).
    """
    contenido = []
    if icono:
        contenido.append(html.Span(f"{icono} ", style={"color": color or TOKENS["texto"]}))
    contenido.append(html.Span(str(valor), style={"color": color or TOKENS["texto"], "fontWeight": 600}))
    return html.Div(
        [
            html.Div(
                etiqueta,
                style={
                    "fontSize": "10px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.05em",
                },
            ),
            html.Div(contenido, style={"fontSize": "14px", "fontFamily": TOKENS["fuente_mono"]}),
        ],
        style={"padding": f"0 {TOKENS['espacio_md']}"},
    )


def indicadores_topbar(ctx: dict | None = None) -> html.Div:
    """Indicadores en vivo de la barra superior (Requirement 1.3).

    Bloque reconstruible con reloj (t_min + fase), estado corriendo/pausado,
    velocidad, escenario y contadores de alarmas activas/críticas. El bucle en
    vivo (``app/callbacks.py``, tarea 5.8) invoca esta función en cada tick y
    reemplaza el contenido del contenedor :data:`ID_TOPBAR_INDICADORES`, de modo
    que la barra siempre refleja el estado real del motor (Req 1.3/1.4).
    """
    ctx = {**CONTEXTO_INICIAL, **(ctx or {})}
    corriendo = ctx["corriendo"]
    return html.Div(
        [
            _indicador("Reloj", f"{_formato_reloj(ctx['t_min'])} · {fase_partido(ctx['t_min'])}"),
            _indicador(
                "Estado",
                "Corriendo" if corriendo else "Pausado",
                color=TOKENS["en_linea"] if corriendo else TOKENS["texto_muted"],
                icono="▶" if corriendo else "⏸",
            ),
            _indicador("Velocidad", f"{ctx['velocidad']:.0f}x", color=TOKENS["acento"]),
            _indicador("Escenario", str(ctx["escenario"]).capitalize(), color=TOKENS["acento"]),
            _indicador(
                "Alarmas activas",
                ctx["alarmas_activas"],
                color=TOKENS["mayor"] if ctx["alarmas_activas"] else TOKENS["texto_muted"],
                icono="▲",
            ),
            _indicador(
                "Críticas",
                ctx["alarmas_criticas"],
                color=TOKENS["critica"] if ctx["alarmas_criticas"] else TOKENS["texto_muted"],
                icono="■",
            ),
        ],
        style={"display": "flex", "alignItems": "center"},
    )


def _top_bar(ctx: dict | None = None) -> html.Div:
    """Barra superior persistente (Requirement 1.3, design §9.1)."""
    ctx = {**CONTEXTO_INICIAL, **(ctx or {})}

    selector = html.Div(
        [
            html.Div(
                "Estadio",
                style={
                    "fontSize": "10px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.05em",
                },
            ),
            dcc.Dropdown(
                id="selector-estadio",
                options=_ESTADIOS,
                value=ctx["estadio"],
                clearable=False,
                style={"width": "200px"},
            ),
        ],
        style={"padding": f"0 {TOKENS['espacio_md']}"},
    )

    # Contenedor con id estable: el bucle en vivo reescribe su contenido cada
    # tick (Req 1.3/1.4). Arranca poblado con el contexto de inicio (Req 15.3).
    indicadores = html.Div(indicadores_topbar(ctx), id=ID_TOPBAR_INDICADORES)

    navegacion = dcc.Tabs(
        id="nav-vistas",
        value=TAB_INICIAL,
        children=[
            dcc.Tab(label="Resumen", value=TAB_RESUMEN),
            dcc.Tab(label="Monitoreo", value=TAB_MONITOREO),
            dcc.Tab(label="Simulación", value=TAB_SIMULACION),
            dcc.Tab(label="Negocio", value=TAB_NEGOCIO),
        ],
        style={"height": "40px"},
    )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        "POL COMMAND CENTER",
                        style={
                            "fontWeight": 700,
                            "fontSize": "16px",
                            "letterSpacing": "0.08em",
                            "color": TOKENS["texto"],
                            "padding": f"0 {TOKENS['espacio_md']}",
                        },
                    ),
                    selector,
                    indicadores,
                ],
                style={"display": "flex", "alignItems": "center", "flexWrap": "wrap", "gap": TOKENS["espacio_sm"]},
            ),
            html.Div(navegacion, style={"marginTop": TOKENS["espacio_sm"]}),
        ],
        style={
            "backgroundColor": TOKENS["bg_panel"],
            "borderBottom": f"1px solid {TOKENS['borde']}",
            "padding": TOKENS["espacio_md"],
            "position": "sticky",
            "top": 0,
            "zIndex": 100,
        },
    )


def render_view(tab: str | None) -> html.Div:
    """Ruteo de vistas: retorna el layout de la pestaña seleccionada.

    Conserva el estado de la barra superior porque la barra es persistente y
    solo cambia el contenido de esta región (Requirement 1.4).
    """
    vistas = {
        TAB_RESUMEN: resumen.layout,
        TAB_MONITOREO: monitoreo.layout,
        TAB_SIMULACION: simulacion.layout,
        TAB_NEGOCIO: negocio.layout,
    }
    factory = vistas.get(tab or TAB_INICIAL, resumen.layout)
    return html.Div(
        factory(),
        style={
            "padding": TOKENS["espacio_lg"],
            "maxWidth": "1920px",
            "margin": "0 auto",
        },
    )


def serve_layout() -> html.Div:
    """Layout raíz de la aplicación: barra superior persistente + región de vista."""
    return html.Div(
        [
            # Único dcc.Interval del tablero (design §4): las gráficas y tarjetas
            # en vivo de las vistas se cuelgan de este componente. Vive en el
            # layout raíz para estar siempre presente, con independencia de la
            # pestaña activa (evita un segundo intervalo por vista).
            live.intervalo(),
            _top_bar(CONTEXTO_INICIAL),
            html.Div(
                render_view(TAB_INICIAL),
                id="contenido-vista",
            ),
        ],
        style={
            "backgroundColor": TOKENS["bg"],
            "color": TOKENS["texto"],
            "fontFamily": TOKENS["fuente"],
            "minHeight": "100vh",
            "width": "100%",
        },
    )
