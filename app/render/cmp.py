"""Cookie / CMP banner auto-acceptance for the render tier.

Tries to accept consent dialogs so the ad stack loads fully before signals are
collected.  Three strategies in order:
  1. Known vendor CSS selectors (OneTrust, Usercentrics, Didomi, Quantcast, Cookiebot…)
  2. Text-match on visible <button> labels ("Accept All", "I Agree", …)
  3. Same two strategies applied inside every child iframe (TCF consent frames)

All failures are silently swallowed — if acceptance doesn't work the render
continues without consent, which is still a valid signal state.
"""
from __future__ import annotations

from playwright.async_api import Page

from app.logging_config import get_logger

log = get_logger("render.cmp")

_COMMON_SELECTORS = [
    "#onetrust-accept-btn-handler",              # OneTrust
    "button#onetrust-accept-btn-handler",
    "button[title='Accept All Cookies']",
    "button[title='Accept']",
    "button[aria-label='Accept']",
    "button[aria-label*='Accept all']",
    "button[aria-label*='accept all']",
    "button[aria-label*='agree']",
    "[data-testid='uc-accept-all']",             # Usercentrics
    "button[data-testid='uc-accept-all']",
    "button[data-qa='consent-accept-all']",      # Didomi
    "button[data-qa='accept-all']",
    "button#qc-cmp2-ui-accept-all",              # Quantcast
    "button#qc-cmp2-ui-button--primary",
    "button#cmpbntyestxt",                       # Cookiebot
    ".sp-continue-button",                       # SourcePoint
    "button[data-tracking='cmp-accept-all']",    # Piano/TinyPass
    ".css-accept-all-button",
]

_COMMON_TEXT = [
    "Accept All", "Accept all", "Accept All Cookies",
    "Accept", "Agree", "I Agree", "I agree",
    "Allow All", "Allow all", "OK", "Ok",
    "Continue", "Got it", "Yes, I agree", "I accept",
    "Consent", "Accept cookies", "Accept & Close",
]

import re

_ACCEPT_PATTERNS = [
    # English
    r"\baccept\b",
    r"\bagree\b",
    r"\ballow\b",
    r"\bconsent\b",
    r"\bi accept\b",
    r"\bok\b",
    r"\bcontinue\b",

    # French
    r"tout accepter",
    r"\baccepter\b",
    r"j'accepte",
    r"autoriser",

    # German
    r"alle akzeptieren",
    r"\bakzeptieren\b",
    r"ich stimme zu",
    r"zustimmen",
    r"einverstanden",

    # Spanish
    r"aceptar",
    r"aceptar todo",
    r"aceptar todos",
    r"estoy de acuerdo",

    # Italian
    r"accetta",
    r"accetta tutto",
    r"accetto",

    # Dutch
    r"alles accepteren",
    r"accepteren",
    r"ik ga akkoord",

    # Portuguese
    r"aceitar",
    r"aceitar tudo",
    r"concordo",

    # Nordic
    r"acceptera",
    r"acceptera alla",
    r"accepter alle",
    r"godta",
    r"godta alle",
    r"hyväksy",
    r"hyväksy kaikki",

    # Eastern Europe
    r"zaakceptuj",
    r"zaakceptuj wszystkie",
    r"prijmout",
    r"elfogad",
    r"acceptă",

    # Greek
    r"αποδοχή",
    r"συμφωνώ",

    # Turkish
    r"kabul et",
    r"tümünü kabul et",

    # Russian
    r"принять",
    r"принять все",

    # Arabic
    r"قبول",
    r"قبول الكل",
]

async def _click_selector(page_or_frame, selector: str) -> bool:
    try:
        loc = page_or_frame.locator(selector)
        if await loc.count() > 0:
            await loc.first.click(timeout=2000)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


async def _click_text(page_or_frame) -> bool:
    #
    # Try buttons first
    #
    for pattern in _ACCEPT_PATTERNS:
        try:
            loc = page_or_frame.get_by_role(
                "button",
                name=re.compile(pattern, re.IGNORECASE),
            )

            if await loc.count() > 0:
                await loc.first.click(timeout=2000)
                return True
        except Exception:
            pass

    #
    # Fallback to generic clickable elements
    #
    for pattern in _ACCEPT_PATTERNS:
        try:
            loc = page_or_frame.locator(
                f"text=/{pattern}/i"
            )

            if await loc.count() > 0:
                await loc.first.click(timeout=2000)
                return True
        except Exception:
            pass

    return False

async def accept_cmp(page: Page) -> bool:
    """Attempt to dismiss a cookie/consent banner.  Returns True if accepted."""
    # 1. Known selectors on the main frame.
    for sel in _COMMON_SELECTORS:
        if await _click_selector(page, sel):
            log.debug("cmp accepted via selector=%r", sel)
            return True

    # 2. Text-match on the main frame.
    if await _click_text(page):
        log.debug("cmp accepted via text match")
        return True

    # 3. Same strategies inside child iframes (TCF __tcfapiLocator frames, etc.)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in _COMMON_SELECTORS:
            if await _click_selector(frame, sel):
                log.debug("cmp accepted inside iframe via selector=%r", sel)
                return True
        if await _click_text(frame):
            log.debug("cmp accepted inside iframe via text match")
            return True

    log.debug("cmp: no banner found or dismissed")
    return False
