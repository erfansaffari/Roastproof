import pytest
from src.schemas import ThreadRecord, Critique, QualityFlags, TargetRole
from src.ingestion.assemble import is_junk, resolve_resume_path, assemble_thread
from src.ingestion.structure import redact_pii


# --- Test resume path resolution (assemble.py) ---

def test_resolve_resume_path_uses_original_filename(tmp_path):
    """Scraper keeps Discord filenames — assemble must not require resume.pdf."""
    thread_id = "111"
    thread_dir = tmp_path / thread_id
    thread_dir.mkdir()
    resume = thread_dir / "SWE_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")

    entry = {
        "resume_message_id": thread_id,
        "resume_files": [f"data/export/{thread_id}/SWE_Resume.pdf"],
        "post_message": "pls review",
        "critiques": [],
    }
    assert resolve_resume_path(entry, tmp_path) == resume


def test_resolve_resume_path_prefers_pdf_over_image(tmp_path):
    thread_id = "222"
    thread_dir = tmp_path / thread_id
    thread_dir.mkdir()
    (thread_dir / "shot.png").write_bytes(b"png")
    pdf = thread_dir / "Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    entry = {
        "resume_message_id": thread_id,
        "resume_files": [
            f"data/export/{thread_id}/shot.png",
            f"data/export/{thread_id}/Resume.pdf",
        ],
    }
    assert resolve_resume_path(entry, tmp_path) == pdf


def test_assemble_thread_accepts_png(tmp_path):
    thread_id = "333"
    thread_dir = tmp_path / thread_id
    thread_dir.mkdir()
    png = thread_dir / "resume-1.png"
    png.write_bytes(b"png")

    entry = {
        "resume_message_id": thread_id,
        "resume_files": [f"data/export/{thread_id}/resume-1.png"],
        "post_message": "review please",
        "critiques": [{"author": "a", "content": "add more metrics to bullets", "timestamp": "t"}],
    }
    record = assemble_thread(entry, tmp_path)
    assert record is not None
    assert record["pdf_path"] == str(png)
    assert len(record["critiques"]) == 1


# --- Test Junk Filter (from assemble.py) ---

@pytest.mark.parametrize("message, author, expected", [
    ("bump", "user1", True),
    ("        ", "user2", True),
    ("👍", "user3", True),
    ("this is a short msg", "user4", False), # Assuming MIN_MESSAGE_LENGTH is 15
    ("this is a much longer and therefore valid message", "user5", False),
    ("a message from a bot", "some_bot_name", True),
])
def test_is_junk(message, author, expected):
    assert is_junk(message, author) == expected

# --- Test PII Redaction (from structure.py) ---

def test_redact_pii():
    text = "Contact me at test@example.com or (123) 456-7890. My github is https://github.com/johndoe"
    redacted = redact_pii(text)
    assert "[EMAIL_REDACTED]" in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[USER_URL_REDACTED]" in redacted
    assert "test@example.com" not in redacted
    assert "github.com/johndoe" not in redacted

# --- Test Schema Validation (schemas.py) ---

def test_thread_record_validation():
    # Valid data
    valid_data = {
        "thread_id": "123",
        "target_role": "Software Engineer",
        "resume_text": "My resume...",
        "critiques": [{"author": "critic1", "content": "A good critique."}]
    }
    record = ThreadRecord(**valid_data)
    assert record.thread_id == "123"
    assert record.target_role == TargetRole.SOFTWARE_ENGINEER

    # Invalid role
    invalid_data = {
        "thread_id": "456",
        "target_role": "Wizard", # Not in the enum
        "resume_text": "My resume...",
    }
    with pytest.raises(ValueError):
        ThreadRecord(**invalid_data)

    # Missing required field
    with pytest.raises(ValueError):
        ThreadRecord(thread_id="789")

# --- Fixture Data Example ---

@pytest.fixture
def sample_thread_record():
    """A sample ThreadRecord for other tests to use."""
    return ThreadRecord(
        thread_id="fixture_thread",
        target_role=TargetRole.BACKEND_ENGINEER,
        applicant_profile="Mid-level engineer, 5 YoE.",
        resume_text="Full resume text here.",
        context_message="Please review my resume for a backend role.",
        critiques=[
            Critique(author="critic_a", content="Critique content 1."),
            Critique(author="critic_b", content="Critique content 2.", agreement_signal=3),
        ],
        quality_flags=QualityFlags(non_cs_role=False)
    )

def test_with_fixture(sample_thread_record):
    assert sample_thread_record.thread_id == "fixture_thread"
    assert len(sample_thread_record.critiques) == 2
