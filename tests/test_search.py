import unittest
from unittest import mock

from ollama_manual_pull.search import parse_search_results, search_models


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
        self.assertEqual(results[0]["title"], "qwen3-coder")
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


if __name__ == "__main__":
    unittest.main()
