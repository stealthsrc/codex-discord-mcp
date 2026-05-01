"""Prompt validation + secret redaction."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Patterns of tokens/keys we never want echoed back to Discord.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key",      re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic_key",   re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("github_token",    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("github_app",      re.compile(r"\bghs_[A-Za-z0-9]{30,}\b")),
    ("slack_token",     re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access",      re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_key",      re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("discord_token",   re.compile(
        r"\b[MN][A-Za-z0-9_-]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"
    )),
    ("private_key",     re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("generic_bearer",  re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{20,}")),
)

# Dangerous shell fragments — block before sending to Codex.
DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-rf?\s+/(?:\s|$)"),
    re.compile(r"\bsudo\s+rm\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if=.*of=/dev/"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;:"),
    re.compile(r"\bchmod\s+-R\s+777\s+/"),
)

SENSITIVE_PATH_HINTS: tuple[str, ...] = (
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", ".ssh/", "credentials.json",
    ".aws/credentials", ".kube/config",
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None


def validate_prompt(prompt: str, *, max_chars: int) -> ValidationResult:
    if not prompt or not prompt.strip():
        return ValidationResult(False, "empty prompt")
    if len(prompt) > max_chars:
        return ValidationResult(False, f"prompt exceeds {max_chars} chars")
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(prompt):
            return ValidationResult(False, "prompt contains a dangerous shell pattern")
    lowered = prompt.lower()
    for hint in SENSITIVE_PATH_HINTS:
        if hint in lowered:
            return ValidationResult(False, f"prompt references sensitive path '{hint}'")
    return ValidationResult(True)


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for label, pattern in SECRET_PATTERNS:
        out = pattern.sub(f"[REDACTED:{label}]", out)
    return out


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:16]


def preview(text: str, limit: int = 200) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
