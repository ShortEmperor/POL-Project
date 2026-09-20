# 04 — Modelo económico (`sim/economics.py`)

El modelo económico calcula CAPEX, OPEX anual, TCO y punto de equilibrio
(break-even) de la arquitectura POL frente a una arquitectura de cobre
equivalente, además de la propuesta de valor. Alimenta la vista de **Negocio**.

## Dos principios

1. **Los precios nunca se hardcodean.** Se leen de `data/prices.yaml` en cada
   cálculo (`cargar_precios`). Editar el YAML actualiza el panel económico sin
   tocar código.
2. **Las cantidades se derivan del inventario de topología** (`Estadio`). Al
   cambiar la topología, las cifras se recalculan solas.

## Contrato fail-fast

`cargar_precios()` lanza `PreciosInvalidos` (excepción **no recuperable**) si
`prices.yaml`:

- falta,
- no se puede parsear como YAML,
- su raíz no es un objeto, o
- le faltan secciones/campos requeridos (`capex`, `opex_anual`,
  `comparativa_cobre`, `horizonte_anios` y sus campos numéricos).

La idea es abortar antes de mostrar cifras inventadas.

## CAPEX

### POL (`capex_total`)

```
CAPEX_POL = num_chasis   · olt_chasis
          + num_tarjetas · tarjeta_pon
          + num_onts     · ont
          + num_arboles  · splitter_1x32      (un splitter por árbol PON)
          + metros_fibra · fibra_por_metro
```

El inventario no incluye longitudes de fibra, así que
`metros_fibra = num_arboles · metros_por_arbol`, donde `metros_por_arbol` es un
supuesto opcional de `prices.yaml` (0 por defecto → aporte de fibra nulo, sin
inventar una cifra).

### Cobre (`capex_cobre`, comparativa)

```
CAPEX_cobre = num_switches_cobre · switch_acceso
            + num_onts           · cableado_utp_por_puerto
```

`num_switches_cobre = ceil(num_onts / puertos_por_switch)` (un puerto de switch
por cada puerto de servicio terminado; `puertos_por_switch` por defecto 48).

## OPEX anual

### POL (`opex_anual`)

```
OPEX_POL = num_chasis · energia_por_olt
         + mantenimiento_pct · CAPEX_POL
         + soporte
```

### Cobre (`opex_anual_cobre`)

```
OPEX_cobre = num_switches_cobre · energia_por_switch
           + mantenimiento_pct · CAPEX_cobre
```

Se usa el mismo `mantenimiento_pct` para comparar en igualdad de condiciones.

## TCO y break-even

- **TCO** a `n` años: `tco(capex, opex, n) = capex + opex · n`.
- **Break-even** (`break_even`): primer año del `1..horizonte_anios` en que
  `TCO_POL ≤ TCO_cobre`. La igualdad exacta **cuenta como cruce** (usa `<=`). Si
  POL nunca alcanza al cobre dentro del horizonte, devuelve `None`. El barrido
  empieza en el año 1 (el año 0, solo CAPEX, no se considera equilibrio operativo).

## Resumen para la vista de Negocio

`resumen_economico(estadio, precios)` devuelve el dict listo para formatear
(ningún cálculo en callbacks):

```python
{
  "capex":   {"pol": float, "cobre": float},
  "opex":    {"pol": float, "cobre": float},
  "tco_5":   {"pol": float, "cobre": float},
  "tco_10":  {"pol": float, "cobre": float},
  "break_even": int | None,   # año o None si no cruza en el horizonte
  "horizonte_anios": int,
}
```

## Propuesta de valor (`propuesta_valor`)

Tres cifras que argumentan POL frente a cobre, **derivadas por completo del
inventario** (se recalculan al cambiar la topología):

| Cifra | Derivación |
| ----- | ---------- |
| **Puertos de conmutación eliminados** | `num_onts`: cada puerto de servicio exigiría un puerto de switch activo en cobre; POL los sirve con splitters pasivos. |
| **Cuartos de telecom eliminados** | `ceil(switches_cobre / switches_por_cuarto) − num_chasis`, acotado a ≥ 0. Cobre agrupa switches en cuartos; POL solo necesita el/los cuarto(s) de cabecera del/los chasis OLT. |
| **Kilovatios ahorrados** | `(switches_cobre · watts_por_switch_cobre − num_chasis · watts_por_olt) / 1000`, acotado a ≥ 0. Los splitters pasivos no consumen. |

Los supuestos de dimensionamiento (`switches_por_cuarto`, `watts_por_switch_cobre`,
`watts_por_olt`) **no son precios**; se leen de la sección opcional
`propuesta_valor` de `prices.yaml` y tienen valores por defecto documentados
(4, 250 W, 600 W respectivamente).

## Escalabilidad

El módulo también incluye una sección de planeación de capacidad que reutiliza
los ratios de dimensionamiento del propio inventario (los mismos con los que
`tools/gen_topology.py` construyó cada estadio). El criterio clave es la
**identidad a 0% de crecimiento**: recalcular con crecimiento nulo debe
reproducir exactamente el inventario actual. Por eso la densidad de ONTs por
árbol se toma del inventario real (`num_onts / num_arboles`, ~34 en Azteca por el
overbooking acotado) y no de la razón nominal 1:32.

## Valores actuales de `data/prices.yaml`

```yaml
capex:
  olt_chasis: 45000
  tarjeta_pon: 12000
  ont: 85
  splitter_1x32: 120
  fibra_por_metro: 2.5
opex_anual:
  energia_por_olt: 3200
  mantenimiento_pct: 0.08
  soporte: 15000
horizonte_anios: 10
comparativa_cobre:
  switch_acceso: 3500
  cableado_utp_por_puerto: 45
  energia_por_switch: 900
propuesta_valor:
  switches_por_cuarto: 4
  watts_por_switch_cobre: 250
  watts_por_olt: 600
```

Monedas en USD; porcentajes como fracción (`0.08` = 8%).
