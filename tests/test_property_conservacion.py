"""Prueba basada en propiedad — Property 1: Conservación de ONTs.

Diseño §14 (Property 1) y Requirements 18.3 / 18.4:

* En toda topología generada, ``Σ ONTs por árbol == num_onts``.
* Para el Estadio Azteca, ese total es exactamente 1,990.

A diferencia de ``tests/test_gen_topology.py`` (que verifica la conservación
sobre los tres dimensionamientos fijos), esta prueba usa **Hypothesis** para
generar dimensionamientos aleatorios válidos y comprobar que la invariante de
conservación se mantiene sobre topologías arbitrarias.

**Validates: Requirements 18.3, 18.4**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tools.gen_topology import DIMENSIONAMIENTOS, generar_topologia


def _sumar_onts(topologia: dict) -> int:
    """Σ ONTs recorriendo la jerarquía OLT -> Tarjeta -> Puerto -> Árbol -> ONT."""
    arboles = [
        arbol
        for tarjeta in topologia["olt"]["tarjetas"]
        for puerto in tarjeta["puertos"]
        for arbol in puerto["arboles"]
    ]
    return sum(len(arbol["onts"]) for arbol in arboles)


@st.composite
def dimensionamientos_validos(draw) -> dict:
    """Genera dimensionamientos que satisfacen las precondiciones de
    ``generar_topologia``.

    Restricciones (constriñen el espacio de entrada al dominio válido):
      * ``num_tarjetas >= 1``.
      * ``num_puertos >= num_tarjetas`` (cada tarjeta con al menos un puerto).
      * ``1 <= num_arboles <= num_puertos`` (los árboles cuelgan de puertos
        activos; el resto quedan de reserva).
      * ``num_onts >= num_arboles`` (topología realista: al menos una ONT por
        árbol; la conservación se cumple igual para cualquier num_onts >= 0).
      * ``num_chasis >= 1``.
    """
    num_tarjetas = draw(st.integers(min_value=1, max_value=12))
    num_puertos = draw(st.integers(min_value=num_tarjetas, max_value=256))
    num_arboles = draw(st.integers(min_value=1, max_value=num_puertos))
    num_onts = draw(st.integers(min_value=num_arboles, max_value=5000))
    num_chasis = draw(st.integers(min_value=1, max_value=4))

    return {
        "nombre": "Estadio aleatorio",
        "num_onts": num_onts,
        "num_arboles": num_arboles,
        "num_puertos": num_puertos,
        "num_tarjetas": num_tarjetas,
        "num_chasis": num_chasis,
    }


@settings(max_examples=200, deadline=None)
@given(dim=dimensionamientos_validos())
def test_conservacion_onts_topologias_aleatorias(dim):
    """Property 1: Σ ONTs por árbol == num_onts para topologías aleatorias.

    **Validates: Requirements 18.4**
    """
    topologia = generar_topologia("rnd", dim)
    assert _sumar_onts(topologia) == dim["num_onts"]


def test_conservacion_onts_azteca_1990():
    """Req 18.3: la topología del Azteca conserva exactamente 1,990 ONTs.

    **Validates: Requirements 18.3**
    """
    topologia = generar_topologia("azteca", DIMENSIONAMIENTOS["azteca"])
    assert _sumar_onts(topologia) == 1990
    assert DIMENSIONAMIENTOS["azteca"]["num_onts"] == 1990
