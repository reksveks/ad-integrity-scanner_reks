"""Render a page and collect render-tier signals.

Two planes:
  * in-page JS (page.evaluate): geometry, viewability, CWV, consent, prebid, video
  * CDP (Network/Performance): authoritative bytes, requests, cookies, CPU

Sequence: goto -> settle -> SETUP_JS (tag+observe ads) -> dwell+scroll (let ads
load/refresh and viewability accrue) -> COLLECT_JS + CDP read.
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

import statistics

from app.render.browser import RenderPool
from app.render.cmp import accept_cmp
from app.render.instrument import COLLECT_JS, SETUP_JS, STICKY_PROBE_JS
from app.render.netaccount import NetworkAccountant, count_third_party_cookies
from app.ssrf import SSRFError, assert_public_host
from app.logging_config import get_logger
from app.parsers.requests import VENDOR_URL_PREFIXES, VENDOR_URL_CONTAINS, SSP_BID_REQUEST_PREFIXES, parse_captured_response, parse_captured_request, pull_admantx, pull_doubleverify

_SETTLE_MS = 1500  # let initial ads load before tagging
_CF_CHALLENGE_TITLES = {"just a moment", "verifying you are human", "please wait", "checking your browser"}
log = get_logger("render.net")


async def _wait_for_cf_challenge(page, timeout_s: int = 20) -> None:
    """If Cloudflare's JS challenge is detected, wait up to timeout_s for it to clear."""
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            title = (await page.title()).lower()
        except PWError:
            break
        if not any(t in title for t in _CF_CHALLENGE_TITLES):
            break
        log.debug("cf_challenge detected (title=%r), waiting...", title)
        await asyncio.sleep(1.5)


async def _auto_scroll(page) -> None:
    try:
        await page.evaluate(
            "() => new Promise(r => { window.scrollTo(0, document.body.scrollHeight); setTimeout(r, 500); })"
        )
        await page.evaluate("() => window.scrollTo(0, 0)")
    except PWError:
        pass


def _task_duration(metrics: list[dict]) -> float | None:
    for m in metrics:
        if m.get("name") == "TaskDuration":
            return round(float(m.get("value") or 0), 3)
    return None


async def _safe_body(resp) -> str:
    """Read a Playwright response body safely, handling binary and encoding errors."""
    try:
        body = await resp.body()
        ctype = resp.headers.get("content-type", "").lower()
        if "application/json" in ctype or "text/" in ctype:
            return body.decode("utf-8", errors="replace")
        return f"<binary {len(body)} bytes: {body[:20].hex()}>"
    except Exception as e:
        return f"<error reading body: {e}>"


async def render_page(
    pool: RenderPool, url: str, *, dwell_ms: int = 8000, nav_timeout_ms: int = 25000,
    accept_consent: bool = False,
    capture_responses: list[str] | None = None,
    admantx_token: str = "",
    admantx_max_attempts: int = 5,
    doubleverify_token: str = "",
    doubleverify_max_attempts: int = 5,
) -> dict[str, Any]:
    parts = urlsplit(url)
    try:
        await assert_public_host(parts.hostname, parts.port or 443)
    except SSRFError as e:
        return {"ok": False, "error": f"SSRFError: {e}"}

    async with pool.page() as page:
        page.set_default_timeout(nav_timeout_ms)
        # Playwright-level request/response listeners — DEBUG logging of live traffic.
        async def _log_request(req) -> None:
            if req.method == "POST":
                try:
                    body = req.post_data or ""
                except Exception:
                    body = "<binary>"
                log.debug(">> POST %s body=%s", req.url, body[:300])
            else:
                log.debug(">> %s %s", req.method, req.url)
        page.on("request", _log_request)
        page.on("response", lambda resp: log.debug("<< %s %s", resp.status, resp.url))
        # Always-on vendor response capture (DV, Illuma, etc.) + any caller-supplied prefixes.
        _all_prefixes = set(VENDOR_URL_PREFIXES) | set(capture_responses or [])
        _all_contains = set(VENDOR_URL_CONTAINS)
        _captured: list[dict] = []
        async def _capture_response(resp) -> None:
            url = resp.url
            if any(url.startswith(p) for p in _all_prefixes) or \
               any(s in url for s in _all_contains):
                body = await _safe_body(resp)
                _captured.append({"url": url, "status": resp.status, "body": body})
        page.on("response", _capture_response)
        # SSP bid-request capture — POST bodies sent TO prebid/OpenRTB endpoints.
        _captured_requests: list[dict] = []
        async def _capture_request(req) -> None:
            if req.method != "POST":
                return
            req_url = req.url
            vendor = next(
                (v for p, v in SSP_BID_REQUEST_PREFIXES.items() if req_url.startswith(p)),
                None,
            )
            if vendor is not None:
                try:
                    body = req.post_data or ""
                except Exception:
                    body = "<binary>"
                _captured_requests.append({"url": req_url, "vendor": vendor, "body": body})
                log.debug("captured bid request vendor=%s url=%s body_len=%d", vendor, req_url, len(body))
        page.on("request", _capture_request)
        # CDP plane for authoritative network/cookie/cpu accounting.
        net = NetworkAccountant()
        cdp = None
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Network.enable")
            await cdp.send("Performance.enable")
            cdp.on("Network.responseReceived", net.on_response)
            cdp.on("Network.loadingFinished", net.on_finished)
        except PWError:
            cdp = None

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        except (PWTimeout, PWError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        await _wait_for_cf_challenge(page)
        status = resp.status if resp else None
        await asyncio.sleep(_SETTLE_MS / 1000)
        if accept_consent:
            await accept_cmp(page)
            await asyncio.sleep(0.5)   # brief pause for ad stack to initialise post-consent
        try:
            await page.evaluate(SETUP_JS)          # tag ads + attach observers
        except PWError:
            pass
        await _auto_scroll(page)
        # Run AdmantX pull concurrently with the dwell wait — adds zero extra latency.
        _admantx_task = None
        if admantx_token:
            log.info("admantx task created url=%s", url)
            _admantx_task = asyncio.create_task(
                pull_admantx(url, admantx_token, admantx_max_attempts)
            )
        else:
            log.debug("admantx skipped (no token)")
        await asyncio.sleep(dwell_ms / 1000)       # observers accrue time-in-view

        # Run DoubleVerify pull concurrently with the dwell wait — adds zero extra latency.
        _doubleverify_task = None
        if doubleverify_token:
            log.info("doubleverify task created url=%s", url)
            _doubleverify_task = asyncio.create_task(
                pull_doubleverify(url, doubleverify_token, doubleverify_max_attempts)
            )
        else:
            log.debug("doubleverify skipped (no token)")
        await asyncio.sleep(dwell_ms / 1000)       # observers accrue time-in-view

        # Behavioral sticky probe (scroll + re-read rects), then a synthetic
        # interaction so the Event-Timing observer yields an INP proxy.
        try:
            await page.evaluate(STICKY_PROBE_JS)
            # Keyboard only — a blind mouse click could follow a link and navigate
            # the page away mid-scan. Tab/keydown still yields an Event-Timing entry.
            await page.keyboard.press("Tab")
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.25)
        except PWError:
            pass

        try:
            data = await page.evaluate(COLLECT_JS)
        except PWError as e:
            return {"ok": False, "status": status, "error": f"collect: {e}"}

        final_url = page.url
        if cdp is not None:
            data["resources"] = net.summary(final_url)
            try:
                cookies = (await cdp.send("Network.getAllCookies")).get("cookies", [])
            except PWError:
                cookies = []
            data.setdefault("cmp", {})["cookie_count"] = len(cookies)
            data["cmp"]["third_party_cookie_count"] = count_third_party_cookies(cookies, final_url)
            try:
                metrics = (await cdp.send("Performance.getMetrics")).get("metrics", [])
                data["cpu"] = {"task_duration_s": _task_duration(metrics)}
            except PWError:
                data["cpu"] = {}
        else:
            data["resources"] = data.get("resources_inpage", {})

        data["ok"] = True
        data["status"] = status
        data["final_url"] = final_url
        # Expose raw SSP bid request bodies for inspection / debugging.
        if _captured_requests:
            data["bid_requests"] = _captured_requests
            log.debug("captured %d ssp bid requests", len(_captured_requests))
            parsed_bid_requests: dict[str, Any] = {}
            for entry in _captured_requests:
                vendor = entry["vendor"]
                parsed = parse_captured_request(vendor, entry["body"])
                parsed_bid_requests[vendor] = parsed
                log.debug("parsed bid request vendor=%s", vendor)
            if parsed_bid_requests:
                data["bid_request_signals"] = parsed_bid_requests
        # Parse vendor responses and store under request_signals; also expose
        # raw captures for the temp script / debugging.
        if _captured:
            data["captured_responses"] = _captured
            request_signals: dict[str, Any] = {}
            for entry in _captured:
                url = entry["url"]
                vendor = None
                for prefix, v in VENDOR_URL_PREFIXES.items():
                    if url.startswith(prefix):
                        vendor = v
                        break
                if vendor is None:
                    for substr, v in VENDOR_URL_CONTAINS.items():
                        if substr in url:
                            vendor = v
                            break
                if vendor and entry["body"] != "<unreadable>":
                    request_signals[vendor] = parse_captured_response(vendor, entry["body"])
                    log.debug("parsed vendor response vendor=%s url=%s", vendor, entry["url"])
            if request_signals:
                data["request_signals"] = request_signals
        # Resolve AdmantX pull and merge into request_signals.
        if _admantx_task is not None:
            try:
                admantx_result = await _admantx_task
            except Exception as exc:  # noqa: BLE001
                log.warning("admantx task raised: %s", exc)
                admantx_result = {"error": "task failed"}
            segments = len((admantx_result.get("semantic") or {}).get("admants") or {})
            log.info("admantx result merged segments=%d error=%s",
                     segments, admantx_result.get("error"))
            log.info("admantx result: %s", admantx_result)
            data.setdefault("request_signals", {})["admantx"] = admantx_result
        # Resolve DoubleVerify pull and merge into request_signals.
        if _doubleverify_task is not None:
            try:
                doubleverify_result = await _doubleverify_task
            except Exception as exc:  # noqa: BLE001
                log.warning("doubleverify task raised: %s", exc)
                doubleverify_result = {"error": "task failed"}
            log.info("doubleverify result merged error=%s", doubleverify_result.get("error"))
            log.info("doubleverify result: %s", doubleverify_result)
            data.setdefault("request_signals", {})["doubleverify"] = doubleverify_result
        return data


async def render_page_sampled(
    pool: RenderPool, url: str, *, dwell_ms: int = 8000, samples: int = 1,
    accept_consent: bool = False,
    capture_responses: list[str] | None = None,
    admantx_token: str = "",
    admantx_max_attempts: int = 5,
    doubleverify_token: str = "",
    doubleverify_max_attempts: int = 5,
) -> dict[str, Any]:
    """Render `samples` times and replace run-to-run-variable CLS with the median.

    CLS varies between renders (late-loading ads shift layout differently each
    time); the median of N is far more stable. Other signals come from the first
    successful render. samples<=1 is a plain single render.
    """
    base = await render_page(pool, url, dwell_ms=dwell_ms, accept_consent=accept_consent,
                             capture_responses=capture_responses,
                             admantx_token=admantx_token, admantx_max_attempts=admantx_max_attempts,
                             doubleverify_token=doubleverify_token, doubleverify_max_attempts=doubleverify_max_attempts)
    if samples <= 1 or not base.get("ok"):
        return base
    cls_vals = []
    c = (base.get("cwv") or {}).get("cls")
    if c is not None:
        cls_vals.append(c)
    for _ in range(samples - 1):
        r = await render_page(pool, url, dwell_ms=dwell_ms, accept_consent=accept_consent)
        c = (r.get("cwv") or {}).get("cls") if r.get("ok") else None
        if c is not None:
            cls_vals.append(c)
    if cls_vals:
        base.setdefault("cwv", {})["cls"] = round(statistics.median(cls_vals), 3)
        base["cwv"]["cls_samples"] = cls_vals
    return base
