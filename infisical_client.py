#!/usr/bin/env python3
"""Cliente de Infisical para scripts: ubicación, login y lectura de un secreto.

Una sola responsabilidad por capa, y ésta es la de **acceso**:

  ubicación   de dónde salen proyecto, entorno y ruta      (no se inventa acá)
  login       Universal Auth, un token por proceso        (esto sí es de acá)
  lectura     una clave por nombre, fail-open              (esto sí es de acá)

La ubicación se toma **en bloque**: el proyecto, el entorno y la ruta salen del
mismo sitio o no sale ninguno. Mezclar el proyecto de un sitio con la ruta de otro
produce combinaciones que no existen en ninguna bóveda.

Orden, y el primero que tenga algo gana entero:

  1. el entorno          — lo que el gateway ya inyectó al arrancar
  2. el config del perfil — lo que escribió `hermes infisical setup`
  3. el scope del sistema — /etc/hermes, donde vive la política del administrador
  4. nada                — error explícito, nunca una ruta inventada

Lo usan `print-secret.py` (fuente `command` de un perfil) y cualquier script que
corra fuera del gateway. Dentro del gateway no hace falta: los secretos ya están
en el entorno.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://app.infisical.com"

# Dónde puede estar cada dato. Los tres primeros son la ubicación; los dos últimos,
# la identidad con la que se pide el token.
CLAVES_UBICACION = ("INFISICAL_PROJECT_ID", "INFISICAL_ENV", "INFISICAL_PATH")
ALIAS_RUTA = ("INFISICAL_PATH", "INFISICAL_SECRET_PATH")
CLAVES_IDENTIDAD = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET")

_LAST_ERROR = ""
_TOKEN: str | None = None


def _hermes_home() -> Path:
    """El home de cuyo config se lee la ubicación (SECRET_SOURCE_HOME lo fuerza)."""
    crudo = (
        os.environ.get("SECRET_SOURCE_HOME")
        or os.environ.get("HERMES_HOME")
        or (Path.home() / ".hermes")
    )
    return Path(crudo).expanduser()


def _home_real() -> Path:
    """El home de Hermes, aunque el proceso corra con HERMES_HOME de un perfil.

    Ojo: HERMES_REAL_HOME es el home del USUARIO (/home/andrea), no el de Hermes.
    """
    home = _hermes_home()
    return home.parent.parent if home.parent.name == "profiles" else home


def _leer_dotenv(ruta: Path) -> dict:
    """KEY=VALUE de un .env. Fail-open: {} si no se puede leer."""
    salida: dict = {}
    try:
        texto = ruta.read_text(encoding="utf-8-sig")
    except OSError:
        return salida
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        salida[k.strip()] = v.strip().strip('"').strip("'")
    return salida


def _config_infisical() -> dict:
    """La sección `secrets.infisical` del config del perfil, leída sin PyYAML."""
    ruta = _hermes_home() / "config.yaml"
    if not ruta.exists():
        return {}
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    dentro = False
    salida: dict = {}
    for linea in lineas:
        if linea.strip() == "infisical:" and linea.startswith("  "):
            dentro = True
            continue
        if dentro:
            if linea.strip() and not linea.startswith("      "):
                break
            if ":" in linea:
                k, v = linea.split(":", 1)
                salida[k.strip()] = v.strip().strip('"').strip("'")
    return salida


def _scope() -> dict:
    """El scope administrado (/etc/hermes), vía el core si está; si no, vacío."""
    try:
        sys.path.insert(0, str(_home_real() / "hermes-agent"))
        from hermes_cli.managed_scope import load_managed_env

        return dict(load_managed_env() or {})
    except Exception:  # noqa: BLE001 - máquina sin Hermes: no es un error, es el caso portable
        return {}


def _bloque(origen: dict, nombres: tuple, alias: tuple = ()) -> dict:
    """Devuelve las claves pedidas sólo si el origen trae alguna; si no, {}.

    `alias` permite que una clave llegue con otro nombre (la ruta se llama
    INFISICAL_PATH en el core y INFISICAL_SECRET_PATH en la API del CLI).
    """

    def valor(n: str) -> str:
        crudo = origen.get(n)
        if not str(crudo or "").strip():
            for otro in alias:
                if str(origen.get(otro) or "").strip():
                    crudo = origen.get(otro)
                    break
        return str(crudo or "").strip()

    if not any(valor(n) for n in nombres):
        return {}
    return {n: valor(n) for n in nombres}


def _ubicacion() -> tuple[dict, str]:
    """(claves, origen) — en bloque, del primer sitio que traiga algo."""
    for nombre, origen in (
        ("entorno", dict(os.environ)),
        ("config del perfil", _config_infisical()),
        ("scope /etc/hermes", _scope()),
    ):
        bloque = _bloque(origen, CLAVES_UBICACION, ALIAS_RUTA)
        if bloque:
            return bloque, nombre
    return {}, ""


def _identidad() -> dict:
    """Credenciales de Universal Auth: entorno, luego el .env del perfil, luego el scope."""
    for origen in (dict(os.environ), _leer_dotenv(_hermes_home() / ".env"), _scope()):
        bloque = _bloque(origen, CLAVES_IDENTIDAD)
        if bloque:
            return bloque
    return {}


_UBIC, _ORIGEN_UBIC = _ubicacion()
_IDENT = _identidad()
_API_URL = str(os.environ.get("INFISICAL_API_URL") or API_URL).rstrip("/")


def origen_ubicacion() -> str:
    """De dónde salieron proyecto/entorno/ruta. Útil para el diagnóstico."""
    return _ORIGEN_UBIC


def _login() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    cuerpo = json.dumps(
        {
            "clientId": _IDENT.get("INFISICAL_CLIENT_ID", ""),
            "clientSecret": _IDENT.get("INFISICAL_CLIENT_SECRET", ""),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{_API_URL}/api/v1/auth/universal-auth/login",
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"login HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"login sin red: {exc.reason}") from exc
    token = payload.get("accessToken") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("login devolvio sin accessToken")
    _TOKEN = token
    return token


def _traer(nombre: str) -> str:
    token = _login()
    url = (
        f"{_API_URL}/api/v4/secrets?environment={_UBIC.get('INFISICAL_ENV', 'prod')}"
        f"&secretPath={_UBIC.get('INFISICAL_PATH', '/')}"
        f"&projectId={_UBIC.get('INFISICAL_PROJECT_ID', '')}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"lectura HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"lectura sin red: {exc.reason}") from exc
    for s in payload.get("secrets", []):
        if s.get("secretKey") == nombre:
            return s.get("secretValue", "")
    return ""


def last_error() -> str:
    """Por qué `get_secret()` devolvió el default. Sólo texto: nunca un valor."""
    return _LAST_ERROR


def get_secret(name: str, default: str = "") -> str:
    """El secreto, o `default`. Fail-open; el motivo queda en `last_error()`."""
    global _LAST_ERROR
    if not _UBIC:
        _LAST_ERROR = (
            "no hay ubicacion: ni entorno, ni config del perfil, ni scope /etc/hermes"
        )
        return default
    if not _IDENT:
        _LAST_ERROR = (
            f"faltan credenciales ({' / '.join(CLAVES_IDENTIDAD)}): "
            f"ni en el entorno, ni en {_hermes_home() / '.env'}, ni en el scope"
        )
        return default
    try:
        valor = _traer(name)
    except Exception as exc:  # noqa: BLE001 - login o red: el secreto puede existir
        _LAST_ERROR = f"login/red: {exc}"
        return default
    if not valor:
        _LAST_ERROR = (
            f"{name} no existe en {_UBIC.get('INFISICAL_PATH')} "
            f"(env {_UBIC.get('INFISICAL_ENV')}, origen {_ORIGEN_UBIC})"
        )
        return default
    _LAST_ERROR = ""
    return valor


if __name__ == "__main__":
    print(f"ubicacion desde: {_ORIGEN_UBIC or '(ninguna)'}")
    for k in CLAVES_UBICACION:
        print(f"  {k:26s} {_UBIC.get(k) or '(falta)'}")
    print(f"identidad:       {'completa' if _IDENT else 'FALTA'}")
    for nombre in sys.argv[1:]:
        valor = get_secret(nombre)
        print(f"  {nombre:26s} {'PRESENTE' if valor else 'AUSENTE'} (len={len(valor)})")
    if _LAST_ERROR:
        print(f"motivo: {_LAST_ERROR}")
