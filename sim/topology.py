"""Carga de topología semilla con fail-fast (design §5.3, Requirement 18).

Este módulo reconstruye las dataclasses de :mod:`sim.state` a partir de los
archivos ``data/topology_*.json`` generados **offline** por
``tools/gen_topology.py``. La aplicación **nunca** genera topología en runtime;
solo la lee aquí al arrancar (Req 18.2).

Python puro: **no importa nada de Dash** (principio de separación, §3.1).

Contrato de fail-fast (Req 18.5, design "Errores de carga de datos"):

* Si un archivo ``topology_*.json`` **falta**, **no se puede parsear** como
  JSON, o **viola** la conservación de ONTs (``Σ ONTs por árbol ≠ num_onts``),
  la carga lanza :class:`TopologiaInvalida`. La excepción no se captura aquí,
  de modo que aborta el arranque: el proceso termina, el puerto 8050 nunca se
  abre y no es posible servir tráfico.

Los enums (``servicio``, ``estado``, ``proteccion``) se serializan por su
``value`` en el JSON; aquí se reconstruyen al enum correspondiente. Un valor de
enum desconocido también es un error fatal de configuración.

Uso::

    from sim.topology import cargar_topologia, cargar_todas

    azteca = cargar_topologia("azteca")     # -> Estadio
    estadios = cargar_todas()               # -> dict[str, Estadio]
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.state import (
    ONT,
    OLT,
    ArbolPON,
    Estadio,
    Estado,
    Proteccion,
    PuertoPON,
    Servicio,
    Tarjeta,
)

# Estadios cargados por defecto (alineados con tools/gen_topology.py).
ESTADIOS_POR_DEFECTO: tuple[str, ...] = ("azteca", "bbva", "akron")


class TopologiaInvalida(Exception):
    """Error fatal de carga de topología (Req 18.5).

    Se lanza cuando un archivo ``topology_*.json`` falta, no se puede parsear,
    contiene un campo con forma inesperada o viola la conservación de ONTs.
    Es intencionadamente **no recuperable**: debe propagarse hasta abortar el
    arranque de la aplicación antes de abrir el puerto 8050.
    """


# ---------------------------------------------------------------------------
# Directorio de datos
# ---------------------------------------------------------------------------
def _dir_datos() -> Path:
    """Directorio ``data/`` en la raíz del proyecto."""
    return Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Reconstrucción de enums (fail-fast ante valores desconocidos)
# ---------------------------------------------------------------------------
def _enum(clase, valor, *, contexto: str):
    """Convierte ``valor`` (string del JSON) al enum ``clase``.

    Lanza :class:`TopologiaInvalida` si el valor no corresponde a un miembro
    válido del enum, incluyendo el ``contexto`` para diagnóstico.
    """
    try:
        return clase(valor)
    except ValueError as exc:
        raise TopologiaInvalida(
            f"{contexto}: valor '{valor}' no es un {clase.__name__} válido"
        ) from exc


def _requerir(dic: dict, clave: str, *, contexto: str):
    """Devuelve ``dic[clave]`` o lanza :class:`TopologiaInvalida` si falta."""
    if not isinstance(dic, dict) or clave not in dic:
        raise TopologiaInvalida(
            f"{contexto}: falta el campo requerido '{clave}'"
        )
    return dic[clave]


# ---------------------------------------------------------------------------
# Reconstrucción de la jerarquía JSON -> dataclasses
# ---------------------------------------------------------------------------
def _construir_ont(d: dict, *, contexto: str) -> ONT:
    return ONT(
        id=_requerir(d, "id", contexto=contexto),
        servicio=_enum(
            Servicio, _requerir(d, "servicio", contexto=contexto),
            contexto=f"{contexto}.servicio",
        ),
        arbol_id=_requerir(d, "arbol_id", contexto=contexto),
        estado=_enum(
            Estado, _requerir(d, "estado", contexto=contexto),
            contexto=f"{contexto}.estado",
        ),
        proteccion=_enum(
            Proteccion, _requerir(d, "proteccion", contexto=contexto),
            contexto=f"{contexto}.proteccion",
        ),
        # Campos dinámicos (bw, potencia, acumuladores) no viven en el JSON
        # semilla; se dejan en sus valores por defecto de la dataclass.
    )


def _construir_arbol(d: dict, *, contexto: str) -> ArbolPON:
    arbol_id = _requerir(d, "id", contexto=contexto)
    onts_raw = _requerir(d, "onts", contexto=f"árbol {arbol_id}")
    if not isinstance(onts_raw, list):
        raise TopologiaInvalida(f"árbol {arbol_id}: 'onts' debe ser una lista")
    onts = [
        _construir_ont(o, contexto=f"ONT en árbol {arbol_id}")
        for o in onts_raw
    ]
    return ArbolPON(
        id=arbol_id,
        puerto_id=_requerir(d, "puerto_id", contexto=f"árbol {arbol_id}"),
        ratio_split=_requerir(d, "ratio_split", contexto=f"árbol {arbol_id}"),
        onts=onts,
        estado=_enum(
            Estado, _requerir(d, "estado", contexto=f"árbol {arbol_id}"),
            contexto=f"árbol {arbol_id}.estado",
        ),
        proteccion=_enum(
            Proteccion, _requerir(d, "proteccion", contexto=f"árbol {arbol_id}"),
            contexto=f"árbol {arbol_id}.proteccion",
        ),
        atenuacion_db=_requerir(d, "atenuacion_db", contexto=f"árbol {arbol_id}"),
    )


def _construir_puerto(d: dict, *, contexto: str) -> PuertoPON:
    puerto_id = _requerir(d, "id", contexto=contexto)
    arboles_raw = _requerir(d, "arboles", contexto=f"puerto {puerto_id}")
    if not isinstance(arboles_raw, list):
        raise TopologiaInvalida(
            f"puerto {puerto_id}: 'arboles' debe ser una lista"
        )
    arboles = [
        _construir_arbol(a, contexto=f"árbol en puerto {puerto_id}")
        for a in arboles_raw
    ]
    return PuertoPON(
        id=puerto_id,
        tarjeta_id=_requerir(d, "tarjeta_id", contexto=f"puerto {puerto_id}"),
        capacidad_mbps=_requerir(
            d, "capacidad_mbps", contexto=f"puerto {puerto_id}"
        ),
        capacidad_util_mbps=_requerir(
            d, "capacidad_util_mbps", contexto=f"puerto {puerto_id}"
        ),
        arboles=arboles,
        estado=_enum(
            Estado, _requerir(d, "estado", contexto=f"puerto {puerto_id}"),
            contexto=f"puerto {puerto_id}.estado",
        ),
        es_reserva=_requerir(d, "es_reserva", contexto=f"puerto {puerto_id}"),
    )


def _construir_tarjeta(d: dict, *, contexto: str) -> Tarjeta:
    tarjeta_id = _requerir(d, "id", contexto=contexto)
    puertos_raw = _requerir(d, "puertos", contexto=f"tarjeta {tarjeta_id}")
    if not isinstance(puertos_raw, list):
        raise TopologiaInvalida(
            f"tarjeta {tarjeta_id}: 'puertos' debe ser una lista"
        )
    puertos = [
        _construir_puerto(p, contexto=f"puerto en tarjeta {tarjeta_id}")
        for p in puertos_raw
    ]
    return Tarjeta(
        id=tarjeta_id,
        olt_id=_requerir(d, "olt_id", contexto=f"tarjeta {tarjeta_id}"),
        num_puertos=_requerir(d, "num_puertos", contexto=f"tarjeta {tarjeta_id}"),
        puertos=puertos,
        estado=_enum(
            Estado, _requerir(d, "estado", contexto=f"tarjeta {tarjeta_id}"),
            contexto=f"tarjeta {tarjeta_id}.estado",
        ),
    )


def _construir_olt(d: dict, *, contexto: str) -> OLT:
    olt_id = _requerir(d, "id", contexto=contexto)
    tarjetas_raw = _requerir(d, "tarjetas", contexto=f"OLT {olt_id}")
    if not isinstance(tarjetas_raw, list):
        raise TopologiaInvalida(f"OLT {olt_id}: 'tarjetas' debe ser una lista")
    tarjetas = [
        _construir_tarjeta(t, contexto=f"tarjeta en OLT {olt_id}")
        for t in tarjetas_raw
    ]
    return OLT(
        id=olt_id,
        estadio_id=_requerir(d, "estadio_id", contexto=f"OLT {olt_id}"),
        num_chasis=_requerir(d, "num_chasis", contexto=f"OLT {olt_id}"),
        tarjetas=tarjetas,
        estado=_enum(
            Estado, _requerir(d, "estado", contexto=f"OLT {olt_id}"),
            contexto=f"OLT {olt_id}.estado",
        ),
    )


def _construir_estadio(d: dict, *, contexto: str) -> Estadio:
    estadio_id = _requerir(d, "id", contexto=contexto)
    olt = _construir_olt(
        _requerir(d, "olt", contexto=f"estadio {estadio_id}"),
        contexto=f"estadio {estadio_id}",
    )
    return Estadio(
        id=estadio_id,
        nombre=_requerir(d, "nombre", contexto=f"estadio {estadio_id}"),
        olt=olt,
        num_onts=_requerir(d, "num_onts", contexto=f"estadio {estadio_id}"),
        num_arboles=_requerir(d, "num_arboles", contexto=f"estadio {estadio_id}"),
        num_puertos=_requerir(d, "num_puertos", contexto=f"estadio {estadio_id}"),
        num_tarjetas=_requerir(
            d, "num_tarjetas", contexto=f"estadio {estadio_id}"
        ),
        num_chasis=_requerir(d, "num_chasis", contexto=f"estadio {estadio_id}"),
    )


# ---------------------------------------------------------------------------
# Verificación de conservación de ONTs (Req 18.4/18.5, Property 1)
# ---------------------------------------------------------------------------
def _verificar_conservacion(estadio: Estadio) -> None:
    """Aborta si ``Σ ONTs por árbol != estadio.num_onts`` (Req 18.5).

    Recorre toda la jerarquía OLT -> Tarjeta -> Puerto -> Árbol contando las
    ONTs efectivamente cargadas y las compara con ``num_onts`` declarado.
    """
    suma_onts = sum(
        len(arbol.onts)
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    )
    if suma_onts != estadio.num_onts:
        raise TopologiaInvalida(
            f"estadio {estadio.id}: Σ ONTs por árbol ({suma_onts}) != "
            f"num_onts ({estadio.num_onts})"
        )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def cargar_topologia(estadio_id: str, *, dir_datos: Path | None = None) -> Estadio:
    """Carga ``data/topology_<estadio_id>.json`` a un :class:`Estadio`.

    Args:
        estadio_id: identificador del estadio ("azteca", "bbva", "akron").
        dir_datos: directorio de datos alternativo (por defecto ``data/`` en la
            raíz del proyecto). Útil para pruebas.

    Returns:
        El :class:`Estadio` reconstruido con toda su jerarquía.

    Raises:
        TopologiaInvalida: si el archivo falta, no se puede parsear, tiene una
            forma inesperada, contiene un enum desconocido o viola la
            conservación de ONTs. La excepción está pensada para abortar el
            arranque (Req 18.5): no debe capturarse.
    """
    base = dir_datos if dir_datos is not None else _dir_datos()
    ruta = base / f"topology_{estadio_id}.json"

    # 1) Archivo faltante -> fatal.
    if not ruta.is_file():
        raise TopologiaInvalida(
            f"topología de '{estadio_id}': archivo no encontrado en {ruta}"
        )

    # 2) No parseable como JSON -> fatal.
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            crudo = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise TopologiaInvalida(
            f"topología de '{estadio_id}': no se pudo parsear {ruta}: {exc}"
        ) from exc

    if not isinstance(crudo, dict):
        raise TopologiaInvalida(
            f"topología de '{estadio_id}': el JSON raíz debe ser un objeto"
        )

    # 3) Reconstrucción de dataclasses (fail-fast ante campos/enums inválidos).
    estadio = _construir_estadio(crudo, contexto=f"topología de '{estadio_id}'")

    # 4) Conservación de ONTs -> fatal si no cuadra.
    _verificar_conservacion(estadio)

    return estadio


def cargar_todas(
    estadios: tuple[str, ...] = ESTADIOS_POR_DEFECTO,
    *,
    dir_datos: Path | None = None,
) -> dict[str, Estadio]:
    """Carga todos los estadios indicados desde ``data/``.

    Args:
        estadios: ids de estadio a cargar (por defecto azteca, bbva, akron).
        dir_datos: directorio de datos alternativo (para pruebas).

    Returns:
        ``dict`` mapeando ``estadio_id -> Estadio``.

    Raises:
        TopologiaInvalida: al primer estadio que no cargue correctamente. No se
            captura para preservar el fail-fast del arranque (Req 18.5).
    """
    resultado: dict[str, Estadio] = {}
    for estadio_id in estadios:
        resultado[estadio_id] = cargar_topologia(
            estadio_id, dir_datos=dir_datos
        )
    return resultado
