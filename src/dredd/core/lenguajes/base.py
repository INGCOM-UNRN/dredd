"""Contrato base `AdaptadorLenguaje` y registro de plugins de lenguaje en dredd."""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import metadata
import json
import logging
from pathlib import Path
import subprocess
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger("dredd.lenguajes")

ENTRY_POINT_GROUP = "dredd.languages"

DIRECTORIOS_IGNORADOS = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "target",
    ".gradle",
    "node_modules",
    "__macosx",
})

_ALIAS_LENGUAJE: Dict[str, str] = {
    "c11": "c",
    "c99": "c",
    "py": "python",
    "python3": "python",
    "py3": "python",
    "jvm": "java",
}

_MAPA_SEVERIDAD_DREDD = {
    "error": "ERROR",
    "advertencia": "WARNING",
    "warning": "WARNING",
    "estilo": "INFO",
    "info": "INFO",
}


def es_ruta_ignorada(ruta: Path) -> bool:
    """Devuelve True si la ruta atraviesa directorios internos, ocultos o generados."""
    return any(
        part.lower() in DIRECTORIOS_IGNORADOS or (part.startswith(".") and part not in (".", ".."))
        for part in ruta.parts
    )


def normalizar_lenguaje(lang: Optional[str]) -> str:
    """Normaliza el identificador de lenguaje ('auto', 'c', 'python', 'java', etc.)."""
    if not lang:
        return "auto"
    limpio = str(lang).strip().lower()
    if not limpio or limpio in ("auto", "none"):
        return "auto"
    return _ALIAS_LENGUAJE.get(limpio, limpio)


def normalizar_hallazgo_yutani(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Convierte un hallazgo de Yutani al formato dual compatible con Yutani y reporter.py."""
    codigo = str(item.get("codigo") or item.get("rule_code") or "DREDD000")
    sev_yutani = str(item.get("severidad") or "advertencia").lower()
    sev_dredd = str(item.get("severity") or _MAPA_SEVERIDAD_DREDD.get(sev_yutani, "WARNING")).upper()
    mensaje = str(item.get("mensaje") or item.get("message") or "").strip()
    archivo = item.get("archivo") if item.get("archivo") is not None else item.get("file")
    linea_raw = item.get("linea") if item.get("linea") is not None else item.get("line")
    linea = int(linea_raw) if isinstance(linea_raw, int) and linea_raw >= 1 else 1
    sugerencia = item.get("sugerencia") if item.get("sugerencia") is not None else item.get("suggestion")
    herramienta = str(item.get("herramienta") or "dredd")

    salida: Dict[str, Any] = {
        "herramienta": herramienta,
        "codigo": codigo,
        "categoria": str(item.get("categoria") or "estilo"),
        "severidad": sev_yutani if sev_yutani in ("error", "advertencia", "estilo", "info") else "advertencia",
        "mensaje": mensaje,
        "archivo": str(archivo) if archivo else None,
        "linea": linea,
        "columna": item.get("columna"),
        "sugerencia": str(sugerencia) if sugerencia else None,
        "explicacion": item.get("explicacion"),
        "rule_code": codigo,
        "rule_id": codigo,
        "rule_name": f"[{herramienta}] {codigo}",
        "severity": sev_dredd,
        "message": mensaje,
        "file": str(archivo) if archivo else "-",
        "line": linea,
    }
    if sugerencia:
        salida["suggestion"] = str(sugerencia)
    return salida


def ejecutar_satelite_json(comando: str, ruta: Path, timeout: float = 60.0) -> Optional[List[Dict[str, Any]]]:
    """Ejecuta `<comando> check <ruta> --json` si el binario existe en PATH o entorno hermano."""
    from dredd.core.ecosystem import resolve_sibling_cli

    binario = resolve_sibling_cli(comando)
    if not binario:
        return None
    try:
        proc = subprocess.run(
            [binario, "check", str(ruta), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("No se pudo ejecutar %s: %s", comando, exc)
        return None
    salida = (proc.stdout or "").strip()
    if not salida:
        return None
    try:
        datos = json.loads(salida)
    except json.JSONDecodeError:
        logger.debug("Salida JSON inválida de %s: %s", comando, salida[:200])
        return None
    brutos = datos.get("hallazgos", []) if isinstance(datos, dict) else (datos if isinstance(datos, list) else [])
    return [normalizar_hallazgo_yutani(h) for h in brutos if isinstance(h, Mapping)]


def recolectar_pares_io_locales(ruta: Path) -> List[Tuple[str, Path, Path]]:
    """Busca pares `.in`/`.out` locales en la entrega o bajo `tests/`/`casos/`."""
    dirs = [ruta]
    for sub in ("tests", "test", "casos"):
        cand = ruta / sub
        if cand.is_dir():
            dirs.append(cand)
    pares: List[Tuple[str, Path, Path]] = []
    vistos: set[Path] = set()
    for d in dirs:
        for in_f in sorted(d.glob("*.in")):
            out_f = in_f.with_suffix(".out")
            if out_f.is_file() and in_f.resolve() not in vistos:
                vistos.add(in_f.resolve())
                pares.append((in_f.stem, in_f, out_f))
    return pares


class AdaptadorLenguaje(ABC):
    """Interfaz estándar para adaptar evaluación, chequeos e ingesta por lenguaje."""

    nombre: str = ""
    extensiones_fuente: frozenset[str] = frozenset()
    extensiones_ingesta: frozenset[str] = frozenset()
    archivos_proyecto: frozenset[str] = frozenset()

    def archivos_fuente(self, ruta: Path) -> List[Path]:
        """Devuelve archivos fuente de este lenguaje ordenados bajo `ruta`."""
        if not ruta.exists():
            return []
        if ruta.is_file():
            return [ruta] if ruta.suffix.lower() in self.extensiones_fuente else []
        archivos: List[Path] = []
        for p in sorted(ruta.rglob("*")):
            if p.is_file() and p.suffix.lower() in self.extensiones_fuente:
                try:
                    rel = p.relative_to(ruta)
                except ValueError:
                    rel = p
                if not es_ruta_ignorada(rel):
                    archivos.append(p)
        return archivos

    def detectar(self, ruta: Path) -> int:
        """Puntúa la afinidad de `ruta` con este lenguaje contando fuentes y archivos de proyecto."""
        if not ruta.exists():
            return 0
        if ruta.is_file():
            nombre_low = ruta.name.lower()
            if ruta.suffix.lower() in self.extensiones_fuente or nombre_low in self.archivos_proyecto:
                return 1
            return 0
        score = len(self.archivos_fuente(ruta)) * 2
        for item in ruta.iterdir():
            if item.is_file() and item.name.lower() in self.archivos_proyecto:
                score += 1
        return score

    @abstractmethod
    def compilar_o_verificar(self, ruta: Path, checks: Any = None) -> Dict[str, Any]:
        """Compila o valida la sintaxis de las fuentes devolviendo estado estándar."""

    @abstractmethod
    def analizar_estatico(self, ruta: Path, checks: Any = None) -> List[Dict[str, Any]]:
        """Ejecuta analizadores estáticos y devuelve hallazgos normalizados."""

    @abstractmethod
    def ejecutar_pruebas(
        self,
        ruta: Path,
        guide: Any = None,
        checks: Any = None,
    ) -> List[Dict[str, Any]]:
        """Ejecuta las pruebas unitarias o pares entrada/salida sobre la entrega."""

    def auditar_estructura(self, ruta: Path, checks: Any = None) -> List[Dict[str, Any]]:
        """Auditoría estructural o de convenciones propia del lenguaje (opcional)."""
        return []

    @abstractmethod
    def tokenizar_fallback(self, codigo: str) -> List[Tuple[str, int]]:
        """Tokeniza código fuente para el motor fallback Winnowing."""

    @abstractmethod
    def chequeos_doctor(self) -> List[Tuple[str, str, bool, str]]:
        """Devuelve lista de `(comando, descripcion, requerido, sugerencia_instalacion)`."""

    @abstractmethod
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
        """Ejecuta el flujo completo de evaluación y devuelve el informe de análisis."""


def _valgrind_inactivo() -> Dict[str, Any]:
    return {
        "executed": False,
        "clean": True,
        "definitely_lost_bytes": 0,
        "indirectly_lost_bytes": 0,
        "possibly_lost_bytes": 0,
        "still_reachable_bytes": 0,
        "total_errors": 0,
        "errors": [],
    }


def _cargar_entry_points() -> Dict[str, AdaptadorLenguaje]:
    adaptadores: Dict[str, AdaptadorLenguaje] = {}
    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        return adaptadores
    for ep in eps:
        try:
            obj = ep.load()
            instancia = obj() if isinstance(obj, type) else obj
            if isinstance(instancia, AdaptadorLenguaje):
                clave = normalizar_lenguaje(instancia.nombre or ep.name)
                adaptadores[clave] = instancia
        except Exception as exc:
            logger.debug("No se pudo cargar entry point %s de %s: %s", ep.name, ENTRY_POINT_GROUP, exc)
    return adaptadores


def listar_adaptadores() -> Dict[str, AdaptadorLenguaje]:
    """Devuelve mapa `{nombre: instancia}` con los adaptadores integrados y plugins instalados."""
    from dredd.core.lenguajes.c import AdaptadorC
    from dredd.core.lenguajes.java import AdaptadorJava
    from dredd.core.lenguajes.python import AdaptadorPython

    registro: Dict[str, AdaptadorLenguaje] = {
        "c": AdaptadorC(),
        "python": AdaptadorPython(),
        "java": AdaptadorJava(),
    }
    for nombre, adaptador in _cargar_entry_points().items():
        registro[nombre] = adaptador
    return registro


def extensiones_fuente_globales(adaptadores: Optional[Iterable[AdaptadorLenguaje]] = None) -> set[str]:
    """Unión de todas las extensiones fuente de los adaptadores registrados."""
    fuente: set[str] = set()
    for ad in (adaptadores if adaptadores is not None else listar_adaptadores().values()):
        fuente.update(ad.extensiones_fuente)
    return fuente


def extensiones_ingesta_globales(adaptadores: Optional[Iterable[AdaptadorLenguaje]] = None) -> set[str]:
    """Unión de las extensiones aceptadas en ingesta entre todos los adaptadores."""
    exts: set[str] = {".md", ".txt", ".in", ".out"}
    for ad in (adaptadores if adaptadores is not None else listar_adaptadores().values()):
        exts.update(ad.extensiones_ingesta)
    return exts


def archivos_proyecto_globales(adaptadores: Optional[Iterable[AdaptadorLenguaje]] = None) -> set[str]:
    """Unión de nombres de archivo de proyecto reconocibles entre adaptadores."""
    archivos: set[str] = set()
    for ad in (adaptadores if adaptadores is not None else listar_adaptadores().values()):
        archivos.update(ad.archivos_proyecto)
    return archivos


def detectar_lenguaje(ruta: Optional[Path]) -> str:
    """Infiere el lenguaje dominante en `ruta`, cayendo a 'c' si no hay archivos claros."""
    if ruta is None or not ruta.exists():
        return "c"
    registro = listar_adaptadores()
    puntajes = {nombre: adaptador.detectar(ruta) for nombre, adaptador in registro.items()}
    mejor_nombre = "c"
    mejor_puntaje = puntajes.get("c", 0)
    # Orden determinista: c gana empates por compatibilidad histórica, luego el resto.
    for nombre in ("c", "python", "java", *sorted(set(puntajes) - {"c", "python", "java"})):
        puntaje = puntajes.get(nombre, 0)
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_nombre = nombre
    return mejor_nombre


def resolver_adaptador(lang: Optional[str] = "auto", ruta: Optional[Path] = None) -> AdaptadorLenguaje:
    """Resuelve un `AdaptadorLenguaje` por nombre explícito o autodetección sobre `ruta`."""
    registro = listar_adaptadores()
    normalizado = normalizar_lenguaje(lang)
    elegido = detectar_lenguaje(ruta) if normalizado == "auto" else normalizado
    if elegido not in registro:
        disponibles = ", ".join(sorted(registro))
        raise ValueError(f"Lenguaje no soportado {lang!r}. Disponibles: {disponibles}.")
    return registro[elegido]
