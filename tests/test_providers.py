"""Provider discovery, HTTP handling and deadline behaviour."""

import importlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from lookup import popup, provider, providers
from lookup.provider import ConfigError, ProviderBase, Result, SourceError
from lookup.providers.kagi import Provider as Kagi


class ExampleProvider(ProviderBase):
    title = "Example"

    @staticmethod
    def url(query):
        return f"https://example.org/search?q={query}"

    @staticmethod
    def parseResult(data):
        return Result(data["answer"]) if data is not None else None


class ProviderTests(unittest.TestCase):
    def testDropInDiscoveryAndUnusedBrokenModule(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "broken.py").write_text('raise RuntimeError("unused")')
            (path / "_hidden.py").write_text('raise RuntimeError("hidden")')
            readme = Path(__file__).parents[1] / "README.md"
            example = (
                readme.read_text()
                .split("### Add a provider", 1)[1]
                .split("```python", 1)[1]
                .split("```", 1)[0]
            )
            (path / "custom.py").write_text(
                example.replace('title = "Example"', 'title = "Custom"')
            )
            (path / "incomplete.py").write_text(
                'from ..provider import ProviderBase\nclass Provider(ProviderBase):\n title="Incomplete"\n'
            )
            with patch.object(providers, "__path__", [directory]):
                importlib.invalidate_caches()
                self.assertEqual(
                    providers.availableProviders(), ["broken", "custom", "incomplete"]
                )
                self.assertEqual(providers.loadProvider("custom", {}).title, "Custom")
                with self.assertRaisesRegex(
                    ConfigError, "must implement: parseResult, url"
                ):
                    providers.loadProvider("incomplete", {})
                with self.assertRaises(ConfigError):
                    providers.loadProvider("broken", {})
                with self.assertRaises(ConfigError):
                    providers.loadProvider("../custom", {})

    def testExampleProviderWorksWithOnlyRequestAndParser(self):
        source = ExampleProvider()
        with patch.object(
            provider, "requestJson", return_value={"answer": "Found"}
        ) as request:
            self.assertEqual(source.lookup("a & b"), Result("Found"))
        self.assertEqual(
            request.call_args.args[0], "https://example.org/search?q=a%20%26%20b"
        )
        self.assertEqual(source.timeout, 8)

    def testProviderDefaultsAndValidation(self):
        self.assertEqual(Kagi().timeout, 25)
        self.assertEqual(Kagi({"timeout": 1.5}).timeout, 1.5)
        self.assertEqual(Kagi().config["token-path"], "~/.config/kagi/token")
        for options in [
            {"timeout": True},
            {"timeout": 0},
            {"timeout": float("nan")},
            {"timeout": float("inf")},
            {"token-path": []},
            {"token-path": ""},
            {"typo": 1},
        ]:
            with self.subTest(options=options), self.assertRaises(ConfigError):
                Kagi(options)

    def testUrlBodyAndToken(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("test-token\n")
            source = Kagi({"token-path": str(token), "timeout": 3})
            with patch.object(
                provider, "requestJson", return_value={"data": {"output": "answer"}}
            ) as request:
                self.assertEqual(source.lookup("a & b").text, "answer")
            self.assertEqual(request.call_args.kwargs["method"], "POST")
            self.assertEqual(
                request.call_args.kwargs["body"], {"query": "Briefly explain: a & b"}
            )
            self.assertEqual(
                request.call_args.kwargs["headers"], {"Authorization": "Bot test-token"}
            )
            token.write_text("bad\nheader")
            with self.assertRaises(SourceError):
                source.fetchJson("query")
        url = Mock(return_value="https://example.org")
        with (
            patch.object(Kagi, "url", url),
            patch.object(Path, "read_text", return_value="test-token"),
            patch.object(provider, "requestJson", return_value={}),
        ):
            Kagi().fetchJson("Café &a/b")
        url.assert_called_once_with("Caf%C3%A9%20%26a%2Fb")

    def testHttpPostAndFailures(self):
        with patch.object(
            provider, "urlopen", return_value=io.BytesIO(b'{"ok":true}')
        ) as openUrl:
            self.assertEqual(
                provider.requestJson(
                    "https://example.org",
                    method="POST",
                    body={"query": "q"},
                    headers={"Authorization": "Bot test-token"},
                ),
                {"ok": True},
            )
        request = openUrl.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.unredirected_hdrs["Authorization"], "Bot test-token")
        error = HTTPError("https://example.org", 404, "missing", {}, None)
        with patch.object(provider, "urlopen", side_effect=error):
            self.assertIsNone(
                provider.requestJson("https://example.org", missingOk=True)
            )
            with self.assertRaisesRegex(SourceError, "HTTP 404"):
                provider.requestJson("https://example.org")
        for payload in [b"not json", b"x" * (provider.maxResponseBytes + 1)]:
            with (
                patch.object(provider, "urlopen", return_value=io.BytesIO(payload)),
                self.assertRaises(SourceError),
            ):
                provider.requestJson("https://example.org")

    def testMethodAndBodyValidation(self):
        for method, body, message in [
            ("PUT", {}, "method"),
            ("GET", {}, "must not have a body"),
            ("POST", None, "require a JSON body"),
            ("POST", {"value": float("nan")}, "finite"),
            ("POST", {"value": object()}, "JSON-serializable"),
        ]:
            with (
                self.subTest(method=method, body=body),
                patch.object(provider, "urlopen") as send,
            ):
                with self.assertRaisesRegex(SourceError, message):
                    provider.requestJson(
                        "https://example.org", method=method, body=body
                    )
                send.assert_not_called()
        for body in [{}, [], False, 0, ""]:
            with (
                self.subTest(body=body),
                patch.object(
                    provider, "urlopen", return_value=io.BytesIO(b"{}")
                ) as send,
            ):
                provider.requestJson("https://example.org", method="POST", body=body)
                self.assertEqual(send.call_args.args[0].get_method(), "POST")
                self.assertEqual(provider.json.loads(send.call_args.args[0].data), body)
        with (
            patch.object(ExampleProvider, "method", "invalid"),
            self.assertRaises(ConfigError),
        ):
            ExampleProvider()
        self.assertEqual(Kagi.method, "POST")
        self.assertEqual(ExampleProvider.method, "GET")

    def testDeadlineAndIsolation(self):
        release = threading.Event()
        finished = threading.Event()

        class Slow(ExampleProvider):
            defaultConfig = {"timeout": 0.02}

            def lookup(self, query):
                release.wait(2)
                finished.set()
                return Result("late answer")

        class Broken(ExampleProvider):
            def lookup(self, query):
                raise KeyError("missing")

        class Good(ExampleProvider):
            def lookup(self, query):
                return Result("good answer")

        sources = {"slow": Slow(), "bad": Broken(), "good": Good()}
        model = popup.Lookup("query", sources)
        self.assertIn("Loading", model.body())
        try:
            for name, source in sources.items():
                model.fetch(name, source)
            self.assertIn("timed out", model.body())
            self.assertIn("Source failed (KeyError)", model.body())
            self.assertIn("good answer", model.body())
            self.assertIn("<!--done-->", model.body())
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertNotIn("late answer", model.body())
