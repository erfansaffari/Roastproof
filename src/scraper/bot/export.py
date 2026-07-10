import json
import logging
import shutil
from pathlib import Path

from bot import db
from bot.config import DATA_DIR, EXPORT_DIR

logger = logging.getLogger(__name__)


def _copy_resume_files(attachment_paths: list[str], export_folder: Path) -> list[str]:
    copied_paths: list[str] = []

    for source_path in attachment_paths:
        source = Path(source_path)
        if not source.exists():
            logger.warning("Missing resume file during export: %s", source)
            continue

        destination = export_folder / source.name
        shutil.copy2(source, destination)
        copied_paths.append(str(destination.relative_to(DATA_DIR.parent)))

        source.unlink()
        resume_dir = source.parent
        if resume_dir.exists() and not any(resume_dir.iterdir()):
            resume_dir.rmdir()

    return copied_paths


def _write_post_message_file(resume: dict, export_folder: Path) -> None:
    content = resume.get("message_content") or ""
    author = resume.get("author_name") or "unknown"
    posted_at = resume.get("posted_at") or ""

    if not content.strip():
        return

    text = f"[{posted_at}] {author} (resume post):\n{content}"
    (export_folder / "post.txt").write_text(text, encoding="utf-8")


def _write_critiques_file(critiques: list[dict], export_folder: Path) -> None:
    lines: list[str] = []
    for critique in critiques:
        lines.append(
            f"[{critique['posted_at']}] {critique['author_name']}: {critique['content']}"
        )

    (export_folder / "critiques.txt").write_text("\n\n".join(lines), encoding="utf-8")


def run_export() -> tuple[int, int, Path]:
    db.init_db()

    resumes = db.get_all_resumes()
    dataset: list[dict] = []
    total_critiques = 0

    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    for resume in resumes:
        message_id = resume["message_id"]
        export_folder = EXPORT_DIR / message_id
        export_folder.mkdir(parents=True, exist_ok=True)

        attachment_paths = json.loads(resume["attachment_paths"] or "[]")
        copied_files = _copy_resume_files(attachment_paths, export_folder)

        critiques = db.get_critiques_for_resume(message_id)
        total_critiques += len(critiques)
        _write_post_message_file(resume, export_folder)
        _write_critiques_file(critiques, export_folder)

        dataset.append(
            {
                "resume_message_id": message_id,
                "author": resume["author_name"],
                "posted_at": resume["posted_at"],
                "post_message": resume.get("message_content") or "",
                "resume_files": copied_files,
                "critiques": [
                    {
                        "author": critique["author_name"],
                        "content": critique["content"],
                        "timestamp": critique["posted_at"],
                    }
                    for critique in critiques
                ],
            }
        )

    dataset_path = EXPORT_DIR / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, indent=2), encoding="utf-8")

    logger.info(
        "Export finished: %s resumes, %s critiques",
        len(dataset),
        total_critiques,
    )
    return len(dataset), total_critiques, dataset_path
