"""Erkennt, welches Bewerbungssystem eine Firma nutzt, und trägt sie in config/companies.yaml ein.

Benutzung (im Projektordner):
    .venv/bin/python scripts/detect_ats.py "Firmenname"                 # nur anzeigen
    .venv/bin/python scripts/detect_ats.py "Firmenname" --add           # anzeigen und eintragen
    .venv/bin/python scripts/detect_ats.py "Firma" --website firma.de --add
    .venv/bin/python scripts/detect_ats.py --all                        # alle Firmen ohne System neu prüfen

Vorgehen:
1. Website der Firma öffnen (angegeben oder geraten: name.com, .de, .io, .ai ...),
   Karriereseiten suchen und darin Links auf bekannte Bewerbungssysteme finden.  -> "sicher"
2. Klappt das nicht: die Kennung direkt bei den Systemen ausprobieren.            -> "vermutet"
Jeder Fund wird mit einem echten Abruf der Stellenliste bestätigt.
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from sources.ats_systems import SYSTEMS, api_url, board_name, count_jobs, find_in_html, find_other  # noqa: E402
from sources.common import ROOT, PoliteSession  # noqa: E402

COMPANIES = ROOT / "config" / "companies.yaml"
CAREER_HINT = re.compile(r"career|karriere|jobs|stellen|join|work-with-us|arbeiten|vacanc|openings|hiring", re.I)
LEGAL = re.compile(r"\b(gmbh|ag|se|inc|ltd|llc|co|kg|group|holding|software|technologies|global)\b", re.I)

sess = PoliteSession(pause=0.3, timeout=10, retries=0)
sess.s.headers["User-Agent"] = "Mozilla/5.0 (compatible; jobsammler/0.1; +https://github.com/fabiuschko/jobsammler)"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", LEGAL.sub(" ", (s or "").lower()))


def slugs(name):
    base = re.sub(r"\b(gmbh|ag|se|kg|ug|mbh|co|inc|ltd|llc|group)\b\.?", " ", (name or "").lower())
    base = base.replace("&", "and")
    base = re.sub(r"[^a-z0-9 -]", "", base).strip()
    words = base.replace("-", " ").split()
    out = ["".join(words), "-".join(words)]
    if words:
        out.append(words[0])
    out += [w.capitalize() for w in out[:1]]  # Ashby/Lever unterscheiden Groß-/Kleinschreibung
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def get(url):
    try:
        r = sess.get(url, allow_redirects=True)
        return r
    except Exception:  # noqa: BLE001
        return None


GERMANY = re.compile(r"berlin|germany|deutschland|münchen|munich|hamburg|köln|cologne|frankfurt|dach|\bde\b", re.I)


def validate(system, ident, need_germany=False):
    """(Anzahl Stellen, Name laut System) oder (None, '') wenn ungültig.
    need_germany: nur gültig, wenn mindestens eine Stelle in Deutschland liegt."""
    url = api_url(system, ident)
    if system == "smartrecruiters" and need_germany:
        url += "?country=de"
    r = get(url)
    if r is None or r.status_code != 200:
        return None, ""
    n = count_jobs(system, r)
    if n is None:
        return None, ""
    if need_germany and system != "smartrecruiters" and not GERMANY.search(r.text):
        return None, ""
    name = board_name(system, r)
    if system == "greenhouse":
        b = get(f"https://boards-api.greenhouse.io/v1/boards/{ident}")
        if b is not None and b.status_code == 200:
            name = b.json().get("name", "")
    return n, name


def name_matches(company, board):
    a, b = norm(company), norm(board)
    return bool(a and b and (a in b or b in a))


def site_pages(name, website):
    """Startseite + Karriereseiten einer Firma als (url, html)."""
    if website:
        w = website if website.startswith("http") else f"https://{website}"
        candidates = [w]
    else:
        candidates = [f"https://{s}.{tld}" for s in slugs(name)[:2] for tld in ("com", "de", "io", "ai", "co", "app")]
    home = None
    for url in candidates:
        r = get(url)
        if r is None or r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            continue
        # Bei geratenen Adressen: Firmenname muss auf der Seite stehen (Schutz vor fremden Domains)
        if not website and norm(name) not in norm(r.text[:200000]):
            continue
        home = (r.url, r.text)
        break
    if not home:
        return []
    pages = [home]
    links = []
    for href in re.findall(r'href=["\']([^"\'#]+)', home[1]):
        full = urljoin(home[0], href)
        if CAREER_HINT.search(href) and full not in links:
            links.append(full)
    host = urlparse(home[0]).netloc
    for path in ("/careers", "/karriere", "/jobs", "/en/careers", "/de/karriere"):
        u = f"https://{host}{path}"
        if u not in links:
            links.append(u)
    for u in links[:7]:
        if find_in_html(" ".join(h for _, h in pages)):
            break  # schon fündig
        r = get(u)
        if r is not None and r.status_code < 400:
            pages.append((r.url, r.text))
    return pages


def detect(name, website=""):
    res = {"name": name, "system": "", "id": "", "website": website, "confidence": "", "jobs": None, "note": ""}
    pages = site_pages(name, website)
    if pages:
        res["website"] = res["website"] or pages[0][0]
        all_html = " ".join(u + " " + h for u, h in pages)
        for system, ident in find_in_html(all_html):
            n, bname = validate(system, ident)
            # z. B. VC-Website verlinkt auf eine Portfolio-Firma: Name (oder Kennung) muss zur Firma passen
            if not name_matches(name, bname or ident):
                continue
            if n is not None and (system != "smartrecruiters" or n > 0):
                res.update(system=system, id=ident, confidence="sicher", jobs=n)
                return res
        others = find_other(all_html)
        if others:
            res["note"] = "nutzt vermutlich " + ", ".join(others) + " (noch nicht unterstützt)"
    else:
        res["note"] = "Website nicht gefunden"

    # Kennung direkt bei den Systemen ausprobieren
    for system in SYSTEMS:
        for s in slugs(name):
            ident = s if system != "smartrecruiters" else s.replace("-", "")
            n, bname = validate(system, ident, need_germany=True)
            if not n:
                continue
            if bname and not name_matches(name, bname):
                continue  # gleiche Kennung, andere Firma
            res.update(system=system, id=ident, confidence="vermutet", jobs=n)
            res["note"] = (res["note"] + "; " if res["note"] else "") + (
                f"Name im System: {bname}" if bname else "per Kennung gefunden, bitte prüfen"
            )
            return res
    return res


# ---------- companies.yaml lesen/schreiben ----------

HEADER = """# Zielfirmen und ihre Bewerbungssysteme.
# Neue Firma: im Projektordner ausführen
#   .venv/bin/python scripts/detect_ats.py "Firmenname" --add
# oder hier einen Eintrag mit name (und am besten website) ergänzen und
#   .venv/bin/python scripts/detect_ats.py --all
# laufen lassen. system/id füllt das Skript aus.
# Unterstützte Systeme: greenhouse, lever, ashby, personio, workable, smartrecruiters
# confidence: "sicher" = auf der Karriereseite gefunden, "vermutet" = nur über die Kennung (bitte prüfen),
#             "geprüft" = vermutet und von Hand bestätigt, "manuell" = von Hand eingetragen
# Mehrere Kennungen mit Komma trennen (z. B. think-cell).
# Weitere Orte nur für diese Firma: orte: [Berlin, München]  (sonst gilt: Berlin oder remote in Deutschland)

"""


def load():
    if not COMPANIES.exists():
        return {"companies": [], "nicht_erkannt": []}
    d = yaml.safe_load(COMPANIES.read_text(encoding="utf-8")) or {}
    d.setdefault("companies", [])
    d.setdefault("nicht_erkannt", [])
    return d


def save(d):
    d["companies"].sort(key=lambda c: c["name"].lower())
    d["nicht_erkannt"].sort(key=lambda c: c["name"].lower())
    body = yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=120)
    COMPANIES.write_text(HEADER + body, encoding="utf-8")


def entry(res, old=None):
    e = {"name": res["name"], "system": res["system"], "id": res["id"], "website": res["website"]}
    if res["confidence"]:
        e["confidence"] = res["confidence"]
    note = (old or {}).get("note") or res["note"]
    if note:
        e["note"] = note
    if (old or {}).get("orte"):
        e["orte"] = old["orte"]
    return e


def upsert(d, res, old=None):
    key = norm(res["name"])
    d["companies"] = [c for c in d["companies"] if norm(c["name"]) != key]
    d["nicht_erkannt"] = [c for c in d["nicht_erkannt"] if norm(c["name"]) != key]
    if res["system"]:
        d["companies"].append(entry(res, old))
    else:
        d["nicht_erkannt"].append(
            {"name": res["name"], "website": res["website"], "note": res["note"] or "kein System erkannt"}
        )


def show(res):
    if res["system"]:
        print(f"✓ {res['name']}: {res['system']} / {res['id']} ({res['confidence']}, {res['jobs']} Stellen) {res['note']}")
    else:
        print(f"✗ {res['name']}: nicht erkannt. {res['note']} [{res['website'] or '-'}]")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?")
    ap.add_argument("--website", default="")
    ap.add_argument("--add", action="store_true", help="Ergebnis in config/companies.yaml eintragen")
    ap.add_argument("--all", action="store_true", help="alle Firmen ohne System (und 'nicht_erkannt') neu prüfen")
    a = ap.parse_args()

    if a.all:
        d = load()
        todo = [c for c in d["companies"] if not c.get("system")] + list(d["nicht_erkannt"])
        for c in todo:
            res = detect(c["name"], c.get("website", ""))
            show(res)
            upsert(d, res, c)
        save(d)
        print(f"\n{len(d['companies'])} Firmen mit System, {len(d['nicht_erkannt'])} nicht erkannt. ({sess.calls} Aufrufe)")
    elif a.name:
        res = detect(a.name, a.website)
        show(res)
        if a.add:
            d = load()
            upsert(d, res)
            save(d)
            print("→ in config/companies.yaml eingetragen")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
