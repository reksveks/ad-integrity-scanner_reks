"""Submit a file of URLs/domains (one per line) to a running API and report results.

Usage (API running, venv active):
    python submit_urls.py sample_urls.txt
    python submit_urls.py sample_urls.txt --limit 5 --force
    python submit_urls.py sample_urls.txt --limit 5 --wait
    python submit_urls.py sample_urls.txt --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter

import httpx


def read_urls(path: str, limit: int | None) -> list[str]:
    with open(path, encoding="utf-8") as f:
        urls = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return urls[:limit] if limit else urls


async def submit(client: httpx.AsyncClient, url: str, force: bool) -> dict:
    try:
        r = await client.post("/scan", json={"url": url, "force": force})
    except httpx.HTTPError as e:
        return {"url": url, "status": "request-error", "detail": repr(e)}
    if r.status_code in (200, 202):
        return {"url": url, **r.json()}
    return {"url": url, "status": f"http-{r.status_code}", "detail": r.text[:200]}


async def wait_done(client: httpx.AsyncClient, scan_ids: dict[str, str], timeout: float) -> dict[str, dict]:
    deadline = time.monotonic() + timeout
    pending = dict(scan_ids)
    done: dict[str, dict] = {}
    while pending and time.monotonic() < deadline:
        for url, sid in list(pending.items()):
            r = await client.get(f"/scan/{sid}")
            if r.status_code != 200:
                continue
            body = r.json()
            if body["state"] in ("done", "error"):
                done[url] = body
                del pending[url]
        if pending:
            await asyncio.sleep(2)
    for url in pending:
        done[url] = {"state": "timeout"}
    return done


async def main(args: argparse.Namespace) -> int:
    urls = read_urls(args.file, args.limit)
    if not urls:
        print("no URLs found", file=sys.stderr)
        return 1

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        sem = asyncio.Semaphore(args.concurrency)

        async def one(u: str) -> dict:
            async with sem:
                return await submit(client, u, args.force)

        results = await asyncio.gather(*(one(u) for u in urls))
        for r in results:
            print(f"{r['status']:<14} {r['url']}" + (f"  {r['detail']}" if "detail" in r else ""))
        print("\nsummary:", dict(Counter(r["status"] for r in results)))

        if args.wait:
            ids = {r["url"]: str(r["scan_id"]) for r in results if "scan_id" in r}
            print(f"\nwaiting up to {args.timeout:.0f}s for {len(ids)} scans...")
            finished = await wait_done(client, ids, args.timeout)
            for url, body in finished.items():
                print(f"{body['state']:<10} score={body.get('integrity_score')}  {url}")
            print("summary:", dict(Counter(b["state"] for b in finished.values())))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", help="text file with one URL/domain per line")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--limit", type=int, help="only submit the first N lines")
    p.add_argument("--force", action="store_true", help="re-scan even if a fresh result exists")
    p.add_argument("--wait", action="store_true", help="poll until scans finish and print scores")
    p.add_argument("--timeout", type=float, default=300, help="seconds to wait with --wait")
    p.add_argument("--concurrency", type=int, default=5)
    sys.exit(asyncio.run(main(p.parse_args())))
