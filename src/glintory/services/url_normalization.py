import urllib.parse

from glintory.config import settings


class InvalidSignalUrlError(ValueError):
    pass


class SignalUrlTooLongError(ValueError):
    pass


def normalize_url(value: str) -> str:
    # 1. Trim leading and trailing whitespace
    url_str = value.strip()

    # 2. Enforce URL maximum length
    max_len = settings.signal_url_max_chars
    if len(url_str) > max_len:
        raise SignalUrlTooLongError("URL length exceeds maximum allowed characters")

    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception as e:
        raise InvalidSignalUrlError("Failed to parse URL") from e

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidSignalUrlError(f"Unsupported scheme: {scheme}")

    if not parsed.hostname:
        raise InvalidSignalUrlError("URL is missing a host")

    # Reject credentials
    if parsed.username is not None or parsed.password is not None:
        raise InvalidSignalUrlError("URLs with embedded credentials are not allowed")

    # Safely handle Unicode host (idna encoding)
    try:
        host = parsed.hostname.encode("idna").decode("ascii")
    except Exception as e:
        raise InvalidSignalUrlError("Invalid unicode host") from e

    # Port handling: remove default ports
    port = parsed.port
    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = host
        else:
            netloc = f"{host}:{port}"
    else:
        netloc = host

    # Path handling: remove trailing slash for non-root paths, normalize root to empty
    path = parsed.path
    if path:
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        elif path == "/":
            path = ""
    else:
        path = ""

    # Query parameters handling
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    cleaned_params = []

    # Tracking parameters list (lowercase)
    tracking_keys = {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
    }

    for k, v in query_params:
        k_lower = k.lower()
        # Remove empty query parameters
        if not v:
            continue
        # Remove tracking parameters
        if k_lower.startswith("utm_") or k_lower in tracking_keys:
            continue
        cleaned_params.append((k, v))

    # Sort query parameters by key (and value to stabilize)
    cleaned_params.sort(key=lambda x: (x[0], x[1]))

    new_query = urllib.parse.urlencode(cleaned_params)

    # Reassemble url
    # urlunparse takes 6-tuple: (scheme, netloc, path, params, query, fragment)
    # fragment is stripped by passing empty string
    normalized = urllib.parse.urlunparse((scheme, netloc, path, "", new_query, ""))

    # Enforce maximum length check again just in case the normalized URL became longer
    if len(normalized) > max_len:
        raise SignalUrlTooLongError(
            "Normalized URL length exceeds maximum allowed characters"
        )

    return normalized
