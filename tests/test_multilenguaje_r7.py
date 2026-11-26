"""Pruebas R7: independencia del lenguaje en dredd (C, Python, Java y plugins)."""

from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import zipfile

from typer.testing import CliRunner

from dredd.cli import app
from dredd.core.config import DreddConfig, load_dredd_config
from dredd.core.doctor import diagnosticar
from dredd.core.ecosystem import resolve_sibling_cli
from dredd.core.ingest import MoodleIngestor, is_allowed_file
from dredd.core.lenguajes import (
    AdaptadorLenguaje,
    detectar_lenguaje,
    listar_adaptadores,
    resolver_adaptador,
)
from dredd.core.mapping import InteractiveMapper
from dredd.core.plagiarism import PlagiarismDetector
from dredd.core.ripley_client import run_ripley_analysis

runner = CliRunner()


def _crear_zip_moodle(zip_path: Path, alumnos: dict[str, dict[str, str]]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for carpeta, archivos in alumnos.items():
            for nombre, contenido in archivos.items():
                zf.writestr(f"{carpeta}/{nombre}", contenido.encode("utf-8"))
    zip_path.write_bytes(buf.getvalue())


def _which_sin_c(original_which):
    bloqueados = {"gcc", "cc", "clang", "valgrind", "daedalus", "gaff", "spunkmeyer", "ripley", "nostromo"}

    def _mock(cmd: str):
        if cmd in bloqueados:
            return None
        return original_which(cmd)

    return _mock


def test_registro_deteccion_y_entry_points(tmp_path: Path, monkeypatch):
    registro = listar_adaptadores()
    assert {"c", "python", "java"}.issubset(registro)

    dir_py = tmp_path / "py"
    dir_py.mkdir()
    (dir_py / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert detectar_lenguaje(dir_py) == "python"
    assert resolver_adaptador("auto", dir_py).nombre == "python"

    dir_java = tmp_path / "java"
    dir_java.mkdir()
    (dir_java / "App.java").write_text("public class App {}\n", encoding="utf-8")
    assert detectar_lenguaje(dir_java) == "java"
    assert resolver_adaptador("auto", dir_java).nombre == "java"

    class AdaptadorGo(AdaptadorLenguaje):
        nombre = "go"
        extensiones_fuente = frozenset({".go"})
        extensiones_ingesta = frozenset({".go", ".mod"})
        archivos_proyecto = frozenset({"go.mod"})

        def compilar_o_verificar(self, ruta, checks=None):
            return {"success": True, "errors": [], "warnings": [], "compiler": "go"}

        def analizar_estatico(self, ruta, checks=None):
            return []

        def ejecutar_pruebas(self, ruta, guide=None, checks=None):
            return []

        def tokenizar_fallback(self, codigo):
            return [("go", 1)]

        def chequeos_doctor(self):
            return []

        def evaluar_entrega(self, target_path, **kwargs):
            return {"language": "go", "compilation": {"success": True}, "ast_findings": [], "tests": {}}

    class DummyEP:
        name = "go"

        def load(self):
            return AdaptadorGo

    import importlib.metadata as md

    monkeypatch.setattr(md, "entry_points", lambda *, group=None: [DummyEP()] if group == "dredd.languages" else [])
    reg_ep = listar_adaptadores()
    assert "go" in reg_ep
    assert resolver_adaptador("go").nombre == "go"


def test_ingesta_y_mapeo_python_y_java(tmp_path: Path):
    assert is_allowed_file("programa.py", language="python")
    assert is_allowed_file("App.java", language="java")
    assert is_allowed_file("build.gradle", language="java")

    ws_py = tmp_path / "ws_py"
    (ws_py / "entregas").mkdir(parents=True)
    (ws_py / "dredd.yaml").write_text(
        "version: '1.0'\nworkspace:\n  submissions_dir: entregas\n  language: python\n",
        encoding="utf-8",
    )
    zip_py = tmp_path / "UNRN-TP1-Python-100.zip"
    _crear_zip_moodle(
        zip_py,
        {
            "Ada Lovelace_101_assignsubmission_file_": {
                "ejercicio1_suma.py": "def sumar(a, b):\n    return a + b\n",
            }
        },
    )
    ingestor = MoodleIngestor(ws_py)
    info, resultados = ingestor.process_zip(zip_py)
    assert len(resultados) == 1
    assert resultados[0].version_created == 1
    destino = ws_py / "entregas" / info.activity_slug / resultados[0].student_slug / "r1" / "ejercicio1_suma.py"
    assert destino.is_file()

    mapper = InteractiveMapper(ws_py, info.activity_slug, submissions_dir=ws_py / "entregas" / info.activity_slug, language="python")
    cambios = mapper.run_interactive_session(["ejercicio1"], auto_apply=True)
    assert cambios == 1

    ws_java = tmp_path / "ws_java"
    (ws_java / "entregas").mkdir(parents=True)
    (ws_java / "dredd.yaml").write_text(
        "version: '1.0'\nworkspace:\n  submissions_dir: entregas\n  language: java\n",
        encoding="utf-8",
    )
    zip_java = tmp_path / "UNRN-TP1-Java-200.zip"
    _crear_zip_moodle(
        zip_java,
        {
            "Grace Hopper_202_assignsubmission_file_": {
                "Ejercicio1.java": "public class Ejercicio1 { public static void main(String[] a) {} }\n",
            }
        },
    )
    info_j, res_j = MoodleIngestor(ws_java).process_zip(zip_java)
    assert len(res_j) == 1
    assert (ws_java / "entregas" / info_j.activity_slug / res_j[0].student_slug / "r1" / "Ejercicio1.java").is_file()


def test_evaluacion_completa_python_y_java_sin_gcc_ni_tree_sitter_c(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shutil, "which", _which_sin_c(shutil.which))
    monkeypatch.setitem(sys.modules, "tree_sitter_c", None)
    monkeypatch.setenv("DREDD_PERMITIR_SIN_SANDBOX", "1")

    # 1. Cohorte Python vía CLI `dredd eval --lang python`
    ws_py = tmp_path / "ws_py"
    runner.invoke(app, ["init", str(ws_py), "--lang", "python"])
    cfg_py = load_dredd_config(ws_py)
    assert cfg_py is not None and cfg_py.workspace.language == "python"

    sub_py = ws_py / "entregas" / "tp_py" / "ana"
    sub_py.mkdir(parents=True)
    (sub_py / "main.py").write_text(
        "x = int(input())\n"
        "def insegura(txt):\n"
        "    return eval(txt)\n"
        "print(x * 2)\n",
        encoding="utf-8",
    )
    (sub_py / "caso_1.in").write_text("21\n", encoding="utf-8")
    (sub_py / "caso_1.out").write_text("42\n", encoding="utf-8")

    monkeypatch.chdir(ws_py)
    res_cli_py = runner.invoke(app, ["eval", "tp_py", "--all", "--lang", "python", "--json"])
    assert res_cli_py.exit_code == 0, res_cli_py.output
    datos_py = json.loads(res_cli_py.output)
    assert datos_py["results"][0]["compilation_ok"] is True
    assert datos_py["results"][0]["tests_passed"] == 1
    assert datos_py["results"][0]["ast_observations"] >= 1

    # Validar esquema Yutani directo desde run_ripley_analysis
    analisis_py = run_ripley_analysis(sub_py, workspace_dir=ws_py, language="python")
    assert analisis_py["language"] == "python"
    assert any(h.get("codigo") == "KPY001" for h in analisis_py["ast_findings"])

    # 2. Cohorte Java sin gcc ni tree-sitter-c
    ws_java = tmp_path / "ws_java"
    (ws_java / "entregas" / "tp_java" / "juan").mkdir(parents=True)
    sub_java = ws_java / "entregas" / "tp_java" / "juan"
    (sub_java / "Cuenta.java").write_text(
        "import java.util.Scanner;\n"
        "public class Cuenta {\n"
        "    public int saldo;\n"
        "    public static void main(String[] args) {\n"
        "        Scanner sc = new Scanner(System.in);\n"
        "        int n = sc.nextInt();\n"
        '        String s = "ok";\n'
        '        if (s == "ok") System.out.println(n + 1);\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (sub_java / "caso_1.in").write_text("10\n", encoding="utf-8")
    (sub_java / "caso_1.out").write_text("11\n", encoding="utf-8")

    analisis_java = run_ripley_analysis(sub_java, language="java")
    assert analisis_java["language"] == "java"
    assert analisis_java["compilation"]["success"] is True
    codigos_java = {h.get("codigo") for h in analisis_java["ast_findings"]}
    assert "MJA001" in codigos_java
    if shutil.which("javac") and shutil.which("java"):
        assert analisis_java["tests"]["passed"] == 1


def test_plagio_winnowing_y_doctor_multilenguaje(tmp_path: Path, monkeypatch):
    # Similitud Python con renombre de identificadores
    coh_py = tmp_path / "plagio_py"
    (coh_py / "a").mkdir(parents=True)
    (coh_py / "b").mkdir(parents=True)
    (coh_py / "c").mkdir(parents=True)

    (coh_py / "a" / "sol.py").write_text(
        "def calcular(lista):\n"
        "    acumulado = 0\n"
        "    for elemento in lista:\n"
        "        if elemento > 0:\n"
        "            acumulado += elemento * 2\n"
        "    return acumulado\n",
        encoding="utf-8",
    )
    (coh_py / "b" / "sol.py").write_text(
        "def calcular(datos):\n"
        "    total = 0\n"
        "    for item in datos:\n"
        "        if item > 0:\n"
        "            total += item * 2\n"
        "    return total\n",
        encoding="utf-8",
    )
    (coh_py / "c" / "sol.py").write_text("print('hola')\n", encoding="utf-8")

    det_py = PlagiarismDetector(threshold=0.8, engine="winnowing", language="python")
    pares = det_py.analyze_submissions(coh_py)
    assert len(pares) == 1
    assert {pares[0].student_a, pares[0].student_b} == {"a", "b"}
    assert pares[0].similarity_pct == 100.0

    # Doctor por lenguaje sin gcc disponible
    with monkeypatch.context() as mp:
        mp.setattr(shutil, "which", _which_sin_c(shutil.which))
        res_doc_py = runner.invoke(app, ["doctor", "--lang", "python", "--json"])
        assert res_doc_py.exit_code == 0, res_doc_py.output
        data_py = json.loads(res_doc_py.output)
        assert data_py["ok"] is True
        assert data_py["language"] == "python"
        nombres_py = {c["nombre"] for c in data_py["chequeos"]}
        assert "python3" in nombres_py and "gcc" not in nombres_py

        res_doc_c = runner.invoke(app, ["doctor", "--lang", "c", "--json"])
        assert res_doc_c.exit_code == 1
        assert "gcc" in {c["nombre"] for c in json.loads(res_doc_c.output)["chequeos"]}

    # Compatibilidad con dredd.yaml legacy sin clave language
    legacy_ws = tmp_path / "legacy_ws"
    legacy_ws.mkdir()
    (legacy_ws / "dredd.yaml").write_text("version: '1.0'\nworkspace:\n  name: P1\n", encoding="utf-8")
    cfg_leg = load_dredd_config(legacy_ws)
    assert cfg_leg is not None
    assert cfg_leg.workspace.language == "auto"
    assert cfg_leg.get_language() == "c"
    assert resolve_sibling_cli("daedalus") is not None
    assert any(c["nombre"] == "jplag" for c in diagnosticar(lang="all"))
