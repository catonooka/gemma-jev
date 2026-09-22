"""
webagent-cdp: local-jev controller driving the user's VISIBLE Chrome over CDP.

Connects to the real browser window (CDP :9222), so the user watches every
step. Local jev (:8300, Gemma4 E4B) decides scroll/extract/collect; page text
is harvested from the live DOM. Works on bot-walled sites because the browser
is a real headed Chrome with a persistent profile.

Usage:
  python webagent-cdp.py <url> [--goal "..."] [--out DIR] [--max-rounds N]
                          [--collect "indeed_jobs"|"text"|"links"]
                          [--want N]   # stop when N items collected
"""

import argparse
import asyncio
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import httpx

JEV = os.environ.get("SIMPLEJEV_URL", "http://127.0.0.1:8300")
CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
STATE_CHAR_BUDGET = 6000


async def jev_request(state: str, questions: dict) -> dict:
    async with httpx.AsyncClient(timeout=25.0) as c:
        r = await c.post(f"{JEV}/v1/request", json={"state": state, "questions": questions})
        r.raise_for_status()
        return r.json()["answers"]


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


# ---------- Indeed card harvesting (generic enough for most job boards) ----------

CARD_JS = """
(() => {
  // Indeed: job cards; fall back to generic list items on other boards
  const sels = ['div.job_seen_beacon', 'td.resultContent', 'div.cardOutline',
                'li.css-1yqpzok', '[data-testid="search-serpapp_card"]'];
  let cards = [];
  for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
  if (!cards.length) return [];
  return Array.from(cards).slice(0, 50).map(c => {
    const h = c.querySelector('h3.jobTitle a, h3 a[role="button"], h2 a, h2, [data-testid="job-title"], a[data-tn-element="jobTitle"]');
    const titleEl = c.querySelector('h3.jobTitle span[title], h2 span[title]');
    const comp = c.querySelector('[data-testid="company-name"], .companyName, span.company, [data-testid="company-name"] a');
    const loc = c.querySelector('[data-testid="text-location"], .companyLocation');
    const snip = c.querySelector('[data-testid="belowJobSnippet"], .summary, ul');
    const meta = c.querySelector('.metadata.salary-only, [data-testid="attribute_snippet_testid"]');
    return {
      title: titleEl ? titleEl.getAttribute('title') || (h ? (h.textContent || '').trim() : '') : (h ? (h.textContent || '').trim() : ''),
      url: h && h.href ? h.href : '',
      company: comp ? (comp.textContent || '').trim() : '',
      location: loc ? (loc.textContent || '').trim() : '',
      salary: meta ? (meta.textContent || '').trim() : '',
      snippet: snip ? (snip.textContent || '').trim().slice(0, 300) : '',
    };
  }).filter(x => x.title);
})()
"""


async def harvest(page) -> list[dict]:
    try:
        return await page.evaluate(CARD_JS)
    except Exception:  # noqa: BLE001
        return []


async def run(url: str, goal: str, out: Path, max_rounds: int, want: int, scroll_px: int, query: str = "", location: str = ""):
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    report = {"url": url, "goal": goal, "rounds": [], "collected": [], "jev_decisions": 0, "jev_ms": 0.0}
    t0 = time.time()
    collected: dict[str, dict] = {}  # url -> card

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print(f"[cdp] connected, driving visible Chrome tab: {page.url[:70]}")
        # HUMAN PATTERN: direct URL navigation to search results triggers
        # Indeed's edge block; typing into the site's own search box passes.
        if query:
            await page.goto("https://vn.indeed.com/", wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2500)
            what = page.locator('input[name="q"], #text-input-what').first
            await what.fill(query)
            if location:
                where = page.locator('input[name="l"], #text-input-where').first
                if await where.count() > 0:
                    await where.fill(location)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)
        elif url:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2500)

        zero_yield_rounds = 0
        for rnd in range(1, max_rounds + 1):
            before = len(collected)
            text = clip(await page.evaluate("document.body.innerText"), STATE_CHAR_BUDGET)
            scroll_y = await page.evaluate("window.scrollY")
            scroll_h = await page.evaluate("document.body.scrollHeight")
            at_bottom = scroll_y + 900 >= scroll_h
            state = (
                f"Page: {await page.title()}\nURL: {page.url}\n"
                f"Scrolled: {scroll_y}/{scroll_h} px"
                f"{' — PAGE BOTTOM' if at_bottom else ''}\n"
                f"Collected so far: {len(collected)} items; target {want}.\n\n"
                f"Page text:\n{text}"
            )
            questions = {
                "action": {
                    "type": "choice",
                    "instructions": f"Goal: {goal}. Collected {len(collected)}/{want}. Pick next action.",
                    "criteria": {
                        "keep_scrolling": "More relevant items likely below; keep scrolling",
                        "collect": "Items are loaded on screen; harvest them and continue",
                        "done": "Enough collected or nothing more here; finish",
                    },
                },
                "found": {"type": "noul", "instructions": f"Does the page show items matching: {goal}?"},
            }
            tj = time.perf_counter()
            a = await jev_request(state, questions)
            report["jev_decisions"] += 2
            report["jev_ms"] += (time.perf_counter() - tj) * 1000
            act = a["action"]["value"]
            print(f"[cdp] r{rnd} @{scroll_y}px -> {act} (p={a['action']['probability']:.2f}) found_p={a['found']['probability']:.2f} [{len(collected)}/{want}]")
            report["rounds"].append({"round": rnd, "scrollY": scroll_y, "verdict": act, "p": a["action"]["probability"], "found_p": a["found"]["probability"], "collected": len(collected)})

            # harvest every round regardless (cheap, idempotent by url)
            if a["found"]["probability"] >= 0.5:
                for card in await harvest(page):
                    if card.get("url") and card["url"] not in collected:
                        collected[card["url"]] = card
            zero_yield_rounds = zero_yield_rounds + 1 if len(collected) == before else 0

            # Termination guard: never stop below target on jev's say-so alone.
            # Honor "done" only when target reached, or after 2 consecutive
            # zero-yield rounds (page truly has nothing new).
            if act == "done" and len(collected) < want and zero_yield_rounds < 2:
                print(f"[cdp] jev said done at {len(collected)}/{want} — overriding: try next page")
                nxt = page.locator('a[aria-label="Next Page"], a[data-testid="pagination-page-next"]').first
                if await nxt.count() > 0:
                    try:
                        await nxt.click(timeout=10_000)
                        await page.wait_for_timeout(2500)
                        await page.evaluate("window.scrollTo(0,0)")
                        continue
                    except Exception as e:  # noqa: BLE001
                        print(f"[cdp] next-page click failed: {e}")
                if zero_yield_rounds >= 2:
                    break
                act = "keep_scrolling"

            if act == "done" or len(collected) >= want:
                break
            if act == "keep_scrolling" or act == "collect":
                if at_bottom and act == "keep_scrolling":
                    # try next page if a pager exists, else finish
                    nxt = page.locator('a[data-testid="pagination-page-next"], a[aria-label="Next Page"], a:has-text("Next")').first
                    if await nxt.count() > 0:
                        print("[cdp] page bottom — clicking Next")
                        try:
                            await nxt.click(timeout=10_000)
                            await page.wait_for_timeout(2500)
                            await page.evaluate("window.scrollTo(0,0)")
                            continue
                        except Exception as e:  # noqa: BLE001
                            print(f"[cdp] next-page click failed: {e}")
                            break
                    else:
                        break
                await page.evaluate(f"window.scrollBy(0, {scroll_px})")
                await page.wait_for_timeout(1500)
                continue

        # final harvest pass
        for card in await harvest(page):
            if card.get("url") and card["url"] not in collected:
                collected[card["url"]] = card

        try:
            await browser.close()  # only closes OUR connection, not the browser
        except Exception:  # noqa: BLE001
            pass

    report["collected"] = list(collected.values())[: want * 2]
    report["elapsed_s"] = round(time.time() - t0, 1)
    f = out / "collected.json"
    f.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n[cdp] DONE: {len(report['collected'])} items in {report['elapsed_s']}s, "
          f"{report['jev_decisions']} jev decisions (avg {report['jev_ms']/max(report['jev_decisions'],1):.0f}ms)")
    print(f"[cdp] out -> {f}")
    for c in report["collected"][:20]:
        print(f"  - {c.get('title','')[:55]} | {c.get('company','')[:22]} | {c.get('location','')[:25]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="")
    ap.add_argument("--goal", required=True)
    ap.add_argument("--out", default="webagent_cdp_out")
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--want", type=int, default=20)
    ap.add_argument("--scroll-pixels", type=int, default=1400)
    ap.add_argument("--query", default="", help="search via the site's own search box (human pattern)")
    ap.add_argument("--location", default="")
    a = ap.parse_args()
    asyncio.run(run(a.url, a.goal, Path(a.out), a.max_rounds, a.want, a.scroll_pixels, a.query, a.location))
