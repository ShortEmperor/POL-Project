# 05 — Datos y configuración

El comportamiento del modelo se controla desde `data/` y desde variables de
entorno, **sin tocar código**. Todo lo que se carga aplica un contrato
**fail-fast**: si un archivo falta, no se puede parsear o viola sus invariantes,
el arranque aborta antes de servir cifras inventadas.

## Archivos en `data/`

| Archivo | Uso |
| ------- | --- |
| `topology_azteca.json`, `topology_bbva.json`, `topology_akron.json` | Topología semilla de cada estadio. |
| `prices.yaml` | Precios y parámetros del modelo económico (ver [04](04-modelo-economico.md)). |
| `stadium_map.svg` | Mapa del estadio para la presentación. |

## Topologías (`topology_*.json`)

Generadas **offline** por `tools/gen_topology.py`. La aplicación **nunca**
genera topología en runtime; solo la lee al arrancar (`sim/topology.py`).

### Estructura

El JSON refleja la jerarquía de dataclasses de `sim/state.py`:

```
Estadio → OLT → Tarjeta → PuertoPON → ArbolPON → ONT
```

Encabezado del estadio (ejemplo, Azteca):

```json
{
  "id": "azteca",
  "nombre": "Estadio Azteca",
  "num_onts": 1990,
  "num_arboles": 58,
  "num_puertos": 110,
  "num_tarjetas": 7,
  "num_chasis": 2,
  "olt": { ... }
}
```

Un puerto declara su capacidad nominal y utilizable, y si es de reserva:

```json
{
  "id": "OLT-azteca-T01-P01",
  "capacidad_mbps": 10000.0,
  "capacidad_util_mbps": 8500.0,
  "es_reserva": false,
  "arboles": [ ... ]
}
```

Un árbol declara su ratio de split, protección y atenuación; una ONT su servicio,
estado y protección:

```json
{
  "id": "OLT-azteca-T01-P01-A",
  "ratio_split": 32,
  "proteccion": "type_b",
  "atenuacion_db": 0.0,
  "onts": [
    {
      "id": "OLT-azteca-T01-P01-A-O001",
      "servicio": "palcos",
      "estado": "en_linea",
      "proteccion": "type_b"
    }
  ]
}
```

> Los enums se serializan por su `value` (`"en_linea"`, `"type_b"`, `"palcos"`,
> etc.). Al cargar se reconstruyen al enum correspondiente; un valor desconocido
> es error fatal.

### Convención de identificadores

Los ids son jerárquicos: `OLT-<estadio>-T<tarjeta>-P<puerto>-A-O<ont>`. La
correlación de alarmas (ver [03](03-kpis-y-alarmas.md)) deduce de este patrón a
qué OLT y a qué árbol pertenece cada ONT.

### Invariante de conservación de ONTs

Al cargar, `sim/topology.py` verifica que **Σ ONTs por árbol == `num_onts`**
declarado. Si no cuadra, lanza `TopologiaInvalida` y aborta el arranque. Los
campos dinámicos (bw, potencia, acumuladores) no viven en el JSON semilla: se
inicializan a los valores por defecto de la dataclass.

## Precios (`prices.yaml`)

Ver el detalle en [04 — Modelo económico](04-modelo-economico.md). Editar
cualquier valor actualiza los paneles económicos sin recompilar; carga con
fail-fast (`PreciosInvalidos`) ante archivo faltante, YAML inválido o secciones
requeridas ausentes.

## Variables de entorno

Configurables en `docker-compose.yml` (o en el entorno de ejecución local):

| Variable | Por defecto | Descripción |
| -------- | ----------- | ----------- |
| `POL_ENV` | `demo` | Entorno de ejecución. |
| `POL_TICK_MS` | `1000` | Milisegundos por tick del `dcc.Interval`. Se acota a un mínimo de 200 ms. |
| `POL_SEED` | `42` | Semilla de la simulación estocástica (reproducibilidad). |

`POL_TICK_MS` lo lee `app/live.py` para fijar el período del único intervalo del
tablero. La semilla determinista por defecto (`42`) también está en
`sim/state.py` (`SEMILLA_POR_DEFECTO`).

## Volúmenes en Docker

`docker-compose.yml` monta `./data` y `./tools` como **solo lectura** dentro del
contenedor. Así se pueden ajustar topologías y precios desde el host sin
reconstruir la imagen, manteniendo la garantía de que la app no los modifica.
