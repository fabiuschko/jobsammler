"""Schlägt neue Zielfirmen vor, damit die Liste in config/companies.yaml mitwächst.

Woher: Firmen, die bei Arbeitnow (Start-ups) und optional der Arbeitsagentur mit Stellen
auftauchen, die zu Fabians Rollen passen, und die noch nicht in companies.yaml stehen.
Für die häufigsten wird das Bewerbungssystem erkannt (scripts/detect_ats.py).
Ergebnis: Abschnitt "vorschlaege" in config/companies.yaml.

Benutzung (im Projektordner):
    .venv/bin/python scripts/suggest_companies.py                  # Vorschläge aus Arbeitnow (Top 25)
    .venv/bin/python scripts/suggest_companies.py --mit-ba --top 40
    .venv/bin/python scripts/suggest_companies.py --uebernehmen "Telli, Cosuno"   # Vorschläge in die Zielliste
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_ats as D  # noqa: E402

from sources import arbeitnow, ba  # noqa: E402
from sources.common import load_yaml  # noqa: E402

# Personalvermittler und Agenturen sind keine Zielfirmen
AGENCY = re.compile(
    r"recruit|personal|staffing|talent|headhunt|consult(ing|ants)|vermittlung|search|hr services|"
    r"zeitarbeit|people group|placement|jobs?\b|career",
    re.I,
)


def collect_candidates(with_ba):
    roles = load_yaml("config/roles.yaml")
    jobs, _ = arbeitnow.fetch(roles, log=lambda *_: None)
    if with_ba:
        bjobs, _ = ba.fetch(roles, max_details=0, log=lambda *_: None)
        jobs += bjobs
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=25, help="für so viele Firmen das System erkennen")
    ap.add_argument("--mit-ba", action="store_true", help="auch Firmen aus der Arbeitsagentur berücksichtigen")
    ap.add_argument("--uebernehmen", default="", help="Komma-Liste von Vorschlägen, die in die Zielliste wandern")
    a = ap.parse_args()

    d = D.load()
    d.setdefault("vorschlaege", [])

    if a.uebernehmen:
        wanted = {D.norm(n) for n in a.uebernehmen.split(",") if n.strip()}
        moved = [v for v in d["vorschlaege"] if D.norm(v["name"]) in wanted]
        for v in moved:
            if v.get("system"):
                res = {**v, "confidence": v.get("confidence", "vermutet"), "jobs": None, "note": v.get("note", "")}
                D.upsert(d, res)
            else:
                D.upsert(d, {"name": v["name"], "system": "", "id": "", "website": v.get("website", ""),
                             "confidence": "", "jobs": None, "note": "aus Vorschlägen, kein System erkannt"})
        d["vorschlaege"] = [v for v in d["vorschlaege"] if D.norm(v["name"]) not in wanted]
        save(d)
        print(f"{len(moved)} Firmen übernommen: {', '.join(v['name'] for v in moved)}")
        return

    known = {D.norm(c["name"]) for c in d["companies"] + d["nicht_erkannt"]}
    groups = {}
    for j in collect_candidates(a.mit_ba):
        name = j["company"]
        key = D.norm(name)
        if not key or key in known or AGENCY.search(name):
            continue
        g = groups.setdefault(key, {"name": name, "treffer": 0, "rollen": set(), "beispiel": j["title"], "quellen": set()})
        g["treffer"] += 1
        g["rollen"].add(j["roleGuess"])
        g["quellen"].add(j["source"])

    ranked = sorted(groups.values(), key=lambda g: (-g["treffer"], g["name"].lower()))[: a.top]
    old = {D.norm(v["name"]): v for v in d["vorschlaege"]}
    for g in ranked:
        res = D.detect(g["name"], (old.get(D.norm(g["name"])) or {}).get("website", ""))
        D.show(res)
        old[D.norm(g["name"])] = {
            "name": g["name"],
            "system": res["system"],
            "id": res["id"],
            "website": res["website"],
            **({"confidence": res["confidence"]} if res["confidence"] else {}),
            "treffer": g["treffer"],
            "rollen": ", ".join(sorted(r for r in g["rollen"] if r)),
            "beispiel": g["beispiel"],
            "quelle": ", ".join(sorted(g["quellen"])),
            **({"note": res["note"]} if res["note"] else {}),
        }
    d["vorschlaege"] = sorted(old.values(), key=lambda v: (-v.get("treffer", 0), v["name"].lower()))
    save(d)
    print(f"\n{len(d['vorschlaege'])} Vorschläge in config/companies.yaml (Abschnitt 'vorschlaege').")


def save(d):
    vorschlaege = d.pop("vorschlaege", [])
    D.save(d)  # sortiert companies / nicht_erkannt und schreibt den Kopf
    if vorschlaege:
        import yaml

        with open(D.COMPANIES, "a", encoding="utf-8") as f:
            f.write("\n# Vorschläge (von scripts/suggest_companies.py). Übernehmen mit --uebernehmen \"Name, Name\".\n")
            f.write(yaml.safe_dump({"vorschlaege": vorschlaege}, allow_unicode=True, sort_keys=False, width=120))
    d["vorschlaege"] = vorschlaege


if __name__ == "__main__":
    main()
