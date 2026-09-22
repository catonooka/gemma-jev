"""
jobcrawl: local-jev-driven job data collector.

Pipeline:
  1. fetch listings pages from public no-key job APIs (Arbeitnow, Remotive)
  2. LOCAL JEV (:8300, Gemma4 E4B) gates each listing for goal relevance
     and remote/senior classification — ~30ms per decision
  3. LAN Qwen (gx10-qwen.local) structures the approved ones into clean JSON
  4. writes results + decision log

Usage: python jobcrawl.py --goal "..." --target 15 --out DIR
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

JEV = "http://127.0.0.1:8300"
QWEN = "http://gx10-qwen.local/v1"
QWEN_MODEL = "qwen3.8-flash-next"


async def jev(state: str, questions: dict) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{JEV}/v1/request", json={"state": state, "questions": questions})
        r.raise_for_status()
        return r.json()["answers"]


async def fetch_sources(c: httpx.AsyncClient) -> list[dict]:
    """Pull raw listings from public job APIs. Returns normalized candidates."""
    out = []
    # Arbeitnow: one page = 20 (labeled) jobs
    try:
        r = await c.get("https://www.arbeitnow.com/api/job-board-api")
        r.raise_for_status()
        for j in r.json().get("data", []):
            out.append({
                "source": "arbeitnow",
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": ", ".join(filter(None, [j.get("location"), j.get("remote") and "Remote" or None])),
                "url": j.get("url", ""),
                "tags": j.get("tags", [])[:6],
                "snippet": j.get("description", "")[:900] if j.get("description") else "",
                "remote": bool(j.get("remote")),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[jobcrawl] arbeitnow failed: {e}")
    # Remotive: search endpoint, no key
    try:
        r = await c.get("https://remotive.com/api/remote-jobs", params={"search": "engineer", "limit": 30})
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            out.append({
                "source": "remotive",
                "title": j.get("title", ""),
                "company": (j.get("company_name") or ""),
                "location": j.get("candidate_required_location", ""),
                "url": j.get("url", ""),
                "tags": [t if isinstance(t, str) else t.get("name", "") for t in (j.get("tags") or [])][:6],
                "snippet": (j.get("description") or "")[:900],
                "remote": True,
            })
    except Exception as e:  # noqa: BLE001
        print(f"[jobcrawl] remotive failed: {e}")
    # dedupe by url
    seen, uniq = set(), []
    for j in out:
        if j["url"] and j["url"] not in seen:
            seen.add(j["url"])
            uniq.append(j)
    return uniq


async def qwen_structure(c: httpx.AsyncClient, job: dict, goal: str) -> dict:
    prompt = (
        f"Extract structured job data from this listing. Goal context: {goal}.\n"
        "Return ONLY a JSON object with keys: title, company, location, remote (boolean), "
        "employment_type (best guess: full-time/part-time/contract), salary (string or null), "
        "tech_stack (array of up to 8 technologies mentioned), seniority (junior/mid/senior/lead/unknown).\n\n"
        f"Title: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
        f"Remote: {job['remote']}\nTags: {', '.join(job['tags'])}\nDescription: {job['snippet'][:700]}"
    )
    r = await c.post(f"{QWEN}/chat/completions", json={
        "model": QWEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    # strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"parse_error": text[:200]}


async def main(goal: str, target: int, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log = {"goal": goal, "candidates": 0, "jev_decisions": 0, "jev_ms_total": 0.0,
           "approved": 0, "results": []}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        candidates = await fetch_sources(c)
        log["candidates"] = len(candidates)
        print(f"[jobcrawl] {len(candidates)} candidates from public APIs")
        approved = []
        for job in candidates:
            state = (
                f"Job listing:\nTitle: {job['title']}\nCompany: {job['company']}\n"
                f"Location: {job['location']} (remote={job['remote']})\n"
                f"Tags: {', '.join(job['tags'])}\nDescription excerpt: {job['snippet'][:600]}"
            )
            questions = {
                "relevant": {"type": "noul", "instructions": f"Goal: {goal}. Is this job relevant to the goal?"},
                "tech": {"type": "noul", "instructions": "Is this an engineering/technical role (not sales, HR, marketing, design)?"},
                "seniority": {"type": "choice", "instructions": "Seniority level of this role?",
                              "criteria": {"junior": "Entry or junior level", "mid": "Mid-level engineer",
                                           "senior": "Senior/staff/principal level", "unknown": "Cannot tell from the listing"}},
            }
            tj = time.perf_counter()
            a = await jev(state, questions)
            log["jev_decisions"] += 3
            log["jev_ms_total"] += (time.perf_counter() - tj) * 1000
            rec = {"title": job["title"][:70], "company": job["company"],
                   "relevant_p": a["relevant"]["probability"],
                   "tech_p": a["tech"]["probability"], "jev_seniority": a["seniority"]["value"]}
            if a["relevant"]["value"] == "yes" and a["relevant"]["probability"] >= 0.7 and a["tech"]["value"] == "yes":
                rec["approved"] = True
                approved.append(job)
                log["approved"] += 1
                print(f"[jobcrawl] APPROVED {job['title'][:60]} @ {job['company'][:25]} "
                      f"(rel={a['relevant']['probability']:.2f} tech={a['tech']['probability']:.2f} {a['seniority']['value']})")
            log["results"].append(rec)
            if len(approved) >= target:
                break
        # Qwen structures the winners
        structured = []
        for job in approved[:target]:
            try:
                s = await qwen_structure(c, job, goal)
                structured.append({**job, "snippet": job["snippet"][:200], **s})
                print(f"[jobcrawl] structured: {job['title'][:55]}")
            except Exception as e:  # noqa: BLE001
                print(f"[jobcrawl] qwen parse failed for {job['title'][:40]}: {e}")
    (out / "jobs_structured.json").write_text(json.dumps(structured, indent=2))
    (out / "decision_log.json").write_text(json.dumps(log, indent=2))
    dt = time.time() - t0
    avg = log["jev_ms_total"] / max(log["jev_decisions"], 1)
    print(f"\n[jobcrawl] DONE: {log['candidates']} candidates -> {len(structured)} structured jobs in {dt:.1f}s")
    print(f"[jobcrawl] jev: {log['jev_decisions']} decisions, avg {avg:.1f}ms each")
    print(f"[jobcrawl] out: {out / 'jobs_structured.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default="remote machine learning / AI engineering jobs")
    ap.add_argument("--target", type=int, default=15)
    ap.add_argument("--out", default="jobcrawl_out")
    a = ap.parse_args()
    asyncio.run(main(a.goal, a.target, Path(a.out)))
