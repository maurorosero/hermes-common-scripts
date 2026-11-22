# hermes-common-scripts

Scripts de utilería para [Hermes Agent](https://hermes-agent.nousresearch.com/) que **no
pertenecen a un skill ni a un plugin**, y que siguen vivos cuando termina la tarea que los motivó.

Existen por dos razones. La primera: hay piezas que no son de nadie en particular —leer una
credencial, verificar citas, limpiar un tablero— y que se pierden si viven dentro de un skill,
porque mueren con él. La segunda es una imposición del arnés: **`hermes cron` exige que el script
esté bajo `~/.hermes/scripts/`**, así que los lanzadores de los trabajos programados viven acá
aunque el proceso real esté en otra parte.

## Instalación

```bash
git clone https://github.com/maurorosero/hermes-common-scripts ~/.hermes/scripts/common
```

El instalador de Hermes lo hace solo (`bin/install-hermes.sh`, con `hermes-common-scripts` en
`hermes.conf`) y después lo mantiene con `git pull --ff-only` en cada actualización.

Los hooks de git no se versionan —viven en `.git/hooks/`, que es local—, así que hay que
activarlos una vez por clon:

```bash
cd ~/.hermes/scripts/common
pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Scripts

**Secretos**

| Script | Qué hace |
|---|---|
| `infisical_client.py` | Obtiene un secreto de la bóveda cuando no está en el entorno. Resuelve dónde buscar —entorno, config del perfil o configuración del sistema, en ese orden— y autentica por Universal Auth. |
| `print-secret.py` | Imprime credenciales como `KEY=VALUE` para que un proceso las herede. Se usa como fuente `command` en perfiles que no heredan los secretos del home. |

**Investigación**

| Script | Qué hace |
|---|---|
| `verify-citations.py` | Comprueba que cada cita de una ficha exista y respalde lo que se le atribuye. Sin modelo: no cuesta tokens ni puede inventar. |
| `render-asset.py` | Genera los gráficos y diagramas de un informe desde su manifiesto. Los números salen de las citas, no de un modelo. |
| `render-page.js` | Convierte un contenido y su plantilla en una página. |
| `research-watcher.py` | Lanzador del proceso de investigación: corre su vigilante cada vez que el cron lo despierta. |

**Correo**

| Script | Qué hace |
|---|---|
| `personal_cola.py` | Lanzador: devuelve la cola del ciclo de correo personal. |
| `nivel3_cola.py` | Lanzador: devuelve la cola de la última fase del ciclo de correo. |

**Documentos**

| Script | Qué hace |
|---|---|
| `presenter_dispatcher.py` | Lanzador del despachador que arma los documentos pedidos. |

**Tablero**

| Script | Qué hace |
|---|---|
| `kanban_gc.py` | Borra las tarjetas cerradas y los logs viejos de cada tablero. |
| `backlog_datos_minimos.py` | Señala los contactos a los que les falta un dato mínimo. |

**Telegram**

| Script | Qué hace |
|---|---|
| `push-telegram-menu.py` | Actualiza el menú de comandos del bot sin reiniciar el gateway. |

**Voz**

| Script | Qué hace |
|---|---|
| `tts-normalizar.py` | Sintetiza texto en voz alta con edge-tts, aplicando antes el diccionario `tts-normalizacion.toml`: siglas y anglicismos que el español no pronuncia bien se reemplazan por su forma hablada. Con `--mostrar` imprime el texto normalizado y no sintetiza. |

El diccionario vive al lado del script y se edita sin tocar código: agregar una palabra es
agregar una regla. Los patrones son expresiones regulares entre comillas simples (literales), y
la lista `[intactas]` documenta lo que se deja a propósito —siglas que la voz resuelve bien y
marcas que se dicen en inglés.

Los **lanzadores** no tienen lógica propia: ejecutan el script que vive en su skill y devuelven su
salida tal cual. Si el skill no está instalado, fallan con un mensaje en vez de callarse.

## Uso

Cada script imprime su ayuda con `--help`. Los dos de secretos diagnostican sin mostrar valores:

```bash
python3 ~/.hermes/scripts/common/infisical_client.py NEXTCLOUD_HOST
python3 ~/.hermes/scripts/common/verify-citations.py <ficha.md>
```

`infisical_client.py` es la puerta para lo que corre **fuera** del gateway —ssh, cron del sistema,
a mano—. Dentro del gateway no hace falta: el núcleo ya inyectó los secretos en el entorno al
arrancar, y los procesos hijos los heredan.

## Requisitos

- **Hermes Agent**, con su entorno de dependencias.
- **Python 3.11+**, y **Node** para `render-page.js`.
- Los scripts de investigación requieren el skill `research-pipeline`, que es dueño del contrato,
  el validador y el gate.

## Qué entra y qué no

Dos condiciones:

1. **Es permanente.** No muere cuando termina una tarea. Si solo vive mientras corre un proceso
   concreto, su lugar es el skill de ese proceso.
2. **No lleva nada interno.** Ni credenciales, ni identificadores de proyecto, ni hosts, ni IPs,
   ni rutas de una instalación concreta: todo eso se lee de configuración o del entorno.

Que lo usen varios consumidores es bienvenido, pero no es requisito — lo normal es que un script
responda a una necesidad puntual de quien lo mantiene. Lo que sí se excluye es que tenga dueño:
un script de un skill viaja dentro del skill, y uno de un plugin, dentro del plugin. Este repo es
la casa de los sueltos.

No van acá scripts de skills o plugins, descartables, ni documentos escritos como script: si el
contenido es el valor y no el mecanismo, su lugar es un documento.

## Gate de secretos

El repo intercepta en dos puntos:

- **`pre-commit`** — linters sobre lo que se está por commitear.
- **`pre-push`** — `gitleaks` sobre el **rango de commits que va a salir**, no sobre los archivos:
  un secreto commiteado y después borrado igual viaja en el historial.

Los secretos viven en la bóveda, no en los archivos; el escáner es la red por si alguien se
equivoca, no el método. La única excepción son los archivos cifrados con SOPS: se versionan a
propósito y no están en la lista de excepciones —el que está bien cifrado pasa solo, y el que
quedó sin cifrar tiene que bloquear—.

## Créditos

- **Mauro Rosero Pérez** (`maurorosero`) — autor y mantenedor.
- **Andrea**, agente de ROSERO ONE (Hermes Agent) — migración del cliente de secretos,
  documentación y verificación.

## Cambios

Ver [`CHANGELOG.md`](CHANGELOG.md).

## Licencia

GPL-3.0-or-later. Ver [`LICENSE`](LICENSE).

Copyleft a propósito: estos scripts existen para que un agente haga trabajo real sobre
infraestructura ajena. Quien los mejore para su caso tiene que poder devolver esa mejora.
