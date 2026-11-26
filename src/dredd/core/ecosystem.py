"""Módulo de resolución de herramientas hermanas del ecosistema Cátedra P1."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
import shutil
from typing import Any, Callable, Optional

logger = logging.getLogger("dredd.ecosystem")


def resolve_sibling_tool(
    tool_name: str,
    module_path: str,
    symbol_name: str,
) -> Optional[Callable[..., Any]]:
    """Resuelve un símbolo de otra herramienta del ecosistema instalada en el mismo entorno.

    Las herramientas que dredd usa como biblioteca se declaran en los extras
    `ecosistema` y `guias` de pyproject (referencias git fijadas); ya no se buscan
    en carpetas hermanas del monorepo (N-ECO-01). Si no están instaladas, devuelve
    None y el llamador usa su camino propio.
    """
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        logger.debug(f"'{tool_name}' no está instalado en este entorno ({e}); se usa el camino propio.")
        return None
    return getattr(mod, symbol_name, None)


def _raiz_workspace() -> Optional[Path]:
    inicio = Path(__file__).resolve()
    for ancestro in inicio.parents:
        if (ancestro / "c" / "gestion" / "p1-tools" / "ecosistema.toml").is_file() or (
            ancestro / "reinstalar_herramientas.py"
        ).is_file():
            return ancestro
    return None


def resolve_sibling_cli(tool_name: str) -> Optional[str]:
    """Resuelve la ruta ejecutable de un CLI hermano en PATH, árbol reorganizado o ~/.local/bin."""
    # 1. En PATH
    bin_path = shutil.which(tool_name)
    if bin_path:
        return bin_path

    # 2. En .venv de la herramienta hermana dentro del workspace reorganizado (o plano legacy)
    raiz = _raiz_workspace()
    if raiz is not None:
        candidatos = [
            raiz / "c" / "satelites" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "c" / "soporte" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "c" / "gestion" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "python" / "satelites" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "java" / "satelites" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "docente" / "contenidos" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "docente" / "evaluacion" / tool_name / ".venv" / "bin" / tool_name,
            raiz / "docente" / "administracion" / tool_name / ".venv" / "bin" / tool_name,
        ]
        # Grupos de multi-CLI como ripley-py / ripley-java alojados en una carpeta padre
        for grupo in ("ripley-py", "ripley-java"):
            candidatos.append(raiz / "python" / "satelites" / grupo / ".venv" / "bin" / tool_name)
            candidatos.append(raiz / "java" / "satelites" / grupo / ".venv" / "bin" / tool_name)
        for cand in candidatos:
            if cand.is_file():
                return str(cand)

    tools_root = Path(__file__).resolve().parents[3]
    sibling_venv_bin = tools_root / tool_name / ".venv" / "bin" / tool_name
    if sibling_venv_bin.is_file():
        return str(sibling_venv_bin)

    # 3. En ~/.local/bin
    local_bin = Path.home() / ".local" / "bin" / tool_name
    if local_bin.is_file():
        return str(local_bin)

    return None
