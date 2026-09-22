"""
webagent: Jev-style local controller driving a real browser (Playwright).

Loop:
  1. open the target URL
  2. ask the local simplejev server: { scroll_more?, found?, relevance } each round
  3. scroll while it says yes; extract links/text it marks relevant
  4. download the files/pages it approves, with per-file verdicts logged

Usage:
  python webagent.py <url> [--goal "what to find"] [--out DIR] [--max-rounds N] [--scroll-pixels PX]
"""

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

JEV = os.environ.get("SIMPLEJEV_URL", "http://127.0.0.1:8300")
STATE_CHAR_BUDGET = 6000


async def jev_request(state: str, questions: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{JEV}/v1/request", json={"state": state, "questions": questions})
        r.raise_for_status()
        return r.json()["answers"]


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


async def run(url: str, goal: str, out: Path, max_rounds: int, scroll_px: int):
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    report = {"url": url, "goal": goal, "rounds": [], "downloads": [], "decision_ms": []}
    t_start = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        print(f"[webagent] open {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(1500)

        scroll_height = await page.evaluate("document.body.scrollHeight")
        viewport = page.viewport_size
        viewport_h = viewport["height"] if viewport else 800
        done = False
        bottom_rounds = 0

        for rnd in range(1, max_rounds + 1):
            text = clip(await page.evaluate("document.body.innerText"), STATE_CHAR_BUDGET)
            scroll_y = await page.evaluate("window.scrollY")
            at_bottom = scroll_y + viewport_h >= scroll_height - 4
            if at_bottom:
                bottom_rounds += 1
            state = (
                f"Page: {await page.title()}\nURL: {page.url}\n"
                f"Scrolled to: {scroll_y} px of {scroll_height} px"
                f"{' — PAGE BOTTOM REACHED, there is no content below' if at_bottom else ''}\n\n"
                f"Page text:\n{text}"
            )
            questions = {
                "scroll_more": {
                    "type": "noul",
                    "instructions": f"Goal: {goal}. Is there likely more relevant content below the current viewport that has not been seen yet?",
                },
                "found": {
                    "type": "noul",
                    "instructions": f"Goal: {goal}. Does the page text already contain content matching the goal?",
                },
                "action": {
                    "type": "choice",
                    "instructions": "Pick the next controller action.",
                    "criteria": {
                        "keep_scrolling": "Relevant content may continue below; keep scrolling this page",
                        "extract": "Goal content is on screen now; extract and finish this page",
                        "stop": "Nothing relevant here; stop",
                    },
                },
            }
            answers = await jev_request(state, questions)
            report["decision_ms"].append(None)  # filled by server timing below
            act = answers["action"]["value"]
            rnd_entry = {
                "round": rnd,
                "scrollY": scroll_y,
                "verdict": act,
                "p": answers["action"]["probability"],
                "dist": answers["action"]["distribution"],
                "found_p": answers["found"]["probability"],
                "scroll_more_p": answers["scroll_more"]["probability"],
            }
            report["rounds"].append(rnd_entry)
            print(f"[webagent] r{rnd} @{scroll_y}px -> {act} (p={answers['action']['probability']}) "
                  f"found_p={answers['found']['probability']} scroll_more_p={answers['scroll_more']['probability']}")

            # Signal composition in code (not one clever prompt): the choice
            # read can waffle at page bottom; `found` + stop means the goal is
            # here — extract. A bare stop still leaves with the page text.
            if act == "stop" and answers["found"]["probability"] >= 0.8:
                print("[webagent] stop with found_p>=0.8 — composing to extract")
                act = "extract"

            if act == "keep_scrolling":
                if at_bottom and bottom_rounds >= 2:
                    # The page is fully scrolled; scrolling again cannot reveal
                    # anything. Force extraction instead of burning rounds.
                    print("[webagent] at page bottom twice — forcing extract")
                    act = "extract"
                else:
                    await page.evaluate(f"window.scrollBy(0, {scroll_px})")
                    await page.wait_for_timeout(1200)
                    # lazy-load growth
                    scroll_height = await page.evaluate("document.body.scrollHeight")
                    continue
            if act == "extract":
                full_text = await page.evaluate("document.body.innerText")
                base = urllib.parse.urlparse(page.url).netloc.replace(".", "_")
                f = out / f"{base}.txt"
                f.write_text(full_text, encoding="utf-8")
                report["downloads"].append({"file": str(f), "kind": "page_text", "bytes": f.stat().st_size})
                print(f"[webagent] extracted -> {f} ({f.stat().st_size} bytes)")
                # find downloadable-looking links and let jev gate each
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim().slice(0,120)})).filter(x => x.text)",
                )
                seen = set()
                for link in links[:40]:
                    href = link["href"]
                    if href in seen or href.startswith(("javascript:", "#", "mailto:")):
                        continue
                    seen.add(href)
                    q = {
                        "download": {
                            "type": "noul",
                            "instructions": f"Goal: {goal}. Should this link be downloaded as it likely contains goal-relevant data? Link text: '{link['text']}'",
                        }
                    }
                    a = await jev_request(f"Source page: {page.url}\nCandidate link: {href}", q)
                    if a["download"]["value"] == "yes" and a["download"]["probability"] >= 0.6:
                        try:
                            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
                                resp = await c.get(href)
                            name = Path(urllib.parse.urlparse(href).path).name or "download"
                            if not Path(name).suffix:
                                name += ".html" if "text/html" in resp.headers.get("content-type", "") else ".bin"
                            f = out / f"{rnd}_{name[:80]}"
                            f.write_bytes(resp.content)
                            report["downloads"].append({
                                "file": str(f), "kind": "link", "url": href,
                                "bytes": len(resp.content), "p": a["download"]["probability"],
                            })
                            print(f"[webagent] downloaded {href} -> {f} ({len(resp.content)} b, p={a['download']['probability']})")
                        except Exception as e:  # noqa: BLE001
                            print(f"[webagent] download failed {href}: {e}")
                done = True
                break
            if act == "stop":
                done = True
                break

        if not done:
            # budget exhausted: grab what we have
            full_text = await page.evaluate("document.body.innerText")
            f = out / "final_page.txt"
            f.write_text(full_text, encoding="utf-8")
            report["downloads"].append({"file": str(f), "kind": "page_text_final", "bytes": f.stat().st_size})

        await browser.close()

    report["elapsed_s"] = round(time.time() - t_start, 1)
    rp = out / "webagent_report.json"
    rp.write_text(json.dumps(report, indent=2))
    print(f"[webagent] report -> {rp}")
    print(json.dumps({"rounds": len(report["rounds"]), "downloads": len(report["downloads"]), "elapsed_s": report["elapsed_s"]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--goal", required=True)
    ap.add_argument("--out", default="webagent_out")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--scroll-pixels", type=int, default=1200)
    a = ap.parse_args()
    asyncio.run(run(a.url, a.goal, Path(a.out), a.max_rounds, a.scroll_pixels))
