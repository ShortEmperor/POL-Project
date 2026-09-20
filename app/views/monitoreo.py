"""Vista 2: Monitoreo (design §9.3, Requirement 3 y 15).

Compone tres regiones sobre la retícula de 12 columnas:

* **Árbol de inventario** navegable con la jerarquía
  ``Estadio → OLT → Tarjeta → Puerto PON → Árbol PON → ONT`` (Req 3.1).
  La navegación es de tipo *drill-down* con miga de pan: los niveles que solo
  tienen un hijo (el OLT del estadio y el árbol único de un puerto poblado) se
  saltan automáticamente, de modo que **cualquier ONT se alcanza en un máximo
  de tres selecciones** — Tarjeta → Puerto → ONT (Req 3.4).
* **Panel de detalle** del elemento seleccionado con estado, protección, ancho
  de banda, potencia óptica, disponibilidad y alarmas. Si un dato no está
  disponible en el estado en reposo se muestra un valor por defecto o un
  indicador, nunca un panel vacío (Req 3.2, 3.3, 15.2, 15.3).
* **Heatmap de puertos 11x10** (110 puertos del Azteca) coloreado por
  utilización con la escala ``util_ok/util_medio/util_alto/util_critico``
  (Req 3.5, design §9.6). El color nunca es el único canal: cada celda lleva
  además su índice y el porcentaje aparece en el detalle/leyenda.

Notas de integración:

* Regla de integración visual (Req 9.3): este módulo **no declara ningún color
  literal**; todo estilo proviene de :data:`app.theme.TOKENS`.
* Los callbacks específicos de Monitoreo se registran aquí con ``@callback``
  para no colisionar con ``app/callbacks.py`` (bucle en vivo, tarea 5.8).
* La vista renderiza solo con la topología (``sim.topology.cargar_topologia``);
  no requiere un ``SimState`` en vivo. En reposo la utilización derivada de
  ``bw_asignado`` puede ser 0: se muestra como tal con su indicador.
"""

from __future__ import annotations

from dash import ALL, Input, Output, callback, ctx, html

import dash_bootstrap_components as dbc

from app.components import panel, placeholder_texto
from app.theme import TOKENS
from sim.state import Estadio, Estado, Proteccion
from sim.topology import ESTADIOS_POR_DEFECTO, TopologiaInvalida, cargar_topologia

# ---------------------------------------------------------------------------
# Cache de topología por estadio (solo lectura, se carga una vez por estadio).
# ---------------------------------------------------------------------------
_CACHE_TOPOLOGIA: dict[str, Estadio] = {}

# Indicador por defecto para un dato ausente (Req 15.3).
_SIN_DATO = "—"


def _topologia(estadio_id: str) -> Estadio | None:
    """Devuelve el :class:`Estadio` cargado, cacheado por id.

    Si la topología no puede cargarse (archivo faltante en un entorno de
    ejecución acotado, por ejemplo) devuelve ``None`` para que la vista pueda
    renderizar con indicadores en lugar de propagar el error a la UI.
    """
    if estadio_id in _CACHE_TOPOLOGIA:
        return _CACHE_TOPOLOGIA[estadio_id]
    try:
        estadio = cargar_topologia(estadio_id)
    except TopologiaInvalida:
        return None
    _CACHE_TOPOLOGIA[estadio_id] = estadio
    return estadio


# ---------------------------------------------------------------------------
# Utilidades de estado y utilización
# ---------------------------------------------------------------------------
_COLOR_ESTADO = {
    Estado.EN_LINEA: "en_linea",
    Estado.DEGRADADO: "degradado",
    Estado.FUERA: "fuera",
    Estado.RESERVA: "reserva",
}

_ICONO_ESTADO = {
    Estado.EN_LINEA: "●",
    Estado.DEGRADADO: "◐",
    Estado.FUERA: "■",
    Estado.RESERVA: "○",
}

_ETIQUETA_PROTECCION = {
    Proteccion.NINGUNA: "Ninguna",
    Proteccion.TYPE_B: "Type B",
    Proteccion.TYPE_C: "Type C",
}


def _color_estado(estado: Estado) -> str:
    return TOKENS[_COLOR_ESTADO.get(estado, "texto_muted")]


def _carga_puerto(puerto) -> float:
    """Carga instantánea (Mbps) de un puerto = Σ bw_asignado de sus ONTs.

    En reposo (sin bucle en vivo) los ``bw_asignado_mbps`` valen 0, por lo que
    la carga es 0 y la utilización se muestra como 0% con su indicador.
    """
    return sum(
        ont.bw_asignado_mbps
        for arbol in puerto.arboles
        for ont in arbol.onts
    )


def _utilizacion_puerto(puerto) -> float | None:
    """Utilización del puerto en 0..1, o ``None`` si no aplica.

    Devuelve ``None`` para puertos de reserva o sin capacidad utilizable
    (no hay una utilización significativa que colorear).
    """
    if puerto.es_reserva or puerto.capacidad_util_mbps <= 0:
        return None
    return _carga_puerto(puerto) / puerto.capacidad_util_mbps


def _token_utilizacion(util: float | None) -> str:
    """Token de color de la escala de utilización (design §9.6).

    Umbrales alineados con el catálogo de alarmas (§8.2): PON-001 a 60% y
    PON-002 a 90%.
    """
    if util is None:
        return "reserva"
    if util >= 0.90:
        return "util_critico"
    if util >= 0.60:
        return "util_alto"
    if util >= 0.40:
        return "util_medio"
    return "util_ok"


# ---------------------------------------------------------------------------
# Recorridos de la jerarquía
# ---------------------------------------------------------------------------
def _puertos(estadio: Estadio) -> list:
    """Lista plana de los puertos del estadio en orden de tarjeta/puerto."""
    return [
        puerto
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
    ]


# ---------------------------------------------------------------------------
# Fila / ítem del árbol de inventario
# ---------------------------------------------------------------------------
def _fila(texto: str, estado: Estado | None, *, sangria: int, mono: bool = True) -> html.Div:
    hijos = []
    if estado is not None:
        hijos.append(
            html.Span(
                f"{_ICONO_ESTADO.get(estado, '·')} ",
                style={"color": _color_estado(estado)},
            )
        )
    hijos.append(html.Span(texto))
    return html.Div(
        hijos,
        style={
            "paddingLeft": f"{sangria * 14}px",
            "paddingTop": TOKENS["espacio_xs"],
            "paddingBottom": TOKENS["espacio_xs"],
            "fontFamily": TOKENS["fuente_mono"] if mono else TOKENS["fuente"],
            "fontSize": "13px",
            "color": TOKENS["texto"],
        },
    )


def _arbol_inventario(estadio: Estadio | None) -> html.Div:
    """Árbol de inventario completo del estadio activo (Req 3.1).

    Muestra la jerarquía Estadio → OLT → Tarjeta → Puerto → Árbol → ONT. Para
    no listar las 1990 ONTs de golpe, cada árbol resume su cantidad de ONTs y
    su primer/último id; la selección fina se realiza con el drill-down de
    detalle (que alcanza cualquier ONT en ≤ 3 selecciones, Req 3.4).
    """
    if estadio is None:
        return placeholder_texto(
            "Topología no disponible; se poblará cuando el estadio cargue."
        )

    filas: list = [
        _fila(f"{estadio.nombre}  ({estadio.num_onts} ONTs)", None, sangria=0, mono=False),
        _fila(f"OLT {estadio.olt.id}", estadio.olt.estado, sangria=1),
    ]
    for tarjeta in estadio.olt.tarjetas:
        filas.append(
            _fila(f"Tarjeta {tarjeta.id}  ({tarjeta.num_puertos} puertos)", tarjeta.estado, sangria=2)
        )
        for puerto in tarjeta.puertos:
            etiqueta_puerto = puerto.id
            if puerto.es_reserva:
                etiqueta_puerto += "  · reserva"
            filas.append(_fila(etiqueta_puerto, puerto.estado, sangria=3))
            for arbol in puerto.arboles:
                filas.append(
                    _fila(
                        f"Árbol {arbol.id}  (1:{arbol.ratio_split}, {len(arbol.onts)} ONTs)",
                        arbol.estado,
                        sangria=4,
                    )
                )
    return html.Div(
        filas,
        style={
            "maxHeight": "620px",
            "overflowY": "auto",
        },
    )


# ---------------------------------------------------------------------------
# Navegador drill-down (≤ 3 selecciones a una ONT) — Req 3.4
# ---------------------------------------------------------------------------
def _boton_drill(texto: str, valor: str, estado: Estado | None = None) -> html.Button:
    hijos = []
    if estado is not None:
        hijos.append(html.Span(f"{_ICONO_ESTADO.get(estado, '·')} ", style={"color": _color_estado(estado)}))
    hijos.append(html.Span(texto))
    return html.Button(
        hijos,
        id={"tipo": "drill-monitoreo", "valor": valor},
        n_clicks=0,
        style={
            "display": "block",
            "width": "100%",
            "textAlign": "left",
            "backgroundColor": TOKENS["bg_elevated"],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["radio"],
            "color": TOKENS["texto"],
            "fontFamily": TOKENS["fuente_mono"],
            "fontSize": "13px",
            "padding": f"{TOKENS['espacio_sm']} {TOKENS['espacio_md']}",
            "marginBottom": TOKENS["espacio_xs"],
            "cursor": "pointer",
        },
    )


# ---------------------------------------------------------------------------
# Panel de detalle
# ---------------------------------------------------------------------------
def _fila_detalle(etiqueta: str, valor, *, color: str | None = None) -> html.Div:
    return html.Div(
        [
            html.Span(
                etiqueta,
                style={
                    "color": TOKENS["texto_muted"],
                    "width": "150px",
                    "display": "inline-block",
                },
            ),
            html.Span(
                str(valor),
                style={
                    "fontFamily": TOKENS["fuente_mono"],
                    "color": color or TOKENS["texto"],
                },
            ),
        ],
        style={"padding": f"{TOKENS['espacio_xs']} 0", "fontSize": "13px"},
    )


def _estado_texto(estado: Estado) -> html.Span:
    return html.Span(
        [
            html.Span(f"{_ICONO_ESTADO.get(estado, '·')} ", style={"color": _color_estado(estado)}),
            html.Span(estado.value, style={"color": _color_estado(estado)}),
        ]
    )


def _fmt(valor, unidad: str = "", *, dec: int = 1) -> str:
    if valor is None:
        return f"{_SIN_DATO} {unidad}".strip()
    if isinstance(valor, float):
        return f"{valor:.{dec}f} {unidad}".strip()
    return f"{valor} {unidad}".strip()


def _detalle_estadio(estadio: Estadio) -> html.Div:
    return html.Div(
        [
            _fila_detalle("Elemento", f"Estadio {estadio.nombre}"),
            _fila_detalle("Estado", _estado_texto(estadio.olt.estado)),
            _fila_detalle("Protección", _SIN_DATO),
            _fila_detalle("Ancho de banda", _fmt(None, "Mbps")),
            _fila_detalle("Potencia óptica", _fmt(None, "dBm")),
            _fila_detalle("Disponibilidad", _fmt(None, "%")),
            _fila_detalle("ONTs / Árboles", f"{estadio.num_onts} / {estadio.num_arboles}"),
            _fila_detalle("Puertos / Tarjetas", f"{estadio.num_puertos} / {estadio.num_tarjetas}"),
            _fila_detalle("Chasis", estadio.num_chasis),
            _fila_detalle("Alarmas", "Sin alarmas del elemento en reposo"),
        ]
    )


def _detalle_puerto(puerto) -> html.Div:
    util = _utilizacion_puerto(puerto)
    onts = [ont for arbol in puerto.arboles for ont in arbol.onts]
    return html.Div(
        [
            _fila_detalle("Elemento", f"Puerto {puerto.id}"),
            _fila_detalle("Estado", _estado_texto(puerto.estado)),
            _fila_detalle("Protección", "Reserva (Type B)" if puerto.es_reserva else "Activo"),
            _fila_detalle(
                "Ancho de banda",
                f"{_fmt(_carga_puerto(puerto), 'Mbps')} / {_fmt(puerto.capacidad_util_mbps, 'Mbps')}",
            ),
            _fila_detalle(
                "Utilización",
                _SIN_DATO if util is None else _fmt(util * 100.0, "%"),
                color=TOKENS[_token_utilizacion(util)],
            ),
            _fila_detalle("Potencia óptica", _fmt(None, "dBm")),
            _fila_detalle("Disponibilidad", _fmt(None, "%")),
            _fila_detalle("Árboles / ONTs", f"{len(puerto.arboles)} / {len(onts)}"),
            _fila_detalle("Alarmas", "Sin alarmas del elemento en reposo"),
        ]
    )


def _detalle_ont(ont, puerto) -> html.Div:
    disp = None  # sin ticks acumulados en reposo
    if ont.ticks_totales > 0:
        disp = ont.ticks_en_linea / ont.ticks_totales * 100.0
    util = _utilizacion_puerto(puerto)
    return html.Div(
        [
            _fila_detalle("Elemento", f"ONT {ont.id}"),
            _fila_detalle("Servicio", ont.servicio.value),
            _fila_detalle("Estado", _estado_texto(ont.estado)),
            _fila_detalle("Protección", _ETIQUETA_PROTECCION.get(ont.proteccion, ont.proteccion.value)),
            _fila_detalle("Ancho de banda", _fmt(ont.bw_asignado_mbps, "Mbps")),
            _fila_detalle("Potencia óptica", _fmt(ont.potencia_optica_dbm, "dBm")),
            _fila_detalle(
                "Utilización asociada",
                _SIN_DATO if util is None else _fmt(util * 100.0, "%"),
                color=TOKENS[_token_utilizacion(util)],
            ),
            _fila_detalle("Disponibilidad", _fmt(disp, "%")),
            _fila_detalle("Alarmas", "Sin alarmas del elemento en reposo"),
        ]
    )


# ---------------------------------------------------------------------------
# Heatmap de puertos 11x10 (110 puertos) — Req 3.5, design §9.6
# ---------------------------------------------------------------------------
def _celda_heatmap(indice: int, puerto) -> html.Div:
    util = _utilizacion_puerto(puerto)
    token = _token_utilizacion(util)
    # El color nunca es el único canal: cada celda muestra su índice (1..110).
    return html.Div(
        str(indice + 1),
        title=f"{puerto.id} · {'reserva' if util is None else f'{util * 100:.0f}%'}",
        style={
            "backgroundColor": TOKENS[token],
            "border": f"1px solid {TOKENS['borde']}",
            "borderRadius": TOKENS["espacio_xs"],
            "aspectRatio": "1 / 1",
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "fontFamily": TOKENS["fuente_mono"],
            "fontSize": "9px",
            "color": TOKENS["bg"],
            "opacity": 0.55 if util is None else 1.0,
        },
    )


def _leyenda_heatmap() -> html.Div:
    entradas = [
        ("util_ok", "< 40%"),
        ("util_medio", "40–60%"),
        ("util_alto", "60–90% · PON-001"),
        ("util_critico", "≥ 90% · PON-002"),
        ("reserva", "Reserva / sin carga"),
    ]
    items = [
        html.Div(
            [
                html.Span(
                    "■ ",
                    style={"color": TOKENS[token], "fontFamily": TOKENS["fuente_mono"]},
                ),
                html.Span(texto, style={"fontSize": "11px", "color": TOKENS["texto_muted"]}),
            ],
            style={"marginRight": TOKENS["espacio_md"]},
        )
        for token, texto in entradas
    ]
    return html.Div(
        items,
        style={"display": "flex", "flexWrap": "wrap", "marginTop": TOKENS["espacio_sm"]},
    )


def _heatmap(estadio: Estadio | None) -> html.Div:
    """Rejilla 11x10 (110 celdas) coloreada por utilización de puerto."""
    if estadio is None:
        return placeholder_texto("Topología no disponible para el heatmap de puertos.")

    puertos = _puertos(estadio)
    celdas = []
    for i in range(110):
        if i < len(puertos):
            celdas.append(_celda_heatmap(i, puertos[i]))
        else:
            # Rejilla siempre completa a 110 celdas aunque falten puertos.
            celdas.append(
                html.Div(
                    style={
                        "backgroundColor": TOKENS["bg_elevated"],
                        "border": f"1px solid {TOKENS['borde']}",
                        "borderRadius": TOKENS["espacio_xs"],
                        "aspectRatio": "1 / 1",
                    }
                )
            )

    en_reposo = all(_carga_puerto(p) == 0 for p in puertos)
    nota = (
        f"{len(puertos)} puertos PON · color = utilización"
        + (" (0% en reposo; se anima con la simulación en vivo)" if en_reposo else "")
    )
    return html.Div(
        [
            html.Div(
                celdas,
                style={
                    "display": "grid",
                    "gridTemplateColumns": "repeat(11, 1fr)",
                    "gap": TOKENS["espacio_xs"],
                },
            ),
            _leyenda_heatmap(),
            html.Div(
                nota,
                style={"color": TOKENS["texto_muted"], "fontSize": "12px", "marginTop": TOKENS["espacio_sm"]},
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Navegador de detalle (drill-down): resuelve la selección a ≤ 3 clics
# ---------------------------------------------------------------------------
def _navegador(estadio: Estadio | None, seleccion: str | None) -> html.Div:
    """Construye la miga de pan + botones del nivel actual y el detalle.

    ``seleccion`` codifica el camino: ``None`` (estadio), ``"t:<idx>"``
    (tarjeta), ``"p:<t_idx>:<p_idx>"`` (puerto) o
    ``"o:<t_idx>:<p_idx>:<o_idx>"`` (ONT). El OLT y el árbol único de un puerto
    se saltan, garantizando Tarjeta → Puerto → ONT en 3 selecciones (Req 3.4).
    """
    if estadio is None:
        return placeholder_texto("Seleccione un estadio con topología disponible.")

    tarjetas = estadio.olt.tarjetas
    partes = (seleccion or "").split(":") if seleccion else []
    nivel = partes[0] if partes else ""

    migas: list = [_boton_drill(f"◆ {estadio.nombre}", "raiz", estadio.olt.estado)]
    opciones: list = []
    detalle: html.Div

    if not seleccion:
        # Nivel estadio: elegir tarjeta (1er clic).
        detalle = _detalle_estadio(estadio)
        opciones = [
            _boton_drill(f"Tarjeta {t.id}", f"t:{i}", t.estado)
            for i, t in enumerate(tarjetas)
        ]
        titulo_opciones = "Seleccione una tarjeta"
    elif nivel == "t":
        ti = int(partes[1])
        tarjeta = tarjetas[ti]
        migas.append(_boton_drill(f"Tarjeta {tarjeta.id}", f"t:{ti}", tarjeta.estado))
        detalle = _detalle_estadio(estadio)
        opciones = [
            _boton_drill(
                f"{p.id}{' · reserva' if p.es_reserva else ''}", f"p:{ti}:{pi}", p.estado
            )
            for pi, p in enumerate(tarjeta.puertos)
        ]
        titulo_opciones = "Seleccione un puerto"
    elif nivel == "p":
        ti, pi = int(partes[1]), int(partes[2])
        tarjeta = tarjetas[ti]
        puerto = tarjeta.puertos[pi]
        migas.append(_boton_drill(f"Tarjeta {tarjeta.id}", f"t:{ti}", tarjeta.estado))
        migas.append(_boton_drill(puerto.id, f"p:{ti}:{pi}", puerto.estado))
        detalle = _detalle_puerto(puerto)
        onts = [ont for arbol in puerto.arboles for ont in arbol.onts]
        opciones = [
            _boton_drill(f"ONT {o.id} · {o.servicio.value}", f"o:{ti}:{pi}:{oi}", o.estado)
            for oi, o in enumerate(onts)
        ]
        titulo_opciones = "Seleccione una ONT" if onts else "Puerto de reserva (sin ONTs)"
    else:  # nivel == "o"
        ti, pi, oi = int(partes[1]), int(partes[2]), int(partes[3])
        tarjeta = tarjetas[ti]
        puerto = tarjeta.puertos[pi]
        onts = [ont for arbol in puerto.arboles for ont in arbol.onts]
        ont = onts[oi]
        migas.append(_boton_drill(f"Tarjeta {tarjeta.id}", f"t:{ti}", tarjeta.estado))
        migas.append(_boton_drill(puerto.id, f"p:{ti}:{pi}", puerto.estado))
        migas.append(_boton_drill(f"ONT {ont.id}", f"o:{ti}:{pi}:{oi}", ont.estado))
        detalle = _detalle_ont(ont, puerto)
        opciones = []
        titulo_opciones = ""

    contenido = [
        html.Div(
            migas,
            style={"marginBottom": TOKENS["espacio_md"]},
        ),
        detalle,
    ]
    if opciones:
        contenido.append(
            html.Div(
                titulo_opciones,
                style={
                    "color": TOKENS["texto_muted"],
                    "fontSize": "12px",
                    "textTransform": "uppercase",
                    "letterSpacing": "0.04em",
                    "marginTop": TOKENS["espacio_md"],
                    "marginBottom": TOKENS["espacio_sm"],
                },
            )
        )
        contenido.append(
            html.Div(opciones, style={"maxHeight": "300px", "overflowY": "auto"})
        )
    elif titulo_opciones:
        contenido.append(
            html.Div(
                titulo_opciones,
                style={"color": TOKENS["texto_muted"], "fontSize": "12px", "marginTop": TOKENS["espacio_md"]},
            )
        )
    return html.Div(contenido)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def layout(estadio_id: str = "azteca") -> html.Div:
    """Retorna la vista de Monitoreo poblada con la topología del estadio.

    Todos los paneles se pueblan con datos del estado actual (Req 15.2); donde
    un dato no existe en reposo se muestra un valor por defecto o indicador
    (Req 15.3). La vista renderiza aunque la topología no pueda cargarse.
    """
    if estadio_id not in ESTADIOS_POR_DEFECTO:
        estadio_id = "azteca"
    estadio = _topologia(estadio_id)

    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        panel(
                            "Árbol de inventario",
                            _arbol_inventario(estadio),
                            subtitulo="Estadio → OLT → Tarjeta → Puerto → Árbol → ONT",
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        panel(
                            "Detalle del elemento",
                            html.Div(
                                _navegador(estadio, None),
                                id="monitoreo-detalle",
                            ),
                            subtitulo="Navegue a una ONT en ≤ 3 selecciones",
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        panel("Heatmap de puertos", _heatmap(estadio)),
                        width=4,
                    ),
                ],
                className="g-3",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Callback de drill-down (registrado aquí, no en app/callbacks.py).
# ---------------------------------------------------------------------------
@callback(
    Output("monitoreo-detalle", "children"),
    Input({"tipo": "drill-monitoreo", "valor": ALL}, "n_clicks"),
    Input("selector-estadio", "value"),
    prevent_initial_call=True,
)
def _drill_detalle(_clicks, estadio_id):
    """Recalcula el panel de detalle según el botón de drill-down pulsado.

    Usa ``callback_context`` para identificar qué botón disparó el evento y
    reconstruye el navegador en el nivel correspondiente.
    """
    if estadio_id not in ESTADIOS_POR_DEFECTO:
        estadio_id = "azteca"
    estadio = _topologia(estadio_id)

    disparador = ctx.triggered_id
    seleccion: str | None = None
    if isinstance(disparador, dict) and disparador.get("tipo") == "drill-monitoreo":
        valor = disparador.get("valor")
        seleccion = None if valor in (None, "raiz") else valor
    # Si el disparador fue el selector de estadio, volvemos a la raíz.
    return _navegador(estadio, seleccion)
