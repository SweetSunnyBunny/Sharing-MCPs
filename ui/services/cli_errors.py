"""Readable, redacted failures for short-lived Claude Code jobs."""

from services.log_redaction import redact_text


def claude_exit_detail(returncode: int, stdout: bytes, stderr: bytes) -> str:
    # Claude can put the actual failure (auth, usage limits, model errors) on
    # stdout in text mode. stderr may be empty or only contain startup warnings.
    parts = [f"Claude Code exited with {returncode}"]
    for label, output in (("stdout", stdout), ("stderr", stderr)):
        text = redact_text(output.decode("utf-8", errors="replace").strip())
        if text:
            parts.append(f"{label}: {text[-1200:]}")
    return "\n".join(parts)
