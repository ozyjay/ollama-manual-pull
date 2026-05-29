from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, unquote
from urllib.request import urlopen


SEARCH_URL = "https://ollama.com/search"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_tags(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def fetch_search_html(query: str) -> str:
    url = f"{SEARCH_URL}?q={quote_plus(query)}"
    with urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, Any]] = []
        self.seen_names: set[str] = set()
        self.current: dict[str, Any] | None = None
        self.anchor_depth = 0
        self.capture_tag: str | None = None
        self.capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.current is not None:
            if tag not in VOID_TAGS:
                self.anchor_depth += 1
            if tag in {"h1", "h2", "p", "span"}:
                self.capture_tag = tag
                self.capture_parts = []
            return

        if tag != "a":
            return

        href = dict(attrs).get("href")
        name = _model_name_from_href(href)
        if name is None or name in self.seen_names:
            return

        self.current = {"name": name, "heading": None, "description": None, "tags": []}
        self.anchor_depth = 1

    def handle_data(self, data: str) -> None:
        if self.capture_tag is not None:
            self.capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return

        if tag == self.capture_tag:
            text = " ".join("".join(self.capture_parts).split())
            if text:
                if tag in {"h1", "h2"} and self.current["heading"] is None:
                    self.current["heading"] = text
                elif tag == "p" and self.current["description"] is None:
                    self.current["description"] = text
                elif tag == "span":
                    self.current["tags"].append(text)
            self.capture_tag = None
            self.capture_parts = []

        self.anchor_depth -= 1
        if self.anchor_depth <= 0:
            self.seen_names.add(self.current["name"])
            self.results.append(self.current)
            self.current = None
            self.anchor_depth = 0


def _model_name_from_href(href: str | None) -> str | None:
    if href is None or not href.startswith("/library/"):
        return None

    model_name = href.removeprefix("/library/").split("?", 1)[0].split("#", 1)[0]
    model_name = model_name.strip("/").split("/", 1)[0]
    if not model_name:
        return None
    return unquote(model_name)


def parse_search_results(page: str) -> list[dict[str, Any]]:
    parser = _SearchResultParser()
    parser.feed(page)
    parser.close()
    return parser.results


def search_models(query: str) -> dict[str, Any]:
    if not query.strip():
        return {"available": True, "results": [], "error": None}

    try:
        page = fetch_search_html(query)
        results = parse_search_results(page)
    except Exception as error:
        return {"available": False, "results": [], "error": str(error)}

    return {"available": True, "results": results, "error": None}
