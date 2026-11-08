"""Stellen direkt aus den Bewerbungssystemen der Zielfirmen (sourceTier "kern").

Firmen und Kennungen stehen in config/companies.yaml (erstellt mit scripts/detect_ats.py).
Unterstützt: Greenhouse, Lever, Ashby, Personio, Workable, SmartRecruiters.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .ats_systems import api_url
from .common import PoliteSession, RoleMatcher, is_excluded_title, place, senior_flag, to_text

# Beschäftigungsarten, die rausfallen (Praktikum, Werkstudent, Teilzeit, Ausbildung, freie Mitarbeit)
EXCLUDE_TYPE = re.compile(r"intern|praktik|werkstudent|working student|trainee|apprentice|ausbildung|part[- ]?time|teilzeit|freelance|temporary", re.I)


def _day(value):
    if not value:
        return ""
    if isinstance(value, (int, float)):  # Lever: Millisekunden
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(value)[:10]


def _base(source, ident, jid, company, title, url, **kw):
    rec = {
        "id": f"{source}:{ident}:{jid}",
        "source": source,
        "company": company,
        "title": (title or "").strip(),
        "url": url or "",
        "applyUrl": "",
        "location": "",
        "remote": False,
        "published": "",
        "roleGuess": "",
        "seniorFlag": senior_flag(title),
        "sourceTier": "kern",
        "salary": "",
        "description": "",
    }
    rec.update(kw)
    return rec


# ---------- je System: Rohdaten -> (Eintrag, Ortsangaben, remote_flag, Beschäftigungsart) ----------

def _greenhouse(sess, company, ident):
    r = sess.get(api_url("greenhouse", ident))
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        meta = {m.get("name", ""): str(m.get("value") or "") for m in j.get("metadata") or []}
        work_model = " ".join(v for k, v in meta.items() if re.search(r"work ?model|remote|workplace", k, re.I))
        emp = " ".join(v for k, v in meta.items() if re.search(r"working time|employment|contract", k, re.I))
        locs = [(j.get("location") or {}).get("name", "")] + [o.get("name", "") for o in j.get("offices") or []]
        rec = _base(
            "greenhouse", ident, j["id"], company, j.get("title"), j.get("absolute_url"),
            published=_day(j.get("first_published") or j.get("updated_at")),
            description=to_text(html.unescape(j.get("content") or "")),
        )
        yield rec, locs, bool(re.search(r"remote", work_model, re.I)), emp


def _lever(sess, company, ident):
    r = sess.get(api_url("lever", ident))
    r.raise_for_status()
    slug = ident[3:] if ident.startswith("eu:") else ident
    for j in r.json():
        cat = j.get("categories") or {}
        locs = [cat.get("location", "")] + list(cat.get("allLocations") or [])
        sal = j.get("salaryRange") or {}
        salary = ""
        if sal.get("min") or sal.get("max"):
            salary = f"{sal.get('min', '')}–{sal.get('max', '')} {sal.get('currency', '')} / {sal.get('interval', '')}".strip()
        rec = _base(
            "lever", slug, j["id"], company, j.get("text"), j.get("hostedUrl"),
            applyUrl=j.get("applyUrl", "") or "",
            published=_day(j.get("createdAt")),
            salary=salary,
            description=to_text(j.get("descriptionPlain") or j.get("description") or ""),
        )
        yield rec, locs, j.get("workplaceType") == "remote", cat.get("commitment", "")


def _ashby(sess, company, ident):
    r = sess.get(api_url("ashby", ident))
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        locs.append(" ".join(filter(None, [addr.get("addressLocality"), addr.get("addressCountry")])))
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        rec = _base(
            "ashby", ident, j["id"], company, j.get("title"), j.get("jobUrl"),
            applyUrl=j.get("applyUrl", "") or "",
            published=_day(j.get("publishedAt")),
            salary=comp if j.get("shouldDisplayCompensationOnJobPostings") else "",
            description=to_text(j.get("descriptionPlain") or j.get("descriptionHtml") or ""),
        )
        remote = bool(j.get("isRemote")) or str(j.get("workplaceType", "")).lower() == "remote"
        yield rec, locs, remote, j.get("employmentType", "")


def _personio(sess, company, ident):
    r = sess.get(api_url("personio", ident))
    r.raise_for_status()
    host = f"{ident[:-4]}.jobs.personio.com" if ident.endswith(".com") else f"{ident}.jobs.personio.de"
    for p in ET.fromstring(r.content).findall("position"):
        pid = p.findtext("id")
        locs = [p.findtext("office") or ""] + [o.text or "" for o in p.findall("additionalOffices/office")]
        desc = "\n".join(
            f"{d.findtext('name') or ''}\n{d.findtext('value') or ''}" for d in p.findall("jobDescriptions/jobDescription")
        )
        rec = _base(
            "personio", ident, pid, company, p.findtext("name"), f"https://{host}/job/{pid}",
            published=_day(p.findtext("createdAt")),
            description=to_text(desc),
        )
        emp = f"{p.findtext('employmentType') or ''} {p.findtext('schedule') or ''}"
        yield rec, locs, False, emp


def _workable(sess, company, ident):
    r = sess.get(api_url("workable", ident) + "?details=true")
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        locs = [" ".join(filter(None, [j.get("city"), j.get("country")]))]
        locs += [" ".join(filter(None, [l.get("city"), l.get("country")])) for l in j.get("locations") or []]
        rec = _base(
            "workable", ident, j.get("shortcode"), company, j.get("title"), j.get("url") or j.get("shortlink"),
            applyUrl=j.get("application_url", "") or "",
            published=_day(j.get("published_on") or j.get("created_at")),
            description=to_text(j.get("description") or ""),
        )
        yield rec, locs, bool(j.get("telecommuting")), j.get("employment_type", "")


def _smartrecruiters(sess, company, ident):
    offset = 0
    while True:
        # Securitas & Co. sind weltweit: nur Stellen in Deutschland abfragen
        r = sess.get(api_url("smartrecruiters", ident), params={"limit": 100, "offset": offset, "country": "de"})
        r.raise_for_status()
        d = r.json()
        items = d.get("content") or []
        for j in items:
            loc = j.get("location") or {}
            rec = _base(
                "smartrecruiters", ident, j["id"], company, j.get("name"),
                f"https://jobs.smartrecruiters.com/{ident}/{j['id']}",
                published=_day(j.get("releasedDate")),
            )
            rec["_detail"] = j.get("ref", "")
            emp = f"{(j.get('typeOfEmployment') or {}).get('label', '')} {(j.get('experienceLevel') or {}).get('id', '')}"
            yield rec, [loc.get("fullLocation") or loc.get("city", "")], bool(loc.get("remote")), emp
        offset += len(items)
        if not items or offset >= (d.get("totalFound") or 0) or offset >= 1000:
            break


FETCHERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "personio": _personio,
    "workable": _workable,
    "smartrecruiters": _smartrecruiters,
}


def company_postings(sess, company):
    """Alle Stellen einer Firma (ungefiltert) als Liste von (Eintrag, Ortsangaben, remote_flag, Beschäftigungsart).
    Wird auch genutzt, um Stellen von VC-Börsen bei der Firma selbst zu bestätigen."""
    out = []
    for ident in [i.strip() for i in str(company.get("id", "")).split(",") if i.strip()]:
        out.extend(FETCHERS[company["system"]](sess, company["name"], ident))
    return out


def fetch(companies, roles, known=None, max_details=150, sess=None, log=print):
    """Sammelt passende Stellen aller Firmen aus companies.yaml.

    Rückgabe: (jobs, info) mit info = {"calls", "errors", "per_company", "dropped"}
    """
    sess = sess or PoliteSession(pause=0.3)
    known = known or {}
    matcher = RoleMatcher(roles)
    jobs, errors, per_company, dropped = {}, [], {}, {}

    def drop(why):
        dropped[why] = dropped.get(why, 0) + 1

    for c in companies:
        if not c.get("system") or c["system"] not in FETCHERS:
            continue
        try:
            postings = company_postings(sess, c)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{c['system'].capitalize()} antwortet nicht für {c['name']}: {exc}")
            continue
        kept = 0
        for rec, locs, remote_flag, emp in postings:
            if is_excluded_title(rec["title"]) or EXCLUDE_TYPE.search(emp or ""):
                drop("praktikum/teilzeit/ausbildung")
                continue
            location, remote, ok = place(locs, remote_flag, c.get("orte"))
            if not ok:
                drop("ort passt nicht")
                continue
            role = matcher.guess(rec["title"])
            if not role:
                drop("titel passt zu keiner rolle")
                continue
            rec.update(location=location, remote=remote, roleGuess=role)
            jobs.setdefault(rec["id"], rec)
            kept += 1
        per_company[c["name"]] = {"gesamt": len(postings), "passend": kept}

    # SmartRecruiters liefert den Text nur einzeln: für neue Stellen nachladen
    loaded = 0
    for rec in jobs.values():
        ref = rec.pop("_detail", "")
        if not ref or rec["description"]:
            continue
        old = known.get(rec["id"]) or {}
        if old.get("description"):
            rec["description"] = old["description"]
            continue
        if loaded >= max_details:
            continue
        try:
            d = sess.get(ref).json()
            loaded += 1
            secs = ((d.get("jobAd") or {}).get("sections") or {})
            rec["description"] = to_text("\n".join((s or {}).get("text", "") for s in secs.values()))
            rec["applyUrl"] = d.get("applyUrl", "") or ""
        except Exception as exc:  # noqa: BLE001
            errors.append(f"SmartRecruiters: Details für {rec['id']} fehlgeschlagen: {exc}")

    log(f"Firmen: {len(jobs)} Stellen aus {len(per_company)} Firmen, {sess.calls} Aufrufe")
    return list(jobs.values()), {"calls": sess.calls, "errors": errors, "per_company": per_company, "dropped": dropped}


if __name__ == "__main__":
    import json

    from .common import ROOT, load_yaml

    roles = load_yaml("config/roles.yaml")
    companies = load_yaml("config/companies.yaml").get("companies", [])
    jobs, info = fetch(companies, roles)
    out = ROOT / "data" / "ats_test.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"jobs": jobs, "info": info}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=1))
