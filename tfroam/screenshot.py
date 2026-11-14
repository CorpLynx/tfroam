"""Image mode: render the served page in a headless browser and save the SVG.

Port of ``screenshot.go``. The Go version drives Chrome with chromedp: it opens the
page, waits for the graph, clicks "Save Graph" (which downloads ``rover.svg``) and
moves the download to ``./rover.svg``. Playwright does the same here and is an
optional dependency (``pip install tfroam[image]``). The CLI passes ``-imagePath``
(default ``rover.svg``) as ``output`` and the address the server actually bound.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger("tfroam")

OUTPUT_FILE = "./rover.svg"
TIMEOUT_MS = 60_000
BROWSER_ENV = "ROVER_BROWSER_PATH"

_SYSTEM_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
INSTALL_HINT = (
    "Image generation needs Playwright and a Chromium browser: "
    "pip install 'tfroam[image]' && playwright install chromium "
    f"(or set {BROWSER_ENV} to a Chrome/Chromium executable)"
)


class ImageError(RuntimeError):
    pass


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def page_url(ip_port: str) -> str:
    """URL for the browser: a wildcard listen address is not connectable on every platform."""
    host, _, port = ip_port.rpartition(":")
    host = host.strip("[]")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def _launch(playwright: Any) -> Any:
    """Launch Chromium: explicit path, then Playwright's own browser, then a system one."""
    explicit = os.environ.get(BROWSER_ENV)
    if explicit:
        candidates: list[str | None] = [explicit]
    else:
        candidates = [None, *(shutil.which(name) for name in _SYSTEM_BROWSERS)]  # None = Playwright's own
    errors: list[str] = []
    for path in dict.fromkeys(candidates):  # unique, in order
        try:
            kwargs: dict[str, Any] = {"headless": True}
            if path:
                kwargs["executable_path"] = path
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    kwargs["args"] = ["--no-sandbox"]  # Docker runs as root
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:  # playwright raises its own Error type
            errors.append(f"{path or 'playwright default'}: {str(exc).splitlines()[0]}")
    raise ImageError(f"Could not start a browser. {INSTALL_HINT}\n" + "\n".join(errors))


def screenshot(ip_port: str, output: str | os.PathLike[str] = OUTPUT_FILE, timeout_ms: int = TIMEOUT_MS) -> None:
    """Open the page at ``ip_port``, click "Save Graph" and store the downloaded SVG at ``output``."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ImageError(INSTALL_HINT) from exc

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(accept_downloads=True)
            page.set_default_timeout(timeout_ms)
            page.goto(page_url(ip_port))
            page.wait_for_selector("#cytoscape-div", state="visible")  # graph is visible
            with page.expect_download() as download_info:
                page.click("#saveGraph")
            download_info.value.save_as(str(Path(output)))
        except Exception as exc:
            raise ImageError(f"{exc}") from exc
        finally:
            browser.close()

    log.info("Image generation complete.")
