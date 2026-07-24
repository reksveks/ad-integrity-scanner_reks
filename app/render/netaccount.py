"""CDP-based network accounting.

The in-page Resource Timing API zeroes `transferSize` for cross-origin responses
without Timing-Allow-Origin and for cache hits — exactly where ad/tracker bytes
live — so it undercounts page weight badly. The Chrome DevTools Protocol
`Network` domain reports the true `encodedDataLength`, so we account bytes there.
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
from typing import Any

import tldextract

_extract = tldextract.TLDExtract(suffix_list_urls=())


@functools.lru_cache(maxsize=1)
def _tracker_db() -> dict:
    """Disconnect tracking-protection dataset: registrable domain -> {e:entity, c:category}."""
    p = pathlib.Path(__file__).resolve().parents[1] / "data" / "disconnect_trackers.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — fall back to the small curated set
        return {}

_AD_HOST_RE = re.compile(
    r"(googlesyndication|doubleclick|amazon-adsystem|adnxs|criteo|rubiconproject"
    r"|pubmatic|adsrvr|3lift|sharethrough|smartadserver|teads|adform|openx"
    r"|casalemedia|33across|gampad|adsystem)", re.I)

# Prebid auction / OpenRTB bid-request endpoints.  Matches the URL path so it
# fires regardless of which SSP host the Prebid adapter calls.
_PREBID_AUCTION_RE = re.compile(
    r"(/openrtb2/auction|/openrtb2/amp|/pbs/v1/openrtb"
    r"|/ut/v3/prebid|/prebid/auction|/prebid\.js"
    r"|[?&]pbjs|/openrtb/bids|/bid\?.*pbjs"
    r"|prebid\.adnxs\.com|prebid\-server\.rubiconproject\.com"
    r"|prebid\.a-mo\.net|prebid\.media\.net"
    r"|ib\.adnxs\.com/ut/v3"
    r"|exchange\.mediavine\.com/openrtb"
    r"|ix\.com/api/openrtb)", re.I)

# Contextual-intelligence / brand-safety / semantic-targeting vendors.
_CONTEXTUAL_DOMAINS: frozenset[str] = frozenset({
    # AdmantX (contextual signals)
    "admantx.com",
    # Illuma Technology (semantic targeting)
    "illumatechnology.com", "illuma.io",
    # Peer39 / Sizmek contextual
    "peer39.com",
    # Oracle/Grapeshot brand safety
    "grapeshot.co.uk", "oracle.com",
    # ContextWeb / Pulsepoint
    "contextweb.com",
    # 1plusX (first-party data + contextual)
    "1plusx.com",
    # Permutive (audience + contextual)
    "permutive.com",
    # Browsi (AI contextual / viewability)
    "browsi.com",
    # Seedtag contextual
    "seedtag.com",
    # GumGum (contextual in-image/video)
    "gumgum.com",
    # Comscore brand safety
    "comscore.com",
    # Integral Ad Science (IAS) brand safety / contextual
    "integralads.com", "adsafeprotected.com",
    # DoubleVerify brand safety
    "doubleverify.com",
    # Moat / Oracle viewability/contextual
    "moat.com",
})
# Curated tracker domains (registrable). A drop-in for DuckDuckGo Tracker Radar /
# Disconnect services.json later; conservative but covers the common set.
_TRACKER_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "scorecardresearch.com",
    "quantserve.com", "quantcount.com", "chartbeat.com", "segment.com",
    "segment.io", "mixpanel.com", "hotjar.com", "facebook.net", "facebook.com",
    "doubleclick.net", "adnxs.com", "krxd.net", "crwdcntrl.net", "demdex.net",
    "bluekai.com", "rlcdn.com", "adsrvr.org", "amazon-adsystem.com",
    "criteo.com", "pubmatic.com", "rubiconproject.com", "casalemedia.com",
    "bidswitch.net", "agkn.com", "mathtag.com", "sharethrough.com",
    "newrelic.com", "nr-data.net", "branch.io", "amplitude.com", "fullstory.com",
    "cloudflareinsights.com", "tiktok.com", "snapchat.com", "bing.com",
}
_TYPE_KEYS = ("Document", "Script", "Stylesheet", "Image", "Font", "Media",
              "XHR", "Fetch", "Other")
_MAX_REQUESTS = 8000  # defensive cap against a hostile request flood


def registrable(host: str | None) -> str | None:
    if not host:
        return None
    return _extract(host.split(":")[0]).registered_domain or host


class NetworkAccountant:
    def __init__(self) -> None:
        self._meta: dict[str, dict] = {}      # requestId -> {url, type}
        self._bytes: dict[str, int] = {}      # requestId -> encodedDataLength
        self._overflow = False

    # CDP event handlers (sync callbacks receiving the event params dict).
    def on_response(self, params: dict) -> None:
        if len(self._meta) >= _MAX_REQUESTS:
            self._overflow = True
            return
        rid = params.get("requestId")
        resp = params.get("response") or {}
        if rid:
            self._meta[rid] = {"url": resp.get("url", ""), "type": params.get("type", "Other")}

    def on_finished(self, params: dict) -> None:
        rid = params.get("requestId")
        if rid:
            self._bytes[rid] = int(params.get("encodedDataLength") or 0)

    def summary(self, page_url: str) -> dict[str, Any]:
        page_reg = registrable((page_url.split("//", 1)[-1].split("/", 1)[0]))
        total = sum(self._bytes.values())
        by_type = {k: 0 for k in _TYPE_KEYS}
        third_party: set[str] = set()
        trackers: set[str] = set()
        tracker_entities: set[str] = set()
        tracker_categories: dict[str, int] = {}
        ad_requests = 0
        hosts: set[str] = set()
        prebid_calls: list[dict] = []
        contextual_calls: list[dict] = []
        _MAX_CAPTURED = 50  # per-category URL cap to bound signal size
        db = _tracker_db()
        for rid, meta in self._meta.items():
            b = self._bytes.get(rid, 0)
            t = meta["type"] if meta["type"] in by_type else "Other"
            by_type[t] += b
            url = meta["url"]
            try:
                host = url.split("//", 1)[1].split("/", 1)[0]
            except IndexError:
                continue
            reg = registrable(host)
            if reg:
                hosts.add(reg)
                if reg != page_reg:
                    third_party.add(reg)
                hit = db.get(reg) or db.get(host)
                if hit:
                    trackers.add(reg)
                    tracker_entities.add(hit.get("e") or reg)   # owner-collapse
                    cat = hit.get("c") or "Other"
                    tracker_categories[cat] = tracker_categories.get(cat, 0) + 1
                elif not db and reg in _TRACKER_DOMAINS:         # fallback set
                    trackers.add(reg)
                    tracker_entities.add(reg)
            if _AD_HOST_RE.search(url):
                ad_requests += 1
            if _PREBID_AUCTION_RE.search(url) and len(prebid_calls) < _MAX_CAPTURED:
                prebid_calls.append({"url": url, "bytes": b})
            if reg in _CONTEXTUAL_DOMAINS and len(contextual_calls) < _MAX_CAPTURED:
                contextual_calls.append({"url": url, "domain": reg, "bytes": b})
        # Deduplicate contextual calls by domain for the summary counts.
        contextual_domains_seen = sorted({c["domain"] for c in contextual_calls})
        return {
            "request_count": len(self._meta),
            "page_weight_bytes": total,
            "bytes_by_type": by_type,
            "distinct_host_count": len(hosts),
            "third_party_host_count": len(third_party),
            "tracker_domain_count": len(trackers),
            "tracker_entity_count": len(tracker_entities),   # distinct owners
            "tracker_entities": sorted(tracker_entities)[:40],
            "tracker_categories": tracker_categories,
            "ad_request_count": ad_requests,
            "prebid_auction_calls": prebid_calls,
            "prebid_auction_count": len(prebid_calls),
            "contextual_calls": contextual_calls,
            "contextual_domains": contextual_domains_seen,
            "contextual_call_count": len(contextual_calls),
            "source": "cdp",
            "request_overflow": self._overflow,
        }


def count_third_party_cookies(cookies: list[dict], page_url: str) -> int:
    page_reg = registrable((page_url.split("//", 1)[-1].split("/", 1)[0]))
    n = 0
    for c in cookies:
        if registrable((c.get("domain") or "").lstrip(".")) != page_reg:
            n += 1
    return n
