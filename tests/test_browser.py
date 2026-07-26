"""Render the pages in a real browser and assert what a human would see.

**Opt-in.** Skips unless Playwright and a browser are installed:

    pip install -e ".[browser]" && playwright install chromium
    pytest tests/test_browser.py

Not in the default suite on purpose. It needs a ~150 MB browser download and
runs in seconds rather than milliseconds, and the rest of the suite is
network-free and fast enough to run on every save. CI keeps that property; this
is what you run before shipping a frontend change.

It exists because the web app was once shipped without anyone rendering it. The
HTML parsed, every id resolved, every endpoint returned 200 — and the dashboard
still laid out at 13,171px tall with rows 1,571px high, because a `<pre>` full of
thesis prose had no line clamp. No amount of parsing finds that. A browser finds
it immediately.
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import pytest

from alpha_engine.web.api import ApiConfig, ApiState
from alpha_engine.web.server import AppHandler

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="install with: pip install -e '.[browser]'"
)

VIEWPORTS = [("desktop", 1440, 900), ("laptop", 1024, 768), ("mobile", 390, 844)]
PAGES = ["/", "/dashboard", "/terminal"]

#: An element wider than the viewport is only a bug when nothing between it and
#: the root can scroll. A wide table inside `overflow-x: auto` is a deliberate,
#: usable pattern, and flagging it is noise that hides real breakage.
OVERFLOW_PROBE = """
() => {
  const problems = [];
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) {
    problems.push(`page scrolls horizontally: ${de.scrollWidth}px in ${de.clientWidth}px`);
  }
  const vw = de.clientWidth;
  document.querySelectorAll('*').forEach((el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || r.width === 0) return;
    if (r.right <= vw + 1) return;
    for (let a = el.parentElement; a; a = a.parentElement) {
      const ov = getComputedStyle(a).overflowX;
      if (ov === 'auto' || ov === 'scroll') return;
    }
    const id = el.id ? '#' + el.id : el.tagName;
    problems.push(`overflows the viewport by ${Math.round(r.right - vw)}px: ${id}`);
  });
  return [...new Set(problems)].slice(0, 8);
}
"""

SCROLL_THROUGH = """
async () => {
  for (let y = 0; y < document.body.scrollHeight; y += 400) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 40));
  }
  window.scrollTo(0, 0);
}
"""


@pytest.fixture(scope="module")
def server():
    AppHandler.state = ApiState(config=ApiConfig(rate_limit_per_min=0))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    AppHandler.state = ApiState()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - browser binary not downloaded
            pytest.skip(f"chromium unavailable ({e}); run: playwright install chromium")
        yield b
        b.close()


def _open(browser, server, path, width, height, theme="dark"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"JS ERROR: {e}"))
    page.on(
        "console",
        lambda m: errors.append(f"CONSOLE {m.type}: {m.text}") if m.type == "error" else None,
    )
    page.on("requestfailed", lambda r: errors.append(f"REQUEST FAILED: {r.url}"))
    page.on(
        "response",
        lambda r: errors.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 else None,
    )
    page.goto(server + path, wait_until="networkidle")
    page.wait_for_timeout(500)
    # Cards below the fold are revealed by an IntersectionObserver, so a check
    # that never scrolls inspects a mostly-empty document.
    page.evaluate(SCROLL_THROUGH)
    page.wait_for_timeout(400)
    return ctx, page, errors


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize(("name", "width", "height"), VIEWPORTS)
def test_page_renders_without_errors_or_overflow(browser, server, path, name, width, height):
    ctx, page, errors = _open(browser, server, path, width, height)
    try:
        problems = errors + page.evaluate(OVERFLOW_PROBE)
        assert not problems, f"{path} at {name} ({width}x{height}): {problems}"
    finally:
        ctx.close()


def test_the_signal_feed_stays_readable(browser, server):
    """The regression that a 13,171px page was made of: an unclamped `<pre>` of
    thesis prose gave rows 1,571px tall — one signal per screenful."""
    ctx, page, _ = _open(browser, server, "/dashboard", 1024, 768)
    try:
        tallest = page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('#signals-table tbody tr')];
                return rows.length ? Math.max(...rows.map(r => r.getBoundingClientRect().height)) : 0;
            }"""
        )
        assert tallest < 400, f"a feed row is {tallest}px tall; the thesis clamp is gone"
    finally:
        ctx.close()


def test_the_composer_placeholder_is_not_clipped(browser, server):
    """It used to carry the keyboard hint too, which wrapped to two lines and was
    cut off mid-word on a 390px screen."""
    for name, width, height in VIEWPORTS:
        ctx, page, _ = _open(browser, server, "/terminal", width, height)
        try:
            clipped = page.evaluate(
                """() => { const t = document.getElementById('input');
                           return t.scrollHeight > t.clientHeight + 1; }"""
            )
            assert not clipped, f"the composer placeholder is clipped at {name} ({width}px)"
        finally:
            ctx.close()


def test_below_the_fold_content_becomes_visible(browser, server):
    """`.card.reveal` starts at opacity 0. If the observer ever stops firing, the
    dashboard is a header and a lot of white space."""
    ctx, page, _ = _open(browser, server, "/dashboard", 1440, 900)
    try:
        hidden = page.evaluate(
            """() => [...document.querySelectorAll('.card.reveal')]
                 .filter(c => getComputedStyle(c).opacity === '0')
                 .map(c => (c.querySelector('.label')?.textContent || '').trim().slice(0, 30))
                 .filter(label => label && label !== 'Signal History')"""
        )
        assert not hidden, f"cards never became visible after scrolling: {hidden}"
    finally:
        ctx.close()


def test_both_themes_keep_text_visible(browser, server):
    for theme in ("dark", "light"):
        ctx, page, _ = _open(browser, server, "/dashboard", 1440, 900, theme=theme)
        try:
            invisible = page.evaluate(
                """() => [...document.querySelectorAll('h1,h2,p,td,th,button,a,label')]
                     .filter(el => el.textContent.trim())
                     .filter(el => { const cs = getComputedStyle(el);
                        return cs.color === cs.backgroundColor
                            && cs.backgroundColor !== 'rgba(0, 0, 0, 0)'; })
                     .map(el => el.tagName)"""
            )
            assert not invisible, f"{theme}: text the same colour as its background: {invisible}"
        finally:
            ctx.close()
