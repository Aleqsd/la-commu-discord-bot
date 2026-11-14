from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


def _clean_text(value: Any) -> Optional[str]:
    """Coerce a model-provided scalar to a stripped string, or None when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_clean_text(item) for item in value if not isinstance(item, (list, tuple, dict))]
        return ", ".join(part for part in parts if part) or None
    return None


def _clean_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _clean_team(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if isinstance(item, str) and item.strip()), None)
    team = _clean_text(value) or "dev"
    return team.lower().replace(" ", "_")


def _ensure_list(value: Any) -> List[str]:
    if not value or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if isinstance(item, (list, tuple, dict)):
                continue
            text = _clean_text(item)
            if text:
                items.append(text)
        return items
    return []


@dataclass(slots=True)
class JobPosting:
    job_title: str
    company_name: str
    job_url: str
    team: str
    location: Optional[str] = None
    work_model: Optional[str] = None
    seniority: Optional[str] = None
    contract_type: Optional[str] = None
    remote_friendly: Optional[bool] = None
    compensation: Optional[str] = None
    description_summary: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    known_titles: List[str] = field(default_factory=list)
    # URL or image reference the job was extracted from (used for duplicate detection).
    source_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "JobPosting":
        if not isinstance(data, dict):
            data = {}
        job_url = _clean_text(data.get("job_url"))
        source_url = _clean_text(data.get("source_url"))
        return cls(
            job_title=_clean_text(data.get("job_title")) or "Unknown Role",
            company_name=_clean_text(data.get("company_name")) or "Unknown Studio",
            job_url=job_url or source_url or "",
            team=_clean_team(data.get("team")),
            location=_clean_text(data.get("location")),
            work_model=_clean_text(data.get("work_model")),
            seniority=_clean_text(data.get("seniority")),
            contract_type=_clean_text(data.get("contract_type")),
            remote_friendly=_clean_bool(data.get("remote_friendly")),
            compensation=_clean_text(data.get("compensation")) or _clean_text(data.get("salary")),
            description_summary=_clean_text(data.get("description_summary"))
            or _clean_text(data.get("highlights")),
            skills=_ensure_list(data.get("skills")),
            known_titles=_ensure_list(data.get("known_titles") or data.get("portfolio_titles")),
            source_url=source_url,
        )
