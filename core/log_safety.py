"""Редакція секретів у логах: токени, ключі в query-рядках, Authorization, паролі в DSN."""
import logging
import re

_PATTERNS = (
    # Connect: WebSocket-URL несе підписаний initData і device_id у query (access-лог пише повний URL).
    (re.compile(r"(?i)(/api/connect/ws\?)[^\s'\"<>]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b((?:init_?data|device_id|query_id|session_id|pair(?:ing)?_?(?:code|token))=)[^&\s'\"<>]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(x-telegram-init-data\s*[:=]\s*)[^\s'\",;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(hash(?:=|%3D))[0-9a-f]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b((?:client_id|client_secret|api_key|apikey|key|token|access_token|"
                r"refresh_token|secret|password|passwd|signature|sig|hash|auth)=)[^&\s'\"<>]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|oauth)?\s*)[^\s'\",;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "[REDACTED_BOT_TOKEN]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]*:)[^@/\s]+@"), r"\1[REDACTED]@"),
)


def redact(text) -> str:
    text = str(text)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


_installed = False


def install() -> None:
    """Глобально обгортає LogRecord factory (діє на всі логери й хендлери; ідемпотентно)."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg, record.args = cleaned, None
            if record.exc_info:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        except Exception:  # логування ніколи не має ламати застосунок
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
