"""Generador de topología semilla offline (design §5.3, Requirement 18).

Este módulo se ejecuta **fuera de tiempo de ejecución** para producir los
archivos ``data/topology_*.json`` que la aplicación carga al arrancar. La
aplicación **nunca** genera topología en runtime; solo la lee (Req 18.2).

Jerarquía generada (design §5.1/§5.2)::

    OLT -> Tarjeta -> PuertoPON -> ArbolPON -> ONT

El dict devuelto por :func:`generar_topologia` es serializable a JSON y sus
claves están alineadas con las dataclasses de ``sim/state.py`` (los enums se
serializan por su ``value``) para que la tarea 3.3 pueda reconstruir el estado.

Postcondiciones garantizadas antes de escribir (Req 18.4 / Property 1):

* ``Σ ONTs por árbol == num_onts``.
* El número de puertos, tarjetas y chasis coincide con el dimensionamiento.

Uso::

    python -m tools.gen_topology            # genera los tres estadios
    python tools/gen_topology.py            # equivalente
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Import robusto tanto si se ejecuta como módulo (`python -m tools.gen_topology`)
# como si se ejecuta el archivo directamente (`python tools/gen_topology.py`).
try:  # pragma: no cover - selección de import según modo de ejecución
    from sim.state import Estado, Proteccion, Servicio
except ModuleNotFoundError:  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sim.state import Estado, Proteccion, Servicio


# ---------------------------------------------------------------------------
# Dimensionamiento por estadio
# ---------------------------------------------------------------------------
# El dimensionamiento del Estadio Azteca proviene del cálculo de ingeniería
# (design §5.3): 1,990 ONTs, 58 árboles PON, 110 puertos, 7 tarjetas, 2 chasis.
#
# Nota de ingeniería sobre puertos vs. árboles: hay 110 puertos pero solo 58
# árboles PON. Los 52 puertos restantes quedan como puertos de reserva
# (``es_reserva=True``, Estado.RESERVA) que dan soporte a la conmutación de
# protección Type B (design §5.2, §7). Cada árbol activo cuelga de un puerto
# activo; los puertos de reserva no llevan árboles.
#
# Distribución de servicios: pesos relativos que reparten las ONTs entre los
# 10 servicios del enum Servicio. Se normalizan al total de ONTs y el residuo
# se asigna de forma determinista para que la suma cierre exactamente.
#
# Protección por servicio: los servicios críticos para la operación del
# estadio reciben redundancia; el resto va sin protección (design §10.2).

# Peso relativo de cada servicio en el reparto de ONTs.
DISTRIBUCION_SERVICIOS: dict[Servicio, float] = {
    Servicio.WIFI: 0.42,        # WiFi de asistentes: la mayor población de ONTs
    Servicio.PALCOS: 0.10,      # IPTV/palcos
    Servicio.CCTV: 0.12,        # cámaras de videovigilancia
    Servicio.POS: 0.10,         # puntos de venta / TPV
    Servicio.TORNIQUETES: 0.06,  # control de accesos
    Servicio.SIGNAGE: 0.06,     # cartelería digital
    Servicio.VOZ: 0.05,         # telefonía / radios
    Servicio.MACRO: 0.03,       # cobertura macro
    Servicio.PRENSA: 0.04,      # sala de prensa
    Servicio.ESPORTS: 0.02,     # zona de esports
}

# Esquema de protección por servicio (design §10.2).
#   Type C  -> servicios de misión crítica (seguridad, accesos, ingresos)
#   Type B  -> servicios importantes (redundancia lado OLT)
#   Ninguna -> servicios de mejor esfuerzo
PROTECCION_SERVICIO: dict[Servicio, Proteccion] = {
    Servicio.CCTV: Proteccion.TYPE_C,        # videovigilancia: seguridad
    Servicio.TORNIQUETES: Proteccion.TYPE_C,  # accesos: caída = fila/parón
    Servicio.POS: Proteccion.TYPE_C,         # ingresos: caída = pérdida directa
    Servicio.VOZ: Proteccion.TYPE_B,         # comunicaciones operativas
    Servicio.PALCOS: Proteccion.TYPE_B,      # experiencia premium
    Servicio.PRENSA: Proteccion.TYPE_B,      # transmisión
    Servicio.WIFI: Proteccion.NINGUNA,       # mejor esfuerzo
    Servicio.SIGNAGE: Proteccion.NINGUNA,
    Servicio.MACRO: Proteccion.NINGUNA,
    Servicio.ESPORTS: Proteccion.NINGUNA,
}

# Ratio de split del árbol PON (splitter 1:32). Con 58 árboles el promedio de
# ONTs/árbol para Azteca es ~34, coherente con splitters 1:32 más overbooking
# acotado; se documenta como parámetro del dimensionamiento.
RATIO_SPLIT = 32

# Capacidad de puerto XGS-PON (design §6, throughput K1/K11).
CAP_PUERTO_MBPS = 10000.0        # 10 Gbps nominal (XGS-PON)
CAP_PUERTO_UTIL_MBPS = 8500.0    # utilizable tras overhead PON

DIMENSIONAMIENTOS: dict[str, dict] = {
    "azteca": {
        "nombre": "Estadio Azteca",
        "num_onts": 1990,
        "num_arboles": 58,
        "num_puertos": 110,
        "num_tarjetas": 7,
        "num_chasis": 2,
    },
    # BBVA (Monterrey). Supuesto: aforo menor que el Azteca (~53k vs ~87k), por
    # lo que se escala la topología ~65%: menos ONTs y árboles, un solo chasis.
    "bbva": {
        "nombre": "Estadio BBVA",
        "num_onts": 1300,
        "num_arboles": 40,
        "num_puertos": 72,
        "num_tarjetas": 5,
        "num_chasis": 1,
    },
    # Akron (Guadalajara). Supuesto: aforo intermedio (~49k); escala ~60%.
    "akron": {
        "nombre": "Estadio Akron",
        "num_onts": 1200,
        "num_arboles": 36,
        "num_puertos": 64,
        "num_tarjetas": 4,
        "num_chasis": 1,
    },
}


# ---------------------------------------------------------------------------
# Repartos deterministas (mayor residuo)
# ---------------------------------------------------------------------------
def _reparto_por_pesos(total: int, pesos: dict) -> dict:
    """Reparte ``total`` unidades enteras entre claves según ``pesos``.

    Usa el método del mayor residuo (Hamilton) para que la suma de las partes
    sea exactamente ``total`` de forma determinista.
    """
    suma_pesos = sum(pesos.values())
    exactos = {k: (total * p / suma_pesos) for k, p in pesos.items()}
    base = {k: int(v) for k, v in exactos.items()}
    asignado = sum(base.values())
    resto = total - asignado
    # Reparte el residuo a las claves con mayor parte fraccionaria.
    orden = sorted(
        pesos.keys(),
        key=lambda k: (exactos[k] - base[k]),
        reverse=True,
    )
    for k in orden[:resto]:
        base[k] += 1
    return base


def _reparto_uniforme(total: int, partes: int) -> list[int]:
    """Reparte ``total`` en ``partes`` enteros lo más iguales posible.

    La suma de la lista devuelta es exactamente ``total``.
    """
    if partes <= 0:
        raise ValueError("partes debe ser >= 1")
    base = total // partes
    resto = total % partes
    # Los primeros `resto` elementos reciben una unidad extra.
    return [base + (1 if i < resto else 0) for i in range(partes)]


# ---------------------------------------------------------------------------
# Generador principal
# ---------------------------------------------------------------------------
def generar_topologia(estadio_id: str, dimensionamiento: dict) -> dict:
    """Construye la jerarquía OLT -> Tarjeta -> PuertoPON -> ArbolPON -> ONT.

    Precondiciones:
      - ``dimensionamiento`` contiene ``num_onts``, ``num_arboles``,
        ``num_puertos``, ``num_tarjetas``, ``num_chasis`` y ``nombre``.

    Postcondiciones:
      - Devuelve un dict serializable a JSON con la jerarquía completa.
      - La suma de ONTs por árbol == ``num_onts`` (Req 18.4 / Property 1).
      - El número de puertos, tarjetas y chasis coincide con el dimensionamiento.
    """
    num_onts = dimensionamiento["num_onts"]
    num_arboles = dimensionamiento["num_arboles"]
    num_puertos = dimensionamiento["num_puertos"]
    num_tarjetas = dimensionamiento["num_tarjetas"]
    num_chasis = dimensionamiento["num_chasis"]
    nombre = dimensionamiento["nombre"]

    if num_arboles > num_puertos:
        raise ValueError(
            f"{estadio_id}: num_arboles ({num_arboles}) no puede exceder "
            f"num_puertos ({num_puertos})"
        )

    olt_id = f"OLT-{estadio_id}"

    # 1) Reparto de ONTs por árbol (uniforme, suma exacta == num_onts).
    onts_por_arbol = _reparto_uniforme(num_onts, num_arboles)

    # 2) Reparto global de ONTs por servicio (suma exacta == num_onts).
    cupos_servicio = _reparto_por_pesos(num_onts, DISTRIBUCION_SERVICIOS)
    # Cola de servicios expandida en el orden fijo del enum (design §9.6:
    # "orden de servicios fijo") para asignación determinista a las ONTs.
    cola_servicios: list[Servicio] = []
    for servicio in Servicio:
        cola_servicios.extend([servicio] * cupos_servicio.get(servicio, 0))
    # (La suma de cupos == num_onts, por lo que la cola tiene exactamente
    # num_onts elementos.)

    # 3) Reparto de puertos por tarjeta (uniforme, suma exacta == num_puertos).
    puertos_por_tarjeta = _reparto_uniforme(num_puertos, num_tarjetas)

    # 4) Reparto de tarjetas por chasis (solo informativo en el dict OLT; el
    #    modelo de datos no anida chasis, pero registramos el conteo).

    # Construcción de la jerarquía --------------------------------------------
    tarjetas: list[dict] = []
    idx_ont = 0          # cursor sobre la cola de servicios
    idx_arbol_global = 0  # cursor sobre onts_por_arbol
    arboles_restantes = num_arboles

    for t in range(num_tarjetas):
        tarjeta_id = f"{olt_id}-T{t + 1:02d}"
        n_puertos_tarjeta = puertos_por_tarjeta[t]
        puertos: list[dict] = []

        for p in range(n_puertos_tarjeta):
            puerto_id = f"{tarjeta_id}-P{p + 1:02d}"

            # ¿Este puerto lleva un árbol activo o es de reserva?
            if arboles_restantes > 0:
                arbol_id = f"{puerto_id}-A"
                n_onts_arbol = onts_por_arbol[idx_arbol_global]
                idx_arbol_global += 1
                arboles_restantes -= 1

                onts: list[dict] = []
                for o in range(n_onts_arbol):
                    servicio = cola_servicios[idx_ont]
                    idx_ont += 1
                    ont_id = f"{arbol_id}-O{o + 1:03d}"
                    proteccion = PROTECCION_SERVICIO.get(
                        servicio, Proteccion.NINGUNA
                    )
                    onts.append(
                        {
                            "id": ont_id,
                            "servicio": servicio.value,
                            "arbol_id": arbol_id,
                            "estado": Estado.EN_LINEA.value,
                            "proteccion": proteccion.value,
                        }
                    )

                # La protección del árbol es la más fuerte entre sus ONTs.
                prot_arbol = _proteccion_arbol(onts)
                arbol = {
                    "id": arbol_id,
                    "puerto_id": puerto_id,
                    "ratio_split": RATIO_SPLIT,
                    "estado": Estado.EN_LINEA.value,
                    "proteccion": prot_arbol.value,
                    "atenuacion_db": 0.0,
                    "onts": onts,
                }
                puerto = {
                    "id": puerto_id,
                    "tarjeta_id": tarjeta_id,
                    "capacidad_mbps": CAP_PUERTO_MBPS,
                    "capacidad_util_mbps": CAP_PUERTO_UTIL_MBPS,
                    "estado": Estado.EN_LINEA.value,
                    "es_reserva": False,
                    "arboles": [arbol],
                }
            else:
                # Puerto de reserva para conmutación de protección Type B.
                puerto = {
                    "id": puerto_id,
                    "tarjeta_id": tarjeta_id,
                    "capacidad_mbps": CAP_PUERTO_MBPS,
                    "capacidad_util_mbps": CAP_PUERTO_UTIL_MBPS,
                    "estado": Estado.RESERVA.value,
                    "es_reserva": True,
                    "arboles": [],
                }

            puertos.append(puerto)

        tarjetas.append(
            {
                "id": tarjeta_id,
                "olt_id": olt_id,
                "num_puertos": n_puertos_tarjeta,
                "estado": Estado.EN_LINEA.value,
                "puertos": puertos,
            }
        )

    olt = {
        "id": olt_id,
        "estadio_id": estadio_id,
        "num_chasis": num_chasis,
        "estado": Estado.EN_LINEA.value,
        "tarjetas": tarjetas,
    }

    topologia = {
        "id": estadio_id,
        "nombre": nombre,
        "num_onts": num_onts,
        "num_arboles": num_arboles,
        "num_puertos": num_puertos,
        "num_tarjetas": num_tarjetas,
        "num_chasis": num_chasis,
        "olt": olt,
    }

    _verificar_postcondiciones(estadio_id, topologia, dimensionamiento)
    return topologia


def _proteccion_arbol(onts: list[dict]) -> Proteccion:
    """Devuelve la protección más fuerte presente entre las ONTs del árbol."""
    orden = {
        Proteccion.NINGUNA.value: 0,
        Proteccion.TYPE_B.value: 1,
        Proteccion.TYPE_C.value: 2,
    }
    mejor = Proteccion.NINGUNA
    for o in onts:
        if orden[o["proteccion"]] > orden[mejor.value]:
            mejor = Proteccion(o["proteccion"])
    return mejor


def _verificar_postcondiciones(
    estadio_id: str, topologia: dict, dim: dict
) -> None:
    """Asserts de las postcondiciones (Req 18.3/18.4, Property 1).

    Se ejecuta **antes** de escribir el JSON: un dimensionamiento inconsistente
    debe abortar la generación, no producir un archivo inválido.
    """
    olt = topologia["olt"]
    tarjetas = olt["tarjetas"]
    puertos = [p for t in tarjetas for p in t["puertos"]]
    arboles = [a for p in puertos for a in p["arboles"]]

    # Conservación de ONTs (Req 18.4 / Property 1).
    suma_onts = sum(len(a["onts"]) for a in arboles)
    assert suma_onts == dim["num_onts"], (
        f"{estadio_id}: Σ ONTs por árbol ({suma_onts}) != num_onts "
        f"({dim['num_onts']})"
    )

    # Conteos estructurales (Req 18.3).
    assert len(arboles) == dim["num_arboles"], (
        f"{estadio_id}: árboles ({len(arboles)}) != num_arboles "
        f"({dim['num_arboles']})"
    )
    assert len(puertos) == dim["num_puertos"], (
        f"{estadio_id}: puertos ({len(puertos)}) != num_puertos "
        f"({dim['num_puertos']})"
    )
    assert len(tarjetas) == dim["num_tarjetas"], (
        f"{estadio_id}: tarjetas ({len(tarjetas)}) != num_tarjetas "
        f"({dim['num_tarjetas']})"
    )
    assert olt["num_chasis"] == dim["num_chasis"], (
        f"{estadio_id}: chasis ({olt['num_chasis']}) != num_chasis "
        f"({dim['num_chasis']})"
    )
    # Coherencia de los campos de resumen del dict de topología.
    assert topologia["num_onts"] == dim["num_onts"]
    assert topologia["num_arboles"] == dim["num_arboles"]


# ---------------------------------------------------------------------------
# Escritura de archivos
# ---------------------------------------------------------------------------
def _dir_datos() -> Path:
    """Directorio ``data/`` en la raíz del proyecto."""
    return Path(__file__).resolve().parent.parent / "data"


def escribir_topologia(estadio_id: str, dimensionamiento: dict) -> Path:
    """Genera y escribe ``data/topology_<estadio_id>.json``.

    Devuelve la ruta del archivo escrito.
    """
    topologia = generar_topologia(estadio_id, dimensionamiento)
    destino = _dir_datos() / f"topology_{estadio_id}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(topologia, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return destino


def _resumen(topologia: dict) -> dict:
    """Conteos derivados de la jerarquía (para reporte en consola)."""
    olt = topologia["olt"]
    tarjetas = olt["tarjetas"]
    puertos = [p for t in tarjetas for p in t["puertos"]]
    arboles = [a for p in puertos for a in p["arboles"]]
    onts = [o for a in arboles for o in a["onts"]]
    return {
        "onts": len(onts),
        "arboles": len(arboles),
        "puertos": len(puertos),
        "tarjetas": len(tarjetas),
        "chasis": olt["num_chasis"],
    }


def main() -> None:
    """Genera los tres archivos de topología e imprime un resumen."""
    for estadio_id, dim in DIMENSIONAMIENTOS.items():
        topologia = generar_topologia(estadio_id, dim)
        destino = _dir_datos() / f"topology_{estadio_id}.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(topologia, f, ensure_ascii=False, indent=2)
            f.write("\n")
        r = _resumen(topologia)
        rel = os.path.relpath(destino, Path(__file__).resolve().parent.parent)
        print(
            f"[{estadio_id}] {rel}: "
            f"{r['onts']} ONTs, {r['arboles']} árboles, {r['puertos']} puertos, "
            f"{r['tarjetas']} tarjetas, {r['chasis']} chasis"
        )


if __name__ == "__main__":
    main()
