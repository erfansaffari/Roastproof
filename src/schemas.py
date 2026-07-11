from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class TargetRole(str, Enum):
    """Enum for common target roles."""
    SOFTWARE_ENGINEER = "Software Engineer"
    FRONTEND_ENGINEER = "Frontend Engineer"
    BACKEND_ENGINEER = "Backend Engineer"
    FULL_STACK_ENGINEER = "Full Stack Engineer"
    DATA_SCIENTIST = "Data Scientist"
    MACHINE_LEARNING_ENGINEER = "Machine Learning Engineer"
    DEVOPS_ENGINEER = "DevOps Engineer"
    SRE = "Site Reliability Engineer"
    QA_ENGINEER = "QA Engineer"
    ENGINEERING_MANAGER = "Engineering Manager"
    PRODUCT_MANAGER = "Product Manager"
    OTHER = "Other"


# --- Core Data Schemas for the Ingestion Pipeline (Phase 1) ---

class Critique(BaseModel):
    """A single critique message."""
    author: str
    content: str
    agreement_signal: int = Field(0, description="Count of agreement signals like '+1', 'this', etc.")
    original_text: Optional[str] = Field(None, description="Quoted text from the resume, if any.")


class QualityFlags(BaseModel):
    """Flags for the quality and characteristics of a thread."""
    parse_failed: bool = False
    no_critiques: bool = False
    non_cs_role: bool = False
    low_quality_extraction: bool = False


class ThreadRecord(BaseModel):
    """
    Represents a single, structured thread from the raw data.
    One record per thread.
    """
    thread_id: str
    target_role: TargetRole = Field(..., description="The job role the applicant is targeting.")
    applicant_profile: Optional[str] = Field(None, description="A summary of the applicant's background.")

    resume_text: str

    resume_sections: Dict[str, str] = Field({}, description="Resume text split into standard sections.")

    context_message: Optional[str] = Field(None, description="The original message providing context for the resume.")
    critiques: List[Critique] = []

    quality_flags: QualityFlags = Field(default_factory=QualityFlags)


# --- Schemas for Knowledge Base and Generation (Phases 3 & 4) ---

class Rule(BaseModel):
    """
    A single entry in the rulebook, derived from community critiques.
    """
    category: str
    section: str
    applies_to: List[str] = Field([], description="Roles or profiles this rule applies to.")
    statement: str
    frequency: float
    evidence_examples: List[str]
    supporting_thread_ids: List[str] = Field(
        default_factory=list,
        description="Thread IDs cited as evidence (used by the hallucination guard).",
    )


class ApplicantProfile(BaseModel):
    """Intake profile used for retrieval and generation (Phases 3–4)."""
    target_role: str
    year: Optional[str] = Field(
        None,
        description="Normalized year label: year_1..year_4, new_grad, senior, grad, unknown.",
    )
    has_internships: bool = False
    profile_summary: str = ""
    skills: List[str] = Field(default_factory=list)


class CritiquePoint(BaseModel):
    """
    One critique exploded from a ThreadRecord for the vector store / retriever.
    """
    id: str
    thread_id: str
    target_role: str
    section: str
    year: Optional[str] = None
    has_internships: bool = False
    agreement_signal: int = 0
    issue: str
    suggestion: str = ""
    original_text: Optional[str] = None
    category: str = "other"
    composite: str = ""
    score: Optional[float] = None


BULLET_MIN_LEN = 60
BULLET_MAX_LEN = 140
MAX_BULLETS_PER_EXPERIENCE = 4
MAX_BULLETS_PER_PROJECT = 3
MAX_PROJECTS = 4
MAX_TOTAL_BULLETS = 22


class ResumeContent(BaseModel):
    """
    The structured content of a resume, used for generation.
    Includes validators based on best practices (one-page heuristic).
    """
    contact: Dict[str, str]
    education: List[Dict[str, str]]
    experience: List[Dict]
    projects: List[Dict]
    skills: Dict[str, List[str]]
    section_order: List[str]

    @field_validator("experience")
    @classmethod
    def check_experience_bullets(cls, entries: List[Dict]) -> List[Dict]:
        for entry in entries:
            bullets = entry.get("bullets", [])
            if len(bullets) > MAX_BULLETS_PER_EXPERIENCE:
                raise ValueError(
                    f"experience entry has {len(bullets)} bullets, max is {MAX_BULLETS_PER_EXPERIENCE}"
                )
            for bullet in bullets:
                if not (BULLET_MIN_LEN <= len(bullet) <= BULLET_MAX_LEN):
                    raise ValueError(
                        f"bullet length {len(bullet)} outside [{BULLET_MIN_LEN}, {BULLET_MAX_LEN}]: {bullet!r}"
                    )
        return entries

    @field_validator("projects")
    @classmethod
    def check_projects(cls, entries: List[Dict]) -> List[Dict]:
        if len(entries) > MAX_PROJECTS:
            raise ValueError(f"{len(entries)} projects, max is {MAX_PROJECTS}")
        for entry in entries:
            bullets = entry.get("bullets", [])
            if len(bullets) > MAX_BULLETS_PER_PROJECT:
                raise ValueError(
                    f"project entry has {len(bullets)} bullets, max is {MAX_BULLETS_PER_PROJECT}"
                )
            for bullet in bullets:
                if not (BULLET_MIN_LEN <= len(bullet) <= BULLET_MAX_LEN):
                    raise ValueError(
                        f"bullet length {len(bullet)} outside [{BULLET_MIN_LEN}, {BULLET_MAX_LEN}]: {bullet!r}"
                    )
        return entries

    @model_validator(mode="after")
    def check_total_bullet_budget(self) -> "ResumeContent":
        total = sum(len(e.get("bullets", [])) for e in self.experience)
        total += sum(len(p.get("bullets", [])) for p in self.projects)
        if total > MAX_TOTAL_BULLETS:
            raise ValueError(f"total bullet count {total} exceeds budget of {MAX_TOTAL_BULLETS}")
        return self


class Suggestion(BaseModel):
    """Gap surfaced to the user — never silently added to the resume (G1)."""
    type: str = Field(..., description="missing_skill | missing_metric | content_gap")
    detail: str


class GenerationResult(BaseModel):
    """Generator output: structured resume + suggestions report seed."""
    resume: ResumeContent
    suggestions: List[Suggestion] = Field(default_factory=list)


class IntakeEducation(BaseModel):
    school: str
    degree: str
    dates: str
    location: str = ""
    details: str = Field("", description="Free-text GPA, coursework, honors.")


class IntakeExperience(BaseModel):
    company: str
    title: str
    dates: str
    location: str = ""
    description: str = Field(..., description="Raw free-text description of the role.")


class IntakeProject(BaseModel):
    name: str
    technologies: str = ""
    dates: str = ""
    description: str = Field(..., description="Raw free-text project description.")

    @field_validator("dates", mode="before")
    @classmethod
    def coerce_dates(cls, v):
        return "" if v is None else str(v)


class Intake(BaseModel):
    """
    v1 user intake — YAML/JSON the applicant fills before generation.
    """
    name: str
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    target_role: str = "Software Engineer"
    year: Optional[str] = None
    has_internships: bool = False
    profile_summary: str = ""
    education: List[IntakeEducation] = Field(default_factory=list)
    experience: List[IntakeExperience] = Field(default_factory=list)
    projects: List[IntakeProject] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)

    def to_applicant_profile(self) -> ApplicantProfile:
        return ApplicantProfile(
            target_role=self.target_role,
            year=self.year,
            has_internships=self.has_internships,
            profile_summary=self.profile_summary
            or f"{self.year or 'unknown'} targeting {self.target_role}",
            skills=list(self.skills),
        )
