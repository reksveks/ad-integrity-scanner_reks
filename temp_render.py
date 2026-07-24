"""Temporary script: render a URL directly and print the result.

Usage (from project root, with venv active):
    python temp_render.py https://www.independent.co.uk/some/article
    python temp_render.py https://www.dailymail.co.uk/article --dwell 12000
    python temp_render.py https://example.com --log debug
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def main(url: str, dwell_ms: int) -> None:
    # Must be set before importing app modules that call get_settings().
    os.environ.setdefault("AI_DATABASE_URL", "postgresql://localhost:5432/ad_integrity")

    from app.config import get_settings
    from app.logging_config import configure_logging
    from app.render.browser import RenderPool
    from app.render.collect import render_page_sampled

    settings = get_settings()
    configure_logging(settings.log_level)

    blocked = {t.strip() for t in settings.render_block_resources.split(",") if t.strip()}
    pool = RenderPool(concurrency=1, blocked_types=blocked, headless=settings.render_headless)
    await pool.start()
    try:
        print(f"\n→ Rendering: {url}  (dwell={dwell_ms}ms)\n", flush=True)
        result = await render_page_sampled(
            pool, url, dwell_ms=dwell_ms,
            accept_consent=settings.render_accept_cmp,
            capture_responses=["https://pub.doubleverify.com/dvtag/signals/bsc/pub.json"],
            admantx_token=settings.admantx_token,
            admantx_max_attempts=settings.admantx_max_attempts,
            doubleverify_token=settings.doubleverify_token,
            doubleverify_max_attempts=settings.doubleverify_max_attempts,
        )
    finally:
        await pool.stop()

    # Pretty-print key sections so the output is readable.
    sections = ["ok", "status", "final_url"]
    for key in sections:
        if key in result:
            print(f"{key}: {result[key]}")

    for section in ("gpt", "prebid", "cmp", "cwv", "cpu", "video", "layout", "request_signals", "bid_requests"):
        if section in result:
            print(f"\n--- {section} ---")
            # print(json.dumps(result[section], indent=2, default=str))

    resources = result.get("resources") or {}
    if resources:
        print("\n--- resources (summary) ---")
        summary_keys = (
            "request_count", "page_weight_bytes", "ad_request_count",
            "third_party_host_count", "tracker_domain_count",
            "prebid_auction_count", "contextual_call_count", "contextual_domains",
            "request_overflow",
        )
        # for k in summary_keys:
        #     if k in resources:
        #         print(f"  {k}: {resources[k]}")

        # if resources.get("prebid_auction_calls"):
        #     print("\n  prebid auction calls:")
        #     for c in resources["prebid_auction_calls"]:
        #         print(f"    [{c['bytes']}B] {c['url']}")

        # if resources.get("contextual_calls"):
        #     print("\n  contextual calls:")
        #     for c in resources["contextual_calls"]:
        #         print(f"    [{c['bytes']}B] {c['domain']}  {c['url']}")

    # captured = result.get("captured_responses") or []
    # if captured:
    #     print(f"\n--- captured responses ({len(captured)}) ---")
    #     for r in captured:
    #         print(f"\n  [{r['status']}] {r['url']}")
    #         try:
    #             print(json.dumps(json.loads(r["body"]), indent=2))
    #         except Exception:
    #             print(r["body"][:2000])

    # temp print 
    print('temp print: captured_responses')
    print(result.keys())
    print(result['request_signals'].keys())
    for key in result['request_signals']:
        print(result['request_signals'][key])

    # for key in result['bid_requests']:
    #     print(result['bid_requests'][key])

    # check if we can find index exchange bid requests and if so, print them
    if 'bid_requests' in result.keys():
        for bid_request in result['bid_requests']:
            if 'ix' in bid_request['vendor'].lower():
                print('Found Index Exchange bid request:')
                print(json.dumps(bid_request, indent=2))

    if not result.get("ok"):
        print(f"\nERROR: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render a URL and print signals.")
    parser.add_argument("url", help="Full URL to render")
    parser.add_argument("--dwell", type=int, default=8000, help="Dwell time in ms (default 8000)")
    parser.add_argument("--log", default="info", choices=["debug", "info", "warning"],
                        help="Log level (default info)")
    args = parser.parse_args()

    os.environ["AI_LOG_LEVEL"] = args.log.upper()
    asyncio.run(main(args.url, args.dwell))
