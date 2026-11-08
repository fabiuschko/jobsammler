"""Arbeitnow: öffentliche Job-Board-API (Start-ups, viele englischsprachige Stellen in Deutschland).

https://www.arbeitnow.com/api/job-board-api
Bedingungen laut API-Antwort: kostenlos, nicht missbrauchen, Verlinkung auf Arbeitnow erwünscht.
Jede Stelle verlinkt deshalb auf ihre Arbeitnow-Seite.
"""

import re
from datetime import datetime, timezone

from .common import (
    PoliteSession,
    RoleMatcher,
    is_excluded_title,
    needs_other_language,
    place,
    senior_flag,
    to_text,
)

API = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 30  # je 300 Stellen
EXCLUDE_TYPE = re.compile(r"intern|praktik|werkstudent|working student|part[- ]?time|teilzeit|ausbildung|apprentice|mini", re.I)


def fetch(roles, sess=None, log=print):
    sess = sess or PoliteSession(pause=2.0)  # Arbeitnow bremst bei schnellen Abrufen (429)
    matcher = RoleMatcher(roles)
    jobs, errors, dropped = {}, [], {}
    total = 0

    def drop(why):
        dropped[why] = dropped.get(why, 0) + 1

    for page in range(1, MAX_PAGES + 1):
        try:
            r = sess.get(API, params={"page": page})
            r.raise_for_status()
            d = r.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Arbeitnow: Seite {page} fehlgeschlagen: {exc}")
            break
        items = d.get("data") or []
        total += len(items)
        for j in items:
            title = (j.get("title") or "").strip()
            if is_excluded_title(title) or EXCLUDE_TYPE.search(" ".join(j.get("job_types") or [])):
                drop("praktikum/teilzeit/ausbildung")
                continue
            if needs_other_language(title):
                drop("andere sprache als deutsch/englisch")
                continue
            location, remote, ok = place([j.get("location", "")], bool(j.get("remote")))
            if not ok:
                drop("ort passt nicht")
                continue
            role = matcher.guess(title)
            if not role:
                drop("titel passt zu keiner rolle")
                continue
            ts = j.get("created_at")
            published = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""
            rec = {
                "id": f"arbeitnow:{j['slug']}",
                "source": "arbeitnow",
                "company": (j.get("company_name") or "").strip(),
                "title": title,
                "url": j.get("url", ""),
                "applyUrl": "",
                "location": location,
                "remote": remote,
                "published": published,
                "roleGuess": role,
                "seniorFlag": senior_flag(title),
                "sourceTier": "kern",
                "salary": "",
                "description": to_text(j.get("description", "")),
            }
            jobs.setdefault(rec["id"], rec)
        if not (d.get("links") or {}).get("next") or not items:
            break

    log(f"Arbeitnow: {len(jobs)} von {total} Stellen passend, {sess.calls} Aufrufe")
    return list(jobs.values()), {"calls": sess.calls, "errors": errors, "dropped": dropped, "total": total}


if __name__ == "__main__":
    from .common import load_yaml

    jobs, info = fetch(load_yaml("config/roles.yaml"))
    print(info)
    for j in jobs:
        print(f"[{j['roleGuess']}] {j['title'][:60]} | {j['company'][:25]} | {j['location'][:15]}{' 🏠' if j['remote'] else ''} | {j['url']}")
