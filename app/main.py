"""Punto de entrada de la Capa de Presentación (design §3.3, §9.1).

Crea la aplicación Dash con ``dash-bootstrap-components`` (retícula de 12
columnas), instala el layout raíz y expone ``server`` a nivel de módulo para
gunicorn. El ``CMD`` del Dockerfile referencia ``app.main:server``:

    gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8050 app.main:server

Un solo worker es obligatorio: el estado vive en memoria del proceso (design
§3.2/§3.3).

Ruteo de vistas: un único callback mínimo conmuta el contenido de la región de
vista según la pestaña activa, conservando la barra superior persistente
(Requirement 1.4). El bucle en vivo y los controles se registran desde
``app/callbacks.py`` mediante :func:`app.callbacks.register_callbacks` (tarea
5.8): cablean el único ``dcc.Interval`` al ``tick`` y los controles del operador
al estado compartido.
"""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output

from app.callbacks import register_callbacks
from app.layout import render_view, serve_layout

app = dash.Dash(
    __name__,
    title="POL Command Center",
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)

# Layout raíz (función: se evalúa en cada carga de página).
app.layout = serve_layout

# WSGI callable para gunicorn (app.main:server).
server = app.server


@app.callback(
    Output("contenido-vista", "children"),
    Input("nav-vistas", "value"),
)
def _rutear_vista(tab: str):
    """Renderiza la vista seleccionada en la región de contenido."""
    return render_view(tab)


# Registra el bucle en vivo y los controles (tarea 5.8): cablea el único
# dcc.Interval al tick del motor y los controles del operador al estado
# compartido (design §4). Se hace tras crear la app para colgar el callback
# maestro de su ``callback_map``.
register_callbacks(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
