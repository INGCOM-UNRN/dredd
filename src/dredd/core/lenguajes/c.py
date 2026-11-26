"""Adaptador de lenguaje C para dredd (encapsula el pipeline histórico de P1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dredd.core.lenguajes.base import AdaptadorLenguaje

HERRAMIENTAS_C: List[Tuple[str, str, bool, str]] = [
    ("gcc", "Compilación de código C (fallback nativo)", True, "sudo apt install build-essential"),
    ("daedalus", "Compilador pedagógico oficial con flags cátedra P1", False, "uv tool install git+https://github.com/INGCOM-UNRN-P1/daedalus"),
    ("valgrind", "Detección de fugas de memoria y memory errors", False, "sudo apt install valgrind"),
    ("nostromo", "Sandbox de aislamiento Bubblewrap y runner de tests", False, "uv tool install git+https://github.com/INGCOM-UNRN-P1/nostromo"),
    ("gaff", "Linter canónico de reglas de estilo de cátedra", False, "uv tool install git+https://github.com/INGCOM-UNRN-P1/gaff"),
    ("spunkmeyer", "Detector de antipatrones didácticos C", False, "uv tool install git+https://github.com/INGCOM-UNRN-P1/spunkmeyer"),
    ("ripley", "Motor de análisis pedagógico P1 (satélite opcional)", False, "uv tool install git+https://github.com/INGCOM-UNRN-P1/ripley"),
]


class AdaptadorC(AdaptadorLenguaje):
    """Adaptador para entregas en C (Programación 1)."""

    nombre = "c"
    extensiones_fuente = frozenset({".c", ".h"})
    extensiones_ingesta = frozenset({".c", ".h", ".mk", ".md", ".txt", ".in", ".out"})
    archivos_proyecto = frozenset({"makefile", "cmakelists.txt"})

    def compilar_o_verificar(self, ruta: Path, checks: Any = None) -> Dict[str, Any]:
        from dredd.core.compiler import compile_c_sources, compile_with_make

        if (ruta / "Makefile").is_file() or (ruta / "makefile").is_file():
            res = compile_with_make(ruta)
            return res.to_dict()
        fuentes = [f for f in self.archivos_fuente(ruta) if f.suffix.lower() == ".c"]
        if not fuentes:
            return {"success": True, "errors": [], "warnings": [], "compiler": "none"}
        flags = getattr(checks, "compiler_flags", "-Wall -Wextra -std=c11")
        prefer = getattr(checks, "daedalus_compiler", "daedalus")
        res = compile_c_sources(fuentes, extra_flags=flags.split(), prefer_compiler=prefer)
        return res.to_dict()

    def analizar_estatico(self, ruta: Path, checks: Any = None) -> List[Dict[str, Any]]:
        from dredd.core.config import ToolChecksConfig
        from dredd.core.ripley_client import _collect_ast_findings

        cfg = checks or ToolChecksConfig()
        return _collect_ast_findings(ruta, cfg, set())

    def ejecutar_pruebas(
        self,
        ruta: Path,
        guide: Any = None,
        checks: Any = None,
    ) -> List[Dict[str, Any]]:
        from dredd.core.ripley_client import discover_and_run_local_testcases, evaluate_guide_testcases

        if guide and getattr(guide, "exercises", None):
            return evaluate_guide_testcases(ruta, guide)
        return discover_and_run_local_testcases(ruta)

    def tokenizar_fallback(self, codigo: str) -> List[Tuple[str, int]]:
        from dredd.core.plagiarism import tokenize_c_code

        return tokenize_c_code(codigo)

    def chequeos_doctor(self) -> List[Tuple[str, str, bool, str]]:
        return list(HERRAMIENTAS_C)

    def evaluar_entrega(
        self,
        target_path: Path,
        *,
        guide: Any = None,
        activity_slug: Optional[str] = None,
        workspace_dir: Optional[Path] = None,
        checks_override: Any = None,
        tipo_entrega: Optional[str] = None,
        baseline_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        from dredd.core.ripley_client import _run_c_analysis

        res = _run_c_analysis(
            target_path,
            guide=guide,
            activity_slug=activity_slug,
            workspace_dir=workspace_dir,
            checks_override=checks_override,
            tipo_entrega=tipo_entrega,
            baseline_dir=baseline_dir,
        )
        res["language"] = "c"
        return res
