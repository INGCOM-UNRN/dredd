"""Unit tests for Dredd Moodle exporter, cohort dashboard, and report export (HTML/PDF)."""

from pathlib import Path

from dredd.core.db import DatabaseManager, StudentRecord
from dredd.core.exporter import MoodleExporter
from dredd.core.report_export import export_report, render_html_report, render_pdf_report, parse_markdown


def test_moodle_exporter(tmp_path: Path):
    act_slug = "tp1_100"
    act_dir = tmp_path / act_slug
    student_dir = act_dir / "perez-juan_123"
    student_dir.mkdir(parents=True, exist_ok=True)

    db = DatabaseManager(student_dir / ".metadata.db")
    db.upsert_student(StudentRecord("123", "Perez Juan", "perez-juan_123", "999"))
    rev_id = db.add_revision(
        student_slug="perez-juan_123",
        version_num=1,
        sources_hash="hash1",
        folder_path=str(student_dir / "r1"),
        sources=[],
        ignored=[],
    )
    db.save_evaluation(
        revision_id=rev_id,
        compilation_status="OK",
        preliminary_grade=9.5,
        grade_compilation=10.0,
        grade_style=9.0,
        grade_linter=10.0,
        grade_tests=9.5,
        unified_diff="",
        compilation_logs="",
        test_results=[],
    )
    (student_dir / "perez-juan_123_tp1_100.md").write_text("# Informe Perez\n\nTodo OK.", encoding="utf-8")

    exporter = MoodleExporter(workspace_dir=tmp_path)
    csv_file = exporter.export_grades_csv(act_slug)
    zip_file = exporter.export_feedback_zip(act_slug)
    dash_file = exporter.generate_dashboard(act_slug)

    assert csv_file.exists()
    assert zip_file.exists()
    assert dash_file.exists()

    csv_content = csv_file.read_text(encoding="utf-8-sig")
    assert "Perez Juan" in csv_content
    assert "9.50" in csv_content

    dash_content = dash_file.read_text(encoding="utf-8")
    assert "100.0%" in dash_content
    assert "Perez Juan" in dash_content


def test_report_export_html_and_pdf(tmp_path: Path):
    md_file = tmp_path / "informe.md"
    md_file.write_text(
        """# Informe de Evaluación
## Resumen
- **Compilación**: OK
- **Estilo**: 10/10

```c
int main() { return 0; }
```

| Métrica | Valor |
| --- | --- |
| Nota | 10 |
""",
        encoding="utf-8",
    )

    html_out = export_report(md_file, fmt="html")
    assert html_out.exists()
    html_text = html_out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_text
    assert "Informe de Evaluación" in html_text

    pdf_out = export_report(md_file, fmt="pdf")
    assert pdf_out.exists()
    pdf_bytes = pdf_out.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-1.4")


# ── N-DREDD-01: el parser no debe colgarse con líneas que solo empiezan con `#` o `|` ──

import multiprocessing
import resource

import pytest


def _parsea_en_hijo(md: str) -> None:
    # Un bucle infinito que acumula bloques agotaría la memoria del host:
    # el hijo la tiene acotada y el padre lo mata si no termina a tiempo.
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    parse_markdown(md)


def _termina(md: str, segundos: float = 5.0) -> bool:
    hijo = multiprocessing.get_context("fork").Process(target=_parsea_en_hijo, args=(md,))
    hijo.start()
    hijo.join(segundos)
    if hijo.is_alive():
        hijo.kill()
        hijo.join()
        return False
    return hijo.exitcode == 0


@pytest.mark.parametrize(
    "md",
    [
        "# Informe\n\n#### Detalle\n\ntexto\n",
        "#include <stdio.h>\nint main(void) { return 0; }\n",
        "texto\n#hashtag sin espacio\nmás texto\n",
        "| tabla sin cierre\n| otra fila\n",
        "*énfasis* al comienzo\n",
        "-sin espacio tras el guion\n",
    ],
)
def test_parse_markdown_siempre_termina(md: str):
    assert _termina(md), "parse_markdown no terminó (bucle infinito o sin memoria)"
    assert parse_markdown(md)


def test_parse_markdown_encabezados_h4_a_h6():
    md = "#### Cuatro\n\n##### Cinco\n\n###### Seis\n"
    assert _termina(md)
    bloques = parse_markdown(md)
    assert [(b.kind, b.level, b.text) for b in bloques] == [("h", 4, "Cuatro"), ("h", 5, "Cinco"), ("h", 6, "Seis")]
    assert "<h4>Cuatro</h4>" in render_html_report(bloques)


def test_parse_markdown_include_es_texto_del_parrafo():
    md = "Encabezado del programa:\n#include <stdio.h>\n"
    assert _termina(md)
    bloques = parse_markdown(md)
    assert len(bloques) == 1
    assert bloques[0].kind == "p"
    assert "#include <stdio.h>" in bloques[0].text


def test_export_report_de_un_fuente_c_no_se_cuelga(tmp_path: Path):
    fuente = tmp_path / "lista.c"
    fuente.write_text("#include <stdio.h>\n#include \"lista.h\"\n\nint main(void)\n{\n    return 0;\n}\n")
    assert _termina(fuente.read_text())
    salida = export_report(fuente, fmt="html", out_path=tmp_path / "lista.html")
    assert Path(salida).exists()


def test_export_de_github_va_al_directorio_de_entregas(tmp_path: Path):
    """F-03: sin .metadata.db (GitHub) las salidas van a entregas/<act>/ y salen de auditoria.json."""
    from dredd.core import auditoria

    (tmp_path / "dredd.yaml").write_text(
        "version: '1.0'\nworkspace:\n  submissions_dir: entregas\nmapeos:\n- entrega: TP0\n  source: github\n",
        encoding="utf-8",
    )
    alumno = tmp_path / "entregas" / "TP0" / "TP0-facubnv"
    (alumno / "repo").mkdir(parents=True)
    auditoria.registrar(alumno / "i_b763cc8", alumno="TP0-facubnv", actividad="TP0", revision="b763cc8",
                        estado="completa",
                        resultado={"compilacion": True, "tests": {"aprobados": 3, "total": 4, "casos": []}})
    (alumno / "TP0-facubnv_b763cc8.md").write_text("# Informe\n", encoding="utf-8")
    sin_evaluar = tmp_path / "entregas" / "TP0" / "TP0-otro"
    (sin_evaluar / "repo").mkdir(parents=True)

    exporter = MoodleExporter(workspace_dir=tmp_path)
    csv_file = exporter.export_grades_csv("TP0")
    zip_file = exporter.export_feedback_zip("TP0")
    dash_file = exporter.generate_dashboard("TP0")

    destino = tmp_path / "entregas" / "TP0"
    assert {csv_file.parent, zip_file.parent, dash_file.parent} == {destino}
    assert not (tmp_path / "TP0").exists()

    filas = csv_file.read_text(encoding="utf-8-sig").splitlines()
    assert "Revisión b763cc8 | Compilación: OK | Estilo: —/10 | Tests: 7.5/10" in filas[1]
    assert ",," in filas[1]  # sin nota conocida la calificación queda vacía, no en 0
    assert "SIN EVALUAR" in filas[2]
    dash = dash_file.read_text(encoding="utf-8")
    assert "50.0% (1/2)" in dash
    assert "| b763cc8 | ✓ OK |" in dash


def test_export_de_actividad_inexistente_no_crea_carpetas(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        MoodleExporter(workspace_dir=tmp_path).generate_dashboard("TP9")
    assert list(tmp_path.iterdir()) == []


def test_export_de_revision_sin_auditoria(tmp_path: Path):
    """Una revisión evaluada antes del cursor (sin auditoria.json) no se confunde con una sin evaluar."""
    alumno = tmp_path / "TP0" / "TP0-ana"
    (alumno / "i_253ca9a").mkdir(parents=True)
    (alumno / "TP0-ana_253ca9a.md").write_text("# Informe\n", encoding="utf-8")
    datos = MoodleExporter(workspace_dir=tmp_path)._get_all_activity_data("TP0")
    assert (datos[0]["revision"], datos[0]["compilation_status"]) == ("253ca9a", "SIN REGISTRO")
    assert datos[0]["report_file"].name == "TP0-ana_253ca9a.md"


# ── markdown-it-py (decisión del 19/11/26): CommonMark + tablas, sin HTML crudo ──

INFORME = """<!-- dredd-section: callahan, tool=callahan, version=1.0.0, status=fail -->
## Verificación formal (Callahan)

- **Archivo analizado:** `abs.c`
- Ver [la regla](https://apunte.example/reglas/0x2003h) y [esto](javascript:alert(1))

> [!CAUTION]
> **Contratos mal escritos:** Frama-C no pudo leer las anotaciones.

1. Primero
2. Segundo

| Función | Estado |
| :--- | :---: |
| `a\\|b()` | **No probado** |

Código del alumno: <script>alert("x")</script>

---
"""


def test_el_marcado_inline_y_los_enlaces_se_conservan():
    html = render_html_report(parse_markdown(INFORME))
    assert "<strong>Archivo analizado:</strong> <code>abs.c</code>" in html  # antes se perdía en las listas
    assert '<a href="https://apunte.example/reglas/0x2003h">la regla</a>' in html
    assert 'href="javascript:' not in html


def test_sin_html_crudo_ni_comentarios_visibles():
    bloques = parse_markdown(INFORME)
    html = render_html_report(bloques)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "dredd-section" not in html  # el comentario de fusión de secciones no es texto
    assert all("dredd-section" not in b.text for b in bloques)


def test_admonicion_lista_numerada_tabla_y_separador():
    bloques = parse_markdown(INFORME)
    tipos = [b.kind for b in bloques]
    assert tipos == ["h", "li", "nota", "li", "table", "p", "hr"]
    nota = bloques[2]
    assert nota.tipo == "caution" and nota.text.startswith("Contratos mal escritos:")
    assert '<div class="nota nota-caution"><p class="nota-titulo">Cuidado</p>' in nota.html
    assert "[!CAUTION]" not in nota.html
    assert bloques[3].ordered and bloques[3].items == ["Primero", "Segundo"]
    assert bloques[4].rows == [["Función", "Estado"], ["a|b()", "No probado"]]  # `\|` no parte la celda
    # El enlace `javascript:` ni siquiera es un enlace: queda como texto.
    assert bloques[1].items[1] == "Ver la regla y [esto](javascript:alert(1))"


def test_el_pdf_numera_y_muestra_la_nota(tmp_path: Path):
    md = tmp_path / "informe.md"
    md.write_text(INFORME, encoding="utf-8")
    pdf = export_report(md, fmt="pdf").read_bytes()
    assert b"1. Primero" in pdf and b"Cuidado: Contratos mal escritos" in pdf
    assert b"dredd-section" not in pdf
