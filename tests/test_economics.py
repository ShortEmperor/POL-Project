"""Pruebas de integración del modelo económico sobre `data/prices.yaml` (tarea 9.9).

Cobertura del Requirement 11 (modelo económico) y del Requirement 17.1/17.2
(cobertura pytest del modelo económico **sin levantar la Capa de Presentación**:
este módulo importa **solo** de ``sim`` y del stdlib, nunca de ``app`` ni de Dash).

Alcance (complementario, no duplicado):

* CAPEX / OPEX / TCO / break-even calculados sobre el ``prices.yaml`` **real** y
  la topología real de Azteca, afirmando positividad y consistencia interna
  (``tco == capex + opex * años``; claves y valores de ``resumen_economico``).
* Req 11.1 — fail-fast de ``cargar_precios`` (:class:`PreciosInvalidos` ante
  archivo ausente/mal formado) usando ``tmp_path``.
* Req 11.5 (espíritu) — editar un precio en un YAML de prueba cambia las salidas
  (los precios provienen del archivo, no del código).
* Propuesta de valor, holgura de ONTs y recálculo de dimensionamiento (incl.
  +20%), y las funciones de presupuesto SLA integradas sobre la protección.

Las **propiedades** de break-even y de presupuesto ya se cubren en
``tests/test_property_break_even.py`` y ``tests/test_property_presupuesto.py``.
Aquí se añade la cobertura de **integración sobre `prices.yaml`** que aquellas no
ejercitan (valores reales, consistencia del resumen, edición de precio).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sim.economics import (
    MINUTOS_POR_ANIO,
    PreciosInvalidos,
    break_even,
    capex_cobre,
    capex_total,
    cargar_precios,
    holgura_onts,
    opex_anual,
    opex_anual_cobre,
    presupuesto_indisponibilidad_min,
    presupuesto_indisponibilidad_min_proteccion,
    propuesta_valor,
    recalcular_dimensionamiento,
    resumen_economico,
    tco,
)
from sim.state import Proteccion, objetivo_sla
from sim.topology import cargar_topologia

RUTA_PRICES_REAL = Path(__file__).resolve().parent.parent / "data" / "prices.yaml"


# ---------------------------------------------------------------------------
# Fixtures: precios reales + topología real de Azteca.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def precios():
    """Precios cargados del `data/prices.yaml` real del proyecto."""
    return cargar_precios()


@pytest.fixture(scope="module")
def azteca():
    """Inventario real del Estadio Azteca (data/topology_azteca.json)."""
    return cargar_topologia("azteca")


# ===========================================================================
# CAPEX / OPEX / TCO / break-even sobre datos reales (Req 11.1)
# ===========================================================================
def test_capex_opex_positivos_pol_y_cobre(precios, azteca):
    """CAPEX y OPEX de POL y de cobre son estrictamente positivos sobre datos reales."""
    assert capex_total(azteca, precios) > 0
    assert capex_cobre(azteca, precios) > 0
    assert opex_anual(azteca, precios) > 0
    assert opex_anual_cobre(azteca, precios) > 0


def test_tco_es_capex_mas_opex_por_anios(precios, azteca):
    """Consistencia interna: TCO(años) == CAPEX + OPEX * años (design §10.1)."""
    capex_pol = capex_total(azteca, precios)
    opex_pol = opex_anual(azteca, precios)
    for anios in (1, 5, 10):
        assert tco(capex_pol, opex_pol, anios) == pytest.approx(
            capex_pol + opex_pol * anios
        )


def test_capex_pol_deriva_del_inventario_y_precios(precios, azteca):
    """El CAPEX POL coincide con la suma inventario × precios (sin cifras a mano)."""
    c = precios.capex
    # metros_por_arbol no está en el YAML real -> aporte de fibra nulo por defecto.
    esperado = (
        azteca.num_chasis * c["olt_chasis"]
        + azteca.num_tarjetas * c["tarjeta_pon"]
        + azteca.num_onts * c["ont"]
        + azteca.num_arboles * c["splitter_1x32"]
    )
    assert capex_total(azteca, precios) == pytest.approx(esperado)


def test_break_even_dentro_o_fuera_del_horizonte(precios, azteca):
    """break_even sobre datos reales es None o un año válido del horizonte."""
    capex_pol = capex_total(azteca, precios)
    opex_pol = opex_anual(azteca, precios)
    capex_cu = capex_cobre(azteca, precios)
    opex_cu = opex_anual_cobre(azteca, precios)
    be = break_even(capex_pol, opex_pol, capex_cu, opex_cu, precios.horizonte_anios)
    assert be is None or 1 <= be <= precios.horizonte_anios


def test_resumen_economico_estructura_y_consistencia(precios, azteca):
    """`resumen_economico` expone claves y valores consistentes (Req 11.1/11.2)."""
    r = resumen_economico(azteca, precios)

    # Claves esperadas por la Capa de Presentación (tarea 9.8).
    assert set(r) == {
        "capex",
        "opex",
        "tco_5",
        "tco_10",
        "break_even",
        "horizonte_anios",
    }
    for bloque in ("capex", "opex", "tco_5", "tco_10"):
        assert set(r[bloque]) == {"pol", "cobre"}

    # Los valores del resumen coinciden con las funciones base (una fuente).
    capex_pol = capex_total(azteca, precios)
    capex_cu = capex_cobre(azteca, precios)
    opex_pol = opex_anual(azteca, precios)
    opex_cu = opex_anual_cobre(azteca, precios)

    assert r["capex"]["pol"] == pytest.approx(capex_pol)
    assert r["capex"]["cobre"] == pytest.approx(capex_cu)
    assert r["opex"]["pol"] == pytest.approx(opex_pol)
    assert r["opex"]["cobre"] == pytest.approx(opex_cu)
    assert r["tco_5"]["pol"] == pytest.approx(tco(capex_pol, opex_pol, 5))
    assert r["tco_5"]["cobre"] == pytest.approx(tco(capex_cu, opex_cu, 5))
    assert r["tco_10"]["pol"] == pytest.approx(tco(capex_pol, opex_pol, 10))
    assert r["tco_10"]["cobre"] == pytest.approx(tco(capex_cu, opex_cu, 10))
    assert r["horizonte_anios"] == precios.horizonte_anios

    # TCO a 10 años >= TCO a 5 años (OPEX no negativo se acumula).
    assert r["tco_10"]["pol"] >= r["tco_5"]["pol"]
    assert r["tco_10"]["cobre"] >= r["tco_5"]["cobre"]


# ===========================================================================
# Req 11.1 — fail-fast de cargar_precios (PreciosInvalidos)
# ===========================================================================
def test_cargar_precios_archivo_ausente_falla(tmp_path):
    """Archivo inexistente -> PreciosInvalidos (fail-fast, Req 11.1)."""
    ausente = tmp_path / "no_existe.yaml"
    with pytest.raises(PreciosInvalidos):
        cargar_precios(ausente)


def test_cargar_precios_yaml_malformado_falla(tmp_path):
    """YAML no parseable -> PreciosInvalidos (fail-fast, Req 11.1)."""
    malo = tmp_path / "malo.yaml"
    malo.write_text("capex: [sin cerrar\n  ::", encoding="utf-8")
    with pytest.raises(PreciosInvalidos):
        cargar_precios(malo)


def test_cargar_precios_seccion_faltante_falla(tmp_path):
    """Falta una sección requerida (comparativa_cobre) -> PreciosInvalidos."""
    incompleto = tmp_path / "incompleto.yaml"
    incompleto.write_text(
        "capex:\n"
        "  olt_chasis: 1\n"
        "  tarjeta_pon: 1\n"
        "  ont: 1\n"
        "  splitter_1x32: 1\n"
        "  fibra_por_metro: 1\n"
        "opex_anual:\n"
        "  energia_por_olt: 1\n"
        "  mantenimiento_pct: 0.1\n"
        "  soporte: 1\n"
        "horizonte_anios: 10\n",
        encoding="utf-8",
    )
    with pytest.raises(PreciosInvalidos):
        cargar_precios(incompleto)


def test_cargar_precios_campo_no_numerico_falla(tmp_path):
    """Un precio no numérico dentro de una sección requerida -> PreciosInvalidos."""
    original = RUTA_PRICES_REAL.read_text(encoding="utf-8")
    corrupto = original.replace("ont: 85", 'ont: "gratis"')
    ruta = tmp_path / "corrupto.yaml"
    ruta.write_text(corrupto, encoding="utf-8")
    with pytest.raises(PreciosInvalidos):
        cargar_precios(ruta)


# ===========================================================================
# Req 11.5 (espíritu) — los precios provienen del archivo: editarlos cambia salidas
# ===========================================================================
def test_editar_precio_cambia_capex(tmp_path, azteca):
    """Editar el precio de la ONT en una copia del YAML cambia el CAPEX POL.

    Demuestra que las cifras salen de `prices.yaml` (no hardcodeadas): se carga
    desde una copia con el precio de ONT duplicado y el CAPEX debe crecer en
    ``num_onts * (precio_nuevo - precio_original)``.
    """
    copia = tmp_path / "prices.yaml"
    shutil.copyfile(RUTA_PRICES_REAL, copia)

    base = cargar_precios(copia)
    capex_base = capex_total(azteca, base)
    precio_ont_original = base.capex["ont"]

    # Duplicar el precio de la ONT en la copia.
    texto = copia.read_text(encoding="utf-8")
    copia.write_text(
        texto.replace(
            f"ont: {int(precio_ont_original)}",
            f"ont: {int(precio_ont_original) * 2}",
        ),
        encoding="utf-8",
    )
    modificado = cargar_precios(copia)
    capex_mod = capex_total(azteca, modificado)

    assert modificado.capex["ont"] == pytest.approx(precio_ont_original * 2)
    assert capex_mod == pytest.approx(
        capex_base + azteca.num_onts * precio_ont_original
    )
    assert capex_mod > capex_base


# ===========================================================================
# Propuesta de valor (Req 10) sobre datos reales
# ===========================================================================
def test_propuesta_valor_estructura_y_signos(precios, azteca):
    """Las tres cifras de la propuesta de valor son coherentes y no negativas."""
    pv = propuesta_valor(azteca, precios)
    assert set(pv) == {
        "puertos_conmutacion_eliminados",
        "cuartos_telecom_eliminados",
        "kilovatios_ahorrados",
    }
    # Un puerto de switch por cada ONT servida (design §9.5).
    assert pv["puertos_conmutacion_eliminados"] == azteca.num_onts
    assert pv["cuartos_telecom_eliminados"] >= 0
    assert pv["kilovatios_ahorrados"] >= 0.0


# ===========================================================================
# Holgura y recálculo de dimensionamiento (Req 12) sobre datos reales
# ===========================================================================
def test_holgura_onts_no_negativa(azteca):
    """La holgura de ONTs antes de requerir tarjeta nueva es >= 0."""
    assert holgura_onts(azteca) >= 0


def test_recalcular_identidad_a_cero(azteca):
    """Crecimiento 0% reproduce el inventario actual (identidad, Req 12.3)."""
    r = recalcular_dimensionamiento(azteca, 0.0)
    assert r["nuevo"] == r["actual"]
    assert all(v == 0 for v in r["delta"].values())
    assert r["requiere_hardware_adicional"] is False


def test_recalcular_mas_20_por_ciento_crece(azteca):
    """+20% de aforo aumenta las ONTs y no reduce ningún contador (Req 12.5)."""
    r = recalcular_dimensionamiento(azteca, 0.20)
    assert r["factor_crecimiento"] == pytest.approx(0.20)
    assert r["nuevo"]["num_onts"] == round(azteca.num_onts * 1.20)
    # Ningún contador decrece al crecer el aforo.
    for clave in ("num_onts", "num_arboles", "num_puertos", "num_tarjetas", "num_chasis"):
        assert r["nuevo"][clave] >= r["actual"][clave]
        assert r["delta"][clave] >= 0
    assert r["tarjetas_adicionales"] == max(0, r["delta"]["num_tarjetas"])
    assert r["chasis_adicionales"] == max(0, r["delta"]["num_chasis"])
    assert r["requiere_hardware_adicional"] == (
        r["tarjetas_adicionales"] > 0 or r["chasis_adicionales"] > 0
    )


def test_recalcular_factor_negativo_falla(azteca):
    """Un factor de crecimiento negativo es inválido."""
    with pytest.raises(ValueError):
        recalcular_dimensionamiento(azteca, -0.1)


# ===========================================================================
# Presupuesto SLA por protección integrado (Req 13) — complementa test_property_presupuesto
# ===========================================================================
def test_presupuesto_por_proteccion_usa_objetivo_sla():
    """El presupuesto por esquema deriva de objetivo_sla (una fuente, §6.5)."""
    for proteccion in (Proteccion.TYPE_C, Proteccion.TYPE_B, Proteccion.NINGUNA):
        assert presupuesto_indisponibilidad_min_proteccion(
            proteccion
        ) == pytest.approx(presupuesto_indisponibilidad_min(objetivo_sla(proteccion)))
    # Más protección -> menos minutos de indisponibilidad permitidos.
    assert (
        presupuesto_indisponibilidad_min_proteccion(Proteccion.TYPE_C)
        < presupuesto_indisponibilidad_min_proteccion(Proteccion.TYPE_B)
        < presupuesto_indisponibilidad_min_proteccion(Proteccion.NINGUNA)
    )


def test_minutos_por_anio(azteca):
    """La base anual del presupuesto es 365*24*60 minutos (design §10.2)."""
    assert MINUTOS_POR_ANIO == pytest.approx(365 * 24 * 60)
