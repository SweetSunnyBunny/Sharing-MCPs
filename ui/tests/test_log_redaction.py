import logging

from services.log_redaction import RedactingFormatter, redact_text


def test_redacts_headers_keys_queries_and_authenticated_paths():
    raw = (
        "Authorization: Bearer secret-token-123 "
        "api_key=example-secret-value "
        "https://vox.example/abcDEF1234567890abcDEF1234567890?token=hello"
    )
    safe = redact_text(raw)

    assert "secret-token-123" not in safe
    assert "example-secret-value" not in safe
    assert "abcDEF1234567890abcDEF1234567890" not in safe
    assert "token=hello" not in safe
    assert "vox.example" in safe


def test_formatter_redacts_exception_text_after_rendering():
    formatter = RedactingFormatter("%(levelname)s %(message)s")
    try:
        raise RuntimeError("request failed at https://example.test/Abc123456789012345678901234")
    except RuntimeError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "boom", (), __import__("sys").exc_info()
        )

    rendered = formatter.format(record)
    assert "Abc123456789012345678901234" not in rendered
    assert "[REDACTED]" in rendered
