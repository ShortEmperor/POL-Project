"""Batería de fail-fast de la carga de topología (tarea 3.5, Req 18.5).

Req 18.5 exige que un archivo ``topology_*.json`` que **falta**, **no se puede
parsear** como JSON, o **viola la conservación de ONTs**
(``Σ ONTs por árbol ≠ num_onts``) aborte por completo la carga lanzando
:class:`sim.topology.TopologiaInvalida`, sin éxito parcial ni silencioso.

Cada prueba usa el fixture ``tmp_path`` de pytest y el parámetro ``dir_datos``
de :func:`sim.topology.cargar_topologia` para apuntar a archivos controlados.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sim.topology import TopologiaInvalida, cargar_topologia

# Directorio de datos reales del proyecto (raíz/data).
DIR_DATOS_REAL = Path(__file__).resolve().parent.parent / "data"


def test_archivo_faltante_aborta(tmp_path):
    """Camino 1: archivo inexistente -> TopologiaInvalida (Req 18.5).

    El directorio de datos está vacío, así que no hay
    ``topology_azteca.json`` que cargar.
    """
    assert not (tmp_path / "topology_azteca.json").exists()

    with pytest.raises(TopologiaInvalida):
        cargar_topologia("azteca", dir_datos=tmp_path)


def test_json_no_parseable_aborta(tmp_path):
    """Camino 2: JSON malformado -> TopologiaInvalida (Req 18.5)."""
    ruta = tmp_path / "topology_azteca.json"
    # JSON deliberadamente malformado (llave sin cerrar, sin comillas...).
    ruta.write_text('{"id": "azteca", "num_onts": 1990,,, ', encoding="utf-8")

    with pytest.raises(TopologiaInvalida):
        cargar_topologia("azteca", dir_datos=tmp_path)


def test_conteo_inconsistente_aborta(tmp_path):
    """Camino 3: Σ ONTs por árbol ≠ num_onts -> TopologiaInvalida (Req 18.5).

    Se parte de un archivo real y consistente, se copia al ``tmp_path`` y se
    altera ``num_onts`` a un valor que ya no cuadra con el número de ONTs
    efectivamente presentes en la jerarquía. El JSON sigue siendo válido, de
    modo que el fallo proviene exclusivamente de la verificación de
    conservación de ONTs.
    """
    origen = DIR_DATOS_REAL / "topology_azteca.json"
    assert origen.is_file(), "se requiere data/topology_azteca.json real"

    destino = tmp_path / "topology_azteca.json"
    shutil.copy(origen, destino)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    # Rompe la conservación: cualquier valor distinto del real (1990) sirve.
    datos["num_onts"] = datos["num_onts"] + 1
    destino.write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(TopologiaInvalida):
        cargar_topologia("azteca", dir_datos=tmp_path)


def test_conteo_consistente_no_aborta(tmp_path):
    """Control: la misma copia sin alterar carga sin error (no falso positivo).

    Confirma que el fallo del caso inconsistente proviene de la verificación de
    conteo y no de un problema al copiar el archivo o del ``dir_datos``.
    """
    origen = DIR_DATOS_REAL / "topology_azteca.json"
    destino = tmp_path / "topology_azteca.json"
    shutil.copy(origen, destino)

    estadio = cargar_topologia("azteca", dir_datos=tmp_path)

    suma = sum(
        len(a.onts)
        for t in estadio.olt.tarjetas
        for p in t.puertos
        for a in p.arboles
    )
    assert suma == estadio.num_onts
