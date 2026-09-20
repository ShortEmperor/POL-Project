"""Prueba basada en propiedad — Property 5: Protección Type B/C mantiene servicio.

Diseño §6.4 (Conmutación de protección) y §14 (Property 5). Requirement 6.5:

    WHEN se inyecta una falla en un elemento con protección TYPE_B o TYPE_C, THE
    Motor_de_Simulacion SHALL conmutar al recurso de reserva en el mismo tick de
    la falla y mantener EN_LINEA a las ONTs protegidas.

**Validates: Requirements 6.5**

Enfoque
-------
La propiedad afirma que, *cuando hay una reserva disponible*, inyectar una falla
(``corte_troncal`` sobre un árbol, o ``tarjeta_caida`` sobre una tarjeta) que
afecta a un elemento con protección TYPE_B/TYPE_C conmuta en el **mismo tick** y
**ninguna** ONT protegida termina en FUERA: se mantienen EN_LINEA.

Se construye un ``SimState.crear()`` con la topología real, se localizan los
árboles TYPE_B/TYPE_C, y se hace **explícita** la precondición de "reserva
disponible" (Req 6.5) asegurando que existe al menos un puerto ``es_reserva``
operativo en el estadio activo antes de inyectar la falla.

Frontera Req 6.6 (reserva NO disponible): si no hubiera reserva, la ONT
protegida caería a FUERA/DEGRADADO de inmediato — esa es otra expectativa y no
la cubre Property 5. Aquí sólo se ejercita el caso "reserva disponible", con la
precondición afirmada de forma explícita antes de cada inyección.

Python puro: no se levanta la Capa_de_Presentacion (design §3.1).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.faults import inyectar_falla
from sim.state import (
    ONT,
    ArbolPON,
    Estadio,
    Estado,
    Proteccion,
    PuertoPON,
    SimState,
    Tarjeta,
)

# Esquemas de protección que deben mantener servicio con reserva disponible.
ESQUEMAS_PROTEGIDOS = (Proteccion.TYPE_B, Proteccion.TYPE_C)


# ---------------------------------------------------------------------------
# Utilidades de topología
# ---------------------------------------------------------------------------
def _arboles(estadio: Estadio) -> list[ArbolPON]:
    """Todos los árboles PON del estadio (recorriendo la jerarquía completa)."""
    return [
        arbol
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    ]


def _arboles_protegidos(estadio: Estadio) -> list[ArbolPON]:
    """Árboles con protección TYPE_B o TYPE_C."""
    return [a for a in _arboles(estadio) if a.proteccion in ESQUEMAS_PROTEGIDOS]


def _tarjetas_con_arbol_protegido(estadio: Estadio) -> list[Tarjeta]:
    """Tarjetas que alojan al menos un árbol TYPE_B/TYPE_C (excluye tarjetas de puro reserva)."""
    tarjetas: list[Tarjeta] = []
    for tarjeta in estadio.olt.tarjetas:
        tiene_protegido = any(
            arbol.proteccion in ESQUEMAS_PROTEGIDOS
            for puerto in tarjeta.puertos
            for arbol in puerto.arboles
        )
        if tiene_protegido:
            tarjetas.append(tarjeta)
    return tarjetas


def _puertos_reserva(estadio: Estadio) -> list[PuertoPON]:
    """Todos los puertos marcados como reserva del estadio."""
    return [
        puerto
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        if puerto.es_reserva
    ]


def _hay_reserva_disponible(estadio: Estadio) -> bool:
    """Precondición de Req 6.5: existe al menos un puerto de reserva operativo."""
    return any(p.estado != Estado.FUERA for p in _puertos_reserva(estadio))


def _asegurar_reserva_operativa(estadio: Estadio) -> None:
    """Hace explícita la precondición de reserva disponible (Req 6.5).

    Deja todos los puertos de reserva del estadio en RESERVA (operativos, no
    FUERA) de modo que la conmutación TYPE_B/TYPE_C tenga a dónde conmutar.
    """
    reservas = _puertos_reserva(estadio)
    assert reservas, "la topología debe tener puertos de reserva para Property 5"
    for puerto in reservas:
        if puerto.estado == Estado.FUERA:
            puerto.estado = Estado.RESERVA


def _onts_de_arbol(arbol: ArbolPON) -> list[ONT]:
    return list(arbol.onts)


def _onts_de_tarjeta(tarjeta: Tarjeta) -> list[ONT]:
    return [o for p in tarjeta.puertos for a in p.arboles for o in a.onts]


def _onts_protegidas(onts: list[ONT]) -> list[ONT]:
    return [o for o in onts if o.proteccion in ESQUEMAS_PROTEGIDOS]


# ---------------------------------------------------------------------------
# Sanity: la topología real tiene los ingredientes que la propiedad necesita
# ---------------------------------------------------------------------------
def test_topologia_tiene_protegidos_y_reserva():
    """La topología real expone árboles TYPE_B/TYPE_C y puertos de reserva.

    Sin estos ingredientes Property 5 no sería verificable sobre datos reales.
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]

    protegidos = _arboles_protegidos(estadio)
    assert protegidos, "se esperan árboles TYPE_B/TYPE_C en la topología real"
    assert _puertos_reserva(estadio), "se esperan puertos de reserva en la topología"
    # La precondición de Req 6.5 se puede satisfacer.
    _asegurar_reserva_operativa(estadio)
    assert _hay_reserva_disponible(estadio)


# ---------------------------------------------------------------------------
# Property 5 (corte_troncal sobre árbol protegido) — Hypothesis
# ---------------------------------------------------------------------------
@settings(max_examples=60, deadline=None)
@given(idx=st.integers(min_value=0), data=st.data())
def test_corte_troncal_en_arbol_protegido_mantiene_servicio(idx, data):
    """Property 5: falla en árbol TYPE_B/TYPE_C con reserva ⇒ ONTs no van a FUERA.

    Se elige un árbol protegido cualquiera, se afirma la precondición de reserva
    disponible (Req 6.5) y se inyecta un ``corte_troncal`` sobre ese árbol. Tras
    la inyección (que resuelve la conmutación en el mismo tick), **ninguna** ONT
    protegida del árbol debe quedar en FUERA: se mantienen EN_LINEA.

    **Validates: Requirements 6.5**
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]

    protegidos = _arboles_protegidos(estadio)
    assert protegidos
    arbol = protegidos[idx % len(protegidos)]

    # Precondición explícita de Req 6.5: la reserva está disponible/operativa.
    _asegurar_reserva_operativa(estadio)
    assert _hay_reserva_disponible(estadio)

    onts_prot = _onts_protegidas(_onts_de_arbol(arbol))
    assert onts_prot, "el árbol protegido debe tener ONTs protegidas"

    inyectar_falla(state, "corte_troncal", arbol.id)

    # Property 5: ninguna ONT protegida termina FUERA (se mantiene el servicio).
    fuera = [o for o in onts_prot if o.estado == Estado.FUERA]
    assert not fuera, (
        f"árbol {arbol.id} ({arbol.proteccion.value}): "
        f"{len(fuera)}/{len(onts_prot)} ONTs protegidas cayeron a FUERA pese a "
        f"tener reserva disponible (Req 6.5)"
    )
    # Con reserva, la conmutación las mantiene explícitamente EN_LINEA.
    assert all(o.estado == Estado.EN_LINEA for o in onts_prot)


# ---------------------------------------------------------------------------
# Property 5 (tarjeta_caida sobre tarjeta con árbol protegido) — parametrizado
# ---------------------------------------------------------------------------
def _ids_tarjetas_protegidas() -> list[str]:
    """IDs de tarjetas con árbol protegido de la topología activa (para parametrizar)."""
    estadio = SimState.crear().estadios["azteca"]
    return [t.id for t in _tarjetas_con_arbol_protegido(estadio)]


@pytest.mark.parametrize("tarjeta_id", _ids_tarjetas_protegidas())
def test_tarjeta_caida_con_arbol_protegido_mantiene_servicio(tarjeta_id):
    """Property 5 con ``tarjeta_caida``: las ONTs protegidas de la tarjeta no caen.

    Se localiza una tarjeta que aloja al menos un árbol TYPE_B/TYPE_C, se afirma
    la reserva disponible (Req 6.5) y se inyecta ``tarjeta_caida``. Las ONTs
    protegidas de esa tarjeta deben mantenerse EN_LINEA (no FUERA).

    **Validates: Requirements 6.5**
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]

    tarjeta = next(t for t in estadio.olt.tarjetas if t.id == tarjeta_id)

    _asegurar_reserva_operativa(estadio)
    assert _hay_reserva_disponible(estadio)

    onts_prot = _onts_protegidas(_onts_de_tarjeta(tarjeta))
    assert onts_prot, "la tarjeta debe tener ONTs protegidas"

    inyectar_falla(state, "tarjeta_caida", tarjeta.id)

    fuera = [o for o in onts_prot if o.estado == Estado.FUERA]
    assert not fuera, (
        f"tarjeta {tarjeta.id}: {len(fuera)}/{len(onts_prot)} ONTs protegidas "
        f"cayeron a FUERA pese a tener reserva disponible (Req 6.5)"
    )
    assert all(o.estado == Estado.EN_LINEA for o in onts_prot)


# ---------------------------------------------------------------------------
# Frontera Req 6.6 (contraste): sin reserva, las protegidas SÍ caen
# ---------------------------------------------------------------------------
def test_sin_reserva_las_protegidas_caen_frontera_6_6():
    """Contraste de frontera (Req 6.6): sin reserva, la ONT protegida cae.

    Property 5 cubre el caso "reserva disponible". Esta prueba documenta la
    frontera opuesta: si se retira toda la reserva (todos los puertos de reserva
    a FUERA), la conmutación TYPE_B/TYPE_C no tiene a dónde ir y la ONT protegida
    pasa a FUERA/DEGRADADO en el mismo tick, sin reintentar. No forma parte de
    Property 5; se incluye para dejar explícita la precondición de reserva.

    **Validates: Requirements 6.5** (por contraste con Req 6.6)
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]

    # Retirar toda la reserva: precondición de Req 6.5 deliberadamente NO se cumple.
    for puerto in _puertos_reserva(estadio):
        puerto.estado = Estado.FUERA
    assert not _hay_reserva_disponible(estadio)

    arbol = _arboles_protegidos(estadio)[0]
    onts_prot = _onts_protegidas(_onts_de_arbol(arbol))
    assert onts_prot

    inyectar_falla(state, "corte_troncal", arbol.id)

    # Sin reserva, las protegidas NO se mantienen EN_LINEA (caen de inmediato).
    assert all(o.estado != Estado.EN_LINEA for o in onts_prot)
    assert all(o.estado in (Estado.FUERA, Estado.DEGRADADO) for o in onts_prot)
