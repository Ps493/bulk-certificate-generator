"""Per-recipient validation.

Each item of the request is validated on its own. Invalid items don't raise;
they are returned with an error message so the caller can persist them as
FAILED rows and still process the valid ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.schemas import RecipientIn


@dataclass(frozen=True)
class ValidatedRecipient:
    index: int
    raw: Any
    recipient: RecipientIn | None
    error: str | None

    @property
    def is_valid(self) -> bool:
        return self.recipient is not None


def format_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "recipient"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def validate_recipients(items: list[Any]) -> list[ValidatedRecipient]:
    results: list[ValidatedRecipient] = []
    first_seen: dict[str, int] = {}  # email -> index of first valid occurrence

    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            results.append(ValidatedRecipient(index, raw, None, "recipient: must be a JSON object"))
            continue
        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            results.append(ValidatedRecipient(index, raw, None, format_errors(exc)))
            continue

        email = str(recipient.email)
        if email in first_seen:
            # Same address twice in one request is almost always a copy/paste
            # mistake; don't issue a second certificate.
            results.append(
                ValidatedRecipient(index, raw, None, f"email: duplicate of recipient at index {first_seen[email]}")
            )
            continue
        first_seen[email] = index
        results.append(ValidatedRecipient(index, raw, recipient, None))

    return results
