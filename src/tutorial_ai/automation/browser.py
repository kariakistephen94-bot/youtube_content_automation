"""BrowserController: show - and actually use - the running application in a real Chrome window.

Playwright (Google Chrome channel) drives the page while the *real* pointer travels there with
Quartz mouse events, so the viewer sees the cursor move to a button before it is clicked. Built
for API docs such as FastAPI's Swagger UI (/docs): open an endpoint, click "Try it out", fill in
the parameters (including file uploads, without any OS file dialog), execute, and show the
response. Chrome runs on its own profile under ~/.tutorial-ai, never the user's own browser.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from tutorial_ai.automation import macos
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.browser")

APP_NAME = "Google Chrome"  # the process name macOS reports (x11.py maps Linux's "chrome" to it)
IN_LINUX_SANDBOX = sys.platform.startswith("linux")
PROFILE_DIR = Path("~/.tutorial-ai/chrome").expanduser()
PAGE_ZOOM = 1.25  # readable on a 1080p video
PAGE_LOAD_TIMEOUT = 20.0
RESPONSE_TIMEOUT = 30.0
RELOAD_GRACE_SECONDS = 10.0  # a dev server with --reload is still restarting after a save
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

PLAYWRIGHT_HELP = (
    "Browser automation needs the playwright package (run `uv sync` in tutorial-ai) and Google "
    "Chrome installed in /Applications."
)

# Swagger UI selectors (FastAPI ships Swagger UI 5). A form field or parameter is a table row
# tagged with the parameter name; the request body of a JSON endpoint is one textarea.
_PARAM_ROW = 'tr[data-param-name="{name}"], tr[data-property-name="{name}"]'
_RESPONSE = ".responses-wrapper .live-responses-table"


class BrowserError(macos.AutomationError):
    """The browser could not show what the script asked for."""


class BrowserController:
    def __init__(self, samples_dir: Path, profile_dir: Path = PROFILE_DIR, headless: bool = False) -> None:
        self._samples_dir = samples_dir
        self._profile_dir = profile_dir
        self._headless = headless
        self._playwright = None
        self._context = None
        self._page = None
        self._mouse: macos.Mouse | None = None
        self.current_url: str | None = None

    # --- lifecycle --------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    def open(self, url: str) -> bool:
        """Show `url` in front. Returns True if the page was (re)loaded, False if it was already
        showing - in which case it may still display the app as it was before the last edit."""
        if not self.is_open or self.current_url != url:
            self.navigate(url)
            return True
        self.bring_to_front()
        return False

    def refresh(self) -> None:
        """Reload the page (the docs of a reloaded dev server only update on reload)."""
        page = self._ensure_page()
        try:
            page.reload(wait_until="load", timeout=PAGE_LOAD_TIMEOUT * 1000)
        except Exception as exc:
            raise BrowserError(f"The browser could not reload {self.current_url}: {_short_error(exc)}") from exc
        self._zoom()
        time.sleep(0.4)

    def navigate(self, url: str) -> None:
        page = self._ensure_page()
        log.info("Browser: opening %s", url)
        try:
            page.goto(url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT * 1000)
        except Exception as exc:
            raise BrowserError(f"The browser could not load {url}: {_short_error(exc)}") from exc
        self.current_url = url
        self._zoom()
        self.bring_to_front()
        time.sleep(0.6)

    def close(self) -> None:
        for closer in (lambda: self._context and self._context.close(),
                       lambda: self._playwright and self._playwright.stop()):
            try:
                closer()
            except Exception:  # already gone
                pass
        self._page = self._context = self._playwright = None
        self.current_url = None
        self._quit_profile_instance()

    def bring_to_front(self) -> None:
        if self._headless or not self.is_open:
            return
        pid = self._chrome_pid()
        if pid is not None:
            macos.activate_pid(pid)
            try:
                macos.wait_for_frontmost(APP_NAME, timeout=5.0, pid=pid)
            except macos.AutomationError as exc:
                log.warning("Browser: %s", exc)
        try:
            self._page.bring_to_front()
        except Exception:
            pass

    # --- what the viewer sees ---------------------------------------------------------------

    def try_endpoint(self, method: str, path: str, params: dict[str, str] | None = None,
                     body: str | None = None, sample_files: dict[str, str] | None = None,
                     refresh: bool = False) -> str:
        """In Swagger UI: expand `METHOD path`, Try it out, fill the fields, Execute, show the
        response. Returns the HTTP status shown. `refresh=True` reloads the page first (it was
        already open, so it may show the app as it was before the latest edit)."""
        method = method.lower()
        if refresh:
            self.refresh()
        block = self._operation(method, path)
        block.scroll_into_view_if_needed()
        time.sleep(0.5)
        if "is-open" not in (block.get_attribute("class") or ""):
            self._click(block.locator(".opblock-summary").first,
                        done=lambda: "is-open" in (block.get_attribute("class") or ""))
        time.sleep(0.9)  # let the viewer read the endpoint description

        try_out = block.locator("button.try-out__btn")
        if try_out.count() and "cancel" not in try_out.first.inner_text().lower():
            self._click(try_out.first, done=lambda: block.locator("button.execute").count() > 0)
            time.sleep(0.6)

        for name, value in (params or {}).items():
            self._fill_parameter(block, name, value, sample_files or {})
        if body is not None:
            self._fill_body(block, body)

        execute = block.locator("button.execute").first
        if execute.count() == 0:
            raise BrowserError(f"{method.upper()} {path}: Swagger UI shows no Execute button (is Try it out available?)")
        self._click(execute, done=lambda: block.locator(_RESPONSE).count() > 0,
                    settle=4.0, timeout=RESPONSE_TIMEOUT)
        response = block.locator(_RESPONSE).first
        response.scroll_into_view_if_needed()
        status = ""
        try:  # the body row, not the "Code" column header
            text = response.locator("tr.response .response-col_status").first.inner_text(timeout=3000)
            status = text.strip().splitlines()[0] if text.strip() else ""  # "400\nUndocumented" -> "400"
        except Exception:
            pass
        log.info("Browser: %s %s -> HTTP %s", method.upper(), path, status or "?")
        time.sleep(2.5)  # let the viewer read the response
        return status

    def click(self, selector: str) -> None:
        page = self._ensure_page()
        self._click(page.locator(selector).first)

    def wait_for(self, selector: str | None, timeout: float) -> None:
        page = self._ensure_page()
        if selector:
            page.wait_for_selector(selector, timeout=timeout * 1000)
        else:
            page.wait_for_load_state("load", timeout=timeout * 1000)

    def scroll(self, direction: str, amount: int) -> None:
        page = self._ensure_page()
        for _ in range(amount):
            page.mouse.wheel(0, 120 if direction == "down" else -120)
            time.sleep(0.08)

    # --- Swagger UI helpers -----------------------------------------------------------------

    def _operation(self, method: str, path: str):
        """The Swagger UI block for `METHOD path`. A missing route is retried with page reloads
        for a grace period: right after a save, `fastapi dev --reload` may still serve the old app."""
        page = self._ensure_page()
        deadline = time.monotonic() + RELOAD_GRACE_SECONDS
        while True:
            try:
                page.wait_for_selector(".swagger-ui .opblock", timeout=PAGE_LOAD_TIMEOUT * 1000)
            except Exception as exc:
                raise BrowserError(f"{self.current_url} does not look like a Swagger UI page: {_short_error(exc)}") from exc
            block = page.locator(f".opblock.opblock-{method}").filter(
                has=page.locator(f'.opblock-summary-path[data-path="{path}"]')
            )
            if block.count() > 0:
                return block.first
            if time.monotonic() >= deadline:
                break
            log.info("Browser: %s %s is not in the docs yet; reloading (server restarting?)", method.upper(), path)
            time.sleep(1.5)
            self.refresh()
        shown = []
        for op in page.locator(".opblock").all():
            m = op.locator(".opblock-summary-method").first.inner_text().strip()
            p = op.locator(".opblock-summary-path").first.get_attribute("data-path") or ""
            shown.append(f"{m} {p}")
        raise BrowserError(
            f"{method.upper()} {path} is not in the docs at {self.current_url}. "
            f"Endpoints shown: {', '.join(shown) or 'none'}"
        )

    def _fill_parameter(self, block, name: str, value: str, sample_files: dict[str, str]) -> None:
        row = block.locator(_PARAM_ROW.format(name=name)).first
        if row.count() == 0:
            raise BrowserError(f"the docs show no parameter or form field named {name!r} for this endpoint")
        row.scroll_into_view_if_needed()
        if value.startswith("@"):
            sample = self._sample_file(value[1:], sample_files)
            file_input = row.locator('input[type="file"]').first
            if file_input.count() == 0:
                raise BrowserError(f"field {name!r} is not a file upload, but {value!r} names a sample file")
            file_input.set_input_files(str(sample))  # no OS file dialog
            log.info("Browser: attached %s to %s", sample.name, name)
            time.sleep(0.9)
            return
        select = row.locator("select")
        if select.count():
            select.first.select_option(value)
            return
        field = row.locator("textarea, input").first
        if field.count() == 0:
            raise BrowserError(f"field {name!r} has no input box in the docs")
        self._click(field)
        field.fill("")
        field.press_sequentially(value, delay=45)
        time.sleep(0.3)

    def _fill_body(self, block, body: str) -> None:
        textarea = block.locator(".body-param textarea, textarea.body-param__text").first
        if textarea.count() == 0:
            raise BrowserError("this endpoint has no request body box in the docs")
        self._click(textarea)
        textarea.fill("")
        textarea.press_sequentially(body, delay=12)
        time.sleep(0.3)

    def _sample_file(self, name: str, sample_files: dict[str, str]) -> Path:
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise BrowserError(f"invalid sample file name {name!r}")
        target = self._samples_dir / name
        if name in sample_files:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sample_files[name], encoding="utf-8")
        elif not target.exists():
            raise BrowserError(f"sample file {name!r} was not declared in sample_files")
        return target

    # --- clicking with the real pointer -----------------------------------------------------

    def _click(self, locator, done: Callable[[], bool] | None = None, settle: float = 1.5,
               timeout: float = 5.0) -> None:
        """Move the real cursor onto the element and click. If the page does not react, click
        through Playwright instead (the fallback is invisible to the viewer but always lands)."""
        locator.scroll_into_view_if_needed()
        time.sleep(0.15)
        box = locator.bounding_box()
        if box and self._mouse is not None:
            x, y = self._screen_point(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self._mouse.move_to(x, y)
            time.sleep(0.12)
            self._mouse.click(x, y)
            if done is None or _wait(done, settle):
                return
            log.warning("Browser: the click at (%.0f, %.0f) had no effect; clicking through Playwright", x, y)
        locator.click()
        if done is not None and not _wait(done, timeout):
            raise BrowserError("the page did not react to the click")

    def _screen_point(self, css_x: float, css_y: float) -> tuple[float, float]:
        """Screen coordinates (points) of a page position (CSS pixels)."""
        m = self._page.evaluate(
            "() => ({sx: window.screenX, sy: window.screenY, iw: window.innerWidth, ih: window.innerHeight,"
            " ow: window.outerWidth, oh: window.outerHeight, dpr: window.devicePixelRatio})"
        )
        points_per_px = m["dpr"] / macos.backing_scale()
        x = m["sx"] + (m["ow"] - m["iw"]) / 2 + css_x * points_per_px
        y = m["sy"] + (m["oh"] - m["ih"]) + css_y * points_per_px
        return x, y

    # --- Chrome process -----------------------------------------------------------------------

    def _ensure_page(self):
        if self.is_open:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError(PLAYWRIGHT_HELP) from exc
        self._quit_profile_instance()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        width, height = macos.main_display_size() if not self._headless else (1440, 900)
        args = [
            "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble",
            "--disable-session-crashed-bubble", "--disable-features=TranslateUI",
            "--window-position=0,0", f"--window-size={int(width)},{int(height)}",
        ]
        if IN_LINUX_SANDBOX:
            args += ["--test-type", "--disable-gpu", "--password-store=basic"]
        log.info("Browser: starting Google Chrome (tutorial profile)")
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self._profile_dir), channel="chrome", headless=self._headless, no_viewport=True,
                args=args, ignore_default_args=["--enable-automation"],
                # macOS: keep Chrome's sandbox, otherwise it shows a yellow "--no-sandbox" warning bar.
                # Linux (the recording sandbox container): Chrome's namespace sandbox is blocked by
                # the container's seccomp profile; the container itself is the isolation, and
                # --test-type hides the warning bar.
                chromium_sandbox=not IN_LINUX_SANDBOX,
            )
        except Exception as exc:
            self.close()
            raise BrowserError(f"Google Chrome could not be started: {_short_error(exc)}\n{PLAYWRIGHT_HELP}") from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(PAGE_LOAD_TIMEOUT * 1000)
        if not self._headless:
            self._mouse = macos.Mouse()
        return self._page

    def _zoom(self) -> None:
        try:
            self._page.evaluate(f"document.documentElement.style.zoom = '{PAGE_ZOOM}'")
        except Exception:
            pass

    def _profile_pids(self) -> set[int]:
        result = subprocess.run(["pgrep", "-f", f"user-data-dir={self._profile_dir}"],
                                capture_output=True, text=True)
        return {int(p) for p in result.stdout.split() if p.isdigit()} - {os.getpid()}

    def _chrome_pid(self) -> int | None:
        """Main process of the Chrome running on the tutorial profile."""
        pids = self._profile_pids()
        if not pids:
            return None
        parents = {}
        for pid in pids:
            info = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout
            parents[pid] = int(info.strip() or 0)
        roots = [pid for pid, ppid in parents.items() if ppid not in pids]
        return min(roots) if roots else min(pids)

    def _quit_profile_instance(self) -> None:
        """Kill a Chrome left on the tutorial profile (never the user's own Chrome)."""
        pids = self._profile_pids()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if pids:
            log.info("Closed the previous tutorial Chrome instance (%d processes)", len(pids))
            time.sleep(1.0)


def _wait(done: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if done():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _short_error(exc: BaseException) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__


def describe_request(method: str, path: str, params: dict[str, str] | None, body: str | None) -> str:
    """One line for logs and the UI: 'POST /upload (file=@hello.txt)'."""
    parts = [f"{k}={v}" for k, v in (params or {}).items()]
    if body:
        try:
            parts.append("body=" + json.dumps(json.loads(body), separators=(",", ":"))[:60])
        except ValueError:
            parts.append("body=" + body[:60])
    return f"{method.upper()} {path}" + (f" ({', '.join(parts)})" if parts else "")
