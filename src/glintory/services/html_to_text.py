import html
import re
from html.parser import HTMLParser


class HackerNewsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.in_script_or_style = False

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.in_script_or_style = True
        elif tag in ("p", "br", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.in_script_or_style = False
        elif tag in ("p", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.in_script_or_style:
            self.text_parts.append(data)


def html_to_plain_text(
    value: str | None,
    *,
    max_chars: int,
) -> str:
    if value is None:
        return ""

    # Remove NUL character
    value = value.replace("\0", "")

    try:
        parser = HackerNewsHTMLParser()
        parser.feed(value)
        parser.close()
        parsed_text = "".join(parser.text_parts)
    except Exception:
        # Fallback if parser fails
        parsed_text = re.sub(r"<[^>]+>", " ", value)

    # Decode HTML entities
    decoded = html.unescape(parsed_text)

    # Normalize whitespace: replace multiple whitespace chars with a single space
    normalized = re.sub(r"\s+", " ", decoded).strip()

    # Trim to max_chars
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars]

    return normalized
