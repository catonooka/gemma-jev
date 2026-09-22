"""
bench_jev: latency + accuracy benchmark for the local simplejev server.

Part 1 (latency): N mixed noul/choice decisions, report p50/p95/p99 + tps.
Part 2 (accuracy): labeled test set with known-correct answers.
"""

import asyncio
import json
import random
import statistics
import time

import httpx

JEV = "http://127.0.0.1:8300"
N_LATENCY = 60

# ---- Part 2: labeled decisions (ground truth known by construction) ----
CASES = [
    # (state, question spec, expected value)
    # urgency noul
    ("The production database is down and all orders are failing.", ("noul", "Does this need immediate attention?"), "yes"),
    ("A user reports the footer logo is slightly misaligned on one page.", ("noul", "Does this need immediate attention?"), "no"),
    ("Checkout returns 500 errors; customers cannot pay.", ("noul", "Does this need immediate attention?"), "yes"),
    ("We want to add a new color theme option next quarter.", ("noul", "Does this need immediate attention?"), "no"),
    # routing choice (from djev README example)
    ("The checkout is down. Customers cannot pay.",
     ("choice", "Which team should handle this?", {"billing": "Charges, invoices, and refunds", "engineering": "Broken features and outages", "other": "Anything else"}), "engineering"),
    ("Customer says they were charged twice for their subscription.",
     ("choice", "Which team should handle this?", {"billing": "Charges, invoices, and refunds", "engineering": "Broken features and outages", "other": "Anything else"}), "billing"),
    ("A candidate asks about our vacation policy before an interview.",
     ("choice", "Which team should handle this?", { "billing": "Charges, invoices, and refunds", "engineering": "Broken features and outages", "other": "Anything else"}), "other"),
    # job relevance (the actual production use)
    ("Job: Senior .NET Developer at FPT Software, Hanoi. Requires C#, ASP.NET, 5 years experience.",
     ("noul", "Is this a .NET developer job?"), "yes"),
    ("Job: Marketing Manager at Unilever. Runs social media campaigns.",
     ("noul", "Is this a .NET developer job?"), "no"),
    ("Job: Lập trình viên .NET (Fresher/Junior) at Rikkeisoft, Hà Nội.",
     ("noul", "Is this a .NET developer job?"), "yes"),
    ("Job: Accountant for a restaurant chain, Ho Chi Minh City.",
     ("noul", "Is this a .NET developer job?"), "no"),
    ("Job: C# Developer building Windows desktop applications.",
     ("noul", "Is this a software engineering role?"), "yes"),
    ("Job: HR Business Partner for a tech company.",
     ("noul", "Is this a software engineering role?"), "no"),
    # tech vs non-tech nuance (known weak spot: designer titles)
    ("Job: Product Designer (AI x Greentech) designing user interfaces at Reonic.",
     ("noul", "Is this a software engineering role (writing code)?"), "no"),
    ("Job: Product Engineer building features with TypeScript at LightningAI.",
     ("noul", "Is this a software engineering role (writing code)?"), "yes"),
    # seniority
    ("Job: Junior .NET Developer (SQL / C# .NET) at Rikkeisoft, entry level welcome.",
     ("choice", "Seniority level?", {"junior": "Entry or junior level", "mid": "Mid-level", "senior": "Senior/staff level", "unknown": "Cannot tell"}), "junior"),
    ("Job: Principal Architect leading platform design across 5 teams.",
     ("choice", "Seniority level?", {"junior": "Entry or junior level", "mid": "Mid-level", "senior": "Senior/staff level", "unknown": "Cannot tell"}), "senior"),
    ("Job: Developer wanted. No level mentioned in the description.",
     ("choice", "Seniority level?", {"junior": "Entry or junior level", "mid": "Mid-level", "senior": "Senior/staff level", "unknown": "Cannot tell"}), "unknown"),
    # download gating (webagent use)
    ("Link on docs page: 'Quicktour — get started in 5 minutes' pointing to /docs/quicktour. Goal: find quickstart guide.",
     ("noul", "Should this link be downloaded for the goal?"), "yes"),
    ("Link on docs page: 'Careers at Hugging Face' pointing to /careers. Goal: find quickstart guide.",
     ("noul", "Should this link be downloaded for the goal?"), "no"),
    # scroll decision
    ("Page: search results for .NET jobs. 16 of 200+ results shown. Scrolled 0 of 3000 px. Goal: collect 20 jobs.",
     ("noul", "Is there likely more relevant content below the viewport?"), "yes"),
    ("Page: contact page fully scrolled, bottom reached. Only the address and phone number remain.",
     ("noul", "Is there likely more relevant content below the viewport?"), "no"),
]

async def one_request(c, state, qspec, qname="q"):
    kind, instr = qspec[0], qspec[1]
    q = {"type": kind, "instructions": instr}
    if kind in ("choice",):
        q["criteria"] = qspec[2]
    r = await c.post(f"{JEV}/v1/request", json={"state": state, "questions": {qname: q}})
    r.raise_for_status()
    return r.json()["answers"][qname]

async def latency_bench():
    prompts = [
        ("The build failed with a nil pointer in the payment service at 3am.", "noul", "Does this need immediate attention?"),
        ("Job: Senior .NET Developer, 5 years C# experience required.", "noul", "Is this a .NET developer job?"),
        ("Search results page showing items 1-15 of 200.", "noul", "Is there likely more content below the viewport?"),
    ]
    times = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        # warmup
        await one_request(c, "warmup state", ("noul", "Is this a test?"))
        for i in range(N_LATENCY):
            state, kind, instr = prompts[i % len(prompts)]
            t = time.perf_counter()
            await one_request(c, state, (kind, instr))
            times.append((time.perf_counter() - t) * 1000)
    times.sort()
    def pct(p): return times[min(int(len(times) * p), len(times) - 1)]
    return {
        "n": len(times), "min_ms": round(times[0], 1), "p50_ms": round(pct(0.5), 1),
        "p95_ms": round(pct(0.95), 1), "p99_ms": round(pct(0.99), 1), "max_ms": round(times[-1], 1),
        "mean_ms": round(statistics.mean(times), 1),
    }

async def accuracy_bench():
    results = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for state, qspec, expected in CASES:
            a = await one_request(c, state, qspec)
            ok = a["value"] == expected
            results.append({"state": state[:60], "expected": expected, "got": a["value"],
                            "p": a["probability"], "ok": ok})
    correct = sum(1 for r in results if r["ok"])
    return {"n": len(results), "correct": correct, "accuracy": round(correct / len(results), 3),
            "failures": [r for r in results if not r["ok"]]}

async def main():
    lat = await latency_bench()
    print("LATENCY:", json.dumps(lat))
    acc = await accuracy_bench()
    print(f"ACCURACY: {acc['correct']}/{acc['n']} = {acc['accuracy']*100:.1f}%")
    for f in acc["failures"]:
        print(f"  MISS: expected={f['expected']} got={f['got']} (p={f['p']}) :: {f['state']}")
    json.dump({"latency": lat, "accuracy": acc}, open("/home/aisever/simplejev/bench_results.json", "w"), indent=1)

asyncio.run(main())
