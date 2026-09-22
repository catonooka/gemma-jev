# The Indeed playbook (bot-wall field notes)

Everything below was learned live against vn.indeed.com from a datacenter IP that is **hard-banned at the Cloudflare edge** for direct result-URL navigation. The same IP passes when behaving like the human pattern. These notes are the difference between 0 jobs and 22.

## What triggers the block

| Action | Result |
|---|---|
| `goto('/jobs?q=...')` — deep link to search results | ❌ "Additional Verification Required" (Ray ID) — edge block |
| Any regional variant (.com/.co.uk/.de) + RSS | ❌ same edge block |
| `goto('vn.indeed.com/')` homepage, then **type the query into the site's search box** + Enter | ✅ passes, every time |
| Headless Playwright (default UA/profile) | ❌ blocked even on the human path |
| Headed Chrome, persistent profile, via CDP | ✅ passes |

**Rule 1: navigate via site controls (search box, filter chips, pager), never via result deep links.**

## Anonymous limits (no account)

- ~16 cards per results page.
- Paging past page 2 → login wall (`secure.indeed.com/auth`). Hard limit; not fixable without an account. **Workaround:** multiple variant queries (".NET developer", "C# developer", "ASP.NET") + merge + dedupe by title+company → 22 unique jobs from 3 queries.
- ~10 rapid searches in a row → soft rate limit (search box fill times out). Cool-down ≈ 2 minutes. Pace searches or rotate queries.

## Markup & interaction notes (2026-09 layout)

- Cards: `div.job_seen_beacon` (aliases: `td.resultContent`, `div.cardOutline`).
- Title: `h3.jobTitle span[title]` — **read the `title` attribute**, not `h2` (legacy selectors are gone in the React layout).
- Company: `[data-testid="company-name"]`; location: `[data-testid="text-location"]`.
- **Card clicks are swallowed by the SPA** — no navigation, no side pane, no new tab. Instead extract the `jk` id from the card link (`href` contains `jk=<id>`) and navigate directly to `/viewjob?jk=<id>`.
- Detail page: description is plain body text after the line `Full job description` (parse with regex; `#jobDescriptionText` and friends no longer exist).
- "Date posted" filter: button `#fromAge_filter_button`; options are **bare `<li>`/`<span>` text** ("Last 3 days") — no roles, no testids; match exact text.
- "Job type" filter: opens (`aria-expanded=true`) but rendered no detectable option elements in any probe — unresolved; treat as known-broken.
- Pager: `a[aria-label="Next Page"]` works until the login wall.

## Controller design that survived contact

1. **Compose, don't trust.** The `action` choice read (keep_scrolling/collect/done) waffles at p≈0.5 at page bottom or when bored. Orthogonal binary reads (`found?`, `more_below?`) are the calibrated ones. Ask both; combine in code. Termination is a *code* decision: target reached, or 2 consecutive zero-yield rounds — never the choice read alone.
2. **Feed real state.** The model judges the state string, nothing else. Include scroll position, whether bottom was reached ("PAGE BOTTOM REACHED, there is no content below"), items collected vs target, and a page-text excerpt.
3. **Harvest every round.** Card extraction is cheap and idempotent by URL; run it each round regardless of the decision, then let the decision control *navigation* only.
4. **Dedupe by title+company normalized**, not by URL — the same job appears under different tracking URLs.
