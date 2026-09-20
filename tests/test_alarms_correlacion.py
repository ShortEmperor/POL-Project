"""Prueba basada en propiedad — Property 7: Correlación de alarmas.

Diseño §8.3 (regla de correlación), §8.4 (ciclo de vida) y Requirement 4.5/4.6:

* **Req 4.5**: WHILE una alarma raíz OLT-001 (chasis caído) u OLT-003 (corte
  troncal) está activa, THE Motor_de_Simulacion SHALL agrupar las alarmas
  ONT-001 derivadas bajo la raíz en lugar de listarlas individualmente.
* **Req 4.6**: IF una alarma ONT-001 derivada puede atribuirse a más de una raíz
  activa simultáneamente (p.ej. OLT-001 y OLT-003 activas a la vez), THEN THE
  Motor_de_Simulacion SHALL agruparla bajo una sola raíz, eligiendo la primera
  detectada o la de mayor severidad.

Estrategia de la prueba (design §14, Property 7):

* Se construye un ``SimState`` con la **topología real** (``SimState.crear``) y
  se eligen árboles con protección NINGUNA para que sus ONTs caigan realmente a
  FUERA al inyectar la falla (produciendo ONT-001 derivadas de verdad).
* Se corren **≥ 2 ticks** con ``sim.engine.tick`` para que las alarmas maduren
  de NUEVA → ACTIVA (design §8.4) y la raíz esté ACTIVA cuando se comprueba la
  agrupación.
* Se verifica contra la API pública ``sim.alarms.alarmas_activas``: la tabla de
  activas debe mostrar **exactamente una** raíz para la causa y **ninguna** de
  las ONT-001 derivadas de esa raíz (quedan colapsadas por ``raiz_id``).

**Validates: Requirements 4.5, 4.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.alarms import (
    CODIGOS_RAIZ,
    Alarma,
    EstadoAlarma,
    alarmas_activas,
    correlacionar,
    evaluar,
    _arbol_de_origen,
    _olt_de_origen,
)
from sim.engine import tick
from sim.faults import inyectar_falla
from sim.state import ArbolPON, Estado, Proteccion, SimState


# ---------------------------------------------------------------------------
# Utilidades de recorrido de la topología del estadio activo
# ---------------------------------------------------------------------------
def _arboles_estadio_activo(state: SimState) -> list[ArbolPON]:
    """Todos los árboles PON del estadio activo (recorrido completo)."""
    estadio = state.estadios[state.estadio_activo]
    return [
        arbol
        for tarjeta in estadio.olt.tarjetas
        for puerto in tarjeta.puertos
        for arbol in puerto.arboles
    ]


def _arboles_ninguna_con_onts(state: SimState) -> list[ArbolPON]:
    """Árboles con protección NINGUNA y al menos una ONT (sus ONTs sí caen)."""
    return [
        a
        for a in _arboles_estadio_activo(state)
        if a.proteccion == Proteccion.NINGUNA and a.onts
    ]


def _deshabilitar_todas_las_reservas(state: SimState) -> int:
    """Pone FUERA todos los puertos de reserva del estadio activo.

    Necesario para el caso de chasis caído (OLT-001): sin reservas operativas,
    la conmutación no puede mantener servicio y las ONTs protegidas caen a
    FUERA, produciendo las ONT-001 derivadas que la correlación debe colapsar.
    Devuelve cuántos puertos de reserva se deshabilitaron.
    """
    estadio = state.estadios[state.estadio_activo]
    n = 0
    for tarjeta in estadio.olt.tarjetas:
        for puerto in tarjeta.puertos:
            if puerto.es_reserva:
                puerto.estado = Estado.FUERA
                n += 1
    return n


def _correr_ticks(state: SimState, n: int) -> None:
    """Corre ``n`` ticks del motor (reaplica fallas + evalúa alarmas)."""
    state.corriendo = True
    for _ in range(n):
        tick(state)


def _activas_por_codigo(state: SimState, codigo: str) -> list[Alarma]:
    """Alarmas activas (no derivadas) de un código, según la API de la UI."""
    return [a for a in alarmas_activas(state) if a.codigo == codigo]


def _onts_ids_de_arbol(arbol: ArbolPON) -> set[str]:
    """Ids de las ONTs del árbol."""
    return {o.id for o in arbol.onts}


# ---------------------------------------------------------------------------
# Sanidad de datos: la topología permite ejercitar la correlación
# ---------------------------------------------------------------------------
def test_topologia_tiene_arboles_ninguna_y_reservas():
    """La topología real debe tener árboles NINGUNA (ONTs caen) y reservas.

    Sin árboles NINGUNA con ONTs no habría ONT-001 derivadas que colapsar; sin
    puertos de reserva no se podría montar el caso de chasis (OLT-001). Un fallo
    aquí sería un problema de datos, no del motor de alarmas.
    """
    state = SimState.crear()
    assert _arboles_ninguna_con_onts(state), (
        "se esperaban árboles NINGUNA con ONTs para producir ONT-001 derivadas"
    )
    assert _deshabilitar_todas_las_reservas(SimState.crear()) > 0, (
        "se esperaban puertos de reserva para el caso de chasis (OLT-001)"
    )


# ---------------------------------------------------------------------------
# Req 4.5 — OLT-003 (corte troncal): una sola raíz, ONT-001 colapsadas
# ---------------------------------------------------------------------------
def test_req_4_5_olt003_colapsa_ont001_bajo_una_raiz_ejemplo():
    """Un corte troncal en un árbol NINGUNA produce UNA raíz OLT-003 y colapsa
    sus ONT-001 derivadas (no se listan individualmente).

    **Validates: Requirements 4.5**
    """
    state = SimState.crear()
    arbol = next(iter(_arboles_ninguna_con_onts(state)))
    onts_arbol = _onts_ids_de_arbol(arbol)

    inyectar_falla(state, "corte_troncal", arbol.id)
    # ≥ 2 ticks para que la alarma raíz madure NUEVA -> ACTIVA (design §8.4).
    _correr_ticks(state, 2)

    activas = alarmas_activas(state)

    # (a) Exactamente una raíz OLT-003 para esta causa (el árbol cortado).
    raices_olt003 = [
        a for a in activas if a.codigo == "OLT-003" and a.origen_id == arbol.id
    ]
    assert len(raices_olt003) == 1, (
        f"se esperaba exactamente una raíz OLT-003 para {arbol.id}, "
        f"hay {len(raices_olt003)}"
    )
    raiz = raices_olt003[0]
    assert raiz.estado == EstadoAlarma.ACTIVA, (
        "tras ≥2 ticks la raíz OLT-003 debía estar ACTIVA"
    )

    # (b) Las ONT-001 de ese árbol NO aparecen individualmente en la tabla.
    ont001_del_arbol_en_tabla = [
        a
        for a in activas
        if a.codigo == "ONT-001" and a.origen_id in onts_arbol
    ]
    assert not ont001_del_arbol_en_tabla, (
        "las ONT-001 derivadas del árbol cortado no deben listarse "
        f"individualmente; se listaron {len(ont001_del_arbol_en_tabla)}"
    )

    # (c) En la lista de sesión existen ONT-001 vivas pero derivadas (raiz_id).
    derivadas = [
        a
        for a in state.alarmas
        if a.codigo == "ONT-001" and a.viva and a.origen_id in onts_arbol
    ]
    assert derivadas, "el corte troncal debía generar ONT-001 en el árbol"
    assert all(a.raiz_id == arbol.id for a in derivadas), (
        "toda ONT-001 del árbol debe quedar agrupada bajo la raíz OLT-003"
    )


@settings(max_examples=25, deadline=None)
@given(data=st.data())
def test_req_4_5_olt003_una_sola_raiz_property(data):
    """Property 7 (Req 4.5): para cualquier árbol NINGUNA, un corte troncal deja
    exactamente una raíz OLT-003 activa y colapsa sus ONT-001 derivadas.

    **Validates: Requirements 4.5**
    """
    state = SimState.crear()
    arbol = data.draw(st.sampled_from(_arboles_ninguna_con_onts(state)))
    onts_arbol = _onts_ids_de_arbol(arbol)

    inyectar_falla(state, "corte_troncal", arbol.id)
    _correr_ticks(state, 2)

    activas = alarmas_activas(state)

    # Exactamente una raíz para esta causa.
    raices = [
        a for a in activas if a.codigo == "OLT-003" and a.origen_id == arbol.id
    ]
    assert len(raices) == 1, (
        f"árbol {arbol.id}: se esperaba una sola raíz OLT-003, hay {len(raices)}"
    )

    # Ninguna ONT-001 del árbol listada individualmente.
    assert not [
        a for a in activas if a.codigo == "ONT-001" and a.origen_id in onts_arbol
    ], f"árbol {arbol.id}: ONT-001 derivadas no deben listarse individualmente"

    # Todas las ONT-001 vivas del árbol quedan agrupadas bajo la raíz.
    derivadas = [
        a
        for a in state.alarmas
        if a.codigo == "ONT-001" and a.viva and a.origen_id in onts_arbol
    ]
    assert derivadas and all(a.raiz_id == arbol.id for a in derivadas), (
        f"árbol {arbol.id}: ONT-001 deben agruparse bajo la raíz OLT-003"
    )


# ---------------------------------------------------------------------------
# Req 4.5 — OLT-001 (chasis caído): una sola raíz para toda la OLT
# ---------------------------------------------------------------------------
def test_req_4_5_olt001_chasis_una_raiz_colapsa_ont001_ejemplo():
    """Un chasis caído (sin reservas) produce UNA raíz OLT-001 y colapsa todas
    las ONT-001 de la OLT (no se listan individualmente).

    Se deshabilitan las reservas para que las ONTs protegidas caigan a FUERA y
    generen las ONT-001 que la correlación debe agrupar bajo el chasis.

    **Validates: Requirements 4.5**
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]
    olt_id = estadio.olt.id

    _deshabilitar_todas_las_reservas(state)
    inyectar_falla(state, "chasis_caido", olt_id)
    _correr_ticks(state, 2)

    activas = alarmas_activas(state)

    # Exactamente una raíz OLT-001 (el chasis de la OLT).
    raices = [a for a in activas if a.codigo == "OLT-001"]
    assert len(raices) == 1 and raices[0].origen_id == olt_id, (
        f"se esperaba una sola raíz OLT-001 en {olt_id}; activas={len(raices)}"
    )
    assert raices[0].estado == EstadoAlarma.ACTIVA

    # Ninguna ONT-001 de la OLT aparece individualmente en la tabla de activas.
    ont001_en_tabla = [a for a in activas if a.codigo == "ONT-001"]
    assert not ont001_en_tabla, (
        "con el chasis caído, ninguna ONT-001 debe listarse individualmente; "
        f"se listaron {len(ont001_en_tabla)}"
    )

    # Toda ONT-001 viva de la OLT queda agrupada bajo la raíz del chasis.
    derivadas = [a for a in state.alarmas if a.codigo == "ONT-001" and a.viva]
    assert derivadas, "el chasis caído sin reservas debía generar ONT-001"
    assert all(_olt_de_origen(a.origen_id) == olt_id for a in derivadas)
    assert all(a.raiz_id == olt_id for a in derivadas), (
        "toda ONT-001 debe agruparse bajo la raíz OLT-001 del chasis"
    )


# ---------------------------------------------------------------------------
# Req 4.6 — Doble raíz simultánea (OLT-001 + OLT-003): una sola agrupación
# ---------------------------------------------------------------------------
def test_req_4_6_ont001_bajo_una_sola_raiz_ante_multiples_ejemplo():
    """Con OLT-001 (chasis) y OLT-003 (corte troncal) activas a la vez sobre las
    mismas ONTs, cada ONT-001 se agrupa bajo UNA sola raíz.

    Las ONTs de un árbol NINGUNA cortado pertenecen a la vez a la raíz de su
    árbol (OLT-003) y a la raíz del chasis (OLT-001, misma OLT). La correlación
    debe elegir una sola raíz y no listar la ONT-001 en la tabla de activas.

    **Validates: Requirements 4.6**
    """
    state = SimState.crear()
    estadio = state.estadios[state.estadio_activo]
    olt_id = estadio.olt.id
    arbol = next(iter(_arboles_ninguna_con_onts(state)))
    onts_arbol = _onts_ids_de_arbol(arbol)

    # Ambas causas activas simultáneamente: chasis (sin reservas) + corte troncal.
    _deshabilitar_todas_las_reservas(state)
    inyectar_falla(state, "chasis_caido", olt_id)
    inyectar_falla(state, "corte_troncal", arbol.id)
    _correr_ticks(state, 2)

    activas = alarmas_activas(state)

    # Ambas raíces existen y están activas simultáneamente (precondición 4.6).
    assert any(a.codigo == "OLT-001" and a.origen_id == olt_id for a in activas)
    assert any(a.codigo == "OLT-003" and a.origen_id == arbol.id for a in activas)

    # Las ONT-001 del árbol podrían atribuirse a AMBAS raíces; deben quedar bajo
    # una sola y no aparecer individualmente en la tabla de activas.
    assert not [
        a for a in activas if a.codigo == "ONT-001" and a.origen_id in onts_arbol
    ], "ONT-001 con doble raíz no deben listarse individualmente (Req 4.6)"

    derivadas_arbol = [
        a
        for a in state.alarmas
        if a.codigo == "ONT-001" and a.viva and a.origen_id in onts_arbol
    ]
    assert derivadas_arbol, "debían existir ONT-001 vivas en el árbol cortado"
    for a in derivadas_arbol:
        # Cada derivada apunta a EXACTAMENTE una raíz (no ambigua).
        assert a.raiz_id is not None, "la ONT-001 debe estar agrupada"
        assert a.raiz_id in (olt_id, arbol.id), (
            f"raíz {a.raiz_id!r} inesperada; debía ser el chasis o el árbol"
        )
    # Todas las ONT-001 del árbol comparten UNA misma raíz elegida (consistente).
    raices_elegidas = {a.raiz_id for a in derivadas_arbol}
    assert len(raices_elegidas) == 1, (
        f"las ONT-001 del árbol deben agruparse bajo una sola raíz; "
        f"se agruparon bajo {raices_elegidas}"
    )


def test_req_4_6_desempate_por_primera_detectada_ambas_criticas():
    """Ante dos raíces de igual severidad (OLT-001 y OLT-003 son ambas críticas),
    la ONT-001 se agrupa bajo la **primera detectada** (menor t_min_apertura).

    Verifica directamente la regla de desempate de ``correlacionar`` (Req 4.6):
    a igualdad de severidad gana la de menor ``t_min_apertura``.

    **Validates: Requirements 4.6**
    """
    olt_id = "OLT-azteca"
    arbol_id = "OLT-azteca-T01-P01-A"
    ont_id = "OLT-azteca-T01-P01-A-O001"

    # OLT-001 detectada ANTES (t=5.0) que OLT-003 (t=7.0); ambas críticas.
    raiz_olt001 = Alarma(
        codigo="OLT-001",
        severidad="critica",
        estado=EstadoAlarma.ACTIVA,
        origen_id=olt_id,
        t_min_apertura=5.0,
    )
    raiz_olt003 = Alarma(
        codigo="OLT-003",
        severidad="critica",
        estado=EstadoAlarma.ACTIVA,
        origen_id=arbol_id,
        t_min_apertura=7.0,
    )
    derivada = Alarma(
        codigo="ONT-001",
        severidad="mayor",
        estado=EstadoAlarma.ACTIVA,
        origen_id=ont_id,
        t_min_apertura=7.0,
    )

    correlacionar([raiz_olt001, raiz_olt003, derivada])

    # A igualdad de severidad, gana la primera detectada: OLT-001 (t=5.0).
    assert derivada.raiz_id == olt_id, (
        "ante severidades iguales, la ONT-001 debe agruparse bajo la raíz "
        f"detectada primero (OLT-001); quedó bajo {derivada.raiz_id!r}"
    )
    # Sanidad de los helpers de pertenencia usados por la correlación.
    assert _olt_de_origen(ont_id) == olt_id
    assert _arbol_de_origen(ont_id) == arbol_id


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
