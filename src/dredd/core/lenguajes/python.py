"""Adaptador de lenguaje Python para dredd (sin dependencias C ni tree-sitter-c)."""

from __future__ import annotations

import ast
import io
import keyword
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tokenize
from typing import Any, Dict, List, Optional, Tuple

from dredd.core.lenguajes.base import (
    AdaptadorLenguaje,
    _valgrind_inactivo,
    ejecutar_satelite_json,
    normalizar_hallazgo_yutani,
    recolectar_pares_io_locales,
)


def _limpiar_comments_fallback(codigo: str) -> List[Tuple[str, int]]:
    tokens: List[Tuple[str, int]] = []
    pat = re.compile(r'([a-zA-Z_]\w*|\d+|==|!=|<=|>=|\*\*|//|[+\-*/%<>=!&|^~:;,.(){}\[\]])')
    for lineno, linea in enumerate(re.sub(r"#.*", "", codigo).splitlines(), 1):
        for m in pat.finditer(linea):
            raw = m.group(1)
            if keyword.iskeyword(raw) or not raw.isidentifier():
                tokens.append((raw, lineno))
            elif raw.isdigit():
                tokens.append(("NUM", lineno))
            else:
                tokens.append(("ID", lineno))
    return tokens


def _ejecutar_pytest(ruta: Path, timeout: float = 30.0) -> List[Dict[str, Any]]:
    pytest_bin = shutil.which("pytest")
    cmd = [pytest_bin, "-q", "--tb=short"] if pytest_bin else [sys.executable, "-m", "pytest", "-q", "--tb=short"]
    try:
        proc = subprocess.run(cmd, cwd=ruta, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return [{
            "name": "pytest",
            "passed": False,
            "return_code": -1,
            "timed_out": isinstance(exc, subprocess.TimeoutExpired),
            "memory_leak": False,
            "sanitizer_error": f"No se pudo ejecutar pytest: {exc}",
        }]
    salida = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    m_passed = re.search(r"(\d+)\s+passed", salida)
    m_failed = re.search(r"(\d+)\s+failed", salida)
    n_pass = int(m_passed.group(1)) if m_passed else (1 if proc.returncode == 0 else 0)
    n_fail = int(m_failed.group(1)) if m_failed else (0 if proc.returncode == 0 else 1)
    casos: List[Dict[str, Any]] = []
    for i in range(n_pass):
        casos.append({"name": f"pytest_pass_{i + 1}", "passed": True, "return_code": 0, "timed_out": False, "memory_leak": False})
    for i in range(n_fail):
        casos.append({
            "name": f"pytest_fail_{i + 1}",
            "passed": False,
            "return_code": proc.returncode,
            "timed_out": False,
            "memory_leak": False,
            "sanitizer_error": salida.strip()[-600:],
        })
    return casos


def _archivo_principal_python(fuentes: List[Path]) -> Optional[Path]:
    no_tests = [
        f for f in fuentes
        if not f.name.startswith("test_") and not f.name.endswith("_test.py") and f.name != "__init__.py"
    ]
    for f in no_tests:
        if f.name == "main.py":
            return f
    for f in no_tests:
        txt = f.read_text(encoding="utf-8", errors="replace")
        if "__main__" in txt:
            return f
    return no_tests[0] if no_tests else None


class AdaptadorPython(AdaptadorLenguaje):
    """Adaptador pedagógico para entregas en Python."""

    nombre = "python"
    extensiones_fuente = frozenset({".py"})
    extensiones_ingesta = frozenset({".py", ".toml", ".txt", ".md", ".in", ".out", ".yaml", ".yml", ".json"})
    archivos_proyecto = frozenset({"pyproject.toml", "requirements.txt", "pytest.ini"})

    def compilar_o_verificar(self, ruta: Path, checks: Any = None) -> Dict[str, Any]:
        fuentes = self.archivos_fuente(ruta)
        errores: List[str] = []
        for f in fuentes:
            texto = f.read_text(encoding="utf-8", errors="replace")
            try:
                ast.parse(texto, filename=str(f))
            except SyntaxError as exc:
                linea = exc.lineno if exc.lineno and exc.lineno >= 1 else 1
                errores.append(f"{f.name}:{linea}: error de sintaxis ({exc.msg})")
        return {
            "success": not errores,
            "errors": errores,
            "warnings": [],
            "compiler": "python-ast",
        }

    def analizar_estatico(self, ruta: Path, checks: Any = None) -> List[Dict[str, Any]]:
        hallazgos = ejecutar_satelite_json("ripley-py", ruta)
        if hallazgos is not None:
            return hallazgos

        try:
            from ripley_py.analizadores import ejecutar_suite
            return [normalizar_hallazgo_yutani(h) for h in ejecutar_suite(ruta)]
        except ImportError:
            pass

        salida: List[Dict[str, Any]] = []
        for f in self.archivos_fuente(ruta):
            try:
                ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
            except SyntaxError as exc:
                salida.append(normalizar_hallazgo_yutani({
                    "herramienta": "dredd-py",
                    "codigo": "DPY001",
                    "categoria": "sintaxis",
                    "severidad": "error",
                    "mensaje": f"Error de sintaxis en Python: {exc.msg}.",
                    "archivo": str(f),
                    "linea": exc.lineno or 1,
                    "sugerencia": "Revisá paréntesis, dos puntos e indentación en la línea indicada.",
                }))
        return salida

    def ejecutar_pruebas(
        self,
        ruta: Path,
        guide: Any = None,
        checks: Any = None,
    ) -> List[Dict[str, Any]]:
        from dredd.core.sandbox import execute_sandboxed

        fuentes = self.archivos_fuente(ruta)
        tests_py = [f for f in fuentes if f.name.startswith("test_") or f.name.endswith("_test.py")]
        if tests_py:
            return _ejecutar_pytest(ruta)

        pares = recolectar_pares_io_locales(ruta)
        if not pares:
            return []
        principal = _archivo_principal_python(fuentes)
        if principal is None:
            return []

        timeout = float(getattr(checks, "sandbox_timeout_seconds", 5.0))
        casos: List[Dict[str, Any]] = []
        for nombre, in_f, out_f in pares:
            in_txt = in_f.read_text(encoding="utf-8", errors="replace")
            exp_txt = out_f.read_text(encoding="utf-8", errors="replace").strip()
            rc, out, err, timed_out = execute_sandboxed(
                cmd=[sys.executable, str(principal)],
                input_data=in_txt,
                timeout=timeout,
                max_memory_mb=0,
                workspace=ruta,
                allow_nostromo=False,
            )
            act_txt = out.strip()
            passed = (rc == 0) and not timed_out and (act_txt == exp_txt or not exp_txt)
            casos.append({
                "name": nombre,
                "passed": passed,
                "input_data": in_txt,
                "expected_output": exp_txt,
                "actual_output": act_txt,
                "return_code": rc,
                "timed_out": timed_out,
                "memory_leak": False,
                "sanitizer_error": err.strip() if rc != 0 else (
                    f"Salida esperada:\n{exp_txt}\nObtenida:\n{act_txt}" if not passed else ""
                ),
            })
        return casos

    def tokenizar_fallback(self, codigo: str) -> List[Tuple[str, int]]:
        tokens: List[Tuple[str, int]] = []
        omitir = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING, tokenize.ENDMARKER}
        try:
            for tok in tokenize.generate_tokens(io.StringIO(codigo).readline):
                if tok.type in omitir:
                    continue
                if tok.type == tokenize.NAME:
                    simbolo = tok.string if keyword.iskeyword(tok.string) else "ID"
                elif tok.type == tokenize.NUMBER:
                    simbolo = "NUM"
                elif tok.type == tokenize.STRING:
                    simbolo = "STR"
                else:
                    simbolo = tok.string
                if simbolo:
                    tokens.append((simbolo, max(1, tok.start[0])))
        except (tokenize.TokenError, IndentationError):
            return _limpiar_comments_fallback(codigo)
        return tokens

    def chequeos_doctor(self) -> List[Tuple[str, str, bool, str]]:
        return [
            ("python3", "Intérprete Python para verificar y ejecutar entregas", True, "Instalá Python >= 3.11"),
            ("ripley-py", "Orquestador estático pedagógico para Python (R6)", False, "uv tool install git+https://github.com/INGCOM-UNRN/ripley-py"),
            ("pytest", "Runner de pruebas unitarias Python", False, "pip install pytest"),
        ]

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
        from dredd.core.baseline import classify_submission_exercises, find_baseline_dir
        from dredd.core.ripley_client import _collect_binary_findings, _resolve_checks_and_mode

        checks, _, _ = _resolve_checks_and_mode(
            target_path, guide, activity_slug, workspace_dir, checks_override, tipo_entrega
        )
        binary_findings = _collect_binary_findings(target_path)
        b_dir = baseline_dir or find_baseline_dir(target_path, workspace_dir=workspace_dir)
        baseline_info = classify_submission_exercises(target_path, baseline_dir=b_dir)

        comp = self.compilar_o_verificar(target_path, checks=checks)
        ast_findings = self.analizar_estatico(target_path, checks=checks)
        all_findings = binary_findings + ast_findings
        casos = self.ejecutar_pruebas(target_path, guide=guide, checks=checks)
        tests_dict: Dict[str, Any] = {}
        if casos:
            tests_dict = {
                "total": len(casos),
                "passed": sum(1 for c in casos if c.get("passed")),
                "failed": sum(1 for c in casos if not c.get("passed")),
                "cases": casos,
                "is_project": (target_path / "pyproject.toml").is_file(),
                "has_test_target": True,
            }

        fuentes = self.archivos_fuente(target_path)
        return {
            "language": "python",
            "compilation": comp,
            "ast_findings": all_findings,
            "style_findings": [],
            "spunkmeyer_findings": [],
            "binary_findings": binary_findings,
            "tests": tests_dict,
            "valgrind": _valgrind_inactivo(),
            "baseline_info": baseline_info,
            "metrics": {
                "source_files_count": len(fuentes),
                "ast_findings_count": len(all_findings),
            },
        }
