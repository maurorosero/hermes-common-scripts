# hermes-common-scripts

Utileria transversal para agentes Hermes: scripts que **no pertenecen a un skill ni a un
plugin** y que mas de un consumidor usa.

## Que va aqui

Un script entra a este repo solo si cumple las tres condiciones:

1. **Es permanente.** No muere cuando termina una tarea. Si vive mientras corre un proceso
   concreto, su casa es el skill de ese proceso, no este repo.
2. **Es transversal.** Lo usa mas de un consumidor, o ninguno en particular. Si tiene un solo
   dueno claro, va con su dueno.
3. **No lleva nada interno.** Nada de credenciales, project ids, hosts, IPs ni rutas de una
   instalacion concreta. Todo eso se lee de configuracion o de variables de entorno.

La tercera condicion es la razon de ser de un repo **publico**: lo que se publica es el
mecanismo, nunca la configuracion.

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

## Gate de secretos

El repo lleva un gate en dos puntos:

- `pre-commit` — linters sobre el commit.
- `pre-push` — escaneo de secretos sobre el rango de commits que va a salir.

Escanea **el rango de commits, no los archivos**: un secreto commiteado y despues borrado
igual viaja en el historial.

Los archivos cifrados con SOPS son la unica via legitima de poner un secreto en un repo, y no
estan en la allowlist a proposito: el que esta bien cifrado pasa solo, y el que quedo sin
cifrar tiene que bloquear.
