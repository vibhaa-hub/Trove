"""Small, deterministic agents used by the local Trove MVP."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


ASSET_CUES: dict[str, tuple[str, ...]] = {
    "bank_account": ("bank account", "savings account", "fixed deposit", "fd account"),
    "insurance": ("life insurance", "insurance policy", "policy premium", "annuity"),
    "investment": ("mutual fund", "brokerage", "demat", "shares", "portfolio"),
    "property": ("property", "title deed", "land registry", "mortgage", "plot number"),
    "pension": ("pension", "provident fund", "retirement account", "superannuation"),
    "crypto": ("crypto", "bitcoin", "ethereum", "wallet address", "digital asset"),
    "business": ("business ownership", "shareholder", "company shares", "company registry"),
    "trust": ("trust deed", "trustee", "trust fund"),
}

INSTITUTIONS = (
    "HDFC Bank",
    "ICICI Bank",
    "State Bank of India",
    "Axis Bank",
    "Barclays",
    "HSBC",
    "Lloyds Bank",
    "NatWest",
    "Chase",
    "Bank of America",
    "Wells Fargo",
    "Fidelity",
    "Vanguard",
    "AIG",
    "Prudential",
    "Aviva",
)

REFERENCE_PATTERN = re.compile(
    r"\b(?:account|policy|member|folio|reference|ref)\s*(?:number|no\.?|#)?"
    r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,})\b",
    re.IGNORECASE,
)


def mask_reference(value: str) -> str:
    """Keep only the final four characters of an extracted identifier."""
    normalized = re.sub(r"[^A-Za-z0-9]", "", value)
    if not normalized:
        return ""
    return f"***{normalized[-4:]}"


@dataclass(frozen=True)
class DocumentAnalysis:
    text_sha256: str
    entities: dict[str, Any]
    candidates: list[dict[str, Any]]


class DocumentAgent:
    """Classify document text locally; never stores the supplied raw text."""

    def analyze(
        self, text: str, file_name: str, subject_name: str, subject_dob: str | None
    ) -> DocumentAnalysis:
        normalized = text.casefold()
        found_types = [
            asset_type
            for asset_type, cues in ASSET_CUES.items()
            if any(cue in normalized for cue in cues)
        ]
        institutions = [
            name for name in INSTITUTIONS if name.casefold() in normalized
        ]
        references = [
            mask_reference(match.group(1))
            for match in REFERENCE_PATTERN.finditer(text)
        ]
        references = list(dict.fromkeys(reference for reference in references if reference))
        subject_name_found = subject_name.casefold() in normalized
        subject_dob_found = bool(subject_dob and subject_dob in text)
        confidence = 0.82 if subject_name_found and subject_dob_found else (
            0.68 if subject_name_found or subject_dob_found else 0.35
        )
        candidates = [
            {
                "asset_type": asset_type,
                "institution": institutions[0] if institutions else None,
                "masked_reference": references[0] if references else None,
                "confidence": confidence,
                "status": "needs_review",
                "description": f"Possible {asset_type.replace('_', ' ')} referenced in {file_name}",
            }
            for asset_type in found_types
        ]
        return DocumentAnalysis(
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            entities={
                "asset_types": found_types,
                "institutions": institutions,
                "masked_references": references,
                "subject_name_found": subject_name_found,
                "subject_dob_found": subject_dob_found,
            },
            candidates=candidates,
        )


class DiscoveryAgent:
    """Turn extracted document signals into reviewable asset leads."""

    def discover(self, analysis: DocumentAnalysis) -> list[dict[str, Any]]:
        return analysis.candidates


class CaseManagerAgent:
    """Enforce human-review gates in the case workflow."""

    STAGES = (
        "intake",
        "death_verification",
        "evidence_review",
        "asset_verification",
        "beneficiary_review",
        "claims",
        "settlement",
    )

    def next_stage(
        self,
        current: str,
        unresolved_candidates: int,
        death_status: str = "not_provided",
        unverified_beneficiaries: int = 0,
    ) -> str:
        if current not in self.STAGES:
            raise ValueError(f"Unknown workflow stage: {current}")
        next_index = self.STAGES.index(current) + 1
        if next_index >= len(self.STAGES):
            raise ValueError("The case is already at the final workflow stage")
        if current == "intake" and death_status != "pending_review":
            raise ValueError(
                "Upload a death certificate before starting the post-death workflow"
            )
        next_stage = self.STAGES[next_index]
        if current == "death_verification" and death_status != "reviewed":
            raise ValueError(
                "A human must review the uploaded death certificate before continuing"
            )
        if next_stage == "claims" and unverified_beneficiaries:
            raise ValueError(
                "Beneficiary records must be reviewed before claims; this is not legal heir verification"
            )
        if next_stage == "claims" and unresolved_candidates:
            raise ValueError(
                "All discovered asset candidates must be confirmed or rejected before claims"
            )
        return next_stage
