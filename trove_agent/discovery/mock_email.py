"""Entirely fictional, in-memory inboxes for the Trove Discovery demo."""

from __future__ import annotations

import hashlib
import re
from typing import Any


class MockEmailConnector:
    """Email-address selector for fixtures; does not authenticate or connect."""

    @staticmethod
    def _normalize_email(email: Any) -> str:
        if not isinstance(email, str):
            raise ValueError("email must be a string")
        normalized = email.strip().casefold()
        if len(normalized) > 254 or not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.test", normalized
        ):
            raise ValueError("Enter one of the fictional @example.test demo emails")
        return normalized

    def list_messages(
        self,
        email: Any,
        limit: int = 20,
        subject_name: str = "Demo subject",
        case_id: str = "demo",
    ) -> list[dict[str, str]]:
        if not 1 <= limit <= 25:
            raise ValueError("mock inbox result limit must be between 1 and 25")
        address = self._normalize_email(email)
        if not isinstance(subject_name, str) or not subject_name.strip():
            raise ValueError("a case subject name is required")
        seed = hashlib.sha256(f"{address}:{case_id}".encode()).hexdigest()[:10]
        examples = (
            ("Annual life insurance policy statement", "life insurance policy", "policy"),
            ("Fixed deposit renewal reminder", "fixed deposit account", "deposit"),
            ("Pension plan annual summary", "pension plan annual summary", "pension"),
            ("Investment portfolio statement", "mutual fund portfolio statement", "investment"),
            ("Property record search receipt", "property title deed search", "property"),
            ("Trust document reminder", "trust deed and trustee record", "trust"),
            ("Business shareholding notice", "company shares ownership record", "business"),
        )
        messages = []
        for index, (subject, signal, kind) in enumerate(examples[:limit], 1):
            token = hashlib.sha256(f"{seed}:{kind}".encode()).hexdigest()[:8].upper()
            message_id = f"synthetic-{seed}-{kind}"
            messages.append(
                {
                    "id": message_id,
                    "from": f"Sample {kind.title()} Notices <notices@{kind}.example.test>",
                    "date": f"2025-{(index % 12) + 1:02d}-{(index * 3 % 27) + 1:02d}",
                    "subject": subject,
                    "text": (
                        f"Entirely fictional sample for {subject_name.strip()}. "
                        f"A demo provider sent a {signal}. Reference "
                        f"DEMO-{kind.upper()}-{token}. No real account, asset, "
                        "person, or institution is represented."
                    ),
                }
            )
        return messages

    def status(self) -> dict[str, Any]:
        return {
            "provider": "mock_email",
            "is_mock": True,
            "external_connection": False,
            "password_required": False,
            "sample_accounts": [],
            "sample_email_count": 7,
            "sample_data_is_generated_per_case": True,
        }
