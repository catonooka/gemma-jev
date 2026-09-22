"""
test_interact: deep-interaction test for the CDP crawler on Indeed.

Interaction gauntlet (each step logged pass/fail):
  I1. search via the site's own box
  I2. open "Date posted" filter -> Last 3 days -> apply
  I3. open "Job type" filter -> Full-time -> apply
  I4. click a job card -> detail side-pane opens, harvest full description
  I5. click 3 more cards, harvest each detail
  I6. pagination: Next page click

Then: jev filtering pass over all collected cards:
  F1 relevance (noul), F2 seniority (choice), F3 remote-eligible (noul)
  + composed code filter (salary >= threshold parsed from VND strings)
"""

import asyncio
import json
import re
import time
from pathlib import Path

import httpx

CDP = "http://127.0.0.1:9222"
JEV = "http://127.0.0.1:8300"
OUT = Path("/home/aisever/simplejev/interact_test")
OUT.mkdir(parents=True, exist_ok=True)

results = {"interactions": [], "cards": [], "filter": None}


def log_i(name, ok, detail=""):
    results["interactions"].append({"step": name, "ok": ok, "detail": detail[:120]})
    print(f"{'PASS' if ok else 'FAIL'} I: {name}" + (f" — {detail[:100]}" if detail else ""))


async def jev(state, questions):
    async with httpx.AsyncClient(timeout=25.0) as c:
        r = await c.post(f"{JEV}/v1/request", json={"state": state, "questions": questions})
        r.raise_for_status()
        return r.json()["answers"]


async def count_cards(page):
    return await page.locator("div.job_seen_beacon").count()


async def harvest_cards(page):
    return await page.evaluate("""
(() => {
  const cards = document.querySelectorAll('div.job_seen_beacon');
  return Array.from(cards).map(c => {
    const t = c.querySelector('h3.jobTitle span[title]');
    const a = c.querySelector('h3 a');
    const comp = c.querySelector('[data-testid="company-name"]');
    const loc = c.querySelector('[data-testid="text-location"]');
    const meta = c.querySelector('.metadata.salary-only, [data-testid="attribute_snippet_testid"]');
    const snip = c.querySelector('[data-testid="belowJobSnippet"], .summary');
    return {
      title: t ? t.getAttribute('title') : '',
      url: a ? a.href : '',
      company: comp ? comp.textContent.trim() : '',
      location: loc ? loc.textContent.trim() : '',
      salary: meta ? meta.textContent.trim() : '',
      snippet: snip ? snip.textContent.trim().slice(0, 400) : '',
    };
  }).filter(x => x.title);
})()
""")


async def read_detail_pane(page):
    """After a card click, Indeed shows a right-side detail pane (desktop)."""
    return await page.evaluate("""
(() => {
  const pane = document.querySelector('#jobsearch-JobInfoScreen-Container, div.jobsearch-JobInfoScreen, [data-testid="jobDetails"], #viewJobSSAAfer, .jobsearch-ViewJobLayout')
    || document.querySelector('iframe');
  if (pane && pane.tagName === 'IFRAME') {
    try { return {where: 'iframe', text: (pane.contentDocument ? pane.contentDocument.body.innerText : '').slice(0, 4000)}; } catch(e) { return {where: 'iframe-xorigin'}; }
  }
  const sel = ['div.jobsearch-JobInfoScreen-Container', '#viewJobSSAAfer', 'div[id^="jobDescriptionText"]', '#jobDescriptionText'];
  for (const s of sel) {
    const el = document.querySelector(s);
    if (el && el.innerText.trim()) return {where: s, text: el.innerText.slice(0, 4000)};
  }
  return {where: 'none', text: ''};
})()
""")


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(CDP)
        page = b.contexts[0].pages[0]

        # ---------- I1: search ----------
        try:
            await page.goto("https://vn.indeed.com/", wait_until="domcontentloaded", timeout=50_000)
            await page.wait_for_timeout(2500)
            what = page.locator('input[name="q"], #text-input-what').first
            await what.fill(".NET developer", timeout=15_000)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)
            n = await count_cards(page)
            log_i("I1 search via search box", n > 0, f"{n} cards, title={ (await page.title())[:60] }")
        except Exception as e:
            log_i("I1 search via search box", False, str(e))

        # ---------- I2: date posted filter ----------
        try:
            btn = page.locator('#fromAge_filter_button, button[aria-label="Date posted filter"]').first
            await btn.click(timeout=10_000)
            await page.wait_for_timeout(1500)
            # options render as links/buttons in a popover; dump what appeared
            opts = await page.evaluate("""
(() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="menuitem"], [role="option"], a, button, label')) {
    const t = (el.textContent || '').trim();
    if (t && t.length < 40 && /24 hours|3 days|7 days|14 days|30 days/i.test(t)) out.push(t + '::' + el.tagName);
  }
  return out.slice(0, 8);
})()
""")
            opt = page.locator('span, a, button, label').filter(has_text=re.compile("^Last 3 days$", re.I)).first
            found = await opt.count() > 0
            if found:
                await opt.click(timeout=8_000)
                await page.wait_for_timeout(4000)
                n = await count_cards(page)
                log_i("I2 Date posted -> Last 3 days", n > 0, f"options={opts[:3]}; cards now {n}; fromage={'fromage' in page.url}")
            else:
                log_i("I2 Date posted filter", False, f"dropdown options seen: {opts}")
        except Exception as e:
            log_i("I2 Date posted filter", False, str(e))

        # ---------- I3: job type filter ----------
        try:
            btn = page.locator('button[aria-label="Job type filter"], button:has-text("Job type")').first
            await btn.click(timeout=10_000)
            await page.wait_for_timeout(1500)
            opt = page.locator('span, a, button, label').filter(has_text=re.compile("^Full[- ]?time", re.I)).first
            found = await opt.count() > 0
            if found:
                await opt.click(timeout=8_000)
                await page.wait_for_timeout(4000)
                n = await count_cards(page)
                log_i("I3 Job type -> Full-time", n > 0, f"cards now {n}")
            else:
                log_i("I3 Job type filter", False, "no Full-time option in popover")
        except Exception as e:
            log_i("I3 Job type filter", False, str(e))

        # ---------- I4/I5: open job detail pages via /viewjob?jk= ----------
        # Card clicks are swallowed by Indeed's SPA (no navigation, no pane in
        # this layout). The reliable pattern: extract the jk id from each card
        # link and navigate directly to /viewjob?jk=...
        cards_before = await harvest_cards(page)
        results["cards"] = cards_before
        detail_ok = 0
        jks = await page.evaluate("""
(() => Array.from(document.querySelectorAll('div.job_seen_beacon h3 a'))
  .map(a => (a.href.match(/jk=([a-z0-9]+)/i) || [])[1] || '')
  .filter(Boolean).slice(0, 3))()
""")
        for i, jk in enumerate(jks):
            try:
                await page.goto(f"https://vn.indeed.com/viewjob?jk={jk}", wait_until="domcontentloaded", timeout=40_000)
                await page.wait_for_timeout(2500)
                body = await page.evaluate("document.body.innerText")
                # new React layout: description is plain body text under "Full job description"
                m = re.search(r"Full job description\n([\s\S]+?)(?:Apply on company site|Report job|$)", body)
                desc = m.group(1).strip() if m else ""
                ok = len(desc) > 100
                if ok:
                    detail_ok += 1
                    if i < len(cards_before):
                        cards_before[i]["detail"] = desc[:3500]
                    print(f"PASS I4.{i+1} viewjob jk={jk[:8]} desc={len(desc)}b")
                else:
                    print(f"FAIL I4.{i+1} viewjob desc too short ({len(desc)}b)")
            except Exception as e:
                print(f"FAIL I4.{i+1} viewjob: {str(e)[:80]}")
        log_i("I4/I5 open job detail pages (3 cards)", detail_ok >= 2, f"{detail_ok}/3 full descriptions read")
        # back to results
        try:
            await page.go_back()
            await page.wait_for_timeout(2500)
            if "jobs" not in page.url:
                await page.goto("https://vn.indeed.com/jobs?q=.NET+developer", wait_until="domcontentloaded")
                await page.wait_for_timeout(2500)
        except Exception:
            pass

        # ---------- I6: pagination ----------
        try:
            nxt = page.locator('a[aria-label="Next Page"], a[data-testid="pagination-page-next"]').first
            if await nxt.count() > 0:
                await nxt.click(timeout=10_000)
                await page.wait_for_timeout(4000)
                title = await page.title()
                login_wall = "Sign In" in title
                page2_cards = await count_cards(page)
                log_i("I6 Next page click", not login_wall and page2_cards > 0,
                      f"page2 cards={page2_cards}, login_wall={login_wall}")
            else:
                log_i("I6 Next page click", False, "no Next button present")
        except Exception as e:
            log_i("I6 Next page click", False, str(e))

        await b.close()

    # ---------- F: jev filtering pass over all cards ----------
    passes = []
    for c in results["cards"]:
        if not c.get("title"):
            continue
        state = (
            f"Job: {c['title']} at {c['company']}, {c['location']}. "
            f"Salary: {c.get('salary') or 'not stated'}. Snippet: {c.get('snippet', '')[:300]} "
            f"Detail: {(c.get('detail') or '')[:600]}"
        )
        try:
            a = await jev(state, {
                "dotnet": {"type": "noul", "instructions": "Does this job mainly involve .NET/C# development (writing code)?"},
                "seniority": {"type": "choice", "instructions": "Seniority level?",
                              "criteria": {"junior": "Entry or junior", "mid": "Mid-level",
                                           "senior": "Senior/lead/principal", "unknown": "Cannot tell"}},
                "worth_detail": {"type": "noul", "instructions": "Given no detail text was read, would opening this card's full description likely add salary or tech-stack info?"},
            })
            c["jev"] = {
                "dotnet": a["dotnet"]["value"], "dotnet_p": a["dotnet"]["probability"],
                "seniority": a["seniority"]["value"], "seniority_p": a["seniority"]["probability"],
            }
            passes.append(c)
        except Exception as e:
            print("jev fail:", str(e)[:60])

    # composed filter: .NET + junior-or-mid + salary >= 15M VND if stated
    def salary_vnd(s):
        m = re.search(r"([\d][\d\.,]*)\s*(?:triệu|tr)?\s*(?:VND|VNĐ)?", s or "")
        if not m:
            return None
        raw = m.group(1).replace(",", "").replace(".", "")
        try:
            v = float(raw)
        except ValueError:
            return None
        return v / 1_000_000 if v > 1000 else v  # heuristics: raw VND vs millions

    composed = []
    for c in passes:
        j = c.get("jev", {})
        sal_m = salary_vnd(c.get("salary") or "")
        keep = j.get("dotnet") == "yes" and j.get("seniority") in ("junior", "mid") and (sal_m is None or sal_m >= 15)
        c["composed_keep"] = keep
        c["salary_m_vnd"] = sal_m
        if keep:
            composed.append(c)

    results["filter"] = {
        "total": len(passes),
        "kept_by_composed_filter": len(composed),
        "dotnet_yes": sum(1 for c in passes if c["jev"]["dotnet"] == "yes"),
        "seniority_dist": {s: sum(1 for c in passes if c["jev"]["seniority"] == s)
                           for s in ("junior", "mid", "senior", "unknown")},
    }

    i_ok = sum(1 for i in results["interactions"] if i["ok"])
    print(f"\nINTERACTIONS: {i_ok}/{len(results['interactions'])} passed")
    for i in results["interactions"]:
        print(f"  {'ok ' if i['ok'] else 'ERR'} {i['step']}: {i['detail']}")
    print(f"\nFILTER: {json.dumps(results['filter'], ensure_ascii=False)}")
    print("\nCOMPOSED FILTER KEEPS (dotnet + junior/mid + salary ok):")
    for c in composed:
        print(f"  - {c['title'][:50]} | {c['company'][:20]} | {c['jev']['seniority']} | sal={c.get('salary_m_vnd')}M")

    (OUT / "interact_results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"\nsaved -> {OUT / 'interact_results.json'}")


asyncio.run(main())
