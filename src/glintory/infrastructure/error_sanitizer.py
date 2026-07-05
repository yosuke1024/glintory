import re

# RegEx patterns for sanitization
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.\~=]+")
AUTH_HEADER_PATTERN = re.compile(r"(?i)authorization\s*:\s*[^\s]+")
QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(api_key|token|auth|password|secret|key)=[^&\s\?]+"
)
DB_URL_PATTERN = re.compile(
    r"(?i)(sqlite|postgresql|mysql|mssql|mongodb|redis|amqp|odbc):\/\/[^\s]+"
)


def sanitize_error(message: str) -> str:
    if not message:
        return ""

    # 1. Normalize newlines to spaces
    # Replace any sequences of whitespace/newlines with a single space
    sanitized = re.sub(r"\s+", " ", message).strip()

    # 2. Mask authorization headers / bearer tokens
    sanitized = BEARER_PATTERN.sub("Bearer [MASKED]", sanitized)
    sanitized = AUTH_HEADER_PATTERN.sub("Authorization: [MASKED]", sanitized)

    # 3. Mask sensitive URL query parameters
    sanitized = QUERY_SECRET_PATTERN.sub(r"\1=[MASKED]", sanitized)

    # 4. Mask Database URLs
    sanitized = DB_URL_PATTERN.sub(r"\1://[MASKED]", sanitized)

    # 5. Truncate to maximum 1000 characters
    if len(sanitized) > 1000:
        sanitized = sanitized[:997] + "..."

    return sanitized
