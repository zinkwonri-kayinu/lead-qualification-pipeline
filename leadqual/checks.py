"""Deterministic checks. Cheap, explainable, and run before any LLM call.

Each check returns a Check(name, status, detail) where status is
"pass", "fail" or "unknown" (data missing). The rule verdict is:
  - invalid  if any hard check fails
  - valid    if every check passes
  - unclear  otherwise (sent to the LLM judge)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me"}
HARD_CHECKS = {"email_domain", "linkedin_company", "excluded_title", "excluded_industry"}
COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc|group|hq)\b\.?", re.I)


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # pass | fail | unknown
    detail: str


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    return value.split("/")[0] or None


def normalize_company(value: str | None) -> str:
    if not value:
        return ""
    value = COMPANY_SUFFIXES.sub("", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def company_similarity(a: str | None, b: str | None) -> float:
    a, b = normalize_company(a), normalize_company(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def check_email_domain(lead: dict) -> Check:
    email, domain = lead.get("email", ""), normalize_domain(lead.get("domain"))
    if not email or "@" not in email or not domain:
        return Check("email_domain", "unknown", "email or company domain missing")
    email_domain = email.rsplit("@", 1)[1].lower()
    if email_domain in FREE_EMAIL_DOMAINS:
        return Check("email_domain", "unknown", f"personal inbox ({email_domain})")
    if email_domain == domain or email_domain.endswith("." + domain):
        return Check("email_domain", "pass", f"{email_domain} matches {domain}")
    return Check("email_domain", "fail", f"email is @{email_domain}, company is {domain}")


def check_linkedin_company(lead: dict) -> Check:
    """Is the person still at the company we think they're at?"""
    current = lead.get("linkedin_company")
    if not current:
        return Check("linkedin_company", "unknown", "no LinkedIn company on record")
    score = company_similarity(current, lead.get("company"))
    if score >= 0.8:
        return Check("linkedin_company", "pass", f"LinkedIn shows {current}")
    if score >= 0.5:
        return Check("linkedin_company", "unknown", f"LinkedIn shows {current} (partial match {score:.2f})")
    return Check("linkedin_company", "fail", f"LinkedIn shows {current}, not {lead.get('company')}")


def _title_hits(title: str, keywords: list[str]) -> list[str]:
    title = title.lower()
    return [kw for kw in keywords if kw.lower() in title]


def check_target_title(lead: dict, icp: dict) -> Check:
    title = lead.get("linkedin_title") or lead.get("title") or ""
    if not title:
        return Check("target_title", "unknown", "no title")
    hits = _title_hits(title, icp.get("target_titles", []))
    if hits:
        return Check("target_title", "pass", f"'{title}' matches {hits[0]}")
    return Check("target_title", "unknown", f"'{title}' not in target list")


def check_excluded_title(lead: dict, icp: dict) -> Check:
    title = lead.get("linkedin_title") or lead.get("title") or ""
    hits = _title_hits(title, icp.get("exclude_titles", []))
    if hits:
        return Check("excluded_title", "fail", f"'{title}' is excluded ({hits[0]})")
    return Check("excluded_title", "pass", "no excluded title keywords")


def check_excluded_industry(lead: dict, icp: dict) -> Check:
    industry = (lead.get("industry") or "").lower()
    if not industry:
        return Check("excluded_industry", "unknown", "no industry")
    hits = [x for x in icp.get("exclude_industries", []) if x.lower() in industry]
    if hits:
        return Check("excluded_industry", "fail", f"industry '{industry}' is excluded")
    return Check("excluded_industry", "pass", f"industry '{industry}' allowed")


def check_employee_band(lead: dict, icp: dict) -> Check:
    raw = str(lead.get("employee_count") or "").replace(",", "").strip()
    if not raw.isdigit():
        return Check("employee_band", "unknown", "no headcount")
    n, band = int(raw), icp.get("employee_count", {})
    lo, hi = band.get("min", 0), band.get("max", 10**9)
    if lo <= n <= hi:
        return Check("employee_band", "pass", f"{n} employees in {lo}-{hi}")
    return Check("employee_band", "fail", f"{n} employees outside {lo}-{hi}")


def run_checks(lead: dict, icp: dict) -> list[Check]:
    return [
        check_email_domain(lead),
        check_linkedin_company(lead),
        check_target_title(lead, icp),
        check_excluded_title(lead, icp),
        check_excluded_industry(lead, icp),
        check_employee_band(lead, icp),
    ]


def rule_verdict(checks: list[Check]) -> str:
    if any(c.status == "fail" and c.name in HARD_CHECKS for c in checks):
        return "invalid"
    if all(c.status == "pass" for c in checks):
        return "valid"
    return "unclear"
