"""Modelo de estado estructural del Motor de Simulación (design §5).

Python puro: este módulo **no importa nada de Dash** (principio de
separación, design §3.1). Contiene las enumeraciones y las dataclasses de
topología y equipo que son la representación canónica del estado estructural.

`SimState` (el estado dinámico completo de una corrida) se define aquí
(design §6.1); el reloj y el bucle `tick` viven en `sim.engine`. Los
acumuladores de disponibilidad de nivel superior también residen en este
módulo (§6.5, tarea 5.5).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones de tipo
    # Alarmas (tarea 7.5) y eventos (log de inyecciones/goles) se definen en
    # sus propios módulos. Se referencian por nombre para no crear una
    # dependencia real y permitir importar este módulo de forma aislada.
    from sim.alarms import Alarma
    from sim.events import Evento


# ---------------------------------------------------------------------------
# 5.1 Enumeraciones
# ---------------------------------------------------------------------------
class Estado(Enum):
    EN_LINEA = "en_linea"      # operando normal
    DEGRADADO = "degradado"    # opera pero con senal/desempeno reducido
    FUERA = "fuera"            # sin servicio
    RESERVA = "reserva"        # puerto/tarjeta en espera (proteccion)


class Proteccion(Enum):
    NINGUNA = "ninguna"        # sin redundancia
    TYPE_B = "type_b"          # puerto OLT redundante (protege lado OLT)
    TYPE_C = "type_c"          # redundancia completa (protege OLT + fibra troncal + splitter)


class Servicio(Enum):
    PALCOS = "palcos"
    WIFI = "wifi"
    CCTV = "cctv"
    TORNIQUETES = "torniquetes"
    POS = "pos"
    SIGNAGE = "signage"
    MACRO = "macro"
    VOZ = "voz"
    PRENSA = "prensa"
    ESPORTS = "esports"


# ---------------------------------------------------------------------------
# 5.2 Dataclasses de topología y equipo
# ---------------------------------------------------------------------------
@dataclass
class ONT:
    id: str
    servicio: Servicio
    arbol_id: str
    estado: Estado = Estado.EN_LINEA
    proteccion: Proteccion = Proteccion.NINGUNA
    bw_demandado_mbps: float = 0.0      # demanda instantanea del servicio
    bw_asignado_mbps: float = 0.0       # lo que realmente recibe tras contencion
    potencia_optica_dbm: float = -22.0  # nivel optico simulado
    # Acumuladores de disponibilidad (seccion 6.5)
    ticks_totales: int = 0
    ticks_en_linea: int = 0


@dataclass
class ArbolPON:
    id: str
    puerto_id: str
    ratio_split: int                    # p.ej. 1:32, 1:64
    onts: list[ONT] = field(default_factory=list)
    estado: Estado = Estado.EN_LINEA
    proteccion: Proteccion = Proteccion.NINGUNA
    atenuacion_db: float = 0.0          # degradacion del arbol (falla optica)


@dataclass
class PuertoPON:
    id: str
    tarjeta_id: str
    capacidad_mbps: float               # capacidad nominal del puerto (p.ej. XGS-PON)
    capacidad_util_mbps: float          # capacidad utilizable tras overhead
    arboles: list[ArbolPON] = field(default_factory=list)
    estado: Estado = Estado.EN_LINEA
    es_reserva: bool = False            # puerto en espera para conmutacion Type B


@dataclass
class Tarjeta:
    id: str
    olt_id: str
    num_puertos: int
    puertos: list[PuertoPON] = field(default_factory=list)
    estado: Estado = Estado.EN_LINEA


@dataclass
class OLT:
    id: str
    estadio_id: str
    num_chasis: int
    tarjetas: list[Tarjeta] = field(default_factory=list)
    estado: Estado = Estado.EN_LINEA


@dataclass
class Estadio:
    id: str                             # "azteca", "bbva", "akron"
    nombre: str
    olt: OLT
    num_onts: int
    num_arboles: int
    num_puertos: int
    num_tarjetas: int
    num_chasis: int

# ---------------------------------------------------------------------------
# Acumulación de disponibilidad (§6.5)
#
# Funciones puras (sin Dash) que acumulan y calculan disponibilidad por ONT y
# por servicio. Son la base de la evidencia de convergencia SLA (Requirement 8).
#
# Nota de alcance: esta sección se añade en la tarea 5.5. `SimState`, el reloj
# y el bucle `tick` (que invocan `acumular_disponibilidad` cada tick) pertenecen
# a la tarea 5.3 (design §6.1) y NO se definen aquí.
# ---------------------------------------------------------------------------

# Objetivos de SLA por esquema de protección (design §6.5, Req 8.5).
#   TYPE_C  -> 99.999% (cinco nueves)
#   TYPE_B  -> 99.99%  (cuatro nueves)
#   NINGUNA -> 99.9%   (tres nueves)
OBJETIVO_SLA: dict[Proteccion, float] = {
    Proteccion.TYPE_C: 0.99999,
    Proteccion.TYPE_B: 0.9999,
    Proteccion.NINGUNA: 0.999,
}

# Tolerancia de un solo lado para la propiedad de convergencia (Req 8.5).
# La disponibilidad observada es conforme si es >= objetivo - TOLERANCIA_SLA;
# exceder el objetivo por cualquier margen NO constituye incumplimiento.
# La verificación de convergencia como tal se prueba en las tareas 5.6/5.7.
TOLERANCIA_SLA: float = 0.005  # 0.5%


def objetivo_sla(proteccion: Proteccion) -> float:
    """Objetivo de disponibilidad teórico según el esquema de protección (Req 8.5)."""
    return OBJETIVO_SLA[proteccion]


def acumular_disponibilidad(ont: ONT) -> None:
    """Acumula un tick de disponibilidad sobre la ONT (Req 8.1).

    Incrementa `ticks_totales` siempre y `ticks_en_linea` cuando la ONT está
    disponible (estado EN_LINEA o DEGRADADO). DEGRADADO cuenta como disponible.
    Mantiene la invariante 0 <= ticks_en_linea <= ticks_totales (Req 8.3), ya
    que en línea nunca se incrementa sin incrementar también totales.
    """
    ont.ticks_totales += 1
    if ont.estado in (Estado.EN_LINEA, Estado.DEGRADADO):
        ont.ticks_en_linea += 1


def disponibilidad_ont(ont: ONT) -> float:
    """Disponibilidad acumulada de una ONT: ticks_en_linea / ticks_totales.

    Manejo de ticks_totales == 0 (Req 8.4): el requisito pide "forzar" la
    disponibilidad sin dejarla indefinida cuando aún no hay ticks. Como la
    división cruda 0/0 es indefinida (ZeroDivisionError en Python), se sigue el
    criterio del diseño (§6.5) y se devuelve 1.0: una ONT sin observaciones se
    considera plenamente disponible por defecto, evitando penalizar corridas
    recién iniciadas. Esto satisface el objetivo del requisito (valor definido)
    conservando la semántica del diseño.
    """
    if ont.ticks_totales == 0:
        return 1.0
    return ont.ticks_en_linea / ont.ticks_totales


def disponibilidad_servicio(onts: list[ONT]) -> float:
    """Media ponderada de la disponibilidad de las ONTs del servicio (Req 8.2).

    Se pondera por los ticks de cada ONT: es la razón de la suma de ticks en
    línea sobre la suma de ticks totales, equivalente a una media ponderada por
    el número de observaciones de cada ONT. Con `total == 0` se devuelve 1.0
    siguiendo el mismo criterio que `disponibilidad_ont` (Req 8.4).
    """
    total = sum(o.ticks_totales for o in onts)
    if total == 0:
        return 1.0
    return sum(o.ticks_en_linea for o in onts) / total


# ---------------------------------------------------------------------------
# 5.3 Estado dinámico del motor: SimState (design §6.1)
#
# Estado completo de una corrida de simulación. Python puro (sin Dash, §3.1).
# El reloj y el bucle `tick` viven en `sim.engine`; aquí solo se define la
# estructura de datos y una fábrica que la construye con la topología cargada.
# ---------------------------------------------------------------------------

# Velocidades permitidas: 1x, 5x y 15x (minutos simulados por tick, Req 16.1).
VELOCIDADES_PERMITIDAS: tuple[float, ...] = (1.0, 5.0, 15.0)

# Límites del reloj del partido: T-180 a T+135 = 315 minutos (design §6.1).
T_MIN_INICIAL: float = -180.0
T_MIN_FINAL: float = 135.0

# Cota de memoria del historial: ventana deslizante de 600 puntos por serie
# (Req 16.4/16.5). Cada KPI/serie es un `deque(maxlen=HISTORIAL_MAXLEN)`.
HISTORIAL_MAXLEN: int = 600

# Semilla determinista por defecto (Req 17.3).
SEMILLA_POR_DEFECTO: int = 42


def _normalizar_velocidad(velocidad: float) -> float:
    """Restringe `velocidad` al conjunto {1.0, 5.0, 15.0} (Req 16.1/16.2).

    Ajusta al valor permitido más cercano de forma continua, de modo que el
    estado nunca queda con una velocidad fuera del conjunto soportado,
    independientemente de la ejecución de un tick. Ante empates (equidistante)
    se prefiere el valor menor por estabilidad de la demo.
    """
    return min(VELOCIDADES_PERMITIDAS, key=lambda v: (abs(v - velocidad), v))


@dataclass
class SimState:
    """Estado dinámico completo del motor de simulación (design §6.1).

    Una **única** instancia vive a nivel de módulo en la app (design §3.2);
    todas las peticiones comparten y mutan este estado. Los valores por defecto
    corresponden al estado de arranque (partido en T-180, pausado, 1x).
    """

    t_min: float = T_MIN_INICIAL                 # minuto de partido; arranca en T-180
    corriendo: bool = False                      # pausa/marcha
    velocidad: float = 1.0                        # minutos simulados por tick (1x/5x/15x)
    escenario: str = "diseno"                     # "diseno" (50.6%) | "estres" (60%)
    estadio_activo: str = "azteca"
    estadios: dict[str, Estadio] = field(default_factory=dict)
    alarmas: list["Alarma"] = field(default_factory=list)
    # serie -> deque(maxlen=600): historial acotado para graficar (Req 16.4).
    historial: dict[str, deque] = field(default_factory=dict)
    eventos: list["Evento"] = field(default_factory=list)  # log de inyecciones/goles
    # Minutos de partido en que ocurrió un gol; sim.traffic los lee para el
    # multiplicador de actividad f_gol (design §6.2). La UI de simulación (tarea
    # 7.8) agrega goles con app.live.gol al t_min actual.
    goles: list[float] = field(default_factory=list)
    semilla: int = SEMILLA_POR_DEFECTO

    def __post_init__(self) -> None:
        # Garantiza que la velocidad inicial pertenezca al conjunto permitido
        # (Req 16.2): si se construye con un valor arbitrario se normaliza.
        self.velocidad = _normalizar_velocidad(self.velocidad)

    def set_velocidad(self, velocidad: float) -> float:
        """Fija la velocidad restringiéndola a {1x, 5x, 15x} (Req 16.1/16.2).

        Es el punto de entrada recomendado para cambiar la velocidad desde la
        capa de presentación: acepta cualquier valor y lo normaliza al conjunto
        permitido, de modo que el estado nunca sale del conjunto soportado de
        forma continua. Devuelve la velocidad efectiva ya normalizada.
        """
        self.velocidad = _normalizar_velocidad(velocidad)
        return self.velocidad

    @classmethod
    def crear(
        cls,
        *,
        estadio_activo: str = "azteca",
        escenario: str = "diseno",
        semilla: int = SEMILLA_POR_DEFECTO,
    ) -> "SimState":
        """Construye un `SimState` con la topología real cargada (design §6.1).

        Carga los tres estadios desde `sim.topology.cargar_todas` (data/*.json)
        y devuelve un estado listo para arrancar (pausado, T-180, 1x). Es la
        fábrica que la app usa para crear la instancia única del estado.

        La importación de `sim.topology` es local para no crear una dependencia
        de import a nivel de módulo (mantiene `sim.state` importable de forma
        aislada, útil para pruebas que no necesitan datos).
        """
        from sim.topology import cargar_todas

        estadios = cargar_todas()
        if estadio_activo not in estadios:
            # Fail-soft: si el id pedido no existe, usa el primero disponible.
            estadio_activo = next(iter(estadios), estadio_activo)
        return cls(
            estadio_activo=estadio_activo,
            escenario=escenario,
            semilla=semilla,
            estadios=estadios,
        )
