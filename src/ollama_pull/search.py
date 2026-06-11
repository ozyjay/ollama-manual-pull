from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, unquote
from urllib.request import urlopen


SEARCH_URL = "https://ollama.com/search"
LIBRARY_URL = "https://ollama.com/library"
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


def fetch_tag_html(model: str) -> str:
    url = f"{LIBRARY_URL}/{quote_plus(model)}/tags"
    with urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, Any]] = []
        self.seen_names: set[str] = set()
        self.current: dict[str, Any] | None = None
        self.anchor_depth = 0
        self.capture_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.current is not None:
            if tag not in VOID_TAGS:
                self.anchor_depth += 1
            if tag in {"h1", "h2", "p"} or (
                tag == "span" and not self.capture_stack
            ):
                self.capture_stack.append({"tag": tag, "parts": []})
            return

        if tag != "a":
            return

        href = dict(attrs).get("href")
        name = _model_name_from_href(href)
        if name is None or name in self.seen_names:
            return

        self.current = {"name": name, "heading": None, "description": None, "tags": []}
        self.anchor_depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            if self.current is None:
                self.handle_starttag(tag, attrs)
            return

        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for capture in self.capture_stack:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return

        if self.capture_stack and tag == self.capture_stack[-1]["tag"]:
            capture = self.capture_stack.pop()
            text = " ".join("".join(capture["parts"]).split())
            if text:
                if tag in {"h1", "h2"} and self.current["heading"] is None:
                    self.current["heading"] = text
                elif tag == "p" and self.current["description"] is None:
                    self.current["description"] = text
                elif tag == "span":
                    self.current["tags"].append(text)

        self.anchor_depth -= 1
        if self.anchor_depth <= 0:
            self.seen_names.add(self.current["name"])
            self.results.append(self.current)
            self.current = None
            self.anchor_depth = 0
            self.capture_stack = []


def _model_name_from_href(href: str | None) -> str | None:
    if href is None or not href.startswith("/"):
        return None

    path = href.split("?", 1)[0].split("#", 1)[0].strip("/")
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "library":
        return parts[1]

    return None


class _TagResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.seen_names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        name = _variant_name_from_href(dict(attrs).get("href"))
        if name is None or name in self.seen_names:
            return

        self.seen_names.add(name)
        self.results.append({"name": name, "label": _variant_label(name)})


def _variant_name_from_href(href: str | None) -> str | None:
    name = _model_name_from_href(href)
    if name is None or ":" not in name:
        return None
    return name


def _variant_label(name: str) -> str:
    return name.split(":", 1)[1]


def parse_search_results(page: str) -> list[dict[str, Any]]:
    parser = _SearchResultParser()
    parser.feed(page)
    parser.close()
    return parser.results


def parse_tag_results(page: str) -> list[dict[str, str]]:
    parser = _TagResultParser()
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

    for result in results:
        try:
            result["variants"] = parse_tag_results(fetch_tag_html(result["name"]))
        except Exception:
            result["variants"] = []

    return {"available": True, "results": results, "error": None}
