"""Smoke test de la carga de topología (sim/topology.py, tarea 3.3).

Verificación ligera de que los archivos reales ``data/topology_*.json`` cargan
a las dataclasses de ``sim/state.py`` con los enums reconstruidos. La prueba de
propiedad de conservación (Property 1) es la tarea 3.4 y la batería completa de
fail-fast es la tarea 3.5; aquí solo se comprueba el camino feliz y un caso
mínimo de fail-fast (archivo faltante).
"""

from __future__ import annotations

import pytest

from sim.state import Estadio, Estado, Proteccion, Servicio
from sim.topology import (
    ESTADIOS_POR_DEFECTO,
    TopologiaInvalida,
    cargar_todas,
    cargar_topologia,
)


def test_carga_azteca_reconstruye_dataclasses_y_enums():
    """Los archivos reales cargan a Estadio con enums reconstruidos (Req 18.2)."""
    estadio = cargar_topologia("azteca")

    assert isinstance(estadio, Estadio)
    assert estadio.num_onts == 1990

    ont = estadio.olt.tarjetas[0].puertos[0].arboles[0].onts[0]
    assert isinstance(ont.servicio, Servicio)
    assert isinstance(ont.estado, Estado)
    assert isinstance(ont.proteccion, Proteccion)


def test_cargar_todas_incluye_los_tres_estadios():
    """cargar_todas() devuelve azteca, bbva y akron (Req 18.2)."""
    estadios = cargar_todas()

    assert set(estadios) == set(ESTADIOS_POR_DEFECTO)
    # Σ ONTs por árbol == num_onts para cada estadio (Property 1).
    for estadio in estadios.values():
        suma = sum(
            len(a.onts)
            for t in estadio.olt.tarjetas
            for p in t.puertos
            for a in p.arboles
        )
        assert suma == estadio.num_onts


def test_archivo_faltante_aborta(tmp_path):
    """Archivo faltante lanza TopologiaInvalida (fail-fast, Req 18.5)."""
    with pytest.raises(TopologiaInvalida):
        cargar_topologia("noexiste", dir_datos=tmp_path)
