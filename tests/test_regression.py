"""Regression tests for bundled parsers and popup rendering/lifecycle."""

import subprocess
import unittest
from unittest.mock import Mock, patch

from lookup import popup as lookup
from lookup.provider import SourceError
from lookup.providers.dictionary import DictionaryProvider
from lookup.providers.kagi import KagiProvider
from lookup.providers.wikipedia import WikipediaProvider


class RegressionTests(unittest.TestCase):
    def testDictionaryParsingAndFallbackUrl(self):
        result = DictionaryProvider().parseResult(
            [
                {
                    "word": "a/b",
                    "meanings": [
                        {
                            "partOfSpeech": "noun",
                            "definitions": [
                                {"definition": "First"},
                                {"definition": "Second"},
                            ],
                        },
                        {"partOfSpeech": "verb", "definitions": []},
                    ],
                }
            ]
        )
        self.assertEqual(result.text, "noun: First")
        self.assertEqual(result.links[0].url, "https://en.wiktionary.org/wiki/a%2Fb")
        self.assertIsNone(DictionaryProvider().parseResult(None))

    def testWikipediaOptionalFields(self):
        result = WikipediaProvider().parseResult(
            {"title": "Test", "extract": "Summary"}
        )
        self.assertEqual(result.text, "Summary")
        self.assertEqual(result.links, ())
        self.assertIsNone(WikipediaProvider().parseResult({"title": "No summary"}))

    def testKagiAnswerAndError(self):
        result = KagiProvider().parseResult(
            {
                "data": {
                    "output": "**Answer**",
                    "references": [
                        {"title": "Source", "url": "https://example.org"},
                    ],
                }
            }
        )
        self.assertEqual(result.text, "**Answer**")
        self.assertEqual(result.links[0].title, "[1] Source")
        with self.assertRaises(SourceError):
            KagiProvider().parseResult({"error": [{"msg": "Failure"}]})

    def testParsersRejectInvalidJsonShapes(self):
        cases = [
            (DictionaryProvider(), {}),
            (DictionaryProvider(), [{"word": "test", "meanings": "wrong"}]),
            (WikipediaProvider(), []),
            (WikipediaProvider(), {"title": "test", "extract": 42}),
            (KagiProvider(), "wrong"),
            (KagiProvider(), {"data": {"output": 42}}),
        ]
        for source, response in cases:
            with (
                self.subTest(source=source.title, response=response),
                self.assertRaises(SourceError),
            ):
                source.parseResult(response)
        for source in [
            DictionaryProvider(),
            WikipediaProvider(),
            KagiProvider(),
        ]:
            with self.subTest(source=source.title):
                self.assertIsNone(source.parseResult(None))

    def testHtmlAndLinksAreEscaped(self):
        html = lookup.resultHtml(
            lookup.Result(
                text='<img src=x onerror="alert(1)">',
                title="<title>",
                links=(lookup.Link('"click"', 'https://example.org/?x="&y=1'),),
            )
        )
        self.assertNotIn("<img", html)
        self.assertIn("&lt;title&gt;", html)
        self.assertIn("&quot;&amp;y=1", html)
        self.assertEqual(
            lookup.linkHtml(lookup.Link("unsafe", "javascript:alert(1)")), "unsafe"
        )

    def testMarkdownRendererConfiguration(self):
        factory = Mock()
        renderer = factory.return_value.disable.return_value
        renderer.render.return_value = "<p><strong>bold</strong></p>"
        with patch.object(lookup, "MarkdownIt", factory):
            html = lookup.resultHtml(lookup.Result(text="**bold**", markdown=True))
            lookup.resultHtml(lookup.Result(text="plain"))
        factory.assert_called_once_with("commonmark", {"html": False})
        factory.return_value.disable.assert_called_once_with("image")
        renderer.render.assert_called_once_with("**bold**")
        self.assertIn('<div class="markdown"><p><strong>bold</strong></p></div>', html)

    def testRealMarkdownFormattingAndEscaping(self):
        text = (
            "**bold** `a*b*c` [link](https://example.org/a*b*c) "
            "<script>alert(1)</script> [bad](javascript:alert(1)) "
            "![image](https://example.org/image.png)"
        )
        html = lookup.resultHtml(lookup.Result(text=text, markdown=True))
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code>a*b*c</code>", html)
        self.assertIn('href="https://example.org/a*b*c"', html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn("<img", html)

    def testPlainTextPreservesMarkdownAndOmitsEmptyHeaders(self):
        text = "`a*b*c` [example](https://example.org/a*b*c) <script>x</script>"
        html = lookup.resultHtml(lookup.Result(text=text))
        self.assertEqual(html, f'<div class="plain">{lookup.escape(text)}</div>')
        self.assertEqual(
            lookup.resultHtml(lookup.Result(text="body", detail="detail")),
            '<p><i>detail</i></p><div class="plain">body</div>',
        )

    def testMalformedReferenceDoesNotDiscardAnswer(self):
        for url in ["https://[invalid", "javascript:alert(1)", "relative/path"]:
            with self.subTest(url=url):
                html = lookup.resultHtml(
                    lookup.Result(
                        text="Valid answer",
                        links=(lookup.Link("Reference <title>", url),),
                    )
                )
                self.assertIn("Valid answer", html)
                self.assertIn("Reference &lt;title&gt;", html)
                self.assertNotIn("<a ", html)

    def testMalformedOptionalMetadataIsIgnored(self):
        for references in [None, False, [False, {"title": 42, "url": []}]]:
            with self.subTest(references=references):
                result = KagiProvider.parseResult(
                    {"data": {"output": "answer", "references": references}}
                )
                self.assertEqual(result.text, "answer")
                self.assertEqual(result.links, ())
        result = KagiProvider.parseResult(
            {
                "data": {
                    "output": "answer",
                    "references": [
                        False,
                        {"title": "Valid", "url": "https://example.org"},
                    ],
                }
            }
        )
        # Preserve numbering so citations still identify the original reference.
        self.assertEqual(result.links[0].title, "[2] Valid")
        for contentUrls in [None, [], {"desktop": False}, {"desktop": {"page": 42}}]:
            result = WikipediaProvider.parseResult(
                {
                    "title": "Page",
                    "extract": "answer",
                    "content_urls": contentUrls,
                    "description": [],
                }
            )
            self.assertEqual(result.text, "answer")
            self.assertEqual(result.links, ())
            self.assertEqual(result.detail, "")
        for urls in [None, False, [42]]:
            result = DictionaryProvider.parseResult(
                [{"word": "word", "sourceUrls": urls, "phonetic": []}]
            )
            self.assertEqual(result.links[0].url, "https://en.wiktionary.org/wiki/word")
            self.assertEqual(result.detail, "")

    def testPopupLaunchFailureClosesServerWithoutStartingFetches(self):
        server = Mock(server_port=12345)
        model = Mock()
        model.sources = {}
        with (
            patch.object(lookup, "serve", return_value=server),
            patch.object(lookup.subprocess, "Popen", side_effect=FileNotFoundError),
            self.assertRaises(FileNotFoundError),
        ):
            lookup.showPopup(model)
        model.start.assert_not_called()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()

    def testInterruptionTerminatesPopupAndClosesServer(self):
        server = Mock(server_port=12345)
        popup = Mock()
        popup.wait.side_effect = [
            KeyboardInterrupt,
            subprocess.TimeoutExpired("yad", 2),
            0,
        ]
        popup.poll.return_value = None
        model = Mock()
        model.sources = {}
        with (
            patch.object(lookup, "serve", return_value=server),
            patch.object(lookup.subprocess, "Popen", return_value=popup),
            self.assertRaises(KeyboardInterrupt),
        ):
            lookup.showPopup(model)
        popup.terminate.assert_called_once()
        popup.kill.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
