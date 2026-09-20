# Documentación — POL Command Center

Esta carpeta reúne la documentación técnica del **POL Command Center**, la PoC
que simula la operación de una red POL (Passive Optical LAN) en tres estadios
durante un partido, vista desde un centro de comando.

El objetivo de estos documentos es explicar **cómo funciona el sistema por
dentro**: la arquitectura, el motor de simulación, los indicadores que produce
y el modelo económico que lo respalda. Todo está alineado con el código actual
de `sim/`, `app/` y `data/`.

## Índice

| Documento | Contenido |
| --------- | --------- |
| [01 — Arquitectura](01-arquitectura.md) | Separación de capas, estado compartido, proceso único, flujo de un tick y estructura de carpetas. |
| [02 — Motor de simulación](02-motor-de-simulacion.md) | `SimState`, reloj, bucle `tick`, modelo de tráfico, fallas y protección. |
| [03 — KPIs y alarmas](03-kpis-y-alarmas.md) | Catálogo de los 12 KPIs (K1–K12), catálogo de las 10 alarmas, ciclo de vida y correlación. |
| [04 — Modelo económico](04-modelo-economico.md) | CAPEX, OPEX, TCO, break-even y propuesta de valor POL vs cobre. |
| [05 — Datos y configuración](05-datos-y-configuracion.md) | `topology_*.json`, `prices.yaml`, variables de entorno y contrato fail-fast. |
| [06 — Guía del desarrollador](06-guia-del-desarrollador.md) | Instalación, ejecución local y en Docker, pruebas y convenciones. |

## Lectura recomendada según el rol

- **Nuevo en el proyecto:** empieza por [Arquitectura](01-arquitectura.md), luego
  [Motor de simulación](02-motor-de-simulacion.md).
- **Trabajas en la lógica de red:** [Motor de simulación](02-motor-de-simulacion.md)
  y [KPIs y alarmas](03-kpis-y-alarmas.md).
- **Trabajas en el panel de negocio:** [Modelo económico](04-modelo-economico.md)
  y [Datos y configuración](05-datos-y-configuracion.md).
- **Solo quieres levantar la app:** [Guía del desarrollador](06-guia-del-desarrollador.md).

## Principio rector

El sistema aplica una **separación estricta de capas**: el paquete `sim/` es
Python puro y no importa nada de Dash. Recibe un estado y devuelve el siguiente,
lo que permite probar el motor con `pytest` sin levantar la web. La capa `app/`
lee de `sim/` a través de `app/live.py`; nunca al revés.

> Nota: el `README.md` de la raíz del proyecto es el punto de partida para
> instalar y ejecutar. Estos documentos profundizan en el diseño interno.
