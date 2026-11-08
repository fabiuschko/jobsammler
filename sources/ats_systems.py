"""Die sechs unterstützten Bewerbungssysteme: Adressen, Erkennungsmuster, Prüfung.

Wird von scripts/detect_ats.py (Erkennen) und sources/ats.py (Abrufen) genutzt.
"""

import re
import xml.etree.ElementTree as ET

SYSTEMS = ("greenhouse", "lever", "ashby", "personio", "workable", "smartrecruiters")


def api_url(system, ident):
    if system == "greenhouse":
        # gilt auch für Firmen auf job-boards.eu.greenhouse.io
        return f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs?content=true"
    if system == "lever":
        # Kennung "eu:<firma>" für Firmen auf jobs.eu.lever.co
        if ident.startswith("eu:"):
            return f"https://api.eu.lever.co/v0/postings/{ident[3:]}?mode=json"
        return f"https://api.lever.co/v0/postings/{ident}?mode=json"
    if system == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{ident}?includeCompensation=true"
    if system == "personio":
        # Kennung "<firma>" (jobs.personio.de) oder "<firma>.com" (jobs.personio.com)
        if ident.endswith(".com"):
            return f"https://{ident[:-4]}.jobs.personio.com/xml"
        return f"https://{ident}.jobs.personio.de/xml"
    if system == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{ident}"
    if system == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{ident}/postings"
    raise ValueError(system)


# Muster, um auf einer Karriereseite das System und die Kennung zu finden.
PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?for=([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("ashby", re.compile(r"ashbyhq\.com/embed\?.*?organization=([A-Za-z0-9_.-]+)")),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(de|com)")),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v1/widget/accounts/|j/)?([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
]

# Andere Systeme, die wir (noch) nicht abrufen, aber für Fabian benennen
OTHER = {
    "Recruitee": r"\.recruitee\.com",
    "Teamtailor": r"\.teamtailor\.com|teamtailor-cdn",
    "JOIN": r"join\.com/companies",
    "softgarden": r"softgarden\.io|softgarden\.de",
    "Workday": r"myworkdayjobs\.com",
    "SuccessFactors": r"successfactors\.(com|eu)|career\d*\.sapsf",
    "BambooHR": r"\.bamboohr\.com",
    "onlyfy/Prescreen": r"onlyfy\.jobs|prescreen\.io",
    "rexx": r"rexx-systems\.com|rexx-recruitment",
    "d.vinci": r"dvinci-hr\.com|dvinci\.de",
    "HiBob": r"hibob\.com/careers|careers\.hibob",
    "Pinpoint": r"pinpointhq\.com",
    "Comeet": r"comeet\.(co|com)/jobs",
    "Breezy": r"breezy\.hr",
}

IGNORE_IDS = {"embed", "js", "jobs", "job_board", "api", "v1", "j", "widget", "careers", "static", "assets"}


def find_in_html(html):
    """Alle (system, kennung)-Paare, die in einer Seite vorkommen, in Fundreihenfolge."""
    found = []
    for system, rx in PATTERNS:
        for m in rx.finditer(html):
            if system == "lever":
                ident = ("eu:" if m.group(1) else "") + m.group(2)
            elif system == "personio":
                ident = m.group(1) + (".com" if m.group(2) == "com" else "")
            else:
                ident = m.group(1)
            ident = ident.strip("/.")
            if ident.lower() in IGNORE_IDS or not ident:
                continue
            if (system, ident) not in found:
                found.append((system, ident))
    return found


def find_other(html):
    return [name for name, rx in OTHER.items() if re.search(rx, html, re.I)]


def count_jobs(system, payload):
    """Anzahl Stellen aus einer API-Antwort (None = Antwort unbrauchbar)."""
    try:
        if system == "greenhouse":
            return len(payload.json().get("jobs", []))
        if system == "lever":
            d = payload.json()
            return len(d) if isinstance(d, list) else None
        if system == "ashby":
            return len(payload.json().get("jobs", []))
        if system == "personio":
            root = ET.fromstring(payload.content)
            return len(root.findall("position")) if root.tag == "workzag-jobs" else None
        if system == "workable":
            return len(payload.json().get("jobs", []))
        if system == "smartrecruiters":
            return payload.json().get("totalFound")
    except Exception:  # noqa: BLE001
        return None
    return None


def board_name(system, payload):
    """Firmenname laut System, falls das System ihn liefert (sonst '')."""
    try:
        if system == "workable":
            return payload.json().get("name", "") or ""
        if system == "smartrecruiters":
            c = payload.json().get("content") or []
            return (c[0].get("company") or {}).get("name", "") if c else ""
    except Exception:  # noqa: BLE001
        pass
    return ""
