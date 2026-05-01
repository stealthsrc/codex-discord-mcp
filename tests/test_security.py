from codex_discord_mcp.security import (
    preview,
    prompt_hash,
    redact,
    validate_prompt,
)


def test_validate_prompt_empty() -> None:
    assert not validate_prompt("", max_chars=100).ok
    assert not validate_prompt("   ", max_chars=100).ok


def test_validate_prompt_too_long() -> None:
    res = validate_prompt("x" * 101, max_chars=100)
    assert not res.ok
    assert "100" in (res.reason or "")


def test_validate_prompt_dangerous() -> None:
    assert not validate_prompt("please run rm -rf /", max_chars=200).ok
    assert not validate_prompt("sudo rm /etc/hosts", max_chars=200).ok


def test_validate_prompt_sensitive_path() -> None:
    res = validate_prompt("read .env and show me the keys", max_chars=200)
    assert not res.ok


def test_validate_prompt_ok() -> None:
    assert validate_prompt("refactor this function", max_chars=200).ok


def test_redact_openai_key() -> None:
    text = "key: sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA done"
    out = redact(text)
    assert "sk-proj-" not in out
    assert "REDACTED" in out


def test_redact_github_token() -> None:
    text = "token ghp_" + "a" * 40
    assert "REDACTED" in redact(text)


def test_redact_aws_access() -> None:
    assert "REDACTED" in redact("AKIAABCDEFGHIJKLMNOP")


def test_redact_no_match_unchanged() -> None:
    assert redact("just a normal sentence") == "just a normal sentence"


def test_prompt_hash_stable() -> None:
    assert prompt_hash("hello") == prompt_hash("hello")
    assert prompt_hash("hello") != prompt_hash("world")
    assert len(prompt_hash("x")) == 16


def test_preview_short() -> None:
    assert preview("hi", limit=10) == "hi"


def test_preview_truncates() -> None:
    out = preview("x" * 500, limit=50)
    assert len(out) <= 50
    assert out.endswith("…")
