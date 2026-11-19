# hermes-common-scripts

Utileria transversal para [Hermes Agent](https://hermes-agent.nousresearch.com/): scripts que
**no pertenecen a un skill ni a un plugin**, y que siguen vivos cuando termina la tarea que los
motivó.

## Por qué existe

Un agente que trabaja de verdad necesita piezas que no son de nadie en particular: leer una
credencial, verificar citas, recolectar un tablero, empujar un menú. Si viven dentro de un skill,
mueren con él; si viven dentro de un plugin, las despliega una instalación. Este repo es la casa
de las que no tienen dueño y no se quieren perder.

Además hay un caso concreto que lo obliga: **`hermes cron` exige que el script esté bajo
`~/.hermes/scripts/`**. Un lanzador de doce líneas vive acá y ejecuta el script real, que sí
puede estar dentro del skill. Si el skill no está, el lanzador falla con un mensaje en vez de
callarse.

## Qué incluye

| Script | Qué hace |
|---|---|
| `infisical_client.py` | Pide un secreto a la bóveda cuando el entorno no lo trae. Resuelve la ubicación en bloque (entorno → config del perfil → scope `/etc/hermes`) y hace el login Universal Auth. |
| `print-secret.py` | Imprime `KEY=VALUE` de las credenciales pedidas. Se usa como secret source `command` en perfiles que no heredan el `secrets:` del home. |
| `verify-citations.py` | Verifica mecánicamente las citas de una ficha de hallazgos. Sin modelo, sin tokens. |
| `render-asset.py` | Renderiza los assets de una investigación desde su manifiesto. Los números salen de un claim: no consume tokens ni puede inventar una cifra. |
| `render-page.js` | Renderiza una página a partir de su contenido y su plantilla. |
| `research-watcher.py` | Lanzador del watcher de `research-pipeline` — obligación del cron. |
| `presenter_dispatcher.py` | Lanzador del Oficio 2 del presenter — obligación del cron. |
| `personal_cola.py` | Puente del ciclo de correo personal: la cola, dicha por el vigilante. |
| `nivel3_cola.py` | Puente del ciclo de correo: fase 1·2 y cola de la fase 3. |
| `backlog_datos_minimos.py` | Avisa qué tarjetas de `backlog` ya tienen los datos mínimos de contacto. |
| `kanban_gc.py` | Recolector del kanban: eventos y logs viejos de cada tablero. |
| `push-telegram-menu.py` | Empuja el menú de comandos a Telegram sin reiniciar el gateway. |

Los que dicen **lanzador** o **puente** existen por la regla del cron y no tienen lógica propia:
ejecutan el script que vive en su skill y devuelven su salida tal cual.

## Requisitos

- **Hermes Agent**, con su entorno de dependencias.
- **Python 3.11+** y **Node** para `render-page.js`.
- Los scripts de research requieren el skill **`research-pipeline`** —el contrato, el validador y
  el gate son suyos—; acá sólo viven los lanzadores.

## Instalación

El instalador de Hermes clona este repo en el directorio de scripts del home:

```bash
git clone https://github.com/maurorosero/hermes-common-scripts ~/.hermes/scripts/common
```

`bin/install-hermes.sh` lo hace solo (`hermes-common-scripts` en `hermes.conf`) y después lo
mantiene con `git pull --ff-only` en cada actualización.

### Después de clonar

```bash
cd ~/.hermes/scripts/common
pre-commit install --hook-type pre-commit --hook-type pre-push
```

**Los hooks no se versionan: viven en `.git/hooks/`, que es local.** Un clon sin ese comando no
tiene el gate de secretos y el push queda sin proteger.

## Uso

Cada script imprime su ayuda. El de secretos diagnostica sin mostrar valores:

```bash
python3 ~/.hermes/scripts/common/infisical_client.py NEXTCLOUD_HOST
```

Es la puerta para lo que corre **fuera** del gateway (ssh, cron del sistema, a mano). Dentro del
gateway no hace falta: el core ya inyectó la carpeta entera al arrancar y los hijos la heredan.

## Qué entra y qué no

Un script entra con dos condiciones:

1. **Es permanente.** No muere cuando termina una tarea. Si vive mientras corre un proceso
   concreto, su casa es el skill de ese proceso.
2. **No lleva nada interno.** Ni credenciales, ni project ids, ni hosts, ni IPs, ni rutas de una
   instalación concreta. Todo eso se lee de configuración o de variables de entorno.

La primera es la que decide. **Ser transversal no es una condición:** que lo usen varios
consumidores es bienvenido, pero no se exige — lo normal es que un script responda a una
necesidad particular de quien lo mantiene.

Lo que sí excluye es **tener dueño**: un script de un skill viaja dentro del skill, y uno de un
plugin, dentro del plugin. Este repo es la casa de los sueltos.

**No van acá:** scripts de un skill, scripts de un plugin, descartables, y documentos escritos
como script —si el contenido es el valor y no el mecanismo, su lugar es el documento—.

## Gate de secretos

El repo intercepta en dos puntos:

- **`pre-commit`** — linters sobre lo que se está por commitear.
- **`pre-push`** — `gitleaks` sobre el **rango de commits que va a salir**, no sobre los archivos:
  un secreto commiteado y después borrado igual viaja en el historial.

Los secretos viven en la bóveda, no en los archivos. El escáner es la red por si alguien se
equivoca, no el método. La única excepción son los archivos cifrados con SOPS: se versionan a
propósito y no están en la allowlist —el que está bien cifrado pasa solo, y el que quedó sin
cifrar tiene que bloquear—.

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
