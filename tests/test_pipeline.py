import csv
import json
from pathlib import Path

from leadqual.checks import company_similarity, normalize_domain, rule_verdict, run_checks
from leadqual.judge import Judgement, build_prompt
from leadqual.pipeline import main, run

ROOT = Path(__file__).parent.parent
ICP = json.loads((ROOT / "config" / "icp.json").read_text())
LEADS = list(csv.DictReader(open(ROOT / "examples" / "leads.csv", encoding="utf-8")))
BY_NAME = {row["first_name"]: row for row in LEADS}


def verdict(name):
    return rule_verdict(run_checks(BY_NAME[name], ICP))


def test_normalizers():
    assert normalize_domain("https://www.Acme.com/about") == "acme.com"
    assert company_similarity("Brightpath Inc.", "BrightPath") == 1.0
    assert company_similarity("Northwind Analytics", "Contoso Data") < 0.5


def test_clean_lead_is_valid():
    assert verdict("Priya") == "valid"


def test_person_who_left_is_invalid():
    assert verdict("Dana") == "invalid"


def test_excluded_title_and_industry_are_invalid():
    assert verdict("Tom") == "invalid"
    assert verdict("Lena") == "invalid"


def test_soft_signals_are_unclear():
    assert verdict("Aisha") == "unclear"  # personal inbox
    assert verdict("Jon") == "unclear"  # title not on the target list


def test_judge_only_sees_unclear_leads():
    seen = []

    def fake_judge(lead, checks, icp):
        seen.append(lead["first_name"])
        return Judgement(verdict="valid", confidence="medium", reason="notes resolve it")

    results = run(LEADS, ICP, fake_judge)
    assert sorted(seen) == ["Aisha", "Jon"]
    assert {r["first_name"]: r["decided_by"] for r in results}["Jon"] == "claude (medium)"


def test_prompt_carries_evidence():
    lead = BY_NAME["Jon"]
    prompt = build_prompt(lead, run_checks(lead, ICP), ICP)
    assert "target_title: unknown" in prompt
    assert "rebranded" in prompt


def test_dry_run_writes_three_files(tmp_path, capsys):
    main(["--input", str(ROOT / "examples" / "leads.csv"), "--out", str(tmp_path), "--dry-run"])
    assert {p.name for p in tmp_path.iterdir()} == {"valid.csv", "invalid.csv", "unclear.csv"}
    assert "valid: 2, invalid: 3, unclear: 2" in capsys.readouterr().out
