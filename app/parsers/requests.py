"""Parsers for vendor-specific HTTP request/response data captured during rendering.

Each parser receives the raw response body (string) for a matched URL and returns
a normalised dict stored under signals["render"]["request_signals"][<vendor>].
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import urllib

import httpx

_log = logging.getLogger("app.parsers.requests")

_DV_PUB_URL_PREFIX = "https://pub.doubleverify.com/dvtag/signals/bsc/pub.json"
_IAS_PUB_URL_PREFIX = "https://pixel.adsafeprotected.com/services/pub?"
_ILLUMA_PUB_URL_SUFFIX = "/illuma-api?"
_RUBICON_PB_URL_PREFIX = "https://prebid-server.rubiconproject.com/openrtb2/auction"
_IX_PB_URL_PREFIX = "https://htlb.casalemedia.com/openrtb/pbjs"
_PUB_PB_URL_PREFIX = "https://hbopenbid.pubmatic.com/translator?source=prebid-client"
_TTD_PB_URL_PREFIX = "https://direct.adsrvr.org/bid/bidder/theguardian"
_APN_PB_URL_PREFIX = "https://ib.adnxs.com/ut/v3/prebid"
_ADMANTX_PUB_URL_SUFFIX = "https://euasync01.admantx.com/admantx/service?"

# Map URL prefix -> vendor key (used by collect.py to route captured responses).
VENDOR_URL_PREFIXES: dict[str, str] = {
    _DV_PUB_URL_PREFIX: "doubleverify",
    _IAS_PUB_URL_PREFIX: "ias",
    _RUBICON_PB_URL_PREFIX: "rubicon",
    _IX_PB_URL_PREFIX: "ix",
    _PUB_PB_URL_PREFIX: "pubmatic",
    _TTD_PB_URL_PREFIX: "thetradeDesk",
    _APN_PB_URL_PREFIX: "appnexus",
}

# OpenRTB/Prebid SSP endpoints that send POST bid requests — used for request-body capture.
SSP_BID_REQUEST_PREFIXES: dict[str, str] = {
    _RUBICON_PB_URL_PREFIX: "rubicon",
    _IX_PB_URL_PREFIX: "ix",
    _PUB_PB_URL_PREFIX: "pubmatic",
    _TTD_PB_URL_PREFIX: "thetradeDesk",
    _APN_PB_URL_PREFIX: "appnexus",
}

# Map URL substring -> vendor key for vendor APIs proxied through publisher domains
# (e.g. Illuma proxied via ads.thesun.co.uk/illuma-api). Uses `in` matching.
VENDOR_URL_CONTAINS: dict[str, str] = {
    _ILLUMA_PUB_URL_SUFFIX: "illuma",
    _ADMANTX_PUB_URL_SUFFIX: "admantx",
}

# Prebid auction / OpenRTB bid-request endpoints parsers
def parse_rubicon(body: str) -> dict[str, Any]:
    """Parse the Rubicon publisher signals response.
    The auction endpoint returns a JSON object with the winning bid and other
    auction details for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "auction": data,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_ix(body: str) -> dict[str, Any]:
    """Parse the Index Exchange publisher signals response.
    The pbjs endpoint returns a JSON object with the winning bid and other
    auction details for the page being rendered.
    """
    try:
        data = json.loads(body)
        gamera_signals = parse_gamera(body)
        mantis_signals = parse_mantis(body)
        iab_signals = parse_bid_iabcat(body)        
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "auction": data,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_pubmatic(body: str) -> dict[str, Any]:
    """Parse the PubMatic publisher signals response.
    The translator endpoint returns a JSON object with the winning bid and other
    auction details for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "auction": data,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

# Contextual-intelligence / brand-safety / semantic-targeting vendor parsers.
def parse_illuma(body: str) -> dict[str, Any]:
    """Parse the Illuma publisher signals response.
    The illuma-pub.json endpoint returns a JSON object with the page-level
    semantic targeting signals for the page being rendered.
    """
    try:
        data = json.loads(body)
        cat_iab = data.get("cat_iab") or []
        cat_ents = data.get("cat_ents") or []
        cat_brandsafety = data.get("cat_brandsafety") or []
        cat_emotion = data.get("cat_emotion") or []
        cat_sent = data.get("cat_sent") or []
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "semantic": {
            "cat_iab": cat_iab,
            "cat_ents": cat_ents,
            "cat_brandsafety": cat_brandsafety,
            "cat_emotion": cat_emotion,
            "cat_sent": cat_sent,
        },
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_ias(body: str) -> dict[str, Any]:
    """Parse the IAS publisher signals response.
    The pub endpoint returns a JSON object with the page-level viewability and
    brand-safety signals for the page being rendered.
    """
    try:
        data = json.loads(body)
        slots = data.get("slots") or {}
        slot_ids = {k: v.get("id") for k, v in slots.items() if isinstance(v, dict) and "id" in v}
        data["slot_ids"] = slot_ids
        brand_safety_kw = data.get("custom", {}).get("ias-kw") or []

    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "slot_ids": slot_ids,
        "brand_safety_kw": brand_safety_kw,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_gamera(body: str) -> dict[str, Any]:
    """
    Parse the Gamera signals within bid requests. We are going to look at just the index exchange bid request for now. The Gamera signals are in the ext field of the bid request.
    """
    try:
        data = json.loads(body)
        gamera_signals = data.get("ext", {}).get("gamera") or {}
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "gamera": gamera_signals,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_mantis(body: str) -> dict[str, Any]:
    """
    Parse the Mantis signals within bid requests. We are going to look at just the index exchange bid request for now. The Mantis signals are in the ext field of the bid request.
    """
    try:
        data = json.loads(body)
        mantis_signals = data.get("ext", {}).get("mantis") or {}
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "mantis": mantis_signals,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_bid_iabcat(body: str) -> dict[str, Any]:
    """
    Parse the IAB category signals within bid requests. We are going to look at just the index exchange bid request for now. The IAB category signals are in the ext field of the bid request.
    """
    try:
        print("------------------------------------------------------------")
        data = json.loads(body)
        print(f"Parsing bid request for IAB category signals: {data}")
        iab_signals = data.get("ext", {}).get("bid_iabcat") or {}
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "bid_iabcat": iab_signals,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_doubleverify(body: str) -> dict[str, Any]:
    """Parse the DoubleVerify publisher signals response.

    The pub.json endpoint returns brand-safety classifications, content
    category, and viewability/fraud risk signals for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    ABS = data.get("ABS") or []
    BSC = data.get("BSC") or []
    
    return {
        "brand_safety": {
            "abs": ABS,
            "bsc": BSC,
        },
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_appnexus(body: str) -> dict[str, Any]:
    """Parse the AppNexus publisher signals response.

    The prebid endpoint returns a JSON object with the winning bid and other
    auction details for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "auction": data,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_thetradeDesk(body: str) -> dict[str, Any]:
    """Parse the The Trade Desk publisher signals response.

    The prebid endpoint returns a JSON object with the winning bid and other
    auction details for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "auction": data,
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_admantx(body: str) -> dict[str, Any]:
    """Parse the AdmantX publisher signals response.

    The admantx endpoint returns a JSON object with the page-level
    semantic targeting signals for the page being rendered.

        """
    try:
        data = json.loads(body)
        analysis = data.get("admants") or {}
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    return {
        "semantic": {
            "admants": analysis
        },
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

def parse_doubleverify_pull(body: str) -> dict[str, Any]:
    """Parse the DoubleVerify publisher signals response.

    The pub.json endpoint returns brand-safety classifications, content
    category, and viewability/fraud risk signals for the page being rendered.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    ABS = data.get("ABS") or []
    BSC = data.get("BSC") or []
    
    return {
        "brand_safety": {
            "abs": ABS,
            "bsc": BSC,
        },
        # Keep the full raw payload so new fields can be inspected without a code change.
        "raw": data,
    }

# ---------------------------------------------------------------------------
# OpenRTB bid-request parsers (outbound POST bodies sent TO each SSP).
# ---------------------------------------------------------------------------

def _parse_openrtb_common(data: dict) -> dict:
    """Extract fields that are standard across all OpenRTB 2.x bid requests."""
    imps = data.get("imp") or []
    parsed_imps = []
    for imp in imps:
        entry: dict[str, Any] = {"id": imp.get("id")}
        banner = imp.get("banner") or {}
        if banner:
            entry["banner"] = {
                "sizes": banner.get("format") or [],
                "w": banner.get("w"),
                "h": banner.get("h"),
            }
        entry["bidfloor"] = imp.get("bidfloor")
        entry["bidfloorcur"] = imp.get("bidfloorcur")
        entry["ext"] = imp.get("ext") or {}
        parsed_imps.append(entry)

    site = data.get("site") or {}
    user = data.get("user") or {}
    regs = data.get("regs") or {}
    regs_ext = regs.get("ext") or {}

    # Extended IDs (UID2, LiveRamp, etc.)
    user_eids = (user.get("ext") or {}).get("eids") or []

    return {
        "imp": parsed_imps,
        "site": {
            "page": site.get("page"),
            "domain": site.get("domain"),
            "publisher_id": (site.get("publisher") or {}).get("id"),
        },
        "user": {
            "id": user.get("id"),
            "buyeruid": user.get("buyeruid"),
            "eids": user_eids,
        },
        "consent": {
            "gdpr": regs_ext.get("gdpr"),
            "us_privacy": regs_ext.get("us_privacy"),
            "gpp": regs.get("gpp"),
            "gpp_sid": regs.get("gpp_sid"),
            "consent_string": (user.get("ext") or {}).get("consent"),
        },
    }


def parse_rubicon_request(body: str) -> dict[str, Any]:
    """Parse the outbound OpenRTB bid request sent to Rubicon/Magnite."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    result = _parse_openrtb_common(data)
    # Rubicon-specific: rp (Rubicon Project) segments and account info in imp ext.
    rubicon_ext = {}
    for imp in (data.get("imp") or []):
        rp = (imp.get("ext") or {}).get("rubicon") or {}
        if rp:
            rubicon_ext = rp
            break
    result["rubicon"] = {
        "account": rubicon_ext.get("account_id"),
        "site_id": rubicon_ext.get("site_id"),
        "zone_id": rubicon_ext.get("zone_id"),
    }
    result["raw"] = data
    return result


def parse_ix_request(body: str) -> dict[str, Any]:
    """Parse the outbound OpenRTB bid request sent to Index Exchange."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    result = _parse_openrtb_common(data)
    # IX-specific: site ID in imp ext; Gamera/Mantis contextual signals in top-level ext.
    ix_site_ids = []
    for imp in (data.get("imp") or []):
        sid = (imp.get("ext") or {}).get("ix", {}).get("siteId")
        if sid:
            ix_site_ids.append(sid)
    top_ext = data.get("ext") or {}
    result["ix"] = {
        "site_ids": ix_site_ids,
        "gamera": top_ext.get("gamera") or {},
        "mantis": top_ext.get("mantis") or {},
    }
    result["raw"] = data
    return result


def parse_pubmatic_request(body: str) -> dict[str, Any]:
    """Parse the outbound OpenRTB bid request sent to PubMatic."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    result = _parse_openrtb_common(data)
    # PubMatic-specific: publisher ID and ad slot info in imp ext.
    pm_slots = []
    for imp in (data.get("imp") or []):
        pm = (imp.get("ext") or {}).get("pubmatic") or {}
        if pm:
            pm_slots.append({"ad_slot": pm.get("adSlot"), "wrapper": pm.get("wrapper") or {}})
    result["pubmatic"] = {"slots": pm_slots}
    result["raw"] = data
    return result


def parse_ttd_request(body: str) -> dict[str, Any]:
    """Parse the outbound OpenRTB bid request sent to The Trade Desk."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    result = _parse_openrtb_common(data)
    result["thetradedesk"] = {"ext": (data.get("ext") or {}).get("ttd") or {}}
    result["raw"] = data
    return result


def parse_appnexus_request(body: str) -> dict[str, Any]:
    """Parse the outbound OpenRTB bid request sent to AppNexus/Xandr."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": "invalid json"}

    result = _parse_openrtb_common(data)
    # AppNexus-specific: placement ID in imp ext.
    apn_placements = []
    for imp in (data.get("imp") or []):
        apn = (imp.get("ext") or {}).get("appnexus") or {}
        if apn:
            apn_placements.append({"placement_id": apn.get("placement_id")})
    result["appnexus"] = {"placements": apn_placements}
    result["raw"] = data
    return result


# Registry: vendor key -> bid-request parser callable.
_REQUEST_PARSERS: dict[str, Any] = {
    "rubicon": parse_rubicon_request,
    "ix": parse_ix_request,
    "pubmatic": parse_pubmatic_request,
    "thetradeDesk": parse_ttd_request,
    "appnexus": parse_appnexus_request,
}


def parse_captured_request(vendor: str, body: str) -> dict[str, Any]:
    """Route a captured bid-request body to the correct vendor parser."""
    parser = _REQUEST_PARSERS.get(vendor)
    if parser is None:
        return {"error": f"no request parser for vendor '{vendor}'"}
    return parser(body)


# Registry: vendor key -> parser callable.
_PARSERS: dict[str, Any] = {
    "doubleverify": parse_doubleverify,
    "ias": parse_ias,
    "illuma": parse_illuma,
    "rubicon": parse_rubicon,
    "ix": parse_ix,
    "pubmatic": parse_pubmatic,
    "appnexus": parse_appnexus,
    "thetradeDesk": parse_thetradeDesk,
    "mantis": parse_mantis,
    "bid_iabcat": parse_bid_iabcat,
}

def parse_captured_response(vendor: str, body: str) -> dict[str, Any]:
    """Route a captured response body to the correct vendor parser."""
    parser = _PARSERS.get(vendor)
    if parser is None:
        return {"error": f"no parser for vendor '{vendor}'"}
    return parser(body)


_ADMANTX_ENDPOINT = "https://euasync01.admantx.com/admantx/service"
_DOUBLEVERIFY_ENDPOINT = "https://pub.doubleverify.com/dvtag/signals/bsc/pub.json?"

async def pull_admantx(page_url: str, token: str, max_attempts: int = 5) -> dict[str, Any]:
    """Proactively pull AdmantX semantic segments for a page URL.

    Makes a request to the AdmantX service using the supplied token. Retries
    up to max_attempts times — the URL may not be cached on the first call.
    Returns a dict compatible with signals["render"]["request_signals"]["admantx"].
    """
    request_payload = {
        "key": token,
        "type": "url",
        "method": "descriptor",
        "mode": "async",
        "decorator": "json",
        "filter": ["admants"],
        "body": page_url,
    }
    endpoint = f"{_ADMANTX_ENDPOINT}?request={json.dumps(request_payload)}"
    print(f"AdmantX pull: {endpoint}...")  # log the URL
    _log.info("admantx pull starting url=%s max_attempts=%d", page_url, max_attempts)
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_attempts):
            try:
                print(f"AdmantX attempt {attempt + 1} of {max_attempts}...")
                _log.debug("admantx attempt=%d url=%s", attempt + 1, endpoint[:120])
                resp = await client.get(endpoint)
                _log.debug("admantx attempt=%d status=%d", attempt + 1, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    admants = data.get("admants") or {}
                    if len(admants) == 0:
                        _log.warning("admantx pull empty segments attempt=%d", attempt + 1)
                    else:
                        _log.info("admantx pull success attempt=%d segments=%d", attempt + 1, len(admants))
                        return {
                            "semantic": {"admants": admants},
                            "raw": data,
                        }
                # Non-200 — wait briefly and retry (URL may not be cached yet)
                _log.debug("admantx non-200 status=%d sleeping attempt=%d", resp.status_code, attempt + 1)
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                _log.warning("admantx attempt=%d error=%s", attempt + 1, exc)
                await asyncio.sleep(1.0 * (attempt + 1))

    _log.warning("admantx pull failed after %d attempts url=%s", max_attempts, page_url)
    return {
        "semantic": {"admants": []},
        "raw": {"error": f"failed after {max_attempts} attempts"},
    }

async def pull_doubleverify(page_url: str, token: str, max_attempts: int = 5) -> dict[str, Any]:
    """Proactively pull DoubleVerify brand-safety signals for a page URL.

    Makes a request to the DoubleVerify service using the supplied token. Retries
    up to max_attempts times — the URL may not be cached on the first call.

    example url is https://pub.doubleverify.com/dvtag/signals/bsc/pub.json?ctx=42333984&cmp=DV2161907&url=https%3A%2F%2Fwp.pl&bsc=1&abs=1&token=5XhQnXbpJEaPTVJMIQtT4BBm9z7XlUBqrz7ezza88KY0XSFAdqjGmIxBLwRmpXUn8EIqCL10UaBE%2Fk50h4ZaVEfoDQcZHpPoTSmfdDU%2F6oQOdd3SbRQ719SWwc7RnNF%2FY6dqP91q23qxMzIaEalGl0iv%2FrNQDkQ%3D
    baseurl is https://pub.doubleverify.com/dvtag/signals/bsc/pub.json?
    query string is made up of ctx, cmp, url, bsc, abs, token
    ctx is a context id, cmp is a campaign id, url is the page url encoded, bsc and abs are flags to request brand safety and content category signals, and token is the API token.

    Returns a dict compatible with signals["render"]["request_signals"]["doubleverify"].
    """
    ctx = 42333984  # context ID for the request
    cmp = "DV2161907"  # campaign ID for the request
    url = page_url  # the page URL to analyze
    encoded_url = urllib.parse.quote(url, safe="")  # URL-encode the page URL
    # Construct the full endpoint URL with query parameters
    endpoint = f"{_DOUBLEVERIFY_ENDPOINT}ctx={ctx}&cmp={cmp}&url={encoded_url}&bsc=1&abs=1&token={token}"
    print(f"DoubleVerify pull: {endpoint[:120]}...")  # log the first 120 chars of the URL
    _log.info("doubleverify pull starting url=%s max_attempts=%d", page_url, max_attempts)
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_attempts):
            try:
                print(f"DoubleVerify attempt {attempt + 1} of {max_attempts}...")
                _log.debug("doubleverify attempt=%d url=%s", attempt + 1, endpoint[:120])
                resp = await client.get(endpoint)
                _log.debug("doubleverify attempt=%d status=%d", attempt + 1, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    abs_data = data.get("ABS") or []
                    bsc_data = data.get("BSC") or []
                    if len(abs_data) == 0 and len(bsc_data) == 0:
                        _log.warning("doubleverify pull empty segments attempt=%d", attempt + 1)
                    else:
                        _log.info("doubleverify pull success attempt=%d abs=%d bsc=%d", attempt + 1, len(abs_data), len(bsc_data))
                        return {
                            "brand_safety": {"abs": abs_data, "bsc": bsc_data},
                            "raw": data,
                        }
                # Non-200 — wait briefly and retry (URL may not be cached yet)
                _log.debug("doubleverify non-200 status=%d sleeping attempt=%d", resp.status_code, attempt + 1)
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                _log.warning("doubleverify attempt=%d error=%s", attempt + 1, exc)
                await asyncio.sleep(1.0 * (attempt + 1))