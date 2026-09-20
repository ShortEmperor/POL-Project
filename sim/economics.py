"""Modelo económico del Motor de Simulación (design §10.1, Requirement 11).

Python puro: este módulo **no importa nada de Dash** (principio de separación,
design §3.1). Calcula CAPEX, OPEX anual, TCO y punto de equilibrio (break-even)
de la arquitectura POL frente a la comparativa de cobre, derivando todas las
cantidades del **inventario de topología** (`sim.state.Estadio`) y todos los
precios de `data/prices.yaml`.

Principio rector (Req 11.5): los precios **nunca** se hardcodean. Se leen del
archivo en cada cálculo a través de :func:`cargar_precios`, de modo que editar
`data/prices.yaml` actualiza el panel económico sin tocar el código fuente.

Contrato de fail-fast (Req 11.1, alineado con `sim.topology`): si
`data/prices.yaml` **falta**, **no se puede parsear** como YAML o no contiene
las secciones requeridas, :func:`cargar_precios` lanza :class:`PreciosInvalidos`.
La excepción es intencionadamente **no recuperable**: pretende abortar el
arranque/cálculo antes de mostrar cifras inventadas.

Alcance de esta tarea (9.2): carga de precios + `capex_total`, `opex_anual`,
`tco`, `break_even`, más funciones de conveniencia para el TCO POL vs cobre a
5 y 10 años (consumidas por la vista de Negocio, tarea 9.8). La propuesta de
valor (tarea 9.4) y el modelo SLA (tarea 9.6) se implementan en sus propias
tareas; este módulo se diseña para poder extenderse allí sin refactor.

Mapeo inventario -> cantidades (design §10.1):

CAPEX POL
    * ``olt_chasis``            -> ``estadio.num_chasis``
    * ``tarjeta_pon``           -> ``estadio.num_tarjetas``
    * ``ont``                   -> ``estadio.num_onts``
    * ``splitter_1x32``         -> ``estadio.num_arboles`` (un splitter por árbol PON)
    * ``fibra_por_metro``       -> metros de fibra de distribución. El inventario
                                   no incluye longitudes; se usa la clave opcional
                                   ``metros_por_arbol`` de la sección ``capex`` (0
                                   por defecto → aporte de fibra nulo, sin inventar
                                   una cifra). Ver :func:`_metros_fibra`.

CAPEX cobre (comparativa, misma cobertura de servicios/puertos)
    * ``switch_acceso``             -> número de switches de acceso, derivado de
                                       los puertos de acceso necesarios (una ONT =
                                       un puerto de servicio) y la densidad de
                                       puertos por switch (``puertos_por_switch``,
                                       opcional, por defecto 48). Ver
                                       :func:`_num_switches_cobre`.
    * ``cableado_utp_por_puerto``   -> ``estadio.num_onts`` (un tendido UTP por
                                       puerto de servicio terminado).

OPEX POL
    * ``energia_por_olt``   -> ``estadio.num_chasis`` (energía por chasis OLT/año)
    * ``mantenimiento_pct`` -> fracción del CAPEX POL/año
    * ``soporte``           -> contrato de soporte/año (cantidad fija)

OPEX cobre (comparativa)
    * ``energia_por_switch`` -> número de switches de acceso (misma derivación que
                               el CAPEX de cobre).

Uso::

    from sim.economics import cargar_precios, resumen_economico
    from sim.topology import cargar_topologia

    precios = cargar_precios()
    estadio = cargar_topologia("azteca")
    resumen = resumen_economico(estadio, precios)   # dict listo para la vista
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sim.state import Estadio

# ---------------------------------------------------------------------------
# Valores por defecto de parámetros de derivación NO económicos.
#
# Estos NO son precios; son supuestos de dimensionamiento que traducen el
# inventario a cantidades facturables. Se leen de `prices.yaml` si están
# presentes (para poder ajustarlos sin código, Req 11.5) y si no, se usan
# estos valores conservadores y documentados. Nunca son un precio monetario.
# ---------------------------------------------------------------------------
PUERTOS_POR_SWITCH_POR_DEFECTO: int = 48    # densidad típica de un switch de acceso
METROS_POR_ARBOL_POR_DEFECTO: float = 0.0   # sin longitudes en el inventario → 0

# Supuestos de dimensionamiento de la Propuesta de valor (§9.5 R10). Tampoco son
# precios: traducen el inventario a cifras físicas (cuartos de telecom y kW).
# Se leen de la sección opcional ``propuesta_valor`` de `prices.yaml` si está
# presente (ajustables sin código, Req 11.5) y, si no, se usan estos valores.
SWITCHES_POR_CUARTO_POR_DEFECTO: int = 4        # switches de acceso por cuarto de telecom (cobre)
WATTS_POR_SWITCH_COBRE_POR_DEFECTO: float = 250.0   # consumo de un switch de acceso de cobre (W)
WATTS_POR_OLT_POR_DEFECTO: float = 600.0            # consumo de un chasis OLT POL (W)


class PreciosInvalidos(Exception):
    """Error fatal de carga de precios económicos (Req 11.5, fail-fast).

    Se lanza cuando `data/prices.yaml` falta, no se puede parsear como YAML,
    su raíz no es un objeto o le faltan secciones/campos requeridos. Es
    intencionadamente **no recuperable**: debe propagarse para abortar antes
    de presentar cifras económicas inventadas.
    """


# ---------------------------------------------------------------------------
# Estructura de precios (validada al cargar)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Precios:
    """Precios y parámetros económicos leídos de `data/prices.yaml`.

    Estructura conforme a design §10.1. Las secciones se validan al cargar
    (fail-fast). Se conserva también el dict crudo (`bruto`) para parámetros
    opcionales de derivación (p.ej. ``metros_por_arbol``, ``puertos_por_switch``).
    """

    capex: dict[str, float]
    opex_anual: dict[str, float]
    horizonte_anios: int
    comparativa_cobre: dict[str, float]
    bruto: dict[str, Any]


# ---------------------------------------------------------------------------
# Ruta de datos (misma raíz que sim.topology)
# ---------------------------------------------------------------------------
def _ruta_prices_por_defecto() -> Path:
    """Ruta a ``data/prices.yaml`` en la raíz del proyecto."""
    return Path(__file__).resolve().parent.parent / "data" / "prices.yaml"


def _requerir_seccion(bruto: dict, clave: str) -> dict:
    """Devuelve ``bruto[clave]`` exigiendo que sea un objeto (dict)."""
    if clave not in bruto:
        raise PreciosInvalidos(
            f"prices.yaml: falta la sección requerida '{clave}'"
        )
    valor = bruto[clave]
    if not isinstance(valor, dict):
        raise PreciosInvalidos(
            f"prices.yaml: la sección '{clave}' debe ser un objeto"
        )
    return valor


def _requerir_num(seccion: dict, clave: str, *, contexto: str) -> float:
    """Devuelve un número de ``seccion[clave]`` o lanza :class:`PreciosInvalidos`."""
    if clave not in seccion:
        raise PreciosInvalidos(
            f"prices.yaml: falta '{clave}' en la sección '{contexto}'"
        )
    valor = seccion[clave]
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise PreciosInvalidos(
            f"prices.yaml: '{contexto}.{clave}' debe ser numérico, no {type(valor).__name__}"
        )
    return float(valor)


def cargar_precios(path: str | Path | None = None) -> Precios:
    """Carga y valida `data/prices.yaml` con fail-fast (Req 11.1/11.5).

    Args:
        path: ruta alternativa al YAML de precios (por defecto ``data/prices.yaml``
            en la raíz del proyecto). Útil para pruebas.

    Returns:
        Un :class:`Precios` validado, con las secciones ``capex``, ``opex_anual``,
        ``comparativa_cobre`` y el ``horizonte_anios``.

    Raises:
        PreciosInvalidos: si el archivo falta, no se puede parsear como YAML, su
            raíz no es un objeto o le faltan secciones/campos requeridos. No se
            captura: está pensada para abortar antes de mostrar cifras inventadas.
    """
    ruta = Path(path) if path is not None else _ruta_prices_por_defecto()

    # 1) Archivo faltante -> fatal.
    if not ruta.is_file():
        raise PreciosInvalidos(
            f"prices.yaml: archivo no encontrado en {ruta}"
        )

    # 2) No parseable como YAML -> fatal.
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            bruto = yaml.safe_load(f)
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        raise PreciosInvalidos(
            f"prices.yaml: no se pudo parsear {ruta}: {exc}"
        ) from exc

    if not isinstance(bruto, dict):
        raise PreciosInvalidos(
            "prices.yaml: el YAML raíz debe ser un objeto (mapa de secciones)"
        )

    # 3) Secciones requeridas (design §10.1).
    capex = _requerir_seccion(bruto, "capex")
    opex = _requerir_seccion(bruto, "opex_anual")
    cobre = _requerir_seccion(bruto, "comparativa_cobre")

    # Campos requeridos dentro de cada sección (los que consumen las fórmulas).
    for clave in ("olt_chasis", "tarjeta_pon", "ont", "splitter_1x32", "fibra_por_metro"):
        _requerir_num(capex, clave, contexto="capex")
    for clave in ("energia_por_olt", "mantenimiento_pct", "soporte"):
        _requerir_num(opex, clave, contexto="opex_anual")
    for clave in ("switch_acceso", "cableado_utp_por_puerto", "energia_por_switch"):
        _requerir_num(cobre, clave, contexto="comparativa_cobre")

    # 4) Horizonte de análisis: entero positivo.
    if "horizonte_anios" not in bruto:
        raise PreciosInvalidos("prices.yaml: falta 'horizonte_anios'")
    horizonte = bruto["horizonte_anios"]
    if isinstance(horizonte, bool) or not isinstance(horizonte, int) or horizonte <= 0:
        raise PreciosInvalidos(
            "prices.yaml: 'horizonte_anios' debe ser un entero positivo"
        )

    return Precios(
        capex=capex,
        opex_anual=opex,
        horizonte_anios=horizonte,
        comparativa_cobre=cobre,
        bruto=bruto,
    )


# ---------------------------------------------------------------------------
# Parámetros de derivación (inventario -> cantidades). No son precios.
# ---------------------------------------------------------------------------
def _metros_fibra(estadio: Estadio, precios: Precios) -> float:
    """Metros de fibra de distribución derivados del inventario.

    El inventario de topología no incluye longitudes de fibra, por lo que el
    aporte de fibra al CAPEX se calcula como ``num_arboles * metros_por_arbol``.
    ``metros_por_arbol`` es un supuesto de dimensionamiento (no un precio) que
    se lee de la sección ``capex`` de `prices.yaml` si está presente y, si no,
    vale 0 por defecto (no se inventa una longitud). De este modo la fibra puede
    activarse editando el archivo, sin tocar código (Req 11.5).
    """
    metros_por_arbol = precios.capex.get(
        "metros_por_arbol", METROS_POR_ARBOL_POR_DEFECTO
    )
    return float(estadio.num_arboles) * float(metros_por_arbol)


def _num_switches_cobre(estadio: Estadio, precios: Precios) -> int:
    """Número de switches de acceso de cobre equivalentes al inventario.

    Una arquitectura de cobre necesita un puerto de switch por cada puerto de
    servicio terminado (cada ONT del despliegue POL equivale a un puerto de
    acceso de cobre). El número de switches es el número de puertos de acceso
    dividido por la densidad de puertos por switch, redondeando hacia arriba.

    ``puertos_por_switch`` es un supuesto de dimensionamiento (no un precio) que
    se lee de la sección ``comparativa_cobre`` si está presente y, si no, vale
    48 por defecto (densidad típica de un switch de acceso). Editable sin código.
    """
    puertos_por_switch = precios.comparativa_cobre.get(
        "puertos_por_switch", PUERTOS_POR_SWITCH_POR_DEFECTO
    )
    if puertos_por_switch <= 0:
        raise PreciosInvalidos(
            "prices.yaml: 'comparativa_cobre.puertos_por_switch' debe ser > 0"
        )
    puertos_acceso = estadio.num_onts
    # Un switch por cada 'puertos_por_switch' puertos de acceso, redondeando
    # hacia arriba: un puerto sobrante exige un switch adicional completo.
    return math.ceil(puertos_acceso / float(puertos_por_switch))


# ---------------------------------------------------------------------------
# CAPEX
# ---------------------------------------------------------------------------
def capex_total(estadio: Estadio, precios: Precios) -> float:
    """CAPEX total de la arquitectura POL derivado del inventario (design §10.1).

    Suma: chasis OLT, tarjetas PON, ONTs, splitters (uno por árbol) y fibra de
    distribución. Todas las cantidades provienen del inventario del `estadio`;
    todos los precios de `precios` (leídos de `prices.yaml`, nunca hardcodeados).
    """
    c = precios.capex
    total = (
        estadio.num_chasis * c["olt_chasis"]
        + estadio.num_tarjetas * c["tarjeta_pon"]
        + estadio.num_onts * c["ont"]
        + estadio.num_arboles * c["splitter_1x32"]
        + _metros_fibra(estadio, precios) * c["fibra_por_metro"]
    )
    return float(total)


def capex_cobre(estadio: Estadio, precios: Precios) -> float:
    """CAPEX de la arquitectura de cobre equivalente (comparativa, design §10.1).

    Suma: switches de acceso (derivados de los puertos de servicio) y cableado
    UTP (un tendido por puerto de servicio = por ONT).
    """
    cobre = precios.comparativa_cobre
    total = (
        _num_switches_cobre(estadio, precios) * cobre["switch_acceso"]
        + estadio.num_onts * cobre["cableado_utp_por_puerto"]
    )
    return float(total)


# ---------------------------------------------------------------------------
# OPEX anual
# ---------------------------------------------------------------------------
def opex_anual(estadio: Estadio, precios: Precios) -> float:
    """OPEX anual de la arquitectura POL (design §10.1).

    Suma: energía por chasis OLT, mantenimiento como fracción del CAPEX POL y
    contrato de soporte anual. El mantenimiento se calcula sobre el CAPEX POL
    real (derivado del inventario), no sobre un valor fijo.
    """
    o = precios.opex_anual
    total = (
        estadio.num_chasis * o["energia_por_olt"]
        + o["mantenimiento_pct"] * capex_total(estadio, precios)
        + o["soporte"]
    )
    return float(total)


def opex_anual_cobre(estadio: Estadio, precios: Precios) -> float:
    """OPEX anual de la arquitectura de cobre equivalente (comparativa).

    Energía por switch de acceso más mantenimiento como fracción del CAPEX de
    cobre (misma política de mantenimiento que POL, para comparar en igualdad).
    """
    cobre = precios.comparativa_cobre
    mantenimiento_pct = precios.opex_anual["mantenimiento_pct"]
    total = (
        _num_switches_cobre(estadio, precios) * cobre["energia_por_switch"]
        + mantenimiento_pct * capex_cobre(estadio, precios)
    )
    return float(total)


# ---------------------------------------------------------------------------
# TCO
# ---------------------------------------------------------------------------
def tco(capex: float, opex: float, anios: int) -> float:
    """Costo total de propiedad a ``anios`` años: CAPEX + OPEX * años (design §10.1)."""
    return capex + opex * anios


# ---------------------------------------------------------------------------
# Break-even (punto de equilibrio)
# ---------------------------------------------------------------------------
def break_even(
    capex_pol: float,
    opex_pol: float,
    capex_cobre_: float,
    opex_cobre: float,
    horizonte_anios: int,
) -> int | None:
    """Primer año dentro del horizonte en que el TCO de POL cruza al de cobre.

    Semántica (Req 11.3/11.4, design §10.1, Property 11):

    * Se recorren los años ``1..horizonte_anios`` (inclusive). Para cada año se
      compara el TCO acumulado de POL con el de cobre a ese mismo año.
    * Se devuelve el **primer** año en que ``TCO_POL <= TCO_cobre`` (Req 11.3):
      la desigualdad usa ``<=``, de modo que la **igualdad exacta cuenta como
      cruce**.
    * Si POL nunca alcanza al cobre dentro del horizonte, se devuelve ``None``
      para indicar la ausencia de punto de equilibrio (Req 11.4).

    Nota de convención: el año se cuenta desde 1 (fin del primer año de
    operación). El año 0 (solo CAPEX, sin OPEX) no se considera punto de
    equilibrio operativo; el barrido empieza en el año 1.
    """
    for anio in range(1, horizonte_anios + 1):
        tco_pol = tco(capex_pol, opex_pol, anio)
        tco_cobre = tco(capex_cobre_, opex_cobre, anio)
        if tco_pol <= tco_cobre:
            return anio
    return None


# ---------------------------------------------------------------------------
# Conveniencia para la vista de Negocio (tarea 9.8)
# ---------------------------------------------------------------------------
def resumen_economico(estadio: Estadio, precios: Precios) -> dict[str, Any]:
    """Resumen económico POL vs cobre listo para la Capa de Presentación (Req 11.1/11.2).

    Devuelve CAPEX, OPEX anual, TCO a 5 y 10 años de ambas arquitecturas y el
    año de punto de equilibrio. Todo derivado del inventario y de `prices.yaml`;
    la vista solo formatea (ningún cálculo en callbacks, design §3.1).

    Estructura del dict devuelto::

        {
          "capex":   {"pol": float, "cobre": float},
          "opex":    {"pol": float, "cobre": float},
          "tco_5":   {"pol": float, "cobre": float},
          "tco_10":  {"pol": float, "cobre": float},
          "break_even": int | None,        # año o None si no cruza en el horizonte
          "horizonte_anios": int,
        }
    """
    capex_pol = capex_total(estadio, precios)
    capex_cu = capex_cobre(estadio, precios)
    opex_pol = opex_anual(estadio, precios)
    opex_cu = opex_anual_cobre(estadio, precios)

    return {
        "capex": {"pol": capex_pol, "cobre": capex_cu},
        "opex": {"pol": opex_pol, "cobre": opex_cu},
        "tco_5": {
            "pol": tco(capex_pol, opex_pol, 5),
            "cobre": tco(capex_cu, opex_cu, 5),
        },
        "tco_10": {
            "pol": tco(capex_pol, opex_pol, 10),
            "cobre": tco(capex_cu, opex_cu, 10),
        },
        "break_even": break_even(
            capex_pol, opex_pol, capex_cu, opex_cu, precios.horizonte_anios
        ),
        "horizonte_anios": precios.horizonte_anios,
    }


# ===========================================================================
# Propuesta de valor (§9.5 R10)
#
# Sección autocontenida de la tarea 9.4. NO toca las funciones económicas
# anteriores (CAPEX/OPEX/TCO/break-even) ni el alcance de escalabilidad (9.5) o
# SLA (9.6), que se implementan en sus propias tareas sobre este mismo módulo.
#
# Responde "por qué POL en lugar de cobre" con TRES cifras derivadas por
# completo del inventario de topología del `estadio` (Req 10.2), de modo que se
# recalculan solas cuando el inventario cambia (Req 10.3). Los supuestos de
# dimensionamiento (switches por cuarto, vatios por equipo) se leen de la
# sección opcional ``propuesta_valor`` de `prices.yaml` (ajustables sin código,
# Req 11.5); si falta una clave se usa un valor por defecto documentado. Ningún
# número de estas cifras se escribe a mano (criterio de aceptación de Req 10).
#
# Derivaciones (todas parten de contadores del inventario):
#
# 1) Puertos de conmutación eliminados
#    Una arquitectura de cobre necesita **un puerto de switch activo por cada
#    puerto de servicio terminado**; en el despliegue POL cada servicio termina
#    en una ONT, luego hay ``num_onts`` puertos de servicio. POL los sirve a
#    través de splitters ópticos **pasivos** (sin puertos de conmutación
#    activos), por lo que elimina los ``num_onts`` puertos de switch que el
#    cobre necesitaría. Se define, pues, como ``estadio.num_onts``. (Coincide
#    con la base de puertos usada por `_num_switches_cobre`, manteniendo el
#    modelo consistente con la comparativa económica de cobre.)
#
# 2) Cuartos de telecom eliminados
#    El cobre agrupa sus switches de acceso en cuartos de telecom
#    (distribución): ``ceil(switches_de_cobre / switches_por_cuarto)`` cuartos,
#    reutilizando el mismo conteo de switches de cobre de la comparativa
#    económica (`_num_switches_cobre`). POL solo necesita el/los cuarto(s) de
#    cabecera donde vive(n) el/los chasis OLT (``num_chasis``); los splitters,
#    al ser pasivos, no requieren cuartos intermedios energizados. Los cuartos
#    eliminados son ``cuartos_cobre - num_chasis``, acotado a >= 0 (nunca
#    negativo: si el modelo diera menos cuartos de cobre que chasis OLT, el
#    ahorro es 0, no una cifra negativa sin sentido físico).
#
# 3) Kilovatios ahorrados
#    Se modela la **potencia** (kW), no el costo anual de energía (ese ya lo
#    cubre el OPEX). La potencia se deriva de los contadores de equipo con un
#    consumo documentado por dispositivo: cobre = ``switches_de_cobre *
#    watts_por_switch_cobre``; POL = ``num_chasis * watts_por_olt`` (los
#    splitters pasivos no consumen). El ahorro es la diferencia en kW,
#    ``(potencia_cobre_w - potencia_pol_w) / 1000``, acotado a >= 0.
# ===========================================================================


def _switches_por_cuarto(precios: Precios) -> int:
    """Switches de acceso de cobre por cuarto de telecom (supuesto, no precio).

    Se lee de ``propuesta_valor.switches_por_cuarto`` en `prices.yaml` si está
    presente; si no, vale 4 por defecto. Debe ser > 0 para poder dividir.
    """
    seccion = precios.bruto.get("propuesta_valor", {})
    valor = seccion.get("switches_por_cuarto", SWITCHES_POR_CUARTO_POR_DEFECTO)
    if valor <= 0:
        raise PreciosInvalidos(
            "prices.yaml: 'propuesta_valor.switches_por_cuarto' debe ser > 0"
        )
    return int(valor)


def _watts_por_switch_cobre(precios: Precios) -> float:
    """Consumo eléctrico de un switch de acceso de cobre en vatios (supuesto).

    Se lee de ``propuesta_valor.watts_por_switch_cobre`` si está presente; si
    no, vale 250 W por defecto. No es un precio monetario.
    """
    seccion = precios.bruto.get("propuesta_valor", {})
    return float(
        seccion.get("watts_por_switch_cobre", WATTS_POR_SWITCH_COBRE_POR_DEFECTO)
    )


def _watts_por_olt(precios: Precios) -> float:
    """Consumo eléctrico de un chasis OLT POL en vatios (supuesto).

    Se lee de ``propuesta_valor.watts_por_olt`` si está presente; si no, vale
    600 W por defecto. No es un precio monetario.
    """
    seccion = precios.bruto.get("propuesta_valor", {})
    return float(seccion.get("watts_por_olt", WATTS_POR_OLT_POR_DEFECTO))


def puertos_conmutacion_eliminados(estadio: Estadio, precios: Precios) -> int:
    """Puertos de switch activos que POL elimina frente a cobre (Req 10.2).

    Cada puerto de servicio (una ONT del inventario) exigiría un puerto de
    switch de acceso en una arquitectura de cobre; POL los sirve con splitters
    pasivos, sin puertos de conmutación activos. Devuelve ``estadio.num_onts``.

    El parámetro ``precios`` se acepta por consistencia de firma con las demás
    funciones de esta sección (y por si un modelo futuro lo necesitara); esta
    cifra solo depende del inventario.
    """
    return int(estadio.num_onts)


def cuartos_telecom_eliminados(estadio: Estadio, precios: Precios) -> int:
    """Cuartos de telecom que POL elimina frente a cobre (Req 10.2).

    Cobre: ``ceil(switches_de_cobre / switches_por_cuarto)`` cuartos de
    distribución. POL: solo el/los cuarto(s) de cabecera del/los chasis OLT
    (``num_chasis``). Resultado = cuartos de cobre menos cuartos POL, acotado a
    ``>= 0``. Todo derivado del inventario; ``switches_por_cuarto`` es un
    supuesto editable en `prices.yaml`.
    """
    switches_cobre = _num_switches_cobre(estadio, precios)
    cuartos_cobre = math.ceil(switches_cobre / _switches_por_cuarto(precios))
    cuartos_pol = estadio.num_chasis
    return max(0, cuartos_cobre - cuartos_pol)


def kilovatios_ahorrados(estadio: Estadio, precios: Precios) -> float:
    """Potencia eléctrica (kW) que POL ahorra frente a cobre (Req 10.2).

    Potencia de cobre = ``switches_de_cobre * watts_por_switch_cobre``;
    potencia de POL = ``num_chasis * watts_por_olt`` (splitters pasivos: 0 W).
    Devuelve el ahorro en kW, ``(cobre_w - pol_w) / 1000``, acotado a ``>= 0``.
    Los vatios por equipo son supuestos editables en `prices.yaml`; los
    contadores provienen del inventario.
    """
    switches_cobre = _num_switches_cobre(estadio, precios)
    potencia_cobre_w = switches_cobre * _watts_por_switch_cobre(precios)
    potencia_pol_w = estadio.num_chasis * _watts_por_olt(precios)
    ahorro_w = potencia_cobre_w - potencia_pol_w
    return max(0.0, ahorro_w / 1000.0)


def propuesta_valor(estadio: Estadio, precios: Precios) -> dict[str, Any]:
    """Propuesta de valor POL vs cobre: tres cifras del inventario (§9.5 R10).

    Reúne las tres cifras que argumentan la elección de POL frente a cobre,
    todas derivadas del inventario de topología (Req 10.2) y por tanto
    recalculadas automáticamente al cambiar el inventario (Req 10.3). Lista para
    que la Capa de Presentación (tarea 9.8) solo la formatee: ningún cálculo en
    callbacks (design §3.1).

    Estructura del dict devuelto::

        {
          "puertos_conmutacion_eliminados": int,   # puertos de switch activos evitados
          "cuartos_telecom_eliminados":     int,   # cuartos de distribución evitados
          "kilovatios_ahorrados":           float, # kW de potencia ahorrada
        }
    """
    return {
        "puertos_conmutacion_eliminados": puertos_conmutacion_eliminados(
            estadio, precios
        ),
        "cuartos_telecom_eliminados": cuartos_telecom_eliminados(estadio, precios),
        "kilovatios_ahorrados": kilovatios_ahorrados(estadio, precios),
    }


# ===========================================================================
# Escalabilidad (§9.5 R12)
#
# Sección autocontenida de la tarea 9.5. NO toca las funciones económicas
# (CAPEX/OPEX/TCO/break-even) ni la sección de Propuesta de valor (9.4); la
# sección de SLA (9.6) se añadirá después, también autocontenida, sobre este
# mismo módulo.
#
# Responde dos preguntas de planeación de capacidad (Req 12.2/12.3) reutilizando
# la MISMA lógica de dimensionamiento que `tools/gen_topology.py` (design §5.3),
# no un modelo paralelo inventado:
#
#   ONTs  --(ratio de split)-->  árboles PON
#   árboles  --(1 árbol activo por puerto activo)-->  puertos activos
#   puertos (activos + reserva)  --(puertos por tarjeta)-->  tarjetas
#   tarjetas  --(tarjetas por chasis)-->  chasis
#
# Reuso explícito de gen_topology:
#   * ``RATIO_SPLIT`` (splitter 1:32) se importa de `tools.gen_topology` como la
#     COTA FÍSICA superior de ONTs por árbol: ningún árbol puede colgar más de
#     ``RATIO_SPLIT`` ONTs de un splitter 1:32. Se importa para no duplicar la
#     constante; si el import fallara (p.ej. empaquetado sin `tools/`), se cae a
#     un valor de respaldo documentado idéntico (32).
#
# Criterio de consistencia (identidad a factor 0.0):
#   El recálculo debe REPRODUCIR el inventario actual cuando el crecimiento es
#   0%. Por eso los ratios de dimensionamiento se DERIVAN del propio inventario
#   del `estadio` (no se hardcodean ni se recalculan desde una regla de llenado
#   distinta): son exactamente las proporciones con las que `gen_topology`
#   construyó ese estadio.
#
#   En particular la densidad ONTs/árbol se toma del inventario real
#   (``num_onts / num_arboles``, ~34 en Azteca por el "overbooking acotado" que
#   documenta gen_topology), NO como ``ceil(onts / 32)``: usar la regla cruda de
#   32 daría más árboles que los desplegados y rompería la identidad a 0%.
#   ``RATIO_SPLIT`` (1:32) es la razón NOMINAL del splitter y se conserva solo
#   como referencia documental; la densidad efectiva del despliegue puede
#   excederla por diseño (overbooking), por lo que no se usa como tope.
#
#   Los demás ratios (puertos por árbol -incluye la reserva de protección Type
#   B-, puertos por tarjeta, tarjetas por chasis) también salen del inventario.
#   Así cada estadio escala con su propia densidad de ingeniería (Req 12.3) y se
#   recalcula solo si el inventario cambia, sin cifras a mano.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - selección de import según empaquetado
    from tools.gen_topology import RATIO_SPLIT as _RATIO_SPLIT
except ModuleNotFoundError:  # pragma: no cover
    # Respaldo documentado: mismo valor que la constante de gen_topology
    # (splitter 1:32). Se usa solo si el paquete `tools` no está disponible.
    _RATIO_SPLIT = 32


def _ratios_dimensionamiento(estadio: Estadio) -> dict[str, float]:
    """Ratios de dimensionamiento derivados del inventario actual (design §5.3).

    Reconstruye, a partir de los contadores del `estadio`, las mismas
    proporciones con las que `gen_topology` dimensionó esa topología, de modo
    que aplicarlas al inventario actual lo reproduce (identidad a 0% de
    crecimiento):

    * ``onts_por_arbol``     -> densidad real de ONTs por árbol del inventario
      (``num_onts / num_arboles``, ~34 en Azteca). Es la densidad efectiva del
      despliegue (puede exceder la razón nominal 1:32 por overbooking acotado,
      design §5.3); se usa tal cual para que a 0% de crecimiento el recálculo
      reproduzca los árboles actuales.
    * ``ratio_split``        -> razón NOMINAL del splitter ``RATIO_SPLIT`` (1:32),
      solo como referencia documental (no se usa como tope del cálculo).
    * ``puertos_por_arbol``  -> puertos totales por árbol activo. Es >= 1 porque
      además de un puerto activo por árbol hay puertos de reserva para la
      conmutación de protección Type B (design §5.2). Se preserva esta densidad
      al crecer para no perder la reserva de protección.
    * ``puertos_por_tarjeta`` -> capacidad de puertos de una tarjeta PON (techo
      del reparto actual: la tarjeta más cargada define la capacidad).
    * ``tarjetas_por_chasis`` -> capacidad de tarjetas de un chasis OLT.

    Todos se obtienen del propio inventario (no se hardcodean), de modo que cada
    estadio escala con su propia densidad de ingeniería.
    """
    num_arboles = max(1, estadio.num_arboles)
    num_tarjetas = max(1, estadio.num_tarjetas)
    num_chasis = max(1, estadio.num_chasis)
    # Densidad efectiva de ONTs/árbol del inventario (puede exceder 1:32 por
    # overbooking acotado, design §5.3). Se usa tal cual: es la razón con la que
    # se dimensionó el estadio, por lo que reproduce el inventario a 0%.
    onts_por_arbol = estadio.num_onts / num_arboles
    return {
        "onts_por_arbol": onts_por_arbol,
        "ratio_split": float(_RATIO_SPLIT),
        # Puertos por árbol activo (incluye la cuota de puertos de reserva).
        "puertos_por_arbol": estadio.num_puertos / num_arboles,
        # Capacidad de puertos por tarjeta: techo del reparto actual.
        "puertos_por_tarjeta": math.ceil(estadio.num_puertos / num_tarjetas),
        # Capacidad de tarjetas por chasis: mismo criterio (techo del reparto).
        "tarjetas_por_chasis": math.ceil(estadio.num_tarjetas / num_chasis),
    }


def _dimensionar_desde_onts(estadio: Estadio, num_onts: int) -> dict[str, int]:
    """Dimensiona puertos/tarjetas/chasis para ``num_onts`` (lógica de §5.3).

    Aplica en cascada la MISMA lógica de gen_topology usando los ratios del
    `estadio` (:func:`_ratios_dimensionamiento`):

        árboles  = ceil(num_onts / onts_por_arbol)
        puertos  = ceil(árboles * puertos_por_arbol)
        tarjetas = ceil(puertos / puertos_por_tarjeta)
        chasis   = ceil(tarjetas / tarjetas_por_chasis)

    Como ``onts_por_arbol`` es la densidad real del inventario, con ``num_onts``
    igual al actual la cascada reproduce los contadores actuales (identidad a
    0%). Todos los redondeos son hacia arriba: una fracción de
    árbol/puerto/tarjeta exige la unidad completa (no se despliega media
    tarjeta). Devuelve un dict de enteros con los cinco contadores.
    """
    if num_onts < 0:
        raise ValueError("num_onts no puede ser negativo")
    r = _ratios_dimensionamiento(estadio)

    num_arboles = math.ceil(num_onts / r["onts_por_arbol"]) if num_onts > 0 else 0
    num_puertos = math.ceil(num_arboles * r["puertos_por_arbol"])
    num_tarjetas = (
        math.ceil(num_puertos / r["puertos_por_tarjeta"]) if num_puertos > 0 else 0
    )
    num_chasis = (
        math.ceil(num_tarjetas / r["tarjetas_por_chasis"]) if num_tarjetas > 0 else 0
    )
    return {
        "num_onts": int(num_onts),
        "num_arboles": int(num_arboles),
        "num_puertos": int(num_puertos),
        "num_tarjetas": int(num_tarjetas),
        "num_chasis": int(num_chasis),
    }


def holgura_onts(estadio: Estadio) -> int:
    """ONTs adicionales que caben antes de requerir una tarjeta nueva (Req 12.2).

    Modelo (design §5.3): las tarjetas actuales aportan una capacidad de puertos
    de ``num_tarjetas * puertos_por_tarjeta``. Sobre esa capacidad de puertos,
    manteniendo la densidad de puertos por árbol del estadio (que reserva
    puertos para la protección Type B), caben como máximo::

        puertos_disponibles = num_tarjetas * puertos_por_tarjeta
        arboles_max = floor(puertos_disponibles / puertos_por_arbol)
        onts_max    = floor(arboles_max * onts_por_arbol)

    La holgura es ``onts_max - num_onts`` (nunca negativa). En cuanto se supera
    ``onts_max`` el dimensionamiento exige una tarjeta adicional, que es
    justamente el umbral que responde "cuántas ONTs más caben antes de añadir
    una tarjeta".

    Notas de modelo:

    * Se usa ``floor`` para los árboles que caben en los puertos disponibles (un
      árbol solo cuenta si cabe entero) y para las ONTs por árbol (una ONT solo
      cuenta si cabe entera).
    * ``onts_por_arbol`` es la densidad real del inventario y los demás ratios
      también salen del estadio (:func:`_ratios_dimensionamiento`), por lo que la
      respuesta se recalcula sola si el inventario cambia (Req 12.3) y es
      coherente con :func:`recalcular_dimensionamiento`.
    """
    r = _ratios_dimensionamiento(estadio)
    puertos_disponibles = estadio.num_tarjetas * r["puertos_por_tarjeta"]
    arboles_max = math.floor(puertos_disponibles / r["puertos_por_arbol"])
    onts_max = int(math.floor(arboles_max * r["onts_por_arbol"]))
    return max(0, onts_max - estadio.num_onts)


def recalcular_dimensionamiento(
    estadio: Estadio, factor_crecimiento: float
) -> dict[str, Any]:
    """Recalcula el dimensionamiento ante un crecimiento de aforo (Req 12.3/12.5).

    Dado un factor de crecimiento de aforo (fracción; p.ej. ``0.20`` para +20%),
    escala las ONTs y vuelve a dimensionar puertos, tarjetas y chasis con la
    MISMA lógica del dimensionamiento (:func:`_dimensionar_desde_onts`), no un
    modelo paralelo. Soporta el caso +20% requerido (Req 12.5) y cualquier otro
    factor >= 0 (0.0 reproduce el inventario actual).

    El nuevo total de ONTs es ``round(num_onts * (1 + factor))``: se redondea a
    entero porque las ONTs son unidades discretas. A partir de ahí, la cascada
    de dimensionamiento produce los nuevos contadores.

    La bandera ``requiere_hardware_adicional`` es ``True`` cuando el recálculo
    necesita **al menos una tarjeta o un chasis nuevo** frente al inventario
    actual, es decir, cuando se supera la capacidad de hardware instalada. La
    vista (tarea 9.8) usa esta bandera para mostrar una advertencia visible
    (Req 12.4). Un crecimiento que solo consuma holgura de puertos existentes
    (sin sumar tarjetas ni chasis) deja la bandera en ``False``.

    Args:
        estadio: inventario actual del estadio activo.
        factor_crecimiento: fracción de crecimiento de aforo (>= 0). ``0.20`` =
            +20%. Se valida que no sea negativo.

    Returns:
        Dict listo para la Capa de Presentación::

            {
              "factor_crecimiento": float,       # eco del factor aplicado
              "actual": {                        # inventario de partida
                 "num_onts", "num_arboles", "num_puertos",
                 "num_tarjetas", "num_chasis"
              },
              "nuevo": {                         # dimensionamiento recalculado
                 "num_onts", "num_arboles", "num_puertos",
                 "num_tarjetas", "num_chasis"
              },
              "delta": {                         # nuevo - actual (>= 0)
                 "num_onts", "num_arboles", "num_puertos",
                 "num_tarjetas", "num_chasis"
              },
              "tarjetas_adicionales": int,       # tarjetas nuevas requeridas
              "chasis_adicionales": int,         # chasis nuevos requeridos
              "requiere_hardware_adicional": bool,  # advertencia visible (Req 12.4)
            }
    """
    if factor_crecimiento < 0:
        raise ValueError("factor_crecimiento no puede ser negativo")

    actual = {
        "num_onts": int(estadio.num_onts),
        "num_arboles": int(estadio.num_arboles),
        "num_puertos": int(estadio.num_puertos),
        "num_tarjetas": int(estadio.num_tarjetas),
        "num_chasis": int(estadio.num_chasis),
    }

    onts_nuevas = int(round(estadio.num_onts * (1.0 + factor_crecimiento)))
    nuevo = _dimensionar_desde_onts(estadio, onts_nuevas)

    delta = {k: nuevo[k] - actual[k] for k in actual}
    tarjetas_adicionales = max(0, delta["num_tarjetas"])
    chasis_adicionales = max(0, delta["num_chasis"])

    return {
        "factor_crecimiento": float(factor_crecimiento),
        "actual": actual,
        "nuevo": nuevo,
        "delta": delta,
        "tarjetas_adicionales": tarjetas_adicionales,
        "chasis_adicionales": chasis_adicionales,
        "requiere_hardware_adicional": (
            tarjetas_adicionales > 0 or chasis_adicionales > 0
        ),
    }


# ===========================================================================
# Modelo SLA (§10.2 R13)
#
# Sección autocontenida de la tarea 9.6. NO toca las funciones económicas
# (CAPEX/OPEX/TCO/break-even), ni la sección de Propuesta de valor (9.4), ni la
# de Escalabilidad (9.5): se añade al final del módulo sin modificar nada de lo
# anterior. Python puro (sin Dash, design §3.1).
#
# Responde el panel de SLA de la vista de Negocio (Req 13): para un objetivo de
# disponibilidad contractual (por esquema de protección), cuántos minutos de
# indisponibilidad al año están permitidos y qué fracción de ese presupuesto ha
# consumido la corrida actual.
#
# Reutilización explícita (no se reinventa nada):
#   * ``disponibilidad_servicio`` (sim.state, §6.5) calcula la disponibilidad
#     observada ponderada por ticks de una lista de ONTs. Aquí se importa tal
#     cual; la indisponibilidad observada es ``1 - disponibilidad_servicio``.
#   * ``objetivo_sla`` (sim.state, §6.5) mapea una :class:`Proteccion` a su
#     objetivo teórico (TYPE_C→0.99999, TYPE_B→0.9999, NINGUNA→0.999). El helper
#     :func:`presupuesto_indisponibilidad_min_proteccion` lo reutiliza para
#     exponer el presupuesto por esquema de protección sin duplicar la tabla.
#
# Modelo (design §10.2, Req 13.2/13.3):
#
#   Presupuesto anual permitido (minutos/año)
#       Un objetivo de disponibilidad ``objetivo`` (fracción 0..1) admite una
#       fracción de indisponibilidad de ``1 - objetivo``. Sobre un año de
#       365 días * 24 h * 60 min = 525 600 min, eso son
#       ``(1 - objetivo) * 365 * 24 * 60`` minutos/año. De ahí salen las cifras
#       de referencia: 0.99999→5.256, 0.9999→52.56, 0.999→525.6 min/año.
# ---------------------------------------------------------------------------

from sim.state import ONT, Proteccion, disponibilidad_servicio, objetivo_sla

# Minutos en un año (365 días, sin corrección bisiesta): 365 * 24 * 60.
MINUTOS_POR_ANIO: float = 365.0 * 24.0 * 60.0  # 525 600 min/año


def presupuesto_indisponibilidad_min(objetivo: float) -> float:
    """Minutos de indisponibilidad permitidos al año para el ``objetivo`` (0..1).

    Design §10.2 / Req 13.2/13.3. Un objetivo de disponibilidad ``objetivo``
    (fracción, p.ej. 0.9999 para 99.99%) admite una fracción de indisponibilidad
    de ``1 - objetivo``; multiplicada por los minutos de un año da los minutos
    de indisponibilidad permitidos al año::

        (1 - objetivo) * 365 * 24 * 60

    Cifras de referencia (Req 13.3):

    * ``presupuesto_indisponibilidad_min(0.99999) ≈ 5.26``  min/año (cinco nueves)
    * ``presupuesto_indisponibilidad_min(0.9999)  ≈ 52.6``  min/año (cuatro nueves)
    * ``presupuesto_indisponibilidad_min(0.999)   ≈ 525.6`` min/año (tres nueves)

    Args:
        objetivo: objetivo de disponibilidad como fracción en ``[0, 1]``.

    Returns:
        Minutos de indisponibilidad permitidos al año (``>= 0``).
    """
    return (1.0 - objetivo) * MINUTOS_POR_ANIO


def presupuesto_indisponibilidad_min_proteccion(proteccion: Proteccion) -> float:
    """Presupuesto anual de indisponibilidad (min/año) según el esquema de protección.

    Conveniencia que reutiliza :func:`sim.state.objetivo_sla` (la tabla única de
    objetivos por protección, §6.5) para no duplicar la correspondencia
    protección→objetivo. Equivale a
    ``presupuesto_indisponibilidad_min(objetivo_sla(proteccion))``.

    Args:
        proteccion: esquema de protección (TYPE_C, TYPE_B o NINGUNA).

    Returns:
        Minutos de indisponibilidad permitidos al año para ese esquema.
    """
    return presupuesto_indisponibilidad_min(objetivo_sla(proteccion))


def presupuesto_consumido(onts: list[ONT], objetivo: float) -> float:
    """Fracción del presupuesto anual de indisponibilidad consumida (design §10.2, Req 13.4).

    Devuelve qué fracción del presupuesto anual de indisponibilidad permitido
    por ``objetivo`` ha consumido la corrida actual, a partir de la
    indisponibilidad **observada** en la lista de ONTs (ponderada por ticks) y
    extrapolada a un año.

    Modelo de extrapolación (documentado paso a paso):

    1. **Fracción de indisponibilidad observada.** De la disponibilidad
       observada ``d = disponibilidad_servicio(onts)`` (§6.5, ponderada por
       ticks) se obtiene la fracción de tiempo fuera de servicio ``1 - d``. Esta
       fracción es adimensional: no depende de cuántos ticks se hayan corrido,
       solo de la proporción de ticks fuera de línea.

    2. **Extrapolación a minutos/año.** Si esa fracción se mantuviera durante
       todo un año, los minutos de indisponibilidad al año serían
       ``(1 - d) * MINUTOS_POR_ANIO`` (los mismos 525 600 min/año que usa el
       presupuesto). Es el "consumo" anualizado observado.

    3. **Fracción del presupuesto.** El presupuesto permitido es
       ``presupuesto_indisponibilidad_min(objetivo) = (1 - objetivo) *
       MINUTOS_POR_ANIO``. La fracción consumida es el cociente::

           consumido = [ (1 - d) * MINUTOS_POR_ANIO ]
                       / [ (1 - objetivo) * MINUTOS_POR_ANIO ]
                     = (1 - d) / (1 - objetivo)

       Los ``MINUTOS_POR_ANIO`` se cancelan: la fracción consumida es
       simplemente la indisponibilidad observada dividida por la
       indisponibilidad permitida. Interpretación: ``0.0`` = sin consumo
       (disponibilidad perfecta), ``1.0`` = presupuesto justo agotado
       (indisponibilidad observada igual a la permitida), ``> 1.0`` = presupuesto
       excedido (la corrida incumpliría el SLA anualizado).

    Guardas (Req 8.4, evitar divisiones indefinidas):

    * **Sin ticks observados.** ``disponibilidad_servicio`` devuelve ``1.0``
      cuando no hay ticks acumulados (§6.5), por lo que la indisponibilidad
      observada es ``0`` y el consumo es ``0.0`` (nada consumido todavía).
    * **Objetivo == 1.0 (o >= 1.0).** El presupuesto permitido sería ``0``
      minutos (disponibilidad perfecta exigida), lo que haría indefinida la
      división. Se resuelve por el límite físico: si no hay indisponibilidad
      observada el consumo es ``0.0``; si la hay, el presupuesto (cero) está
      excedido y se devuelve ``inf``.

    Args:
        onts: ONTs del servicio (o del conjunto) cuya disponibilidad observada
            se mide; se ponderan por ticks vía :func:`disponibilidad_servicio`.
        objetivo: objetivo de disponibilidad contractual como fracción ``[0, 1]``.

    Returns:
        Fracción del presupuesto anual consumida (``>= 0``; puede exceder 1.0 si
        se sobrepasa el presupuesto). ``float('inf')`` si ``objetivo >= 1`` y hay
        indisponibilidad observada.
    """
    indisponibilidad_observada = 1.0 - disponibilidad_servicio(onts)
    # Sin indisponibilidad observada no hay consumo, con independencia del
    # objetivo (evita 0/0 cuando el objetivo es 1.0).
    if indisponibilidad_observada <= 0.0:
        return 0.0
    indisponibilidad_permitida = 1.0 - objetivo
    # Objetivo de disponibilidad perfecta (presupuesto de cero minutos): con
    # indisponibilidad observada > 0 el presupuesto está excedido → infinito.
    if indisponibilidad_permitida <= 0.0:
        return float("inf")
    return indisponibilidad_observada / indisponibilidad_permitida
