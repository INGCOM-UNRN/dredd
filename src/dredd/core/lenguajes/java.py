"""Adaptador de lenguaje Java para dredd (independiente de gcc y tree-sitter-c)."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from dredd.core.lenguajes.base import (
    AdaptadorLenguaje,
    _valgrind_inactivo,
    ejecutar_satelite_json,
    normalizar_hallazgo_yutani,
    recolectar_pares_io_locales,
)

JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "if", "implements", "import",
    "instanceof", "int", "interface", "long", "native", "new", "package", "private",
    "protected", "public", "record", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws", "transient",
    "try", "var", "void", "volatile", "while",
})

_RE_PAQUETE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")
_RE_CLASE_MAIN = re.compile(r"public\s+class\s+([A-Za-z_]\w*)\b")
_RE_METODO_MAIN = re.compile(r"public\s+static\s+void\s+main\s*\(")


def _balance_llaves_java(fuentes: List[Path]) -> List[str]:
    errores: List[str] = []
    for archivo in fuentes:
        texto = archivo.read_text(encoding="utf-8", errors="replace")
        prof = 0
        for idx, ch in enumerate(texto):
            if ch == "{":
                prof += 1
            elif ch == "}":
                prof -= 1
                if prof < 0:
                    linea = texto.count("\n", 0, idx) + 1
                    errores.append(f"{archivo.name}:{linea}: llave de cierre '}}' sin apertura correspondiente.")
                    break
        if prof > 0:
            errores.append(f"{archivo.name}:{max(1, len(texto.splitlines()))}: falta cerrar {prof} llave(s) '{{'.")
    return errores


def _clase_principal_java(fuentes: List[Path]) -> Optional[str]:
    for f in fuentes:
        texto = f.read_text(encoding="utf-8", errors="replace")
        if _RE_METODO_MAIN.search(texto):
            m_pkg = _RE_PAQUETE.search(texto)
            m_cls = _RE_CLASE_MAIN.search(texto)
            clase = m_cls.group(1) if m_cls else f.stem
            return f"{m_pkg.group(1)}.{clase}" if m_pkg else clase
    return None


class AdaptadorJava(AdaptadorLenguaje):
    """Adaptador pedagógico para entregas en Java."""

    nombre = "java"
    extensiones_fuente = frozenset({".java"})
    extensiones_ingesta = frozenset({".java", ".gradle", ".xml", ".properties", ".txt", ".md", ".in", ".out", ".yaml", ".yml", ".json"})
    archivos_proyecto = frozenset({"build.gradle", "settings.gradle", "pom.xml"})

    def compilar_o_verificar(self, ruta: Path, checks: Any = None) -> Dict[str, Any]:
        fuentes = self.archivos_fuente(ruta)
        if not fuentes:
            return {"success": True, "errors": [], "warnings": [], "compiler": "none"}
        errores_llaves = _balance_llaves_java(fuentes)
        if errores_llaves:
            return {"success": False, "errors": errores_llaves, "warnings": [], "compiler": "java-syntax"}

        javac = shutil.which("javac")
        if not javac:
            return {
                "success": True,
                "errors": [],
                "warnings": ["javac no encontrado en PATH; se validó solo balance sintáctico."],
                "compiler": "java-syntax",
            }
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                [javac, "-Xlint:all", "-encoding", "UTF-8", "-d", tmpdir, *[str(f) for f in fuentes]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            errores = [ln.strip() for ln in (proc.stderr or "").splitlines() if ": error:" in ln]
            avisos = [ln.strip() for ln in (proc.stderr or "").splitlines() if ": warning:" in ln]
            if proc.returncode != 0 and not errores and proc.stderr.strip():
                errores = [proc.stderr.strip()]
            return {
                "success": proc.returncode == 0,
                "errors": errores,
                "warnings": avisos,
                "compiler": "javac",
            }

    def analizar_estatico(self, ruta: Path, checks: Any = None) -> List[Dict[str, Any]]:
        hallazgos = ejecutar_satelite_json("ripley-java", ruta)
        if hallazgos is not None:
            return hallazgos

        try:
            from ripley_java.analizadores import ejecutar_suite
            return [normalizar_hallazgo_yutani(h) for h in ejecutar_suite(ruta)]
        except ImportError:
            pass

        salida: List[Dict[str, Any]] = []
        for err in _balance_llaves_java(self.archivos_fuente(ruta)):
            salida.append(normalizar_hallazgo_yutani({
                "herramienta": "dredd-java",
                "codigo": "DJA001",
                "categoria": "compilacion",
                "severidad": "error",
                "mensaje": err,
                "archivo": str(ruta),
                "linea": 1,
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
        pares = recolectar_pares_io_locales(ruta)
        if not pares or not fuentes:
            return []

        javac = shutil.which("javac")
        java_bin = shutil.which("java")
        main_cls = _clase_principal_java(fuentes)
        if not (javac and java_bin and main_cls):
            return []

        timeout = float(getattr(checks, "sandbox_timeout_seconds", 5.0))
        casos: List[Dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            comp = subprocess.run(
                [javac, "-encoding", "UTF-8", "-d", tmpdir, *[str(f) for f in fuentes]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if comp.returncode != 0:
                return []
            for nombre, in_f, out_f in pares:
                in_txt = in_f.read_text(encoding="utf-8", errors="replace")
                exp_txt = out_f.read_text(encoding="utf-8", errors="replace").strip()
                rc, out, err, timed_out = execute_sandboxed(
                    cmd=[java_bin, "-Xmx256m", "-cp", tmpdir, main_cls],
                    input_data=in_txt,
                    timeout=timeout,
                    max_memory_mb=0,
                    workspace=ruta,
                    allow_nostromo=False,
                    extra_ro_binds=[Path(tmpdir)],
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
        from dredd.core.plagiarism import strip_comments_and_strings

        limpio = strip_comments_and_strings(codigo)
        tokens: List[Tuple[str, int]] = []
        patron = re.compile(r"([a-zA-Z_$][\w$]*|\d+|==|!=|<=|>=|&&|\|\||->|[+\-*/%<>=!&|^~?:;,.(){}\[\]])")
        for lineno, linea in enumerate(limpio.splitlines(), 1):
            for m in patron.finditer(linea):
                raw = m.group(1)
                if raw in JAVA_KEYWORDS or not raw[0].isalpha() and raw[0] not in ("_", "$"):
                    tokens.append((raw, lineno))
                elif raw.isdigit():
                    tokens.append(("NUM", lineno))
                else:
                    tokens.append(("ID", lineno))
        return tokens

    def chequeos_doctor(self) -> List[Tuple[str, str, bool, str]]:
        return [
            ("java", "Máquina virtual Java (JVM) para ejecutar entregas y JPlag", True, "Instalá OpenJDK >= 17"),
            ("javac", "Compilador Java para compilar entregas y diagnósticos -Xlint", False, "Instalá JDK completo"),
            ("ripley-java", "Orquestador estático pedagógico para Java (R6)", False, "uv tool install git+https://github.com/INGCOM-UNRN/ripley-java"),
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
                "is_project": any((target_path / mf).is_file() for mf in ("build.gradle", "pom.xml")),
                "has_test_target": True,
            }

        fuentes = self.archivos_fuente(target_path)
        return {
            "language": "java",
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
