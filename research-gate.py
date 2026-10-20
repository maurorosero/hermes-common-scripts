#!/usr/bin/env python3
"""El gate humano del flujo de research. Los verbos los firma Mauro, no un agente.

Tres decisiones, ninguna automatica:

    approve  <uuid>                    aprueba el informe  -> estado `ready`
    improve  <uuid> --motivo "..."     no aprobado: vuelve al redactor con el motivo
    present  <uuid> [--formato X]      lo manda al presenter -> estado `waiting`
    show     <uuid>                    el estado del gate, sin tocar nada
    assets   <uuid>                    el manifiesto contra el disco (--check)
    panel                              el tablero entero, por etapa (~/research)
    pending                            solo lo que espera una firma tuya

Convencion copiada de `andrea-ai-project`: la card del gate nace en `blocked` (el
"pendiente"), que es sticky — nada la toca hasta que el humano decide — y los gates operan
**sobre kanban**, con `request.md` espejando la decision. El estado no se cambia por fuera.

`done` es terminal, asi que la aprobacion no puede vivir en la card del redactor: vive en una
card de gate aparte, creada por el barrido 5 cuando el trabajo termino.

`present` exige que los assets de research esten completos: un informe que declara recursos en
`assets/manifiesto.yaml` y no los referencia no sale a presentacion.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BOARD = os.environ.get("RESEARCH_BOARD", "research")
HOME = Path(os.environ.get("HOME", ""))


def _validador():
    """Reusa `partir`/`recomponer` del validador: una sola forma de escribir el frontmatter."""
    ruta = Path(__file__).with_name("research-validator.py")
    spec = importlib.util.spec_from_file_location("research_validator", ruta)
    if spec is None or spec.loader is None:
        raise SystemExit(f"no puedo cargar el validador en {ruta}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VAL = _validador()


def _raiz(nombre: str, por_defecto: str) -> Path:
    valor = os.environ.get(nombre) or str(HOME / por_defecto)
    return Path(valor.replace("$HOME", str(HOME))).expanduser()


REQUESTED = _raiz("RESEARCH_REQ_PATH", "Research/working/requested")
PROCESSING = _raiz("RESEARCH_PROC_PATH", "Research/working/processing")
READY = _raiz("RESEARCH_READY_PATH", "Research/working/ready")
COLA_DOCUMENTOS = _raiz("RESEARCH_DOCS_PATH", "Research/documentos/requested")


def _leer(ruta: Path) -> tuple[dict, str]:
    fm, cuerpo = VAL.partir(ruta.read_text(encoding="utf-8"))
    if fm is None:
        raise SystemExit(f"{ruta}: sin frontmatter, no es un contrato")
    import yaml

    datos = yaml.safe_load(fm) or {}
    if not isinstance(datos, dict):
        raise SystemExit(f"{ruta}: el frontmatter no es un mapa")
    return datos, cuerpo


def _escribir(ruta: Path, datos: dict, cuerpo: str) -> None:
    ruta.write_text(VAL.recomponer(datos, cuerpo), encoding="utf-8")


def _carpeta(uuid: str) -> Path:
    for raiz in (PROCESSING, READY, REQUESTED):
        if not raiz.exists():
            continue
        if (raiz / uuid / "request.md").exists():
            return raiz / uuid
        for cand in raiz.iterdir():
            if cand.is_dir() and cand.name.lower().startswith(uuid.lower()):
                return cand
    raise SystemExit(f"no encuentro la investigacion {uuid}")


def _kanban(*args: str) -> tuple[int, str]:
    r = subprocess.run(
        ["hermes", "kanban", "--board", BOARD, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _plan(carpeta: Path) -> tuple[Path, dict]:
    ruta = carpeta / "dispatch.json"
    if not ruta.exists():
        raise SystemExit(
            f"{carpeta.name}: no hay dispatch.json — la puerta aun no la despacho"
        )
    return ruta, json.loads(ruta.read_text())


def _gate(carpeta: Path, datos: dict, decision: str, **extra) -> None:
    registro = {
        "uuid": datos.get("uuid"),
        "decision": decision,
        "firmado_por": extra.pop("firmado_por", "usuario"),
        "fecha": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        **extra,
    }
    (carpeta / "validation").mkdir(exist_ok=True)
    (carpeta / "validation" / "gate.json").write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _assets_incompletos(carpeta: Path) -> list[str]:
    if not (carpeta / "assets" / "manifiesto.yaml").exists():
        return []
    r = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("render-asset.py")),
            str(carpeta),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        return []
    return [
        ln.strip()[2:] for ln in r.stdout.splitlines() if ln.strip().startswith("- ")
    ]


def _brief(
    carpeta: Path, datos: dict, formato: str, audiencia: str, salida: Path
) -> dict:
    salida_expectada = datos.get("salida_expectada") or {}
    return {
        "version": 1,
        "uuid_origen": datos.get("uuid"),
        "tema": datos.get("tema"),
        "tipo": salida_expectada.get("tipo") or "informe",
        "formato": formato,
        "audiencia": audiencia,
        "marca": datos.get("marca"),
        "destino_post_investigacion": datos.get("destino_post_investigacion"),
        "insumos": {
            "informe": str(carpeta / "deliverables" / "informe.md"),
            "claims": str(carpeta / "validation" / "claims.json"),
            "veredictos": str(carpeta / "validation" / "verdicts.json"),
            "manifiesto": str(carpeta / "assets" / "manifiesto.yaml"),
            "assets": str(carpeta / "assets"),
        },
        "salida": str(salida),
        "fecha": dt.date.today().isoformat(),
    }


def verbo_approve(uuid: str, nota: str | None) -> int:
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    if datos.get("estado") == "ready":
        print(f"{uuid}: ya estaba aprobada (ready)")
        return 0
    _, plan = _plan(carpeta)
    card = plan.get("gate")
    if card:
        rc, out = _kanban(
            "complete", card, "--result", nota or "aprobada por el usuario"
        )
        if rc != 0:
            print(f"kanban no cerro la card de gate {card}: {out[:200]}")
            return 1
    incompletos = _assets_incompletos(carpeta)
    if incompletos:
        print("APROBADA CON AVISOS — los assets no estan completos:")
        for p in incompletos:
            print(f"  - {p}")
    datos["estado"] = "ready"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(carpeta, datos, "aprobado", card=card, nota=nota)
    print(f"{uuid}: aprobada -> ready (card de gate {card or 'inexistente'})")
    return 0


def verbo_improve(uuid: str, motivo: str) -> int:
    if not motivo or not motivo.strip():
        raise SystemExit(
            "improve necesita --motivo: sin motivo el redactor repite lo mismo"
        )
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    ruta_plan, plan = _plan(carpeta)
    gate_card = plan.get("gate")
    if gate_card:
        _kanban("complete", gate_card, "--result", f"devuelta con cambios: {motivo}")

    # `done` es terminal: el redactor vuelve en una card nueva, con el motivo adentro.
    anterior = plan.get("redactor") or ""
    corridas = int(plan.get("redactor_corridas") or 1)
    clave = f"{datos.get('uuid')}-r{corridas + 1}"
    cuerpo_card = "\n".join(
        [
            f"Corrección pedida en el gate humano de {datos.get('uuid')}: {motivo}",
            "",
            "Reescribí el informe atendiendo eso y nada más. El material no cambia:",
            f"  fichas:     {carpeta / 'sources'}",
            f"  claims:     {carpeta / 'validation' / 'claims.json'}",
            f"  veredictos: {carpeta / 'validation' / 'verdicts.json'}",
            f"  manifiesto: {carpeta / 'assets' / 'manifiesto.yaml'}",
            f"  informe:    {carpeta / 'deliverables' / 'informe.md'}",
            "",
            "Si el cambio exige un asset nuevo, agregalo al manifiesto y corré render-asset.py.",
            "Escribí el informe completo una vez; corregí después con patch.",
        ]
    )
    rc, out = _kanban(
        "create",
        f"Redactor v{corridas + 1}: {datos.get('tema', '')[:60]}",
        "--assignee",
        "redactor",
        "--tenant",
        str(datos.get("uuid")),
        "--idempotency-key",
        clave,
        "--skill",
        "redactor-documento",
        "--parent",
        str(plan.get("raiz") or ""),
        "--body",
        cuerpo_card,
    )
    if rc != 0:
        print(f"kanban no creo la card de correccion: {out[:300]}")
        return 1
    m = re.search(r"t_[0-9A-Za-z]{6,}", out)
    nueva = m.group(0) if m else ""
    plan["redactor"] = nueva
    plan["redactor_corridas"] = corridas + 1
    plan.pop("gate", None)
    ruta_plan.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    correcciones = datos.get("correcciones") or []
    if not isinstance(correcciones, list):
        correcciones = [str(correcciones)]
    correcciones.append(f"[gate humano] {motivo}")
    datos["correcciones"] = correcciones
    datos["estado"] = "processing"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(
        carpeta,
        datos,
        "devuelto_con_cambios",
        card=nueva,
        anterior=anterior,
        motivo=motivo,
    )
    print(f"{uuid}: devuelta al redactor en la card {nueva} — estado processing")
    return 0


def verbo_present(uuid: str, formato: str | None, audiencia: str | None) -> int:
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    if datos.get("estado") not in ("ready", "waiting"):
        raise SystemExit(
            f"{uuid}: primero hay que aprobarla (estado actual: {datos.get('estado')})"
        )
    salida_expectada = datos.get("salida_expectada") or {}
    formato = formato or salida_expectada.get("formato") or "md"
    audiencia = audiencia or salida_expectada.get("audiencia") or "interno"

    incompletos = _assets_incompletos(carpeta)
    if incompletos:
        print("NO SALE A PRESENTACION — los assets no estan completos:")
        for p in incompletos:
            print(f"  - {p}")
        return 1

    slug = (
        "informe"
        if formato == "md"
        else str(salida_expectada.get("tipo") or "documento")
    )
    salida = carpeta / "deliverables" / f"{slug}.{formato}"
    brief = _brief(carpeta, datos, formato, audiencia, salida)

    import yaml

    texto = yaml.safe_dump(
        brief, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    COLA_DOCUMENTOS.mkdir(parents=True, exist_ok=True)
    destino_cola = COLA_DOCUMENTOS / f"{uuid}.yaml"
    destino_cola.write_text(texto, encoding="utf-8")
    (carpeta / "deliverables" / "document-brief.yaml").write_text(
        texto, encoding="utf-8"
    )

    datos["estado"] = "waiting"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(
        carpeta,
        datos,
        "en_presentacion",
        formato=formato,
        audiencia=audiencia,
        brief=str(destino_cola),
    )
    print(f"{uuid}: enviada al presenter -> waiting")
    print(f"  brief: {destino_cola}")
    print(f"  salida esperada: {salida}")
    return 0


def verbo_show(uuid: str) -> int:
    carpeta = _carpeta(uuid)
    datos, _ = _leer(carpeta / "request.md")
    gate = carpeta / "validation" / "gate.json"
    print(f"uuid      {datos.get('uuid')}")
    print(f"carpeta   {carpeta}")
    print(f"estado    {datos.get('estado')}")
    print(f"salida    {datos.get('salida_expectada') or '(no declarada)'}")
    try:
        _, plan = _plan(carpeta)
        print(f"redactor  {plan.get('redactor') or '(sin card)'}")
        print(f"gate card {plan.get('gate') or '(sin card de gate)'}")
    except SystemExit:
        print("plan      (sin dispatch.json)")
    if gate.exists():
        g = json.loads(gate.read_text())
        print(
            f"decision  {g.get('decision')} · {g.get('fecha')} · {g.get('firmado_por')}"
        )
        if g.get("motivo"):
            print(f"motivo    {g['motivo']}")
    else:
        print("decision  (sin decision registrada)")
    manifiesto = carpeta / "assets" / "manifiesto.yaml"
    if manifiesto.exists():
        import yaml

        n = len((yaml.safe_load(manifiesto.read_text()) or {}).get("assets") or [])
        print(f"assets    {n} en el manifiesto")
    return 0


def _ficha(carpeta: Path) -> dict:
    """Lo que hace falta para nombrar una investigacion en una lista, sin abrir nada mas."""
    datos, _ = _leer(carpeta / "request.md")
    try:
        _, plan = _plan(carpeta)
    except SystemExit:
        plan = {}
    return {
        "uuid": datos.get("uuid") or carpeta.name,
        "tema": (datos.get("tema") or "(sin tema)").strip(),
        "estado": datos.get("estado") or "?",
        "salida": (datos.get("salida_expectada") or {}).get("formato") or "",
        "gate": plan.get("gate") or "",
        "redactor": plan.get("redactor") or "",
        "despachada": bool(plan),
        "tiene_gate": (carpeta / "validation" / "gate.json").exists(),
        "carpeta": carpeta,
    }


def _linea(f: dict, extra: str = "") -> str:
    tema = f["tema"] if len(f["tema"]) <= 42 else f["tema"][:41] + "…"
    return f"{f['uuid']}  {tema}" + (f"\n              {extra}" if extra else "")


def verbo_pending(_uuid: str | None = None) -> int:
    """Solo lo que espera una firma tuya. Es la vista que se consulta a diario."""
    esperando: list[dict] = []
    if PROCESSING.exists():
        for carpeta in sorted(PROCESSING.iterdir()):
            if carpeta.is_dir() and (carpeta / "request.md").exists():
                try:
                    f = _ficha(carpeta)
                except SystemExit:
                    continue
                if f["gate"] and not f["tiene_gate"]:
                    esperando.append(f)
    if not esperando:
        print("Nada espera tu decision.")
        return 0
    print(f"Esperando tu decision: {len(esperando)}\n")
    for f in esperando:
        incompletos = _assets_incompletos(f["carpeta"])
        aviso = ""
        if incompletos:
            aviso = (
                f" · {len(incompletos)} asset(s) incompletos: approve sale con avisos"
            )
        print(_linea(f, f"gate {f['gate']}{aviso}"))
        print(f"              /research approve {f['uuid']}")
        print(f"              /research show {f['uuid']}")
        print()
    return 0


def verbo_panel(_uuid: str | None = None) -> int:
    """El tablero entero de research, por etapa. Sin modelo: lee disco y nada mas."""
    bloques: dict[str, list[dict]] = {
        "Esperando tu decision": [],
        "En curso": [],
        "Sin cerrar": [],
        "En presentacion": [],
        "Aprobadas": [],
        "En cola": [],
    }
    if PROCESSING.exists():
        for carpeta in sorted(PROCESSING.iterdir()):
            if not (carpeta.is_dir() and (carpeta / "request.md").exists()):
                continue
            try:
                f = _ficha(carpeta)
            except SystemExit:
                continue
            if f["estado"] == "waiting":
                bloques["En presentacion"].append(f)
            elif f["gate"] and not f["tiene_gate"]:
                bloques["Esperando tu decision"].append(f)
            elif not f["redactor"]:
                # Despachada pero sin card de redactor: el estado dice `processing` y la
                # cadena no cerro. Sonaba a "corriendo" y no lo estaba.
                bloques["Sin cerrar"].append(f)
            else:
                bloques["En curso"].append(f)
    if READY.exists():
        for carpeta in sorted(READY.iterdir()):
            if carpeta.is_dir() and (carpeta / "request.md").exists():
                try:
                    f = _ficha(carpeta)
                except SystemExit:
                    continue
                bloques["Aprobadas"].append(f)
    if REQUESTED.exists():
        for archivo in sorted(REQUESTED.glob("*.md")):
            if archivo.name in ("PLANTILLA.md",) or archivo.name.startswith("_"):
                continue
            try:
                datos, _ = _leer(archivo)
            except SystemExit:
                continue
            bloques["En cola"].append(
                {
                    "uuid": datos.get("uuid") or archivo.stem,
                    "tema": (datos.get("tema") or "(sin tema)").strip(),
                    "estado": "requested",
                    "salida": "",
                    "gate": "",
                    "redactor": "",
                    "tiene_gate": False,
                    "carpeta": archivo,
                }
            )
    total = sum(len(v) for v in bloques.values())
    print(f"RESEARCH · {total} en el tablero\n")
    if not total:
        print("Nada. Un pedido se deja en la cola y la puerta lo clasifica.")
        return 0
    for titulo, filas in bloques.items():
        if not filas:
            continue
        print(f"{titulo} ({len(filas)})")
        for f in filas:
            if titulo == "Esperando tu decision":
                print(_linea(f, f"gate {f['gate']} · /research approve {f['uuid']}"))
            elif titulo == "En presentacion":
                print(
                    _linea(
                        f,
                        f"{f['salida'] or 'formato sin definir'} · esperando al presenter",
                    )
                )
            elif titulo == "Aprobadas":
                print(_linea(f, f"ready · /research present {f['uuid']}"))
            elif titulo == "Sin cerrar":
                print(
                    _linea(f, "processing sin redactor - hay que relanzarla o cerrarla")
                )
            elif titulo == "En curso":
                if not f["despachada"]:
                    quien = "sin despacho - la puerta no la tomo"
                elif not f["redactor"]:
                    quien = "la cadena no cerro - sin card de redactor"
                else:
                    quien = f"redactor {f['redactor']}"
                print(_linea(f, quien))
            else:
                print(_linea(f))
        print()
    return 0


def verbo_assets(uuid: str) -> int:
    """El manifiesto contra el disco: que falta renderizar y que referencia rota hay."""
    carpeta = _carpeta(uuid)
    ruta = Path(__file__).with_name("render-asset.py")
    r = subprocess.run(
        ["python3", str(ruta), str(carpeta), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    print((r.stdout or "").strip() or (r.stderr or "").strip())
    print(f"carpeta   {carpeta}")
    return r.returncode


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return verbo_panel()
    verbo = argv[1]
    # `panel` y `pending` no hablan de una investigacion: hablan del tablero, asi que no
    # llevan uuid. Todo lo demas si, y sin el se muestra la ayuda en vez de adivinar.
    if verbo == "panel":
        return verbo_panel()
    if verbo == "pending":
        return verbo_pending()
    if len(argv) < 3:
        print(__doc__)
        return 2
    uuid = argv[2]
    if verbo == "approve":
        nota = argv[argv.index("--nota") + 1] if "--nota" in argv else None
        return verbo_approve(uuid, nota)
    if verbo == "improve":
        if "--motivo" not in argv:
            raise SystemExit("improve necesita --motivo")
        return verbo_improve(uuid, argv[argv.index("--motivo") + 1])
    if verbo == "present":
        formato = argv[argv.index("--formato") + 1] if "--formato" in argv else None
        audiencia = (
            argv[argv.index("--audiencia") + 1] if "--audiencia" in argv else None
        )
        return verbo_present(uuid, formato, audiencia)
    if verbo == "show":
        return verbo_show(uuid)
    if verbo == "assets":
        return verbo_assets(uuid)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
