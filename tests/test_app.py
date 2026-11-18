"""Configuration and CLI integration without display servers or credentials."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from lookup import app
from lookup.provider import ConfigError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / "lookup/config.toml"
        self.config.parent.mkdir()
        self.environment = patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.directory)}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def testClipboardBackendSelection(self):
        cases = [
            ({}, "wl-paste"),
            ({"DISPLAY": ":0"}, "xclip"),
            ({"WAYLAND_DISPLAY": "wayland-0"}, "wl-paste"),
            ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "wl-paste"),
            ({"LOOKUP_CLIPBOARD_BACKEND": "wayland", "DISPLAY": ":0"}, "wl-paste"),
            (
                {"LOOKUP_CLIPBOARD_BACKEND": "x11", "WAYLAND_DISPLAY": "wayland-0"},
                "xclip",
            ),
        ]
        for environment, executable in cases:
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
            ):
                self.assertEqual(app.primaryPasteCommand("auto")[0], executable)
                self.assertEqual(
                    app.primaryPasteCommand(["custom", "arg"]), ["custom", "arg"]
                )
        with (
            patch.dict(os.environ, {"LOOKUP_CLIPBOARD_BACKEND": "invalid"}),
            self.assertRaises(ConfigError),
        ):
            app.primaryPasteCommand("auto")

    def testMissingConfigAndIndependentDefaults(self):
        self.assertEqual(app.configPath(), self.config)
        config = app.loadConfig()
        config["popup"]["width"] = 12
        self.assertEqual(app.loadConfig()["popup"]["width"], 520)
        with self.assertRaises(ConfigError):
            app.loadConfig(self.directory / "missing.toml")
        with (
            patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}),
            patch.object(Path, "home", return_value=self.directory),
        ):
            self.assertEqual(
                app.configPath(), self.directory / ".config/lookup/config.toml"
            )

    def testExampleAndUnknownKeys(self):
        example = Path(__file__).parents[1] / "examples/config.toml"
        app.validateConfig(app.loadConfig(example))
        for text in ["typo = 1", "[popup]\ntyop = 1", "popup = 1", "bad toml"]:
            self.config.write_text(text)
            with self.subTest(text=text), self.assertRaises(ConfigError):
                app.loadConfig()

    def testInvalidSettings(self):
        cases = [
            ("width", True),
            ("height", 0),
            ("height", "automatic"),
            ("width", 2.5),
            ("position", "left"),
            ("close-on-unfocus", "true"),
            ("additional-yad-flags", "--mouse"),
            ("additional-yad-flags", [1]),
        ]
        for key, value in cases:
            config = app.loadConfig()
            config["popup"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                app.validateConfig(config)
        for key, value in [
            ("primary-paste", []),
            ("primary-paste", "xclip"),
            ("providers", ["unknown"]),
            ("selection-max-length", False),
            ("provider", {"kagi": 1}),
        ]:
            config = app.loadConfig()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ConfigError):
                app.validateConfig(config)

    def testConfigReachesYad(self):
        self.config.write_text("""providers = ["wikipedia"]
primary-paste = ["clipboard-helper", "argument with spaces"]
selection-max-length = 5
[popup]
width = 600
height = 800
position = "pointer"
close-on-unfocus = true
additional-yad-flags = ["--undecorated", "--text=literal $(text)"]
""")
        with (
            patch.object(sys, "argv", ["lookup", "--config", str(self.config)]),
            patch.object(
                app.subprocess, "run", return_value=Mock(stdout="  hello world ")
            ) as read,
            patch("lookup.popup.serve", return_value=Mock(server_port=12345)),
            patch("lookup.popup.Lookup.start"),
            patch("lookup.popup.subprocess.Popen") as process,
        ):
            process.return_value.wait.return_value = 0
            process.return_value.poll.return_value = 0
            self.assertEqual(app.main(), 0)
        self.assertEqual(
            read.call_args.args[0], ["clipboard-helper", "argument with spaces"]
        )
        argv = process.call_args.args[0]
        self.assertIn("--width=600", argv)
        self.assertIn("--height=800", argv)
        self.assertIn("--title=Lookup: hello", argv)
        self.assertIn("--mouse", argv)
        self.assertIn("--close-on-unfocus", argv)
        self.assertEqual(argv[-2:], ["--undecorated", "--text=literal $(text)"])

    def testProvidersOverrideConfig(self):
        self.config.write_text('providers = ["dictionary"]')
        with (
            patch.object(sys, "argv", ["lookup", "--providers", "wikipedia"]),
            patch.object(app, "readSelection", return_value="query"),
            patch.object(app, "loadProvider") as load,
            patch.object(app, "showPopup"),
        ):
            self.assertEqual(app.main(), 0)
        load.assert_called_once_with("wikipedia", {})

    def testProvidersArguments(self):
        parser = app.makeParser()
        self.assertEqual(
            parser.parse_args(["--providers", "dictionary", "wikipedia"]).providers,
            ["dictionary", "wikipedia"],
        )
        for arguments in [
            ["--providers"],
            ["--providers", "unknown"],
            ["--providers", "dictionary", "--test-source", "wikipedia"],
        ]:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parser.parse_args(arguments)
                self.assertEqual(error.exception.code, 2)

    def testRawJsonBypassesParserAndPopup(self):
        with (
            patch.object(sys, "argv", ["lookup", "--test-source", "wikipedia"]),
            patch.object(app, "readSelection", return_value="query"),
            patch(
                "lookup.providers.wikipedia.Provider.fetchJson",
                return_value={"raw": ["café", None]},
            ),
            patch(
                "lookup.providers.wikipedia.Provider.parseResult",
                side_effect=AssertionError("parser called"),
            ),
            patch.object(app, "showPopup") as popup,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(app.main(), 0)
        self.assertEqual(
            output.getvalue(), '{\n  "raw": [\n    "café",\n    null\n  ]\n}\n'
        )
        popup.assert_not_called()

    def testErrorsBeforeClipboard(self):
        self.config.write_text("[provider.wikipedia]\ntimeout = -1")
        with (
            patch.object(sys, "argv", ["lookup"]),
            patch.object(app, "readSelection") as read,
            patch.object(app.subprocess, "run"),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(app.main(), 1)
        read.assert_not_called()

    def testConfigFlagsRejected(self):
        flags = [
            "--sources",
            "--primary-paste",
            "--selection-max-length",
            "--web-search-url",
            "--window-name",
            "--name",
            "--window-class",
            "--class",
            "--width",
            "--height",
            "--position",
            "--mouse",
            "--close-on-unfocus",
            "--no-close-on-unfocus",
            "--additional-yad-flags",
        ]
        for flag in flags:
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    app.makeParser().parse_args([flag])
                self.assertEqual(error.exception.code, 2)

    def testSelection(self):
        self.assertEqual(
            app.normaliseSelection('  a "quoted"\n path\\name', 100),
            'a "quoted" path\\name',
        )
        with (
            patch.object(app.subprocess, "run", return_value=Mock(stdout=" ")),
            self.assertRaises(app.SourceError),
        ):
            app.readSelection(["clip"], 100)
