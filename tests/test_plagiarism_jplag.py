"""Pruebas unitarias de integración JPlag (ola R5) en dredd."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from dredd.core.plagiarism import PlagiarismDetector, run_jplag_cohort


def test_run_jplag_cohort_convierte_resultado_de_yutani(tmp_path: Path, monkeypatch):
    archivo_zip = tmp_path / "lote.jplag"
    with zipfile.ZipFile(archivo_zip, "w") as zf:
        zf.writestr("overview.json", json.dumps({
            "topComparisons": [{
                "firstSubmission": "ana",
                "secondSubmission": "juan",
                "similarities": {"AVG": 0.94},
                "firstSubmissionTokens": 80,
                "secondSubmissionTokens": 78,
            }]
        }))
        zf.writestr("comparisons/ana-juan.json", json.dumps({
            "firstSubmissionId": "ana",
            "secondSubmissionId": "juan",
            "similarities": {"AVG": 0.94},
            "matches": [{"length": 50}],
        }))

    import yutani.jplag as jplag_mod
    monkeypatch.setattr(jplag_mod, "jplag_disponible", lambda: (True, "mock"))
    monkeypatch.setattr(
        jplag_mod,
        "ejecutar_jplag",
        lambda d, lenguaje="c", codigo_base=None, umbral=0.7: jplag_mod.parsear_zip_jplag(archivo_zip, umbral=umbral),
    )

    matches = run_jplag_cohort(tmp_path, language="python", threshold=0.7)
    assert len(matches) == 1
    assert matches[0].student_a == "ana"
    assert matches[0].student_b == "juan"
    assert matches[0].similarity_pct == 94.0
    assert matches[0].shared_fingerprints == 50

    sub = tmp_path / "entregas"
    sub.mkdir()
    det = PlagiarismDetector(threshold=0.7)
    auto_matches = det.analyze_submissions(sub, engine="auto", language="python")
    assert len(auto_matches) == 1
    assert auto_matches[0].similarity_pct == 94.0
