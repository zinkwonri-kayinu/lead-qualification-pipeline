"""CLI: python -m leadqual.pipeline --input leads.csv --out out/ [--dry-run]"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from .checks import Check, rule_verdict, run_checks

VERDICTS = ("valid", "invalid", "unclear")
JudgeFn = Callable[[dict, list[Check], dict], "object"]


def qualify(lead: dict, icp: dict, judge_fn: JudgeFn | None) -> dict:
    checks = run_checks(lead, icp)
    verdict = rule_verdict(checks)
    source, reason = "rules", "; ".join(f"{c.name}: {c.detail}" for c in checks if c.status != "pass")

    # Hard fails are final. Everything else not cleanly valid goes to the judge.
    if verdict == "unclear" and judge_fn is not None:
        j = judge_fn(lead, checks, icp)
        verdict, source, reason = j.verdict, f"claude ({j.confidence})", j.reason

    return {
        **lead,
        "verdict": verdict,
        "decided_by": source,
        "reason": reason or "all checks passed",
        "checks": json.dumps({c.name: c.status for c in checks}),
    }


def run(rows: list[dict], icp: dict, judge_fn: JudgeFn | None) -> list[dict]:
    return [qualify(row, icp, judge_fn) for row in rows]


def write_outputs(results: list[dict], out_dir: Path) -> Counter:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter(r["verdict"] for r in results)
    if not results:
        return counts
    fields = list(results[0].keys())
    for verdict in VERDICTS:
        with open(out_dir / f"{verdict}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(r for r in results if r["verdict"] == verdict)
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Qualify enriched leads into valid / invalid / unclear.")
    p.add_argument("--input", required=True, type=Path, help="CSV of enriched leads")
    p.add_argument("--out", default=Path("out"), type=Path, help="output directory")
    p.add_argument("--icp", default=Path(__file__).parent.parent / "config" / "icp.json", type=Path)
    p.add_argument("--dry-run", action="store_true", help="rules only, no API calls")
    args = p.parse_args(argv)

    icp = json.loads(args.icp.read_text(encoding="utf-8"))
    with open(args.input, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    judge_fn = None
    if not args.dry_run:
        import anthropic

        from .judge import judge

        client = anthropic.Anthropic()
        judge_fn = lambda lead, checks, icp_: judge(client, lead, checks, icp_)  # noqa: E731

    results = run(rows, icp, judge_fn)
    counts = write_outputs(results, args.out)
    judged = sum(1 for r in results if r["decided_by"].startswith("claude"))
    print(f"{len(rows)} leads -> " + ", ".join(f"{v}: {counts[v]}" for v in VERDICTS) + f" ({judged} sent to Claude)")
    print(f"wrote {args.out}/valid.csv, invalid.csv, unclear.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
