"""Sistema visual del POL Command Center — fuente única de tokens de estilo.

Este módulo define el dict :data:`TOKENS` con todos los colores, la tipografía
y el espaciado usados por la Capa de Presentación (las cuatro vistas: Resumen,
Monitoreo, Simulación y Negocio).

Regla de integración visual (Requirement 9):

    NINGÚN color puede declararse fuera de este archivo.

Cualquier color, familia tipográfica o valor de espaciado que necesite una
vista, un componente o un callback DEBE tomarse de ``TOKENS``. Declarar un
color literal (por ejemplo ``"#f85149"`` o ``"red"``) en cualquier otro módulo
—layout, callbacks, vistas o utilidades— incumple el requisito de integración
visual (Requirement 9.3) y debe corregirse referenciando el token
correspondiente.

Notas de diseño:

- Tema oscuro (centro de comando).
- El color nunca es el único canal de información: cada estado se acompaña de
  un ícono o texto (accesibilidad para daltonismo).
"""

TOKENS = {
    # Colores base (tema oscuro)
    "bg":            "#0d1117",
    "bg_panel":      "#161b22",
    "bg_elevated":   "#1c2333",
    "borde":         "#30363d",
    "texto":         "#e6edf3",
    "texto_muted":   "#8b949e",
    # Severidades de alarma
    "critica":       "#f85149",
    "mayor":         "#ffa657",
    "menor":         "#e3b341",
    "aviso":         "#58a6ff",
    # Estados de red
    "en_linea":      "#3fb950",
    "degradado":     "#e3b341",
    "fuera":         "#f85149",
    "reserva":       "#8b949e",
    # Acento e interacción
    "acento":        "#4a90d9",
    "acento_hover":  "#6aa8e0",
    # Escala de utilización (heatmap)
    "util_ok":       "#3fb950",
    "util_medio":    "#e3b341",
    "util_alto":     "#ffa657",
    "util_critico":  "#f85149",
    # Tipografía y espaciado
    "fuente":        "Inter, system-ui, sans-serif",
    "fuente_mono":   "JetBrains Mono, monospace",
    "espacio_xs":    "4px",
    "espacio_sm":    "8px",
    "espacio_md":    "16px",
    "espacio_lg":    "24px",
    "radio":         "8px",
}
