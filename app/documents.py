"""Documents uploaded by a client: extract text, split into chunks, store.

Supported: .md, .txt, .pdf (with selectable text) and .docx. A document that
has "## headings" (markdown, or Word heading styles) is split by heading;
anything else is split into paragraphs of roughly 150-250 words. Uploading a
file with the same name again replaces the old version.
"""
import io
import re

from app.db import connect, init_schema
from app.ingest import INSERT_CHUNK, chunk_params
from app.lang import detect_language, strip_accents

MAX_BYTES = 5 * 1024 * 1024
MAX_DOCUMENTS = 50
MAX_CHUNKS_PER_DOCUMENT = 400
EXTENSIONS = (".md", ".txt", ".pdf", ".docx")
TARGET_WORDS = 180
MAX_WORDS = 260


class DocumentError(ValueError):
    """The file cannot be used; the message is safe to show to the client."""


def make_slug(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename.replace("\\", "/").split("/")[-1])
    slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(stem).lower()).strip("-")[:60]
    if not slug:
        raise DocumentError("The file name has no usable letters or digits.")
    return slug


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if not name.endswith(EXTENSIONS):
        raise DocumentError("Unsupported file type. Use .md, .txt, .pdf or .docx.")
    if name.endswith((".md", ".txt")):
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return data.decode(enc)
            except UnicodeError:
                continue
        raise DocumentError("Could not read the text encoding of the file.")
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentError("The PDF is password-protected.")
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("Could not read the PDF (it may be damaged).")
        return "\n\n".join(p for p in pages if p)
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise DocumentError("Could not read the Word file (it may be damaged).")
    lines = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
        elif para.style is not None and para.style.name.lower().startswith(("heading", "título", "titulo")):
            lines.append(f"\n## {text}\n")
        else:
            lines.append(text)
    return "\n".join(lines)


def _pack(text: str) -> list[str]:
    """Split text into pieces of about TARGET_WORDS words, on paragraph or sentence edges."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        if len(para.split()) <= MAX_WORDS:
            units.append(para)
        else:  # a very long paragraph: cut it on sentence ends
            units += [s for s in re.split(r"(?<=[.!?])\s+", para) if s]
    pieces, current = [], []
    for unit in units:
        words = unit.split()
        while len(words) > MAX_WORDS:  # one endless sentence: hard cut
            pieces.append(" ".join(words[:MAX_WORDS]))
            words = words[MAX_WORDS:]
        unit = " ".join(words)
        if current and len(" ".join(current).split()) + len(words) > MAX_WORDS:
            pieces.append(" ".join(current))
            current = []
        current.append(unit)
        if len(" ".join(current).split()) >= TARGET_WORDS:
            pieces.append(" ".join(current))
            current = []
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_document(text: str, filename: str) -> tuple[str, list[tuple[str, str]], str]:
    """Return (title, [(heading, content)], lang)."""
    text = text.replace("\r\n", "\n").replace("\x00", "")
    meta = re.match(r"\s*---\s*\ntitle:\s*(.+?)\s*\n---\s*\n", text)
    title = meta.group(1).strip() if meta else None
    if meta:
        text = text[meta.end():]
    lang = detect_language(text[:20000])
    sections: list[tuple[str, str]] = []
    if re.search(r"^## ", text, flags=re.MULTILINE):
        head = re.split(r"^## ", text, flags=re.MULTILINE)
        intro = head[0].strip()
        if title is None:
            m = re.match(r"#\s+(.+)", intro)
            if m:
                title = m.group(1).strip()
                intro = intro[m.end():].strip()
        if intro:
            sections.append(("Introduction" if lang == "en" else "Introducción", intro))
        for block in head[1:]:
            heading, _, content = block.strip().partition("\n")
            content = content.strip()
            if content:
                sections.append((heading.strip(), content))
    else:
        label = "Part" if lang == "en" else "Parte"
        sections = [(f"{label} {i}", piece) for i, piece in enumerate(_pack(text), 1)]
    if title is None:
        title = re.sub(r"\.[A-Za-z0-9]+$", "", filename).replace("_", " ").replace("-", " ").strip() or filename
    return title[:200], sections, lang


def save_document(tenant_id: int, filename: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise DocumentError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise DocumentError("The file is empty.")
    slug = make_slug(filename)
    title, sections, lang = split_document(extract_text(filename, data), filename)
    if not sections:
        raise DocumentError(
            "No text found in the file. Scanned PDFs (images) are not supported yet."
        )
    if len(sections) > MAX_CHUNKS_PER_DOCUMENT:
        raise DocumentError("The document is too long. Split it into smaller files.")
    init_schema()
    with connect() as conn:
        existing = {
            r["slug"]
            for r in conn.execute(
                "SELECT DISTINCT slug FROM chunks WHERE tenant_id = %s", (tenant_id,)
            ).fetchall()
        }
        if slug not in existing and len(existing) >= MAX_DOCUMENTS:
            raise DocumentError(f"Limit of {MAX_DOCUMENTS} documents reached. Delete one first.")
        conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND slug = %s", (tenant_id, slug))
        with conn.cursor() as cur:
            cur.executemany(
                INSERT_CHUNK,
                [
                    chunk_params(
                        {
                            "slug": slug, "lang": lang, "title": title,
                            "heading": heading, "content": content, "tenant_id": tenant_id,
                        }
                    )
                    for heading, content in sections
                ],
            )
    return {"slug": slug, "title": title, "lang": lang, "sections": len(sections),
            "replaced": slug in existing}


def list_documents(tenant_id: int) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT slug, min(title) AS title, min(lang) AS lang, count(*) AS sections "
            "FROM chunks WHERE tenant_id = %s GROUP BY slug ORDER BY slug",
            (tenant_id,),
        ).fetchall()


def delete_document(tenant_id: int, slug: str) -> bool:
    with connect() as conn:
        return conn.execute(
            "DELETE FROM chunks WHERE tenant_id = %s AND slug = %s", (tenant_id, slug)
        ).rowcount > 0
