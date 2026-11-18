"""Concurrent lookups and the Yad HTML popup."""

from __future__ import annotations

import subprocess
import threading
from contextlib import suppress
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt

from .provider import Link, ProviderBase, Result, SourceError


def linkHtml(link: Link) -> str:
    """Escape a link, rendering malformed or non-HTTP(S) destinations as text."""
    title = escape(link.title)
    try:
        parts = urlsplit(link.url)
    except ValueError:
        return title
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return title
    return f'<a href="{escape(link.url, quote=True)}">{title}</a>'


def resultHtml(result: Result) -> str:
    """Render escaped result content for the popup."""
    heading = []
    if result.title:
        heading.append(f"<b>{escape(result.title)}</b>")
    if result.detail:
        heading.append(f"<i>{escape(result.detail)}</i>")
    header = f"<p>{' '.join(heading)}</p>" if heading else ""
    if result.markdown:
        # No raw HTML or remote images; Markdown-it validates link schemes.
        renderer = MarkdownIt("commonmark", {"html": False}).disable("image")
        body = f'<div class="markdown">{renderer.render(result.text)}</div>'
    else:
        body = f'<div class="plain">{escape(result.text)}</div>'
    return (
        header
        + body
        + "".join(f'<p class="src">{linkHtml(link)}</p>' for link in result.links)
    )


class Lookup:
    """Coordinate concurrent sources and their rendered results."""

    def __init__(
        self, query: str, sources: dict[str, ProviderBase], webSearchUrl: str = ""
    ):
        self.webSearchUrl = webSearchUrl
        self.query = query
        self.sources = sources
        self.results: dict[str, str] = {}
        self.lock = threading.Lock()

    def fetch(self, name: str, source: ProviderBase) -> None:
        """Publish a source result or error without blocking other sources."""
        finished = threading.Event()
        html = ""

        def collect() -> None:
            nonlocal html
            try:
                result = source.lookup(self.query)
                html = (
                    resultHtml(result)
                    if result is not None
                    else '<p class="dim">No result</p>'
                )
            except SourceError as error:
                html = f'<p class="dim">{escape(str(error))}</p>'
            except Exception as error:  # noqa: BLE001 - isolate custom sources
                html = f'<p class="dim">Source failed ({type(error).__name__})</p>'
            finally:
                finished.set()

        # A socket timeout alone cannot bound DNS, slow responses or custom parsers.
        # Workers are daemon threads; timed-out work cannot publish a late result.
        threading.Thread(target=collect, daemon=True).start()
        completed = finished.wait(source.timeout)
        with self.lock:
            self.results[name] = (
                html if completed else '<p class="dim">Source timed out</p>'
            )

    def start(self) -> None:
        """Start concurrent source lookups with independent deadlines."""
        for name, source in self.sources.items():
            # These threads cannot keep the CLI alive after the popup closes.
            # Exiting the process closes their HTTP connections; no child curl jobs remain.
            threading.Thread(
                target=self.fetch, args=(name, source), daemon=True
            ).start()

    def body(self) -> str:
        """Render the current results and a marker when all sources finish."""
        with self.lock:
            results = self.results.copy()
        fragments = []
        if self.webSearchUrl:
            search = Link(
                "Search the web",
                self.webSearchUrl.replace("{query}", quote(self.query, safe="")),
            )
            fragments.append(f'<p class="src dim">{linkHtml(search)}</p>')
        for name, source in self.sources.items():
            fragments.append(f"<h2>{escape(source.title)}</h2>")
            fragments.append(results.get(name, '<p class="dim">Loading…</p>'))
        if len(results) == len(self.sources):
            fragments.append("<!--done-->")
        return "".join(fragments)


def serve(lookup: Lookup) -> ThreadingHTTPServer:
    """Start a loopback HTTP server for the popup and live results."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                content = page
            elif self.path == "/body.html":
                content = lookup.body()
            else:
                self.send_error(404)
                return
            payload = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def showPopup(
    lookup: Lookup,
    *,
    name: str = "lookup",
    windowClass: str = "lookup",
    width: int = 520,
    height: int | None = None,
    mouse: bool = False,
    closeOnUnfocus: bool = False,
    additionalYadFlags: list[str] | None = None,
) -> None:
    """Run Yad, cleaning up the popup and server on exit."""
    server = serve(lookup)
    popup = None
    # Allow room per source, with a usable minimum and a cap for large selections.
    if height is None:
        height = min(760, max(360, 160 + 100 * len(lookup.sources)))
    try:
        popup = subprocess.Popen(
            [
                "yad",
                "--html",
                f"--uri=http://127.0.0.1:{server.server_port}/",
                "--no-buttons",
                "--escape-ok",
                f"--name={name}",
                f"--class={windowClass}",
                f"--width={width}",
                f"--height={height}",
                f"--title=Lookup: {lookup.query}",
                *(["--mouse"] if mouse else []),
                *(["--close-on-unfocus"] if closeOnUnfocus else []),
                *(additionalYadFlags or []),
            ]
        )
        lookup.start()
        status = popup.wait()
        # Yad uses 1 for Cancel and 252 for Escape/window-manager dismissal.
        if status not in (0, 1, 252):
            raise SourceError(f"Yad exited with status {status}; check terminal output")
    finally:
        if popup is not None and popup.poll() is None:
            popup.terminate()
            try:
                popup.wait(timeout=2)
            except subprocess.TimeoutExpired:
                popup.kill()
                popup.wait()
        server.shutdown()
        server.server_close()


# Embedded popup assets keep installation to one script.
# language=html
page = r"""
<!doctype html><html><head><meta charset="utf-8">
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.45 sans-serif; margin: 12px 16px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
       opacity: .6; margin: 14px 0 4px; }
  p { margin: 4px 0; }
  .plain { white-space: pre-wrap; }
  .markdown :is(h1, h2, h3, h4, h5, h6) {
    font-size: 14px; text-transform: none; letter-spacing: normal; opacity: 1;
  }
  .markdown pre { white-space: pre-wrap; overflow-wrap: anywhere; }
  .dim { opacity: .5; }
  .src { font-size: 12px; }
  a { text-decoration: none; }
</style></head><body>
<div id="c"><p class="dim">Loading…</p></div>
<script>
let last = "";
async function tick() {
  try {
    const r = await fetch("body.html", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);          // never show an error page
    const t = await r.text();
    if (t !== last) {
      const c = document.getElementById("c");
      c.innerHTML = t; last = t;
    }
    if (t.includes("<!--done-->")) return;
  } catch (e) {}
  setTimeout(tick, 250);
}
tick();
</script></body></html>
"""
