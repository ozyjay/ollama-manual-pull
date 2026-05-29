import unittest
from unittest import mock

from ollama_manual_pull.search import fetch_search_html, parse_search_results, search_models


SAMPLE_HTML = """
<a href="/library/qwen3-coder">
  <h2>qwen3-coder</h2>
  <p>Qwen coding model</p>
  <span>30b</span>
  <span>tools</span>
</a>
<a href="/library/deepseek-r1">
  <h2>deepseek-r1</h2>
  <p>Reasoning model</p>
  <span>14b</span>
</a>
"""


class SearchTests(unittest.TestCase):
    def test_parse_search_results_extracts_names_descriptions_and_tags(self):
        results = parse_search_results(SAMPLE_HTML)

        self.assertEqual(results[0]["name"], "qwen3-coder")
        self.assertEqual(results[0]["heading"], "qwen3-coder")
        self.assertNotIn("title", results[0])
        self.assertEqual(results[0]["description"], "Qwen coding model")
        self.assertIn("30b", results[0]["tags"])
        self.assertIn("tools", results[0]["tags"])
        self.assertEqual(results[1]["name"], "deepseek-r1")
        self.assertEqual(results[1]["description"], "Reasoning model")
        self.assertEqual(results[1]["tags"], ["14b"])

    def test_parse_search_results_avoids_duplicate_model_names(self):
        results = parse_search_results(
            """
            <a href="/library/qwen3-coder"><h2>qwen3-coder</h2></a>
            <a href="/library/qwen3-coder"><h2>qwen3-coder</h2></a>
            """
        )

        self.assertEqual([result["name"] for result in results], ["qwen3-coder"])

    def test_parse_search_results_accepts_namespaced_model_links(self):
        results = parse_search_results(
            '<a href="/QwertyMcQwertz/MutualistLLM">'
            "<h2>MutualistLLM</h2><p>Community model</p></a>"
        )

        self.assertEqual(results[0]["name"], "QwertyMcQwertz/MutualistLLM")
        self.assertEqual(results[0]["heading"], "MutualistLLM")
        self.assertEqual(results[0]["description"], "Community model")

    def test_parse_search_results_keeps_nested_inline_heading_and_description_text(self):
        results = parse_search_results(
            '<a href="/library/foo">'
            "<h2>foo <span>bar</span></h2>"
            "<p>Alpha <span>Beta</span> Gamma</p>"
            "</a>"
        )

        self.assertEqual(results[0]["heading"], "foo bar")
        self.assertEqual(results[0]["description"], "Alpha Beta Gamma")
        self.assertNotIn("bar", results[0]["tags"])
        self.assertNotIn("Beta", results[0]["tags"])

    def test_parse_search_results_handles_void_elements_inside_anchor(self):
        results = parse_search_results(
            '<a href="/library/foo"><img src="x"><h2>foo</h2><br><p>desc</p></a>'
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "foo")
        self.assertEqual(results[0]["heading"], "foo")
        self.assertNotIn("title", results[0])
        self.assertEqual(results[0]["description"], "desc")

    def test_parse_search_results_handles_self_closing_void_elements_inside_anchor(self):
        results = parse_search_results(
            '<a href="/library/foo"><img src="x"/><h2>foo</h2><br/><p>desc</p></a>'
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "foo")
        self.assertEqual(results[0]["heading"], "foo")
        self.assertEqual(results[0]["description"], "desc")

    def test_search_models_returns_empty_success_for_empty_query(self):
        payload = search_models("  ")

        self.assertEqual(payload, {"available": True, "results": [], "error": None})

    def test_search_models_returns_unavailable_on_network_failure(self):
        with mock.patch(
            "ollama_manual_pull.search.fetch_search_html", side_effect=OSError("offline")
        ):
            payload = search_models("qwen")

        self.assertFalse(payload["available"])
        self.assertEqual(payload["results"], [])
        self.assertIn("offline", payload["error"])

    def test_search_models_returns_unavailable_on_parse_failure(self):
        with (
            mock.patch("ollama_manual_pull.search.fetch_search_html", return_value="<html>"),
            mock.patch(
                "ollama_manual_pull.search.parse_search_results",
                side_effect=ValueError("parse failed"),
            ),
        ):
            payload = search_models("qwen")

        self.assertFalse(payload["available"])
        self.assertEqual(payload["results"], [])
        self.assertIn("parse failed", payload["error"])

    def test_fetch_search_html_url_encodes_query(self):
        response = mock.Mock()
        response.read.return_value = b"search page"
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=None)

        with mock.patch("ollama_manual_pull.search.urlopen", return_value=response) as urlopen:
            page = fetch_search_html("qwen coder")

        self.assertEqual(page, "search page")
        urlopen.assert_called_once_with("https://ollama.com/search?q=qwen+coder", timeout=15)


if __name__ == "__main__":
    unittest.main()
