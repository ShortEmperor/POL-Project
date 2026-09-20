"""Vista 4: Negocio (design §9.5, §10.1, §10.2, §5).

Ensambla los cuatro paneles que defienden la inversión, todos poblados desde el
inventario y `data/prices.yaml` (ningún número escrito a mano) más el mapa de
planta por zonas:

* **R10 Propuesta de valor** (Req 10): tres cifras derivadas del inventario
  (``sim.economics.propuesta_valor``) — puertos de conmutación eliminados,
  cuartos de telecom eliminados y kilovatios ahorrados. Se recalculan solas al
  cambiar el inventario (Req 10.3).
* **R11 Modelo económico** (Req 11): CAPEX/OPEX POL vs cobre, TCO a 5 y 10 años
  y año de punto de equilibrio (``sim.economics.resumen_economico``). Reflejan
  cambios de precio/inventario sin tocar código (Req 11.5).
* **R12 Escalabilidad** (Req 12): holgura de ONTs (``holgura_onts``) y un
  ``dcc.Slider`` de crecimiento de aforo que recalcula puertos/tarjetas/chasis
  con ``recalcular_dimensionamiento``; muestra una advertencia **visible**
  cuando ``requiere_hardware_adicional`` es ``True`` (Req 12.4), incluido el
  caso +20% (Req 12.5).
* **R13 SLA** (Req 13): disponibilidad observada por servicio frente al objetivo
  contractual (por protección) y fracción del presupuesto de indisponibilidad
  consumida (``presupuesto_consumido``). Orden de servicios fijo (Req 9.5).
* **Mapa por zonas** (Req 5): SVG de planta coloreado por estado agregado, con
  un canal **adicional al color** (patrón) para zonas críticas y tooltip de
  métricas por zona. Fail-soft: si el SVG no puede cargarse se muestra un
  indicador en lugar de romper la vista (Req 5.5, 15.3).

Separación de capas (design §3.1): todo el cálculo económico, de
escalabilidad y de SLA vive en ``sim.economics`` / ``sim.state``; esta vista
solo formatea. Regla visual (Req 9.3): ningún color literal aquí; todo sale de
``app.theme.TOKENS``.

Callbacks: se registran en este módulo con ``@callback`` usando ids **locales**
de la vista para no colisionar con ``app/callbacks.py`` (barra superior +
controles), ``simulacion.py`` (gráficas + alarmas), ``resumen.py`` (tarjetas +
timeline) ni ``monitoreo.py`` (drill-down). El slider (R12) y el intervalo del
tablero (R13, la disponibilidad avanza con la simulación) alimentan Outputs
exclusivos de la vista.
"""

from __future__ import annotations

from collections import Counter

import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html

from app import live
from app.components import ORDEN_SERVICIOS, panel, valor_kpi
from app.theme import TOKENS
from sim import economics
from sim.state import Estadio, Estado, Proteccion, Servicio, disponibilidad_servicio
from sim.topology import TopologiaInvalida, cargar_topologia

# ---------------------------------------------------------------------------
# Identificadores de componentes (LOCALES a la vista de Negocio).
#
# Todos con prefijo ``negocio-`` para garantizar que no colisionan con los
# Outputs de las demás vistas ni del bucle en vivo (design §4, Req sin salidas
# duplicadas). Solo el slider R12 y el intervalo del tablero alimentan estos
# contenedores; el intervalo ya alimenta otros contenedores en otros callbacks,
# pero aquí los ids de Output son exclusivos.
# ---------------------------------------------------------------------------
ID_SLIDER_AFORO = "negocio-slider-aforo"
ID_ESCALABILIDAD = "negocio-escalabilidad-resultado"
ID_SLA = "negocio-sla-resultado"

_SIN_DATO = "—"

# Marcas del slider de crecimiento de aforo (fracción). Incluye 0% (identidad),
# el caso +20% requerido (Req 12.5) y pasos hasta +100%.
_MARCAS_AFORO = {0: "0%", 10: "+10%", 20: "+20%", 30: "+30%", 50: "+50%", 100: "+100%"}


# ---------------------------------------------------------------------------
# Carga de datos (cacheada; solo lectura)
# ---------------------------------------------------------------------------
_CACHE_TOPOLOGIA: dict[str, Estadio] = {}


def _topologia(estadio_id: str) -> Estadio | None:
    """Inventario del estadio (cacheado). ``None`` si no puede cargarse.

    Usado por los paneles derivados del inventario (R10, R11, R12), que no
    dependen del estado en vivo. Fail-soft para no romper la vista (Req 15.3).
    """
    if estadio_id in _CACHE_TOPOLOGIA:
        return _CACHE_TOPOLOGIA[estadio_id]
    try:
        estadio = cargar_topologia(estadio_id)
    except TopologiaInvalida:
        return None
    _CACHE_TOPOLOGIA[estadio_id] = estadio
    return estadio


def _estadio_vivo() -> Estadio | None:
    """Estadio **activo del estado en vivo** (con ticks de disponibilidad).

    El panel de SLA (R13) necesita la disponibilidad **observada**, que se
    acumula en las ONTs del ``SimState`` compartido conforme corre la
    simulación. Se lee de ``app.live.ESTADO`` por referencia de módulo (misma
    convención que las demás vistas). Fail-soft: ``None`` si no hay estado.
    """
    estado = getattr(live, "ESTADO", None)
    if estado is None:
        return None
    estadios = getattr(estado, "estadios", None) or {}
    return estadios.get(getattr(estado, "estadio_activo", None))


def _estadio_activo_id() -> str:
    """Id del estadio activo del estado en vivo (por defecto ``"azteca"``)."""
    estado = getattr(live, "ESTADO", None)
    return getattr(estado, "estadio_activo", "azteca") if estado else "azteca"


def _precios() -> economics.Precios | None:
    """Precios de `data/prices.yaml`, o ``None`` si son inválidos (Req 11.5).

    El diseño manda fail-fast al arranque; en la vista degradamos con un
    indicador en lugar de tumbar toda la app (Req 15.3), para que el resto de
    paneles siga poblado aunque falten los precios.
    """
    try:
        return economics.cargar_precios()
    except economics.PreciosInvalidos:
        return None


# ---------------------------------------------------------------------------
# Formateo
# ---------------------------------------------------------------------------
def _fmt_moneda(valor: float) -> str:
    """Formatea un monto en USD con separador de miles, sin decimales."""
    return f"${valor:,.0f}"


def _fmt_entero(valor: float) -> str:
    return f"{int(round(valor)):,}"


def _indicador_error(texto: str) -> html.Div:
    """Indicador de estado de error/ausencia de datos (Req 15.3, sin color literal)."""
    return html.Div(
        [
            html.Span("⚠ ", style={"color": TOKENS["mayor"], "fontWeight": 700}),
            html.Span(texto, style={"color": TOKENS["texto_muted"]}),
        ],
        style={"fontSize": "13px", "fontFamily": TOKENS["fuente"]},
    )


# ===========================================================================
# R10 · Propuesta de valor
# ===========================================================================
def _panel_propuesta(estadio: Estadio | None, precios: economics.Precios | None) -> html.Div:
    """Tres cifras de la propuesta de valor derivadas del inventario (Req 10.2)."""
    if estadio is None:
        return _indicador_error("Inventario no disponible para este estadio.")
    if precios is None:
        return _indicador_error("Parámetros económicos no disponibles (revisa prices.yaml).")

    pv = economics.propuesta_valor(estadio, precios)
    cifras = [
        (
            "Puertos de conmutación eliminados",
            _fmt_entero(pv["puertos_conmutacion_eliminados"]),
            "puertos",
        ),
        (
            "Cuartos de telecom eliminados",
            _fmt_entero(pv["cuartos_telecom_eliminados"]),
            "cuartos",
        ),
        (
            "Kilovatios ahorrados",
            f"{pv['kilovatios_ahorrados']:,.1f}",
            "kW",
        ),
    ]
    return dbc.Row(
        [
            dbc.Col(valor_kpi(etiqueta, valor, unidad, color=TOKENS["en_linea"]), width=4)
            for etiqueta, valor, unidad in cifras
        ],
        className="g-3",
    )


# ===========================================================================
# R11 · Modelo económico (CAPEX/OPEX/TCO/break-even)
# ===========================================================================
def _fila_comparativa(concepto: str, pol: str, cobre: str, *, destacar: bool = False) -> html.Tr:
    """Fila de la tabla comparativa POL vs cobre."""
    peso = 700 if destacar else 500
    return html.Tr(
        [
            html.Td(
                concepto,
                style={"padding": TOKENS["espacio_sm"], "color": TOKENS["texto"], "fontWeight": peso},
            ),
            html.Td(
                pol,
                style={
                    "padding": TOKENS["espacio_sm"],
                    "textAlign": "right",
                    "fontFamily": TOKENS["fuente_mono"],
                    "color": TOKENS["acento"],
                    "fontWeight": peso,
                },
            ),
            html.Td(
                cobre,
                style={
                    "padding": TOKENS["espacio_sm"],
                    "textAlign": "right",
                    "fontFamily": TOKENS["fuente_mono"],
                    "color": TOKENS["texto_muted"],
                    "fontWeight": peso,
                },
            ),
        ],
        style={"borderBottom": f"1px solid {TOKENS['borde']}"},
    )


def _panel_economico(estadio: Estadio | None, precios: economics.Precios | None) -> html.Div:
    """Comparativa CAPEX/OPEX/TCO POL vs cobre + año de break-even (Req 11.2)."""
    if estadio is None:
        return _indicador_error("Inventario no disponible para este estadio.")
    if precios is None:
        return _indicador_error("Parámetros económicos no disponibles (revisa prices.yaml).")

    r = economics.resumen_economico(estadio, precios)

    encabezado = html.Tr(
        [
            html.Th("Concepto", style={"padding": TOKENS["espacio_sm"], "textAlign": "left", "color": TOKENS["texto_muted"]}),
            html.Th("POL", style={"padding": TOKENS["espacio_sm"], "textAlign": "right", "color": TOKENS["texto_muted"]}),
            html.Th("Cobre", style={"padding": TOKENS["espacio_sm"], "textAlign": "right", "color": TOKENS["texto_muted"]}),
        ]
    )
    filas = [
        _fila_comparativa("CAPEX inicial", _fmt_moneda(r["capex"]["pol"]), _fmt_moneda(r["capex"]["cobre"])),
        _fila_comparativa("OPEX anual", _fmt_moneda(r["opex"]["pol"]), _fmt_moneda(r["opex"]["cobre"])),
        _fila_comparativa("TCO a 5 años", _fmt_moneda(r["tco_5"]["pol"]), _fmt_moneda(r["tco_5"]["cobre"])),
        _fila_comparativa("TCO a 10 años", _fmt_moneda(r["tco_10"]["pol"]), _fmt_moneda(r["tco_10"]["cobre"]), destacar=True),
    ]
    tabla = html.Table(
        [html.Thead(encabezado), html.Tbody(filas)],
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"},
    )

    # Año de punto de equilibrio: entero, o "no cruza" cuando None (Req 11.4).
    be = r["break_even"]
    if be is None:
        be_valor = "No cruza"
        be_color = TOKENS["mayor"]
        be_sub = f"POL no alcanza al cobre en {r['horizonte_anios']} años"
        be_icono = "→"
    else:
        be_valor = f"Año {be}"
        be_color = TOKENS["en_linea"]
        be_sub = "POL cruza por debajo del TCO de cobre"
        be_icono = "✓"

    break_even = html.Div(
        [
            html.Div(
                "Punto de equilibrio (break-even)",
                style={
                    "fontSize": "12px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                },
            ),
            html.Div(
                [
                    html.Span(f"{be_icono} ", style={"color": be_color, "fontWeight": 700}),
                    html.Span(
                        be_valor,
                        style={
                            "fontSize": "22px",
                            "fontWeight": 700,
                            "fontFamily": TOKENS["fuente_mono"],
                            "color": be_color,
                        },
                    ),
                ]
            ),
            html.Div(be_sub, style={"fontSize": "12px", "color": TOKENS["texto_muted"]}),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_md"],
            "marginTop": TOKENS["espacio_md"],
        },
    )

    return html.Div([tabla, break_even])


# ===========================================================================
# R12 · Escalabilidad (holgura + slider + advertencia de hardware)
# ===========================================================================
def _celda_dimension(etiqueta: str, actual: int, nuevo: int, delta: int) -> html.Div:
    """Bloque compacto por dimensión: actual → nuevo (+delta)."""
    if delta > 0:
        delta_txt = f"+{delta}"
        delta_color = TOKENS["mayor"]
    else:
        delta_txt = "±0"
        delta_color = TOKENS["texto_muted"]
    return html.Div(
        [
            html.Div(
                etiqueta,
                style={
                    "fontSize": "11px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                },
            ),
            html.Div(
                [
                    html.Span(f"{actual:,}", style={"color": TOKENS["texto_muted"], "fontFamily": TOKENS["fuente_mono"]}),
                    html.Span(" → ", style={"color": TOKENS["texto_muted"]}),
                    html.Span(
                        f"{nuevo:,}",
                        style={"color": TOKENS["texto"], "fontWeight": 700, "fontFamily": TOKENS["fuente_mono"]},
                    ),
                    html.Span(f" ({delta_txt})", style={"color": delta_color, "fontSize": "12px"}),
                ],
                style={"fontSize": "16px"},
            ),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_sm"],
        },
    )


def _advertencia_hardware(recalculo: dict) -> html.Div:
    """Advertencia visible cuando se requiere hardware adicional (Req 12.4)."""
    if recalculo["requiere_hardware_adicional"]:
        piezas = []
        if recalculo["tarjetas_adicionales"] > 0:
            piezas.append(f"{recalculo['tarjetas_adicionales']} tarjeta(s)")
        if recalculo["chasis_adicionales"] > 0:
            piezas.append(f"{recalculo['chasis_adicionales']} chasis")
        detalle = " y ".join(piezas)
        return html.Div(
            [
                html.Span("⚠ ", style={"color": TOKENS["critica"], "fontWeight": 700, "fontSize": "16px"}),
                html.Span(
                    f"Requiere hardware adicional: {detalle}.",
                    style={"color": TOKENS["critica"], "fontWeight": 600},
                ),
            ],
            style={
                "backgroundColor": TOKENS["bg_elevated"],
                "border": f"1px solid {TOKENS['critica']}",
                "borderRadius": TOKENS["radio"],
                "padding": TOKENS["espacio_md"],
                "marginTop": TOKENS["espacio_md"],
            },
        )
    return html.Div(
        [
            html.Span("✓ ", style={"color": TOKENS["en_linea"], "fontWeight": 700}),
            html.Span(
                "El crecimiento cabe en el hardware instalado (sin tarjetas ni chasis nuevos).",
                style={"color": TOKENS["en_linea"]},
            ),
        ],
        style={
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "padding": TOKENS["espacio_md"],
            "marginTop": TOKENS["espacio_md"],
        },
    )


def _contenido_escalabilidad(estadio: Estadio | None, factor: float) -> html.Div:
    """Panel de escalabilidad para un ``factor`` de crecimiento (Req 12.2/12.3/12.5)."""
    if estadio is None:
        return _indicador_error("Inventario no disponible para este estadio.")

    holgura = economics.holgura_onts(estadio)
    recalculo = economics.recalcular_dimensionamiento(estadio, factor)
    actual = recalculo["actual"]
    nuevo = recalculo["nuevo"]
    delta = recalculo["delta"]

    holgura_kpi = valor_kpi(
        "ONTs adicionales antes de una tarjeta nueva",
        _fmt_entero(holgura),
        "ONTs",
        color=TOKENS["acento"],
    )

    dimensiones = dbc.Row(
        [
            dbc.Col(_celda_dimension("ONTs", actual["num_onts"], nuevo["num_onts"], delta["num_onts"]), width=6),
            dbc.Col(_celda_dimension("Árboles", actual["num_arboles"], nuevo["num_arboles"], delta["num_arboles"]), width=6),
            dbc.Col(_celda_dimension("Puertos", actual["num_puertos"], nuevo["num_puertos"], delta["num_puertos"]), width=4),
            dbc.Col(_celda_dimension("Tarjetas", actual["num_tarjetas"], nuevo["num_tarjetas"], delta["num_tarjetas"]), width=4),
            dbc.Col(_celda_dimension("Chasis", actual["num_chasis"], nuevo["num_chasis"], delta["num_chasis"]), width=4),
        ],
        className="g-2",
    )

    resumen_factor = html.Div(
        f"Crecimiento de aforo aplicado: +{factor * 100:.0f}%",
        style={
            "fontSize": "13px",
            "color": TOKENS["texto_muted"],
            "marginTop": TOKENS["espacio_sm"],
            "marginBottom": TOKENS["espacio_sm"],
        },
    )

    return html.Div(
        [
            holgura_kpi,
            resumen_factor,
            dimensiones,
            _advertencia_hardware(recalculo),
        ]
    )


def _controles_escalabilidad() -> html.Div:
    """Slider de crecimiento de aforo (R12, Req 12.3)."""
    return html.Div(
        [
            html.Div(
                "Factor de crecimiento de aforo",
                style={
                    "fontSize": "12px",
                    "color": TOKENS["texto_muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                    "marginBottom": TOKENS["espacio_sm"],
                },
            ),
            dcc.Slider(
                id=ID_SLIDER_AFORO,
                min=0,
                max=100,
                step=5,
                value=20,  # arranque en el caso +20% requerido (Req 12.5)
                marks=_MARCAS_AFORO,
                tooltip={"placement": "bottom", "always_visible": False},
            ),
        ],
        style={"marginBottom": TOKENS["espacio_md"]},
    )


# ===========================================================================
# R13 · SLA (disponibilidad por servicio vs objetivo, presupuesto consumido)
# ===========================================================================
def _onts_por_servicio(estadio: Estadio) -> dict[Servicio, list]:
    """Agrupa todas las ONTs del estadio por servicio (orden interno irrelevante)."""
    grupos: dict[Servicio, list] = {}
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            for arbol in puerto.arboles:
                for ont in arbol.onts:
                    grupos.setdefault(ont.servicio, []).append(ont)
    return grupos


def _proteccion_representativa(onts: list) -> Proteccion:
    """Protección más frecuente entre las ONTs del servicio (para el objetivo SLA).

    Un servicio puede mezclar esquemas de protección; el objetivo contractual se
    toma del esquema **dominante** (moda). Ante empate, se elige de forma
    determinista el de mayor exigencia disponible.
    """
    if not onts:
        return Proteccion.NINGUNA
    conteo = Counter(o.proteccion for o in onts)
    max_freq = max(conteo.values())
    candidatos = [p for p, c in conteo.items() if c == max_freq]
    # Preferir el más exigente ante empate: TYPE_C > TYPE_B > NINGUNA.
    orden = {Proteccion.TYPE_C: 3, Proteccion.TYPE_B: 2, Proteccion.NINGUNA: 1}
    return max(candidatos, key=lambda p: orden.get(p, 0))


def _fila_sla(servicio: Servicio, onts: list) -> html.Tr:
    """Fila del panel SLA: disponibilidad observada vs objetivo + presupuesto."""
    proteccion = _proteccion_representativa(onts)
    # El objetivo contractual por protección vive en sim.state (tabla única).
    from sim.state import objetivo_sla as _objetivo_sla

    objetivo = _objetivo_sla(proteccion)
    disponibilidad = disponibilidad_servicio(onts)
    consumido = economics.presupuesto_consumido(onts, objetivo)
    presupuesto_min = economics.presupuesto_indisponibilidad_min(objetivo)

    cumple = disponibilidad >= objetivo - 0.005  # tolerancia de un solo lado (§6.5)
    color_disp = TOKENS["en_linea"] if cumple else TOKENS["critica"]
    icono_disp = "✓" if cumple else "✕"

    if consumido == float("inf"):
        consumido_txt = "∞"
        color_cons = TOKENS["critica"]
    else:
        consumido_txt = f"{consumido * 100:.1f}%"
        if consumido > 1.0:
            color_cons = TOKENS["critica"]
        elif consumido >= 0.5:
            color_cons = TOKENS["mayor"]
        else:
            color_cons = TOKENS["en_linea"]

    etiqueta_prot = {
        Proteccion.TYPE_C: "Type C",
        Proteccion.TYPE_B: "Type B",
        Proteccion.NINGUNA: "Ninguna",
    }[proteccion]

    celda = lambda contenido, **extra: html.Td(
        contenido,
        style={"padding": TOKENS["espacio_sm"], "fontSize": "13px", **extra},
    )

    return html.Tr(
        [
            celda(servicio.value.capitalize(), color=TOKENS["texto"], fontWeight=600),
            celda(etiqueta_prot, color=TOKENS["texto_muted"], textAlign="center"),
            celda(f"{objetivo * 100:.3f}%", color=TOKENS["texto_muted"], textAlign="right", fontFamily=TOKENS["fuente_mono"]),
            html.Td(
                [
                    html.Span(f"{icono_disp} ", style={"color": color_disp, "fontWeight": 700}),
                    html.Span(f"{disponibilidad * 100:.3f}%", style={"color": color_disp, "fontFamily": TOKENS["fuente_mono"]}),
                ],
                style={"padding": TOKENS["espacio_sm"], "textAlign": "right", "fontSize": "13px"},
            ),
            celda(f"{presupuesto_min:.1f} min/año", color=TOKENS["texto_muted"], textAlign="right", fontFamily=TOKENS["fuente_mono"]),
            celda(consumido_txt, color=color_cons, textAlign="right", fontFamily=TOKENS["fuente_mono"], fontWeight=700),
        ],
        style={"borderBottom": f"1px solid {TOKENS['borde']}"},
    )


def _contenido_sla(estadio: Estadio | None) -> html.Div:
    """Tabla de SLA por servicio en orden fijo (Req 13.1/13.4, Req 9.5)."""
    if estadio is None:
        return _indicador_error("Estado en vivo no disponible para el SLA.")

    grupos = _onts_por_servicio(estadio)
    if not grupos:
        return _indicador_error("Sin ONTs en el inventario para calcular disponibilidad.")

    th = lambda t, **extra: html.Th(
        t,
        style={"padding": TOKENS["espacio_sm"], "color": TOKENS["texto_muted"], "fontSize": "12px", **extra},
    )
    encabezado = html.Tr(
        [
            th("Servicio", textAlign="left"),
            th("Protección", textAlign="center"),
            th("Objetivo", textAlign="right"),
            th("Observada", textAlign="right"),
            th("Presupuesto", textAlign="right"),
            th("Consumido", textAlign="right"),
        ]
    )

    # Orden de servicios fijo (Req 9.5): recorrer ORDEN_SERVICIOS y mapear al enum.
    filas = []
    for nombre in ORDEN_SERVICIOS:
        try:
            servicio = Servicio(nombre)
        except ValueError:
            continue
        onts = grupos.get(servicio)
        if not onts:
            continue
        filas.append(_fila_sla(servicio, onts))

    if not filas:
        return _indicador_error("Sin servicios con ONTs para mostrar.")

    tabla = html.Table(
        [html.Thead(encabezado), html.Tbody(filas)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )
    return html.Div(tabla)


# ===========================================================================
# Mapa de planta por zonas (Req 5)
# ===========================================================================
# Estado agregado por zona: color (token) + patrón de criticidad + ícono en el
# tooltip. El color NUNCA es el único canal (Req 5.4): las zonas críticas llevan
# además el patrón ``patron-critico`` definido en el SVG.
_ICONO_ESTADO_ZONA = {
    Estado.EN_LINEA: "●",
    Estado.DEGRADADO: "◐",
    Estado.FUERA: "■",
    Estado.RESERVA: "○",
}
_TOKEN_ESTADO_ZONA = {
    Estado.EN_LINEA: "en_linea",
    Estado.DEGRADADO: "degradado",
    Estado.FUERA: "fuera",
    Estado.RESERVA: "reserva",
}

# Mapa zona (data-zona en el SVG) -> servicios cuya salud agrega esa zona.
# Las zonas sin servicio propio (tribunas, accesos, estacionamiento) agregan la
# salud global de la red. El estado agregado de una zona es el PEOR estado de
# las ONTs asociadas (FUERA > DEGRADADO > RESERVA > EN_LINEA).
_ZONA_SERVICIOS: dict[str, tuple[Servicio, ...]] = {
    "palcos_nororiente": (Servicio.PALCOS,),
    "palcos_norponiente": (Servicio.PALCOS,),
    "prensa": (Servicio.PRENSA,),
    "sala_olt": (Servicio.MACRO, Servicio.VOZ),
    "acceso_norte": (Servicio.TORNIQUETES,),
    "acceso_sur": (Servicio.TORNIQUETES,),
    "acceso_poniente": (Servicio.TORNIQUETES,),
    "acceso_oriente": (Servicio.TORNIQUETES,),
    "tribuna_norte": (Servicio.WIFI, Servicio.CCTV, Servicio.POS),
    "tribuna_sur": (Servicio.WIFI, Servicio.CCTV, Servicio.POS),
    "tribuna_poniente": (Servicio.WIFI, Servicio.CCTV, Servicio.POS),
    "tribuna_oriente": (Servicio.WIFI, Servicio.CCTV, Servicio.SIGNAGE),
    "estacionamiento": (Servicio.CCTV,),
    "cancha": (Servicio.SIGNAGE, Servicio.MACRO),
}

# Prioridad para "peor estado" (mayor = peor).
_PRIORIDAD_ESTADO = {
    Estado.EN_LINEA: 0,
    Estado.RESERVA: 1,
    Estado.DEGRADADO: 2,
    Estado.FUERA: 3,
}


def _estado_agregado_zona(zona: str, grupos: dict[Servicio, list]) -> tuple[Estado, dict]:
    """Estado agregado (peor) y métricas de una zona a partir de sus servicios.

    Devuelve el peor estado entre las ONTs de los servicios asociados a la zona
    y un dict de métricas (conteo por estado, total, disponibilidad observada)
    para el tooltip. Si la zona no tiene servicios mapeados, agrega TODAS las
    ONTs (salud global).
    """
    servicios = _ZONA_SERVICIOS.get(zona)
    if servicios:
        onts = [o for s in servicios for o in grupos.get(s, [])]
    else:
        onts = [o for lista in grupos.values() for o in lista]

    if not onts:
        return Estado.EN_LINEA, {"total": 0, "fuera": 0, "degradado": 0, "disponibilidad": 1.0}

    peor = max((o.estado for o in onts), key=lambda e: _PRIORIDAD_ESTADO.get(e, 0))
    fuera = sum(1 for o in onts if o.estado == Estado.FUERA)
    degradado = sum(1 for o in onts if o.estado == Estado.DEGRADADO)
    metricas = {
        "total": len(onts),
        "fuera": fuera,
        "degradado": degradado,
        "disponibilidad": disponibilidad_servicio(onts),
    }
    return peor, metricas


def _leyenda_zonas() -> html.Div:
    """Leyenda de estados de zona: color + ícono + texto (Req 5.4, §9.6)."""
    items = [
        (Estado.EN_LINEA, "En línea"),
        (Estado.DEGRADADO, "Degradado"),
        (Estado.FUERA, "Fuera / crítico"),
        (Estado.RESERVA, "Reserva"),
    ]
    hijos = []
    for estado, texto in items:
        token = _TOKEN_ESTADO_ZONA[estado]
        hijos.append(
            html.Span(
                [
                    html.Span(f"{_ICONO_ESTADO_ZONA[estado]} ", style={"color": TOKENS[token], "fontWeight": 700}),
                    html.Span(texto, style={"color": TOKENS["texto_muted"]}),
                ],
                style={"marginRight": TOKENS["espacio_md"], "fontSize": "12px"},
            )
        )
    hijos.append(
        html.Span(
            "▨ patrón = zona crítica (canal adicional al color)",
            style={"color": TOKENS["texto_muted"], "fontSize": "12px", "fontStyle": "italic"},
        )
    )
    return html.Div(hijos, style={"marginTop": TOKENS["espacio_sm"]})


def _cargar_svg_planta() -> str | None:
    """Lee el contenido crudo del SVG de planta, o ``None`` si no puede leerse."""
    from pathlib import Path

    ruta = Path(__file__).resolve().parent.parent.parent / "data" / "stadium_map.svg"
    try:
        return ruta.read_text(encoding="utf-8")
    except OSError:
        return None


_ESTADO_TEXTO = {
    Estado.EN_LINEA: "En línea",
    Estado.DEGRADADO: "Degradado",
    Estado.FUERA: "Fuera / crítico",
    Estado.RESERVA: "Reserva",
}


def _colorear_svg(svg: str, estados: dict[str, tuple[Estado, dict]]) -> str:
    """Inyecta relleno por estado, patrón de criticidad y tooltip por zona.

    Para cada zona (``id="zona-..."`` / ``data-zona="X"``) del SVG:

    * fija el ``fill`` al color del token del estado agregado (Req 5.2);
    * si el estado es FUERA (crítico) añade el patrón ``patron-critico`` ya
      definido en el SVG como **capa superpuesta**, un canal **adicional al
      color** (Req 5.4);
    * reescribe el ``<title>`` de la zona con las métricas para el tooltip nativo
      (Req 5.3).

    Se parsea el SVG con ``xml.etree.ElementTree`` (robusto y sin backtracking).
    Es tolerante: una zona no encontrada se ignora. Si el XML no puede parsearse
    devuelve el SVG original sin recolorear (fail-soft, Req 5.5/15.3).
    """
    import xml.etree.ElementTree as ET

    svg_ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", svg_ns)

    try:
        raiz = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    fill_por_estado = {e: TOKENS[_TOKEN_ESTADO_ZONA[e]] for e in _TOKEN_ESTADO_ZONA}

    # Indexar elementos por id para localizar cada zona en una sola pasada.
    por_id: dict[str, ET.Element] = {}
    padre_de: dict[ET.Element, ET.Element] = {}
    for padre in raiz.iter():
        for hijo in list(padre):
            padre_de[hijo] = padre
            _id = hijo.get("id")
            if _id:
                por_id[_id] = hijo

    superposiciones: list[tuple[ET.Element, ET.Element]] = []

    for zona, (estado, metricas) in estados.items():
        elem_id = f"zona-{zona.replace('_', '-')}"
        elem = por_id.get(elem_id)
        if elem is None:
            continue

        color = fill_por_estado.get(estado, TOKENS["reserva"])

        # 1) Fijar el fill de la zona (sobrescribe el placeholder de la cancha).
        elem.set("style", f"fill:{color};")
        elem.set("fill", color)

        # 2) Reescribir/crear el <title> con las métricas (tooltip nativo).
        total = metricas.get("total", 0)
        disp = metricas.get("disponibilidad", 1.0)
        fuera = metricas.get("fuera", 0)
        degradado = metricas.get("degradado", 0)
        tooltip = (
            f"{zona.replace('_', ' ').title()} · {_ESTADO_TEXTO[estado]} · "
            f"{total} ONTs · disp {disp * 100:.2f}% · "
            f"fuera {fuera} · degradadas {degradado}"
        )
        titulo = elem.find(f"{{{svg_ns}}}title")
        if titulo is None:
            titulo = ET.SubElement(elem, f"{{{svg_ns}}}title")
        titulo.text = tooltip

        # 3) Canal adicional al color para zona crítica (FUERA): superponer una
        # copia de la geometría rellena con el patrón ``patron-critico``. El
        # patrón (líneas diagonales) distingue la zona sin depender del color.
        if estado == Estado.FUERA:
            copia = ET.Element(elem.tag)
            for attr in ("d", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "points"):
                if elem.get(attr) is not None:
                    copia.set(attr, elem.get(attr))
            copia.set("fill", "url(#patron-critico)")
            copia.set("stroke", "none")
            copia.set("pointer-events", "none")
            copia.set("data-critico", "1")
            padre = padre_de.get(elem, raiz)
            superposiciones.append((padre, copia))

    # Insertar las superposiciones de patrón después de sus zonas (encima).
    for padre, copia in superposiciones:
        padre.append(copia)

    return ET.tostring(raiz, encoding="unicode")


def _panel_mapa_zonas(estadio: Estadio | None) -> html.Div:
    """Mapa de planta por zonas coloreado por estado (Req 5.1–5.5)."""
    svg = _cargar_svg_planta()
    if svg is None:
        # Req 5.5: si el SVG no se renderiza, no hay canal de criticidad por
        # zona; se muestra explícitamente un indicador de esa carencia.
        return _indicador_error(
            "Mapa de planta no disponible: sin canal de criticidad por zona."
        )

    if estadio is not None:
        grupos = _onts_por_servicio(estadio)
    else:
        grupos = {}

    estados = {zona: _estado_agregado_zona(zona, grupos) for zona in _ZONA_SERVICIOS}
    svg_coloreado = _colorear_svg(svg, estados)

    contenedor_svg = html.Div(
        dcc.Markdown(
            svg_coloreado,
            dangerously_allow_html=True,
        ),
        style={"width": "100%", "overflow": "auto"},
    )
    return html.Div([contenedor_svg, _leyenda_zonas()])


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def layout() -> html.Div:
    """Retorna la vista de Negocio sobre una retícula de 12 columnas.

    Todos los paneles arrancan poblados con el inventario/precios y el estado en
    vivo actuales (Req 15.1/15.2). El slider de escalabilidad arranca en +20%
    (Req 12.5) y su resultado, junto con el SLA, se refrescan por callback.
    """
    estadio_id = _estadio_activo_id()
    estadio_inventario = _topologia(estadio_id)
    estadio_vivo = _estadio_vivo() or estadio_inventario
    precios = _precios()

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "R10 · Propuesta de valor",
                            _panel_propuesta(estadio_inventario, precios),
                            subtitulo="Tres cifras derivadas del inventario (POL vs cobre)",
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
                            "R11 · Modelo económico",
                            _panel_economico(estadio_inventario, precios),
                            subtitulo="CAPEX / OPEX / TCO a 5 y 10 años · punto de equilibrio",
                        ),
                        width=6,
                    ),
                    dbc.Col(
                        panel(
                            "R12 · Escalabilidad",
                            _controles_escalabilidad(),
                            html.Div(
                                _contenido_escalabilidad(estadio_inventario, 0.20),
                                id=ID_ESCALABILIDAD,
                            ),
                            subtitulo="Holgura de capacidad y recálculo de dimensionamiento",
                        ),
                        width=6,
                    ),
                ],
                className="g-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "R13 · SLA por servicio",
                            html.Div(
                                _contenido_sla(estadio_vivo),
                                id=ID_SLA,
                            ),
                            subtitulo="Disponibilidad observada vs objetivo · presupuesto consumido",
                        ),
                        width=6,
                    ),
                    dbc.Col(
                        panel(
                            "Mapa de planta por zonas",
                            _panel_mapa_zonas(estadio_vivo),
                            subtitulo="Coloreado por estado agregado · patrón para zonas críticas",
                        ),
                        width=6,
                    ),
                ],
                className="g-3",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Callbacks (registrados aquí con ids locales; sin Outputs duplicados)
# ---------------------------------------------------------------------------
@callback(
    Output(ID_ESCALABILIDAD, "children"),
    Input(ID_SLIDER_AFORO, "value"),
)
def _refrescar_escalabilidad(valor_pct):
    """Recalcula el panel de escalabilidad al mover el slider (Req 12.3/12.4/12.5).

    Convierte el porcentaje del slider a fracción y delega en
    ``sim.economics.recalcular_dimensionamiento`` (vía :func:`_contenido_escalabilidad`).
    No calcula dimensionamiento aquí (design §3.1); solo formatea.
    """
    factor = (valor_pct or 0) / 100.0
    estadio = _topologia(_estadio_activo_id())
    return _contenido_escalabilidad(estadio, factor)


@callback(
    Output(ID_SLA, "children"),
    Input(live.INTERVALO_ID, "n_intervals"),
)
def _refrescar_sla(_n):
    """Refresca el panel de SLA cada tick: la disponibilidad avanza con la sim (Req 13.1).

    Lee las ONTs del estado en vivo (con ticks acumulados) y reconstruye la
    tabla de disponibilidad observada vs objetivo y presupuesto consumido. No
    avanza el motor (lo hace el callback maestro del intervalo).
    """
    return _contenido_sla(_estadio_vivo())
