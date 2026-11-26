"""Diagnóstico de dependencias de sistema y capacidades de kernel para Dredd."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Dict, Any, List, Optional, Tuple

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from dredd.core.ecosystem import resolve_sibling_cli


def chequear_herramienta(comando: str, args_version: str = "--version") -> Dict[str, Any]:
    """Verifica si un comando está disponible en $PATH o en el ecosistema hermano y obtiene su versión.

    `falla` dice por qué, si el comando está pero `--version` termina con error (p. ej., una
    instalación vieja que ya no importa): antes se tomaba la primera línea del traceback como la
    versión y la herramienta figuraba «OK» (N-DREDD-06).
    """
    path = resolve_sibling_cli(comando)
    if not path:
        return {"disponible": False, "version": None, "ruta": None, "falla": None}

    try:
        res = subprocess.run(
            [path, args_version],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:  # lenta o sin permisos: está, pero no se pudo preguntar la versión
        return {"disponible": True, "version": "Detectada", "ruta": path, "falla": None}
    if res.returncode != 0:
        lineas = [linea.strip() for linea in (res.stderr or res.stdout).splitlines() if linea.strip()]
        falla = lineas[-1] if lineas else f"código de salida {res.returncode}"
        return {"disponible": True, "version": None, "ruta": path, "falla": falla}
    salida = (res.stdout or res.stderr).strip().splitlines()
    return {"disponible": True, "version": salida[0] if salida else "Detectada", "ruta": path, "falla": None}


def chequear_capacidades_kernel() -> Dict[str, bool]:
    """Verifica soporte de namespaces y bubblewrap en el kernel Linux."""
    bwrap_path = shutil.which("bwrap")
    if not bwrap_path:
        return {"unshare_user": False, "bwrap_functional": False}
        
    try:
        res = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "true"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        bwrap_ok = (res.returncode == 0)
    except Exception:
        bwrap_ok = False
        
    return {"unshare_user": True, "bwrap_functional": bwrap_ok}


HERRAMIENTAS: List[Tuple[str, str, bool, str]] = [
    ("git", "Operaciones de repositorio y auditoría de commits", True, "sudo apt install git"),
    ("bwrap", "Sandbox de aislamiento Bubblewrap en el sistema", False, "sudo apt install bubblewrap"),
    ("deckard", "Integración con guías y bancos de ejercicios", False, "uv tool install git+https://github.com/INGCOM-UNRN/deckard"),
]


def _herramientas_para(lang: str = "all") -> List[Tuple[str, str, bool, str]]:
    from dredd.core.config import DreddConfig, load_dredd_config
    from dredd.core.lenguajes import listar_adaptadores, normalizar_lenguaje

    limpio = str(lang or "all").strip().lower()
    if limpio == "auto":
        cfg = load_dredd_config(Path.cwd())
        if cfg is not None:
            norm_cfg = normalizar_lenguaje(cfg.workspace.language)
            limpio = "all" if norm_cfg == "auto" else norm_cfg
        else:
            limpio = "all"

    registro = listar_adaptadores()
    lista: List[Tuple[str, str, bool, str]] = list(HERRAMIENTAS)
    vistos = {cmd for cmd, *_ in lista}

    if limpio == "all":
        for nombre in sorted(registro):
            for cmd, desc, _req, fix in registro[nombre].chequeos_doctor():
                if cmd not in vistos:
                    vistos.add(cmd)
                    # En perfil global 'all', solo git es obligatorio universal.
                    lista.append((cmd, desc, False, fix))
    else:
        norm = normalizar_lenguaje(limpio)
        if norm not in registro:
            raise ValueError(f"Lenguaje desconocido en doctor: {lang!r}")
        for cmd, desc, req, fix in registro[norm].chequeos_doctor():
            if cmd not in vistos:
                vistos.add(cmd)
                lista.append((cmd, desc, req, fix))
    return lista


def diagnosticar(lang: str = "all") -> List[Dict[str, Any]]:
    """Estado de cada herramienta y del sandbox según perfil de lenguaje (`all`, `auto`, `c`, `python`, `java`)."""
    chequeos = []
    for cmd, desc, obligatorio, fix in _herramientas_para(lang):
        info = chequear_herramienta(cmd)
        funciona = info["disponible"] and not info["falla"]
        if funciona:
            detalle = f"{info['version']} ({info['ruta']})"
        elif info["disponible"]:
            detalle = f"instalada, pero `{cmd} --version` falla: {info['falla']} ({info['ruta']})"
        else:
            detalle = "No encontrado en $PATH"
        chequeos.append({
            "nombre": cmd,
            "requerido": obligatorio,
            "ok": funciona,
            "falla": bool(info["falla"]),
            "detalle": detalle,
            "proposito": desc,
            "sugerencia": "" if funciona else (f"Reinstalá: {fix}" if info["falla"] else fix),
        })

    try:
        from yutani.jplag import jplag_disponible
        jplag_ok, jplag_det = jplag_disponible()
    except Exception as exc:
        jplag_ok, jplag_det = False, str(exc)
    chequeos.append({
        "nombre": "jplag",
        "requerido": False,
        "ok": jplag_ok,
        "falla": False,
        "detalle": jplag_det,
        "proposito": "Detección de plagio multi-lenguaje (Winnowing como fallback)",
        "sugerencia": "" if jplag_ok else "mother instalar --perfil docente --setup-jplag",
    })

    capacidades = chequear_capacidades_kernel()
    chequeos.append({
        "nombre": "sandbox",
        "requerido": False,
        "ok": capacidades["bwrap_functional"],
        "detalle": "namespaces operativos para aislar las entregas" if capacidades["bwrap_functional"]
        else "sin bubblewrap funcional: las entregas no se ejecutan salvo DREDD_PERMITIR_SIN_SANDBOX=1",
        "proposito": "Aislamiento de las entregas (N-DREDD-02)",
        "sugerencia": "" if capacidades["bwrap_functional"] else "Instalá bubblewrap o nostromo",
    })
    return chequeos


def informe_json(chequeos: List[Dict[str, Any]], language: str = "all") -> Dict[str, Any]:
    """Sobre JSON común de `doctor --json` (schema_version 1.0.0)."""
    from dredd import __version__

    return {
        "schema_version": "1.0.0",
        "herramienta": "dredd",
        "version": __version__,
        "language": language,
        "ok": all(c["ok"] for c in chequeos if c["requerido"]),
        "chequeos": chequeos,
    }


def ejecutar_diagnostico_doctor(console: Optional[Console] = None, lang: str = "all") -> bool:
    """Muestra el diagnóstico completo de Dredd."""
    cons = console or Console()
    tabla = Table(title=f"🏥 Diagnóstico del Entorno de Dredd (doctor · {lang})", border_style="cyan")
    tabla.add_column("Herramienta", style="bold white")
    tabla.add_column("Estado", justify="center")
    tabla.add_column("Versión / Ruta", style="dim")
    tabla.add_column("Propósito / Acción sugerida", style="yellow")

    chequeos = diagnosticar(lang=lang)
    todo_ok = True
    for chequeo in chequeos:
        if chequeo["nombre"] == "sandbox":
            continue
        if chequeo["ok"]:
            estado = "[bold green]✓ OK[/bold green]"
            detalles = chequeo["detalle"]
            accion = chequeo["proposito"]
        else:
            problema = "Falla" if chequeo.get("falla") else "Faltante"
            if chequeo["requerido"]:
                estado = f"[bold red]✗ {problema} (Crítico)[/bold red]"
                todo_ok = False
            else:
                estado = f"[yellow]! {problema} (opcional)[/yellow]" if chequeo.get("falla") else "[yellow]! Opcional[/yellow]"
            detalles = f"[dim]{escape(chequeo['detalle'])}[/dim]"
            prefijo = "" if chequeo.get("falla") else "Instalar: "  # la sugerencia de una falla ya dice «Reinstalá»
            accion = f"{chequeo['proposito']} ↳ {prefijo}{escape(chequeo['sugerencia'])}"
        tabla.add_row(chequeo["nombre"], estado, detalles, accion)

    cons.print(tabla)

    sandbox = next(c for c in chequeos if c["nombre"] == "sandbox")
    if sandbox["ok"]:
        cons.print("  [bold green]✓ Sandbox Bubblewrap:[/bold green] Kernel namespaces operativos para aislamiento seguro.")
    else:
        cons.print("  [yellow]! Sandbox Bubblewrap:[/yellow] No funcional: las entregas no se ejecutan salvo que "
                   "exportes DREDD_PERMITIR_SIN_SANDBOX=1 (sin aislamiento). Instalá bubblewrap o nostromo.")

    return todo_ok
