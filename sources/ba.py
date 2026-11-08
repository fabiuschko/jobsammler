"""Jobsuche der Bundesagentur für Arbeit (breites Netz, sourceTier "breit").

Inoffizielle, öffentlich genutzte Schnittstelle, dokumentiert von bundesAPI:
https://github.com/bundesAPI/jobsuche-api

Stand der Prüfung (2026-11-08):
- Suche nur noch über /pc/v6/jobs (v4 antwortet mit 403).
- Details (Beschreibungstext) über /pc/v4/jobdetails/<base64(refnr)> (v5/v6 antworten mit 403).
- zeitarbeit=false und pav=false schließen Zeitarbeit und private Arbeitsvermittlung schon bei der Suche aus.
- Ein Homeoffice-Filter in der Suche funktioniert nicht; remote erkennen wir am Feld homeofficeprozent == 100.
"""

import base64
import re

from .common import PoliteSession, RoleMatcher, is_excluded_title, senior_flag, to_text

API = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
PUBLIC_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{}"

PAGE_SIZE = 100
MAX_PAGES_BERLIN = 5   # pro Suchbegriff höchstens 500 Treffer in Berlin
MAX_PAGES_REMOTE = 1   # deutschlandweit nur die ersten 100, daraus nur 100 % Homeoffice

# Unerwünscht im breiten Netz (Titel, Beruf oder Firma)
EXCLUDE_BA = re.compile(
    r"au(ß|ss)endienst|field sales|callcenter|call center|call-center|telefonverkauf|telesales|"
    r"telefonist|personalvermittlung|arbeitnehmerüberlassung|personaldienstleist|zeitarbeit|"
    r"handelsvertreter|promoter|haustür|door to door|immobilien",
    re.I,
)


def _search(sess, params):
    r = sess.get(f"{API}/v6/jobs", params=params, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def _salary(j):
    lo, hi = j.get("gehaltsspanneVon"), j.get("gehaltsspanneBis")
    if not lo and not hi:
        return ""
    unit = {"JAHRESGEHALT": "Jahr", "MONATSGEHALT": "Monat", "STUNDENLOHN": "Stunde"}.get(
        j.get("verguetungsangabe", ""), ""
    )
    fmt = lambda x: f"{x:,.0f}".replace(",", ".")  # noqa: E731
    span = f"{fmt(lo)}–{fmt(hi)} €" if lo and hi and lo != hi else f"{fmt(lo or hi)} €"
    return f"{span} / {unit}" if unit else span


def _orte(j):
    return [
        (l.get("adresse") or {}).get("ort", "")
        for l in j.get("stellenlokationen") or []
        if (l.get("adresse") or {}).get("ort")
    ]


def _keep(j, matcher):
    """Gibt (roleGuess, Grund fürs Aussortieren) zurück."""
    title = j.get("stellenangebotsTitel", "")
    text = " ".join([title, j.get("hauptberuf", ""), " ".join(j.get("alleBerufe") or []), j.get("firma", "")])
    if is_excluded_title(title):
        return "", "praktikum/ausbildung/werkstudent"
    if EXCLUDE_BA.search(text):
        return "", "außendienst/callcenter/vermittlung"
    if j.get("istGeringfuegigeBeschaeftigung"):
        return "", "minijob"
    if j.get("arbeitszeitVollzeit") is False:
        return "", "nur teilzeit"
    role = matcher.guess(title)
    if not role:
        return "", "titel passt zu keiner rolle"
    return role, ""


def _record(j, role, remote):
    ref = j["referenznummer"]
    orte = _orte(j)
    location = "Berlin" if "Berlin" in orte else ", ".join(dict.fromkeys(orte))
    title = j.get("stellenangebotsTitel", "").strip()
    return {
        "id": f"ba:{ref}",
        "source": "ba",
        "company": (j.get("firma") or "").strip(),
        "title": title,
        "url": PUBLIC_URL.format(ref),
        "applyUrl": j.get("externeURL", "") or "",
        "location": location,
        "remote": remote,
        "published": j.get("datumErsteVeroeffentlichung", "") or "",
        "roleGuess": role,
        "seniorFlag": senior_flag(title),
        "sourceTier": "breit",
        "salary": _salary(j),
        "description": "",
    }


def fetch_details(sess, refnr):
    """Holt die Detailseite. Gibt das JSON zurück oder None, wenn die Anzeige weg ist."""
    enc = base64.b64encode(refnr.encode()).decode()
    r = sess.get(f"{API}/v4/jobdetails/{enc}", headers=HEADERS)
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    return r.json()


def fetch(roles, known=None, max_details=250, sess=None, log=print):
    """Sammelt Stellen aus der BA-Jobsuche.

    roles:  Inhalt von config/roles.yaml
    known:  dict id -> bisheriger Eintrag (um Beschreibungen nicht erneut zu laden)
    Rückgabe: (jobs, info) mit info = {"calls", "errors", "dropped", "truncated"}
    """
    sess = sess or PoliteSession()
    known = known or {}
    matcher = RoleMatcher(roles)
    jobs, errors, dropped, truncated = {}, [], {}, []
    base = {"angebotsart": 1, "zeitarbeit": "false", "pav": "false", "size": PAGE_SIZE}

    for key, spec in roles.items():
        for term in spec.get("terms", []):
            # 1) Berlin und 25 km Umkreis, alle Seiten (bis zur Obergrenze)
            # 2) deutschlandweit, nur Stellen mit 100 % Homeoffice
            for mode, extra, max_pages in (
                ("berlin", {"wo": "Berlin", "umkreis": 25}, MAX_PAGES_BERLIN),
                ("remote", {}, MAX_PAGES_REMOTE),
            ):
                for page in range(1, max_pages + 1):
                    try:
                        d = _search(sess, {**base, **extra, "was": term, "page": page})
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"BA: Suche '{term}' ({mode}, Seite {page}) fehlgeschlagen: {exc}")
                        break
                    items = d.get("ergebnisliste") or []
                    for j in items:
                        if not j.get("referenznummer"):
                            continue
                        remote = j.get("homeofficeprozent") == 100
                        if mode == "remote" and not remote:
                            continue
                        if mode == "berlin" and "Berlin" not in _orte(j) and not remote:
                            dropped["nicht in berlin"] = dropped.get("nicht in berlin", 0) + 1
                            continue
                        role, why = _keep(j, matcher)
                        if why:
                            dropped[why] = dropped.get(why, 0) + 1
                            continue
                        rec = _record(j, role, remote)
                        jobs.setdefault(rec["id"], rec)
                    total = d.get("maxErgebnisse") or 0
                    if page * PAGE_SIZE >= total or not items:
                        break
                    if mode == "berlin" and page == max_pages:
                        truncated.append(f"{term}: {total} Treffer, nur {max_pages * PAGE_SIZE} gelesen")

    # Beschreibungstexte: bekannte übernehmen, neue (begrenzt) nachladen
    loaded = 0
    for jid, rec in jobs.items():
        old = known.get(jid) or {}
        if old.get("description"):
            rec["description"] = old["description"]
            continue
        if loaded >= max_details:
            continue
        try:
            det = fetch_details(sess, jid.split(":", 1)[1])
            loaded += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"BA: Details für {jid} fehlgeschlagen: {exc}")
            continue
        if det:
            rec["description"] = to_text(det.get("stellenangebotsBeschreibung", ""))
            rec["applyUrl"] = rec["applyUrl"] or det.get("externeUrl", "") or ""

    log(f"BA: {len(jobs)} Stellen, {sess.calls} Aufrufe, {loaded} Beschreibungen geladen")
    return list(jobs.values()), {
        "calls": sess.calls,
        "errors": errors,
        "dropped": dropped,
        "truncated": truncated,
    }


if __name__ == "__main__":
    import json
    import random
    import sys

    from .common import ROOT, load_yaml

    roles = load_yaml("config/roles.yaml")
    jobs, info = fetch(roles, max_details=int(sys.argv[1]) if len(sys.argv) > 1 else 30)
    out = ROOT / "data" / "ba_test.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"jobs": jobs, "info": info}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if k != "errors"}, ensure_ascii=False, indent=1))
    print("Fehler:", len(info["errors"]))
    for j in random.sample(jobs, min(10, len(jobs))):
        print(f"- [{j['roleGuess']}] {j['title']} | {j['company']} | {j['location']} | {j['url']}")
