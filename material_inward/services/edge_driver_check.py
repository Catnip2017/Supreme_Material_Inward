"""
services/edge_driver_check.py — Self-healing Edge/msedgedriver version guard.

Why this exists:
  taxpayer_search_bot.py and einvoice_bot.py both call webdriver.Edge(options=...)
  with no pinned driver path — they rely on Selenium Manager to auto-resolve a
  matching msedgedriver on every run. Selenium Manager caches whatever driver
  build it resolves under %USERPROFILE%\\.cache\\selenium\\msedgedriver\\win64\\.

  On this server, Edge's own auto-updater is not installed (no
  MicrosoftEdgeUpdateTaskMachineCore/UA scheduled tasks exist), so the browser
  can silently get stuck on an old build while Selenium Manager keeps fetching
  newer driver metadata off the internet independently on each run — stacking
  up multiple mismatched cached driver folders over time (we found three:
  118, 128, 137, none matching the installed browser's 96). The mismatch
  surfaces as `SessionNotCreatedException: Chrome instance exited` with no
  clear indication of *why* — just a driver stack trace.

  Call ensure_matching_edge_driver(logger) once at the top of each bot's
  __init__, before webdriver.Edge() is created. It compares the installed
  Edge build against every cached driver folder; if none match, it clears
  the entire cache so Selenium Manager is forced to re-resolve a fresh,
  correctly-matched driver on that same run instead of silently handing back
  a stale one — and logs a clear, actionable warning either way, so a future
  mismatch shows up immediately in the bot's own log instead of requiring the
  same manual PowerShell diagnosis session done on 2026-07-XX.
"""

import os
import glob
import shutil
import subprocess

EDGE_EXE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SELENIUM_DRIVER_CACHE = os.path.join(
    os.path.expanduser("~"), ".cache", "selenium", "msedgedriver", "win64"
)


def _get_edge_version() -> str:
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f'(Get-Item "{EDGE_EXE_PATH}").VersionInfo.ProductVersion'],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _cached_driver_versions() -> list:
    if not os.path.isdir(SELENIUM_DRIVER_CACHE):
        return []
    return [
        os.path.basename(p)
        for p in glob.glob(os.path.join(SELENIUM_DRIVER_CACHE, "*"))
        if os.path.isdir(p)
    ]


def ensure_matching_edge_driver(logger) -> None:
    """
    Compare installed Edge version against Selenium's cached driver builds.
    If none match, clear the cache so Selenium Manager re-resolves a fresh,
    correctly-matched driver on this run instead of reusing a stale one.
    Non-fatal on any failure — logs and lets the caller proceed either way,
    since webdriver.Edge() will surface its own clear error if this doesn't
    catch the mismatch.
    """
    edge_version = _get_edge_version()
    if not edge_version:
        logger.warning("[EdgeDriverCheck] Could not determine installed Edge version — skipping check.")
        return

    cached = _cached_driver_versions()
    if not cached:
        logger.info(
            f"[EdgeDriverCheck] Edge is {edge_version}, no cached driver yet — "
            "Selenium Manager will resolve one fresh on this run."
        )
        return

    if edge_version in cached:
        logger.info(f"[EdgeDriverCheck] Edge {edge_version} matches a cached driver — OK.")
        return

    logger.warning(
        f"[EdgeDriverCheck] Edge is {edge_version} but cached driver(s) are "
        f"{cached} — none match. Clearing stale cache so Selenium Manager "
        "re-resolves a matching driver on this run."
    )
    try:
        shutil.rmtree(SELENIUM_DRIVER_CACHE)
        logger.info("[EdgeDriverCheck] Stale driver cache cleared.")
    except Exception as e:
        logger.error(
            f"[EdgeDriverCheck] Could not clear stale driver cache: {e}. "
            "webdriver.Edge() may fail with SessionNotCreatedException — "
            f"if so, manually delete {SELENIUM_DRIVER_CACHE} and retry."
        )
