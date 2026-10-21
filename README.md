## El proceso de research ya no vive acá

El contrato (`research-esquema.yaml`), el validador, el watcher y el gate son **del skill
`research-pipeline`** (`~/developers/ai/skills/research/research-pipeline/scripts/`): los scripts y
su cron son parte del skill.

Acá quedan sólo dos cosas:

- `research-watcher.py` — **lanzador** de una docena de líneas. `hermes cron` exige que el script
  esté bajo `~/.hermes/scripts/`, así que no puede entrar a un skill; esto sólo ejecuta el del
  skill. Si el skill no está, falla con un mensaje en vez de callarse.
- Las **herramientas genéricas**, que no son del proceso y sirven a cualquier tarea:
  `render-page.js`, `verify-citations.py`, `render-asset.py`.

# hermes-common-scripts

Utileria transversal para agentes Hermes: scripts que **no pertenecen a un skill ni a un
plugin** y que mas de un consumidor usa.

## Que va aqui

Un script entra a este repo con dos condiciones:

1. **Es permanente.** No muere cuando termina una tarea. Si vive mientras corre un proceso
   concreto, su casa es el skill de ese proceso, no este repo.
2. **No lleva nada interno.** Nada de credenciales, project ids, hosts, IPs ni rutas de una
   instalacion concreta. Todo eso se lee de configuracion o de variables de entorno.

La primera es la que decide. **Ser transversal no es una condicion:** que lo usen varios
consumidores es bienvenido, pero no se exige — un script suelto y persistente vive aca
aunque tenga un solo consumidor, y de hecho lo normal es que responda a una necesidad
particular de quien lo mantiene.

Lo que si excluye es **tener dueño**: un script de un skill viaja dentro del skill, y uno de
un plugin, dentro del plugin. Este repo es la casa de los sueltos.

La segunda condicion no depende de la visibilidad del repo. Lo que se versiona es el
mecanismo; los datos de una instalacion —credenciales, hosts, project ids, rutas, y la
configuracion de un consumidor— pertenecen a su dueño y tienen que sobrevivirle al
codigo. Por eso no viajan nunca: se leen de configuracion o de variables de entorno.

## Que NO va aqui

- **Scripts de un skill** — viajan dentro del skill y mueren con el.
- **Scripts de un plugin** — viajan dentro del plugin; `hermes plugins install` los despliega.
- **Descartables** — se van con un backup y no se versionan.
- **Documentos escritos como script** — si el contenido es el valor y no el mecanismo, su
  lugar es el documento, no un repo de utileria.

## Instalacion

Los scripts de este repo se ejecutan desde el directorio de scripts del home activo de Hermes:

```
$HERMES_HOME/scripts/common/
```

Cada consumidor los resuelve por ruta absoluta, para que el resultado no dependa del perfil
activo.

### Despues de clonar

```bash
pre-commit install
```

**Los hooks de git no se versionan: viven en `.git/hooks/`, que es local.** Un clon sin ese
comando no tiene el gate de secretos, y el push no estaria protegido. Por eso el gate es local
y hay que reinstalarlo en cada clon.

## Los dos scripts del flujo de research

| Script | Qué hace |
|---|---|
| `render-asset.py` | Produce los assets de una investigación desde `assets/manifiesto.yaml`. La spec es dato y el render es código: una gráfica o un diagrama no consume tokens ni puede inventar una cifra, porque los números salen de un claim. `--check` valida que cada asset esté atado a un claim existente y referenciado en el informe. Lo que sólo adorna (ilustración) no se produce acá: lo pide el manifiesto y lo hace el presenter. |
| `research-gate.py` | El gate humano del flujo. `approve <uuid>` cierra la card de gate y deja el estado en `ready` (carpeta en `ready/`, que es área de tránsito: el humano la deja o la mueve, la investigación ya cerró); `improve <uuid> --motivo "..."` devuelve el trabajo en una card nueva con el motivo adentro; `present <uuid>` escribe el brief del documento en la cola del presenter y deja el estado en `waiting`; `show <uuid>` informa sin tocar nada. Los tres verbos exigen que el estado venga del gate: nada se aprueba por fuera. |

## Gate de secretos

El repo lleva un gate en dos puntos:

- `pre-commit` — linters sobre el commit.
- `pre-push` — escaneo de secretos sobre el rango de commits que va a salir.

Escanea **el rango de commits, no los archivos**: un secreto commiteado y despues borrado
igual viaja en el historial.

Los archivos cifrados con SOPS son la unica via legitima de poner un secreto en un repo, y no
estan en la allowlist a proposito: el que esta bien cifrado pasa solo, y el que quedo sin
cifrar tiene que bloquear.

## El brief es el estado compartido con documents

`present` escribe `~/Research/documentos/requested/<uuid>.yaml` con `estado: pedido`. Ese archivo
es el unico punto que los dos procesos ven, y cada uno escribe su parte:

| campo | quien lo escribe | cuando |
|---|---|---|
| `estado: pedido` | research (`present`) | al marcar que hace falta documento |
| `estado: en_proceso` | documents | cuando lo toma |
| `estado: entregado` + `documento: <ruta>` | documents | al terminar |
| `estado: fallido` + `motivo` | documents | si no pudo |
| `estado: cancelado` | research (`close`) | si el humano cierra sin documento |

El research lee `estado` para cerrar la investigacion en `done` cuando el documento existe, y
`close` marca `cancelado` para que del otro lado no quede un pedido vivo. Nadie consulta la base
del otro: el lazo es el archivo.

**Falta construir** el barrido que lee `entregado` y cierra la investigacion: hoy nada escribe ese
valor porque documents no existe, asi que la salida real es `close`.
