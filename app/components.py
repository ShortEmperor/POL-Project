"""Componentes y estilos compartidos de la Capa de Presentación.

Este módulo centraliza helpers de estilo reutilizados por el layout y las
cuatro vistas. **No declara ningún color literal**: todo color, tipografía y
espaciado proviene de :data:`app.theme.TOKENS` (Requirement 9.3). Importa
únicamente ``theme`` (y Dash), nunca ``layout`` ni las vistas, para evitar
dependencias circulares.
"""

from __future__ import annotations

from dash import html

from app.theme import TOKENS


# ---------------------------------------------------------------------------
# Orden de servicios fijo en todas las vistas y leyendas (Requirement 9.5).
# ---------------------------------------------------------------------------
ORDEN_SERVICIOS: tuple[str, ...] = (
    "palcos",
    "wifi",
    "cctv",
    "torniquetes",
    "pos",
    "signage",
    "macro",
    "voz",
    "prensa",
    "esports",
)


def estilo_panel() -> dict:
    """Estilo base de un panel/tarjeta (fondo, borde, radio, padding)."""
    return {
        "backgroundColor": TOKENS["bg_panel"],
        "border": f"1px solid {TOKENS['borde']}",
        "borderRadius": TOKENS["radio"],
        "padding": TOKENS["espacio_md"],
        "color": TOKENS["texto"],
        "fontFamily": TOKENS["fuente"],
        "height": "100%",
        "boxSizing": "border-box",
    }


def panel(titulo: str, *children, subtitulo: str | None = None) -> html.Div:
    """Panel con encabezado y contenido no vacío (Requirement 15.1/15.3).

    Cada panel siempre lleva al menos un título; los ``children`` aportan el
    contenido poblado. Nunca se renderiza un panel vacío.
    """
    encabezado = [
        html.H3(
            titulo,
            style={
                "margin": 0,
                "fontSize": "16px",
                "fontWeight": 600,
                "color": TOKENS["texto"],
            },
        )
    ]
    if subtitulo:
        encabezado.append(
            html.Div(
                subtitulo,
                style={
                    "fontSize": "12px",
                    "color": TOKENS["texto_muted"],
                    "marginTop": TOKENS["espacio_xs"],
                },
            )
        )
    cuerpo = list(children) if children else [placeholder_texto()]
    return html.Div(
        [
            html.Div(
                encabezado,
                style={
                    "borderBottom": f"1px solid {TOKENS['borde']}",
                    "paddingBottom": TOKENS["espacio_sm"],
                    "marginBottom": TOKENS["espacio_md"],
                },
            ),
            html.Div(cuerpo),
        ],
        style=estilo_panel(),
    )


def placeholder_texto(
    texto: str = "Panel pendiente de datos en vivo (se poblará en una tarea posterior).",
) -> html.Div:
    """Indicador de estado para paneles aún sin datos en vivo.

    Cumple Requirement 15.3: en lugar de dejar el panel vacío se muestra un
    indicador de estado legible con los tokens del tema.
    """
    return html.Div(
        [
            html.Span("○ ", style={"color": TOKENS["texto_muted"]}),
            html.Span(texto, style={"color": TOKENS["texto_muted"]}),
        ],
        style={"fontSize": "13px", "fontStyle": "italic"},
    )


def valor_kpi(etiqueta: str, valor: str, unidad: str = "", color: str | None = None) -> html.Div:
    """Tarjeta compacta de métrica con etiqueta, valor y unidad."""
    return html.Div(
        [
            html.Div(
                etiqueta,
                style={
                    "fontSize": "12px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                },
            ),
            html.Div(
                [
                    html.Span(
                        valor,
                        style={
                            "fontSize": "28px",
                            "fontWeight": 700,
                            "fontFamily": TOKENS["fuente_mono"],
                            "color": color or TOKENS["texto"],
                        },
                    ),
                    html.Span(
                        f" {unidad}" if unidad else "",
                        style={"fontSize": "14px", "color": TOKENS["texto_muted"]},
                    ),
                ]
            ),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_md"],
        },
    )


# ---------------------------------------------------------------------------
# Indicación de cumplimiento/exceso de umbral por KPI (Req 2.5, design §9.6).
#
# El color nunca es el único canal: cada estado de umbral lleva además un ícono
# y un texto legible (accesibilidad para daltonismo, design §9.6). Estos
# helpers los usan la vista de Resumen (tarjetas) y la de Simulación (títulos).
# ---------------------------------------------------------------------------

# Estados de cumplimiento de umbral y su presentación (token de color, ícono,
# texto). "cumple" = dentro del umbral; "excede" = fuera; "sin_umbral" = el KPI
# no define umbral (K3, K7 en el catálogo).
_UMBRAL_PRESENTACION = {
    "cumple": ("en_linea", "✓", "Dentro de umbral"),
    "excede": ("critica", "✕", "Excede umbral"),
    "sin_umbral": ("texto_muted", "•", "Sin umbral"),
}


def evaluar_umbral(valor: float, umbral, comparacion: str | None) -> str:
    """Clasifica ``valor`` frente al umbral del KPI (Req 2.5).

    Devuelve uno de ``"cumple"``, ``"excede"`` o ``"sin_umbral"``. La
    ``comparacion`` sigue el catálogo de ``sim.kpis`` (``"<="``, ``">="``,
    ``"=="`` o ``None``). Si el KPI no define umbral, se devuelve
    ``"sin_umbral"`` (no hay cumplimiento que evaluar).
    """
    if umbral is None or comparacion is None:
        return "sin_umbral"
    if comparacion == "<=":
        return "cumple" if valor <= umbral else "excede"
    if comparacion == ">=":
        return "cumple" if valor >= umbral else "excede"
    if comparacion == "==":
        return "cumple" if valor == umbral else "excede"
    return "sin_umbral"


def badge_umbral(estado_umbral: str, umbral_texto: str = "") -> html.Div:
    """Insignia de cumplimiento de umbral con ícono + texto + color (Req 2.5).

    ``estado_umbral`` es el resultado de :func:`evaluar_umbral`. El color nunca
    es el único canal: siempre acompaña un ícono y un texto (design §9.6).
    """
    token, icono, texto = _UMBRAL_PRESENTACION.get(
        estado_umbral, _UMBRAL_PRESENTACION["sin_umbral"]
    )
    etiqueta = texto
    if umbral_texto and estado_umbral != "sin_umbral":
        etiqueta = f"{texto} ({umbral_texto})"
    return html.Div(
        [
            html.Span(f"{icono} ", style={"color": TOKENS[token], "fontWeight": 700}),
            html.Span(etiqueta, style={"color": TOKENS[token]}),
        ],
        style={
            "fontSize": "11px",
            "fontFamily": TOKENS["fuente"],
            "marginTop": TOKENS["espacio_xs"],
        },
    )


def color_umbral(estado_umbral: str) -> str:
    """Token de color asociado al estado de umbral (Req 2.5, §9.6)."""
    token, _icono, _texto = _UMBRAL_PRESENTACION.get(
        estado_umbral, _UMBRAL_PRESENTACION["sin_umbral"]
    )
    return TOKENS[token]
