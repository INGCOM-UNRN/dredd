"""Rich report export: Markdown reports to styled self-contained HTML and pure-Python zero-dependency PDF."""

from dataclasses import dataclass, field
from datetime import datetime
import html as _html
from pathlib import Path
import re
from typing import List, Optional, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass
class Block:
    kind: str  # "h" | "p" | "li" | "code" | "table" | "nota" | "hr"
    level: int = 0  # para headings
    text: str = ""  # texto plano (lo que usa el PDF)
    items: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    html: str = ""  # el bloque en HTML, renderizado por markdown-it
    ordered: bool = False  # lista numerada
    tipo: str = ""  # en una nota: note, tip, important, warning, caution


# CommonMark con tablas (GFM) y sin HTML crudo: los informes llevan código y nombres de
# alumnos, y un `<script>` en una entrega tiene que verse como texto, no ejecutarse.
# markdown-it además rechaza los enlaces `javascript:` y `data:`.
_MD = MarkdownIt("commonmark", {"html": False}).enable("table")
_COMENTARIO = re.compile(r"^\s*<!--.*-->\s*$", re.DOTALL)
_NOTA = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", re.IGNORECASE)
TITULO_NOTA = {"note": "Nota", "tip": "Sugerencia", "important": "Importante",
               "warning": "Atención", "caution": "Cuidado"}


def _plano(inline: Token) -> str:
    """El texto de un token inline sin marcado (los saltos de línea pasan a espacios)."""
    partes = []
    for hijo in inline.children or []:
        if hijo.type in ("text", "code_inline"):
            partes.append(hijo.content)
        elif hijo.type in ("softbreak", "hardbreak"):
            partes.append(" ")
        elif hijo.type == "image":
            partes.append(hijo.content)
    return "".join(partes).strip()


def _grupos(tokens: List[Token]) -> List[List[Token]]:
    """Los tokens partidos por bloque de primer nivel (de cada apertura a su cierre)."""
    grupos: List[List[Token]] = []
    actual: List[Token] = []
    profundidad = 0
    for tok in tokens:
        actual.append(tok)
        profundidad += tok.nesting
        if profundidad == 0:
            grupos.append(actual)
            actual = []
    if actual:
        grupos.append(actual)
    return grupos


def _render(grupo: List[Token]) -> str:
    return _MD.renderer.render(grupo, _MD.options, {})


def _items(grupo: List[Token]) -> List[str]:
    """Texto plano de cada ítem de primer nivel de una lista (con sus sublistas)."""
    items: List[str] = []
    actual: List[str] = []
    nivel_item = grupo[0].level + 1
    for tok in grupo[1:-1]:
        if tok.type == "list_item_open" and tok.level == nivel_item:
            actual = []
        elif tok.type == "list_item_close" and tok.level == nivel_item:
            items.append(" ".join(t for t in actual if t))
        elif tok.type == "inline":
            actual.append(_plano(tok))
    return items


def _filas(grupo: List[Token]) -> List[List[str]]:
    filas: List[List[str]] = []
    for tok in grupo:
        if tok.type == "tr_open":
            filas.append([])
        elif tok.type == "inline" and filas:
            filas[-1].append(_plano(tok))
    return filas


def _nota(grupo: List[Token]) -> Optional[Block]:
    """`> [!WARNING]` (las admoniciones de GitHub que usan los informes de dredd) como nota."""
    primero = next((t for t in grupo if t.type == "inline"), None)
    if primero is None or not (m := _NOTA.match(primero.content)):
        return None
    tipo = m.group(1).lower()
    # Se saca la marca del texto y se vuelve a parsear el contenido de la cita.
    lineas = []
    for tok in grupo:
        if tok.type == "inline":
            lineas.append(tok.content)
    cuerpo = _NOTA.sub("", "\n\n".join(lineas), count=1).strip()
    interior = _MD.render(cuerpo) if cuerpo else ""
    texto = " ".join(_plano(t) for t in grupo if t.type == "inline")
    texto = _NOTA.sub("", texto, count=1).strip()
    html = (f'<div class="nota nota-{tipo}"><p class="nota-titulo">{TITULO_NOTA[tipo]}</p>'
            f"{interior}</div>\n")
    return Block(kind="nota", tipo=tipo, text=texto, html=html)


def parse_markdown(md_text: str) -> List[Block]:
    """Bloques de primer nivel del informe, con markdown-it-py (CommonMark + tablas)."""
    blocks: List[Block] = []
    for grupo in _grupos(_MD.parse(md_text.replace("\r\n", "\n"))):
        abre = grupo[0]
        html = _render(grupo)
        if abre.type == "heading_open":
            blocks.append(Block(kind="h", level=int(abre.tag[1]), text=_plano(grupo[1]), html=html))
        elif abre.type == "paragraph_open":
            texto = _plano(grupo[1])
            if _COMENTARIO.match(grupo[1].content):
                continue  # `<!-- dredd-section: … -->`: metadatos para fusionar secciones, no texto
            blocks.append(Block(kind="p", text=texto, html=html))
        elif abre.type in ("bullet_list_open", "ordered_list_open"):
            blocks.append(Block(kind="li", items=_items(grupo), html=html,
                                ordered=abre.type == "ordered_list_open"))
        elif abre.type in ("fence", "code_block"):
            blocks.append(Block(kind="code", text=abre.content.rstrip("\n"), html=html))
        elif abre.type == "table_open":
            blocks.append(Block(kind="table", rows=_filas(grupo), html=html))
        elif abre.type == "blockquote_open":
            nota = _nota(grupo)
            if nota is None:
                texto = " ".join(_plano(t) for t in grupo if t.type == "inline")
                nota = Block(kind="nota", text=texto, html=html)
            blocks.append(nota)
        elif abre.type == "hr":
            blocks.append(Block(kind="hr", html=html))
    return blocks


_CSS = """
:root { --bg:#ffffff; --panel:#f6f8fa; --border:#cbd5e1; --text:#0f172a;
        --dim:#64748b; --accent:#0369a1; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
       font-size:15px; line-height:1.6; }
.wrap { max-width:860px; margin:0 auto; padding:40px 28px 64px; }
h1 { font-size:1.9em; border-bottom:3px solid var(--accent); padding-bottom:10px; }
h2 { color:var(--accent); margin-top:2em; border-bottom:1px solid var(--border); padding-bottom:4px; }
h3 { margin-top:1.6em; }
code { background:var(--panel); border:1px solid var(--border); border-radius:4px;
       padding:1px 5px; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:0.92em; }
pre { background:var(--panel); border:1px solid var(--border); border-radius:8px;
      padding:12px 14px; overflow-x:auto; }
pre code { border:none; background:none; padding:0; }
table { border-collapse:collapse; width:100%; margin:14px 0; font-size:0.94em; }
th, td { border:1px solid var(--border); padding:6px 10px; text-align:left; }
th { background:var(--panel); }
ul { padding-left:22px; }
li { margin:3px 0; }
.meta { color:var(--dim); font-size:0.85em; margin-top:-6px; }
blockquote { margin:14px 0; padding:4px 14px; border-left:4px solid var(--border); color:var(--dim); }
.nota { margin:14px 0; padding:8px 14px; border-left:4px solid var(--accent); background:var(--panel);
        border-radius:0 6px 6px 0; }
.nota p { margin:6px 0; }
.nota-titulo { font-weight:600; }
.nota-tip { border-left-color:#15803d; } .nota-tip .nota-titulo { color:#15803d; }
.nota-important { border-left-color:#7c3aed; } .nota-important .nota-titulo { color:#7c3aed; }
.nota-warning { border-left-color:#b45309; } .nota-warning .nota-titulo { color:#b45309; }
.nota-caution { border-left-color:#b91c1c; } .nota-caution .nota-titulo { color:#b91c1c; }
@media print {
  body { font-size:11pt; }
  .wrap { max-width:none; padding:0; }
  h2 { break-after:avoid; }
  pre, table, figure, .nota { break-inside:avoid; }
}
"""


def render_html_report(blocks: Sequence[Block], title: str = "Informe de Evaluación — Dredd") -> str:
    body: List[str] = [b.html for b in blocks]

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body><div class="wrap">
<h1>{_html.escape(title)}</h1>
<p class="meta">Generado por Dredd · {stamp} · documento autocontenido</p>
{''.join(body)}
</div></body></html>"""


PAGE_W, PAGE_H = 595, 842
MARGIN = 54


class _PdfDoc:
    FONTS = {"F1": ("Helvetica", 0.50), "F2": ("Helvetica-Bold", 0.53), "F3": ("Courier", 0.60)}

    def __init__(self, title: str) -> None:
        self.title = title
        self.pages: List[List[str]] = [[]]
        self.y: float = PAGE_H - MARGIN

    def _newpage(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def _ensure(self, height: float) -> None:
        if self.y - height < MARGIN:
            self._newpage()

    @staticmethod
    def _escape(text: str) -> str:
        text = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def _line(self, text: str, font: str = "F1", size: float = 10.5, x: int = MARGIN,
              color: str = "") -> None:
        self._ensure(size * 1.45)
        esc = self._escape(text)
        ops = self.pages[-1]
        prefix = f"{color} rg\n" if color else ""
        ops.append(
            f"BT {prefix}/{font} {size:g} Tf {x} {self.y:.1f} Td ({esc}) Tj ET"
        )
        self.y -= size * 1.45

    def _wrap(self, text: str, size: float, factor: float, width: int) -> List[str]:
        max_chars = max(20, int(width / (size * factor)))
        words, lines, current = text.split(), [], ""
        for w in words:
            candidate = f"{current} {w}".strip()
            if len(candidate) <= max_chars or not current:
                while len(candidate) > max_chars:
                    lines.append(candidate[:max_chars])
                    candidate = candidate[max_chars:]
                current = candidate
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines or [""]

    W_TEXT = PAGE_W - 2 * MARGIN

    def heading(self, text: str, level: int) -> None:
        sizes = {1: 18.0, 2: 15.0, 3: 13.0}
        size = sizes.get(level, 13.0)
        self._ensure(size * 2)
        self.y -= size * 0.35
        color = "0.02 0.41 0.63" if level <= 2 else "0.1 0.1 0.12"
        for ln in self._wrap(text, size, self.FONTS["F2"][1], self.W_TEXT):
            self._line(ln, font="F2", size=size, color=color)

    def paragraph(self, text: str) -> None:
        for ln in self._wrap(text, 10.5, self.FONTS["F1"][1], self.W_TEXT):
            self._line(ln, font="F1", size=10.5)

    def bullets(self, items: List[str], ordered: bool = False) -> None:
        for n, it in enumerate(items, start=1):
            marca = f"{n}. " if ordered else "• "
            lines = self._wrap(it, 10.5, self.FONTS["F1"][1], self.W_TEXT - 16)
            for idx, ln in enumerate(lines):
                self._line((marca if idx == 0 else " " * len(marca)) + ln, font="F1", size=10.5, x=MARGIN + 8)

    def code(self, text: str) -> None:
        for ln in text.split("\n"):
            for piece in self._wrap(ln if ln else " ", 9.0, self.FONTS["F3"][1], self.W_TEXT - 12):
                self._line(piece, font="F3", size=9.0, x=MARGIN + 6, color="0.25 0.28 0.33")

    def table(self, rows: List[List[str]]) -> None:
        plain = [list(r) for r in rows]
        ncols = max(len(r) for r in plain)
        widths: List[int] = []
        for col in range(ncols):
            wmax = max((len(r[col]) if col < len(r) else 0) for r in plain)
            widths.append(min(max(wmax + 2, 6), 34))
        total = sum(widths)
        scale = min(1.0, (self.W_TEXT / 0.60) / total)
        widths = [max(4, int(w * scale)) for w in widths]

        for ridx, row in enumerate(plain):
            cells = [
                (row[c] if c < len(row) else "").ljust(widths[c])[: widths[c]]
                for c in range(ncols)
            ]
            self._line(" ".join(cells).rstrip(),
                       font="F2" if ridx == 0 else "F3",
                       size=9.0, color="" if ridx else "0.25 0.28 0.33")

    def build(self) -> bytes:
        objects: List[bytes] = []

        n_pages = len(self.pages)
        kids = " ".join(f"{6 + 2*i} 0 R" for i in range(n_pages))
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(
            f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode("latin-1")
        )
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")

        for idx, ops in enumerate(self.pages):
            content_lines = "\n".join(ops)
            stream = content_lines.encode("latin-1", errors="replace")
            page_num = 6 + 2 * idx
            content_num = page_num + 1
            objects.append(
                (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                 f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                 f"/Contents {content_num} 0 R >>").encode("latin-1")
            )
            objects.append(
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
            )

        info_num = len(objects) + 1
        objects.append(
            f"<< /Title ({self._escape(self.title)}) /Producer (Dredd) >>".encode("latin-1")
        )

        out = bytearray(b"%PDF-1.4\n%\xc7\xec\x8f\xa2\n")
        offsets = [0] * (len(objects) + 1)
        for i, body in enumerate(objects, start=1):
            offsets[i] = len(out)
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_pos = len(out)
        count = len(objects) + 1
        out += f"xref\n0 {count}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {count} /Root 1 0 R /Info {info_num} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
        return bytes(out)


def render_pdf_report(blocks: Sequence[Block], title: str = "Informe de Evaluación — Dredd") -> bytes:
    doc = _PdfDoc(title=title)
    for b in blocks:
        if b.kind == "h":
            doc.heading(b.text, b.level or 3)
        elif b.kind == "p":
            doc.paragraph(b.text)
        elif b.kind == "li":
            doc.bullets(b.items, ordered=b.ordered)
        elif b.kind == "nota":
            titulo = TITULO_NOTA.get(b.tipo, "")
            doc.paragraph(f"{titulo}: {b.text}" if titulo else b.text)
        elif b.kind == "code":
            doc.code(b.text)
        elif b.kind == "table":
            doc.table(b.rows)
    return doc.build()


def export_report(md_source: Path | str, fmt: str = "html", out_path: Path | str | None = None,
                  title: str = "Informe de Evaluación — Dredd") -> Path:
    """Convierte un informe Markdown a HTML enriquecido o PDF."""
    src = Path(md_source)
    if not src.exists():
        raise FileNotFoundError(f"Informe no encontrado: {src}")
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("html", "pdf"):
        raise ValueError(f"Formato no soportado: {fmt!r} (usá html o pdf)")

    blocks = parse_markdown(src.read_text(encoding="utf-8", errors="replace"))
    resolved_title = title if title != "Informe de Evaluación — Dredd" else src.stem.replace("_", " ").replace("-", " ").title()

    if out_path is None:
        out_path = src.with_suffix(f".{fmt}")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "html":
        out.write_text(render_html_report(blocks, title=resolved_title), encoding="utf-8")
    else:
        out.write_bytes(render_pdf_report(blocks, title=resolved_title))
    return out
