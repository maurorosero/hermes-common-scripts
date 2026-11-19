# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

Este repo no publica versiones: los scripts se consumen desde `main`. Los cambios se agrupan
por fecha, y para el detalle fino el historial de git es la fuente (`git log --oneline`).

## [Unreleased]

## 2026-11-18

### Añadido

- **`infisical_client.py`** — pide un secreto a la bóveda cuando el entorno no lo trae.
  Resuelve la **ubicación en bloque** (entorno → config del perfil → scope `/etc/hermes`,
  el primero que traiga algo lo trae entero) y hace el login Universal Auth. Es la puerta
  para lo que corre fuera del gateway.
- Licencia **GPL-3.0-or-later**, con el copyright en el propio `LICENSE`.

### Cambiado

- **`print-secret.py`: 130 → 52 líneas.** Buscaba por nombre un helper dentro de un skill y lo
  cargaba con `importlib`; ahora importa el cliente que está a su lado.
- **`backlog_datos_minimos.py`** pide el secreto al cliente común en vez de resolverlo por su
  cuenta.
- Los scripts de los skills dejaron de importar el helper del skill `hermes-infisical-secrets`
  (que quedó como documentación). Donde corre bajo el gateway, leen el entorno.

### Corregido

- **`HERMES_REAL_HOME` apunta al home del usuario, no al de Hermes.** Componer con él la ruta
  del árbol metía un `/hermes-agent` inexistente y el scope nunca se leía.
- Fuera los datos de una instalación concreta: `/home/andrea`, «home de Andrea», los volúmenes
  de kanban de una máquina, y `~/Research` (que además ya no existe: la raíz es `~/research`).

## 2026-11-06

### Añadido

- `kanban_gc.py` — recolector de tarjetas cerradas del tablero.
- `presenter_dispatcher.py` — el lanzador del Oficio 2.

## 2026-10-21

### Cambiado

- **El proceso de research se mudó al skill `research-pipeline`**: contrato, validador, watcher
  y gate viven allí. En este repo queda sólo `research-watcher.py`, como lanzador, porque
  `hermes cron` exige que el script esté bajo `~/.hermes/scripts/`.

### Corregido

- `ready/` es área de tránsito, no archivo; `done` es una investigación aprobada sin documento.

## 2026-10-11

### Añadido

- Commit inicial: gate de secretos (`pre-commit` + `gitleaks` sobre el rango de commits),
  `.gitignore` con la sección de secretos en claro, y la convención del repo.
