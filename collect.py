"""Job-Sammler: sammelt offene Stellen aus allen Quellen und schreibt data/jobs.json und data/run.json.

Regel: Eine Stelle ist offen, wenn sie heute in der Quelle steht. Fehlt sie, ist sie geschlossen.
Geschlossene Stellen bleiben 60 Tage in der Datei (ab dem letzten Tag, an dem sie gesehen wurden).

Aufruf:  .venv/bin/python collect.py      (läuft täglich als GitHub Action)
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sources import arbeitnow, ats, ba
from sources.common import ROOT, load_yaml, normalize_title

DATA = ROOT / "data"
JOBS_FILE = DATA / "jobs.json"
RUN_FILE = DATA / "run.json"
KEEP_CLOSED_DAYS = 60
# Vorrang beim Zusammenführen: Firmen-Bewerbungssystem > Arbeitnow > Arbeitsagentur
PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "personio": 0, "workable": 0, "smartrecruiters": 0, "arbeitnow": 1, "ba": 2}
FIELDS = ["id", "source", "company", "title", "url", "applyUrl", "location", "remote", "published",
          "firstSeen", "lastSeen", "open", "roleGuess", "seniorFlag", "sourceTier", "salary", "description"]


def today_berlin():
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def company_key(name):
    n = (name or "").lower()
    n = re.sub(r"\b(gmbh|ag|se|kg|ug|mbh|co|inc|ltd|llc|group|holding|deutschland|germany|berlin)\b\.?", " ", n)
    return re.sub(r"[^a-z0-9äöü]", "", n)


def build_excluder(cfg):
    terms = [t for key in ("firmen", "vermittler") for t in (cfg.get(key) or []) if str(t).strip()]
    parts = []
    for t in terms:
        t = re.escape(str(t).strip().lower())
        # kurze Wörter nur als ganzes Wort ("welt" soll nicht "Umwelt" treffen)
        parts.append(rf"\b{t}\b" if len(t) < 7 else rf"\b{t}")
    rx = re.compile("|".join(parts), re.I) if parts else None
    return lambda company: bool(rx and rx.search(company or ""))


def load_old():
    if not JOBS_FILE.exists():
        return {}
    try:
        return {j["id"]: j for j in json.loads(JOBS_FILE.read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return {}


def merge_duplicates(jobs):
    """Dieselbe Stelle aus mehreren Quellen (oder mehrfach ausgeschrieben) zusammenführen.
    Schlüssel: Firma + normalisierter Titel. Gewinner: höchste Quellen-Priorität; leere Felder
    werden aus den anderen Treffern ergänzt."""
    groups = {}
    for j in jobs:
        groups.setdefault((company_key(j["company"]), normalize_title(j["title"])), []).append(j)
    merged = []
    for group in groups.values():
        group.sort(key=lambda j: (PRIORITY.get(j["source"], 9), j["location"] != "Berlin", j["id"]))
        win = dict(group[0])
        for other in group[1:]:
            for f in ("applyUrl", "salary", "description", "published"):
                if not win.get(f) and other.get(f):
                    win[f] = other[f]
            if other.get("remote"):
                win["remote"] = True
        win["_ids"] = [j["id"] for j in group]
        merged.append(win)
    return merged


def main():
    t0 = time.time()
    today = today_berlin()
    today_s = today.isoformat()
    roles = load_yaml("config/roles.yaml")
    companies = load_yaml("config/companies.yaml").get("companies", [])
    is_excluded = build_excluder(load_yaml("config/exclude.yaml"))
    old = load_old()

    found, errors, sources_ok, stats = [], [], set(), {}

    # 1) Firmen-Bewerbungssysteme (Kern)
    try:
        jobs, info = ats.fetch(companies, roles, known=old)
        found += jobs
        errors += info["errors"]
        failed = {e.split(" für ", 1)[1].split(":", 1)[0] for e in info["errors"] if " antwortet nicht für " in e}
        sources_ok.add("ats")
        stats["firmen"] = {"stellen": len(jobs), "aufrufe": info["calls"], "firmen_mit_fehler": sorted(failed),
                           "aussortiert": info["dropped"]}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Firmen-Bewerbungssysteme komplett fehlgeschlagen: {exc}")
        failed = set()

    # 2) Arbeitnow (Kern)
    try:
        jobs, info = arbeitnow.fetch(roles)
        found += jobs
        errors += info["errors"]
        if not info["errors"]:
            sources_ok.add("arbeitnow")
        stats["arbeitnow"] = {"stellen": len(jobs), "aufrufe": info["calls"], "aussortiert": info["dropped"]}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Arbeitnow fehlgeschlagen: {exc}")

    # 3) Arbeitsagentur (breites Netz)
    try:
        jobs, info = ba.fetch(roles, known=old)
        found += jobs
        errors += info["errors"]
        search_errors = [e for e in info["errors"] if "Suche" in e]
        if not search_errors:
            sources_ok.add("ba")
        stats["arbeitsagentur"] = {"stellen": len(jobs), "aufrufe": info["calls"], "aussortiert": info["dropped"],
                                   "abgeschnitten": info["truncated"]}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Arbeitsagentur fehlgeschlagen: {exc}")

    # Ausschlüsse (Axel Springer, Immobilien, Vermittler) und Qualitätsregel: nur direkte Links
    excluded = sum(1 for j in found if is_excluded(j["company"]))
    found = [j for j in found if not is_excluded(j["company"]) and j.get("url")]

    merged = merge_duplicates(found)

    # offen / geschlossen
    result, seen_ids = {}, set()
    new_count = 0
    for j in merged:
        ids = j.pop("_ids")
        seen_ids.update(ids)
        prev = next((old[i] for i in ids if i in old), None)
        if prev is None:
            new_count += 1
        j["firstSeen"] = min([old[i]["firstSeen"] for i in ids if i in old and old[i].get("firstSeen")] or [today_s])
        j["lastSeen"] = today_s
        j["open"] = True
        result[j["id"]] = {f: j.get(f, "") for f in FIELDS}

    closed_today = 0
    cutoff = (today - timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    for jid, j in old.items():
        if jid in result or jid in seen_ids:
            continue
        src = j.get("source")
        source_ran = (
            ("ats" in sources_ok and j.get("company") not in failed) if PRIORITY.get(src) == 0
            else (src in sources_ok)
        )
        if j.get("open") and not source_ran:
            result[jid] = j  # Quelle heute nicht erreichbar: Status unverändert lassen
            continue
        if j.get("open"):
            j = {**j, "open": False}
            closed_today += 1
        if j.get("lastSeen", today_s) >= cutoff:
            result[jid] = j

    # offen zuerst, darin die neuesten zuerst
    jobs_out = sorted(result.values(), key=lambda j: (j["firstSeen"], j["company"].lower()), reverse=True)
    jobs_out.sort(key=lambda j: not j["open"])

    DATA.mkdir(exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs_out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    open_jobs = [j for j in jobs_out if j["open"]]
    per_source = {}
    for j in open_jobs:
        per_source[j["source"]] = per_source.get(j["source"], 0) + 1
    run = {
        "datum": today_s,
        "gestartet": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
        "dauerSekunden": round(time.time() - t0),
        "offen": len(open_jobs),
        "neuHeute": new_count,
        "geschlossenHeute": closed_today,
        "geschlossenInDatei": len(jobs_out) - len(open_jobs),
        "offenProQuelle": dict(sorted(per_source.items(), key=lambda x: -x[1])),
        "offenProRolle": dict(sorted({r: sum(1 for j in open_jobs if j["roleGuess"] == r)
                                      for r in {j["roleGuess"] for j in open_jobs}}.items(), key=lambda x: -x[1])),
        "ausgeschlossen": excluded,
        "zusammengefuehrt": len(found) - len(merged),
        "quellen": stats,
        "fehler": errors,
    }
    RUN_FILE.write_text(json.dumps(run, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"{today_s}: {len(open_jobs)} offen ({new_count} neu, {closed_today} heute geschlossen), "
          f"{len(errors)} Fehler, {run['dauerSekunden']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
