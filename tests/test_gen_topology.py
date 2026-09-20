"""Pruebas del generador de topología offline (tools/gen_topology.py).

Cubren las postcondiciones del diseño (§5.3) y el criterio de aceptación del
Requirement 18: conteos exactos del Azteca y conservación de ONTs.
"""

from __future__ import annotations

import pytest

from sim.state import Proteccion, Servicio
from tools.gen_topology import (
    DIMENSIONAMIENTOS,
    _reparto_por_pesos,
    _reparto_uniforme,
    generar_topologia,
)


def _aplanar(topologia: dict):
    tarjetas = topologia["olt"]["tarjetas"]
    puertos = [p for t in tarjetas for p in t["puertos"]]
    arboles = [a for p in puertos for a in p["arboles"]]
    onts = [o for a in arboles for o in a["onts"]]
    return tarjetas, puertos, arboles, onts


def test_azteca_conteos_exactos():
    """Req 18.3: Azteca = 1990 ONTs, 58 árboles, 110 puertos, 7 tarjetas, 2 chasis."""
    topo = generar_topologia("azteca", DIMENSIONAMIENTOS["azteca"])
    tarjetas, puertos, arboles, onts = _aplanar(topo)
    assert len(onts) == 1990
    assert len(arboles) == 58
    assert len(puertos) == 110
    assert len(tarjetas) == 7
    assert topo["olt"]["num_chasis"] == 2


@pytest.mark.parametrize("estadio_id", list(DIMENSIONAMIENTOS))
def test_conservacion_onts(estadio_id):
    """Req 18.4 / Property 1: Σ ONTs por árbol == num_onts."""
    dim = DIMENSIONAMIENTOS[estadio_id]
    topo = generar_topologia(estadio_id, dim)
    _, _, arboles, _ = _aplanar(topo)
    assert sum(len(a["onts"]) for a in arboles) == dim["num_onts"]


@pytest.mark.parametrize("estadio_id", list(DIMENSIONAMIENTOS))
def test_conteos_estructurales(estadio_id):
    """Req 18.3: puertos/tarjetas/chasis coinciden con el dimensionamiento."""
    dim = DIMENSIONAMIENTOS[estadio_id]
    topo = generar_topologia(estadio_id, dim)
    tarjetas, puertos, arboles, _ = _aplanar(topo)
    assert len(arboles) == dim["num_arboles"]
    assert len(puertos) == dim["num_puertos"]
    assert len(tarjetas) == dim["num_tarjetas"]
    assert topo["olt"]["num_chasis"] == dim["num_chasis"]


@pytest.mark.parametrize("estadio_id", list(DIMENSIONAMIENTOS))
def test_serializable_json(estadio_id):
    """El dict devuelto debe ser serializable a JSON (enums por su value)."""
    import json

    topo = generar_topologia(estadio_id, DIMENSIONAMIENTOS[estadio_id])
    # No debe lanzar: todos los campos son primitivos/strings.
    json.dumps(topo)


@pytest.mark.parametrize("estadio_id", list(DIMENSIONAMIENTOS))
def test_puertos_reserva_sin_arboles(estadio_id):
    """Los puertos de reserva (Type B) no llevan árboles; los activos, uno."""
    dim = DIMENSIONAMIENTOS[estadio_id]
    topo = generar_topologia(estadio_id, dim)
    _, puertos, _, _ = _aplanar(topo)
    activos = [p for p in puertos if not p["es_reserva"]]
    reserva = [p for p in puertos if p["es_reserva"]]
    assert len(activos) == dim["num_arboles"]
    assert all(len(p["arboles"]) == 1 for p in activos)
    assert all(len(p["arboles"]) == 0 for p in reserva)


def test_servicios_y_protecciones_validos():
    """Todos los servicios/protecciones son valores válidos del enum."""
    topo = generar_topologia("azteca", DIMENSIONAMIENTOS["azteca"])
    _, _, _, onts = _aplanar(topo)
    servicios_validos = {s.value for s in Servicio}
    prot_validas = {p.value for p in Proteccion}
    assert all(o["servicio"] in servicios_validos for o in onts)
    assert all(o["proteccion"] in prot_validas for o in onts)


def test_reparto_uniforme_suma_exacta():
    partes = _reparto_uniforme(1990, 58)
    assert sum(partes) == 1990
    assert len(partes) == 58
    # Diferencia máxima de 1 entre partes.
    assert max(partes) - min(partes) <= 1


def test_reparto_por_pesos_suma_exacta():
    pesos = {Servicio.WIFI: 0.4, Servicio.CCTV: 0.3, Servicio.POS: 0.3}
    reparto = _reparto_por_pesos(1990, pesos)
    assert sum(reparto.values()) == 1990


def test_num_arboles_no_excede_puertos():
    """Un dimensionamiento con más árboles que puertos debe fallar explícito."""
    dim = {
        "nombre": "X",
        "num_onts": 10,
        "num_arboles": 5,
        "num_puertos": 3,
        "num_tarjetas": 1,
        "num_chasis": 1,
    }
    with pytest.raises(ValueError):
        generar_topologia("x", dim)
