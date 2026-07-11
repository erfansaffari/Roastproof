"""
Phase 4 — Jinja2 → LaTeX → PDF (no LLM calls).

Compile with Tectonic; on failure save .tex and raise a clear error.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.schemas import ResumeContent

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "templates"
DEFAULT_TEMPLATE_NAME = "jakes_resume.tex.j2"

# Escapes applied after backslash is swapped for a placeholder (so `{`/`}`
# inside `\textbackslash{}` are not double-escaped).
_LATEX_SPECIALS = [
    ("{", r"\{"),
    ("}", r"\}"),
    ("$", r"\$"),
    ("&", r"\&"),
    ("#", r"\#"),
    ("%", r"\%"),
    ("_", r"\_"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]
_BS_PLACEHOLDER = "\0BS\0"


def latex_escape(text: str | None) -> str:
    """Escape LaTeX special characters in plain text."""
    if text is None:
        return ""
    s = str(text).replace("\\", _BS_PLACEHOLDER)
    for raw, repl in _LATEX_SPECIALS:
        s = s.replace(raw, repl)
    return s.replace(_BS_PLACEHOLDER, r"\textbackslash{}")


def _href_display(url: str) -> str:
    """Strip scheme for display; keep path for href."""
    u = (url or "").strip()
    u = re.sub(r"^https?://", "", u, flags=re.I)
    return u


def _ensure_url(url: str, default_prefix: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if re.match(r"^https?://", u, re.I):
        return u
    if u.startswith("mailto:"):
        return u
    return default_prefix + u.lstrip("/")


def render_tex(
    content: ResumeContent,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    template_name: str = DEFAULT_TEMPLATE_NAME,
) -> str:
    """Render ResumeContent to a LaTeX string via Jake's template."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["latex"] = latex_escape
    template = env.get_template(template_name)

    contact = dict(content.contact or {})
    name = contact.get("name", "")
    email = contact.get("email", "")
    phone = contact.get("phone", "")
    linkedin = contact.get("linkedin", "")
    github = contact.get("github", "")
    website = contact.get("website", "")

    links: list[dict[str, str]] = []
    if phone:
        links.append({"href": "", "text": latex_escape(phone)})
    if email:
        links.append(
            {
                "href": f"mailto:{email}",
                "text": latex_escape(email),
            }
        )
    if linkedin:
        links.append(
            {
                "href": _ensure_url(linkedin, "https://"),
                "text": latex_escape(_href_display(linkedin)),
            }
        )
    if github:
        links.append(
            {
                "href": _ensure_url(github, "https://"),
                "text": latex_escape(_href_display(github)),
            }
        )
    if website:
        links.append(
            {
                "href": _ensure_url(website, "https://"),
                "text": latex_escape(_href_display(website)),
            }
        )

    section_order = list(content.section_order or [])
    if not section_order:
        section_order = ["education", "experience", "projects", "skills"]

    return template.render(
        name=latex_escape(name),
        links=links,
        education=content.education,
        experience=content.experience,
        projects=content.projects,
        skills=content.skills,
        section_order=section_order,
    )


def compile_pdf(
    tex_source: str,
    out_dir: Path,
    basename: str = "resume",
) -> Path:
    """
    Write `basename.tex` and compile to `basename.pdf` via Tectonic.

    On failure, leaves the .tex in place and raises RuntimeError with stderr.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{basename}.tex"
    pdf_path = out_dir / f"{basename}.pdf"
    tex_path.write_text(tex_source, encoding="utf-8")

    tectonic = shutil.which("tectonic")
    if not tectonic:
        raise RuntimeError(
            "Tectonic not found on PATH. Install with `brew install tectonic` "
            f"(saved TeX at {tex_path})."
        )

    proc = subprocess.run(
        [tectonic, "--outdir", str(out_dir), str(tex_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not pdf_path.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"Tectonic failed (exit {proc.returncode}). "
            f"TeX saved at {tex_path}.\n{err}"
        )
    return pdf_path


def render_and_compile(
    content: ResumeContent,
    out_dir: Path,
    basename: str = "resume",
) -> tuple[Path, Path]:
    """Render + compile. Returns (tex_path, pdf_path)."""
    tex = render_tex(content)
    pdf = compile_pdf(tex, out_dir, basename=basename)
    return out_dir / f"{basename}.tex", pdf


def count_pdf_pages(pdf_path: Path) -> int:
    """Page count via PyMuPDF."""
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        return doc.page_count
