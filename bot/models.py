from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


def _ensure_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item).strip()]
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

    @classmethod
    def from_dict(cls, data: dict) -> "JobPosting":
        skills = _ensure_list(data.get("skills"))
        titles = _ensure_list(data.get("known_titles") or data.get("portfolio_titles"))
        return cls(
            job_title=data.get("job_title") or "Unknown Role",
            company_name=data.get("company_name") or "Unknown Studio",
            job_url=data.get("job_url") or data.get("source_url") or "",
            team=(data.get("team") or "dev").lower().replace(" ", "_"),
            location=data.get("location"),
            work_model=data.get("work_model"),
            seniority=data.get("seniority"),
            contract_type=data.get("contract_type"),
            remote_friendly=data.get("remote_friendly"),
            compensation=data.get("compensation") or data.get("salary"),
            description_summary=data.get("description_summary") or data.get("highlights"),
            skills=skills,
            known_titles=titles,
        )
