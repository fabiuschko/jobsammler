"""Firmen-Finder, Schritt 1: Mengen in Berlin über OpenRegister abfragen.
Liest firmen/queries.json, schreibt data/firmen/results.json.
Der API-Schlüssel kommt aus dem GitHub-Secret OPENREGISTER_API_KEY und wird nie ausgegeben."""
import json, os, sys, time, urllib.request, urllib.error

KEY = os.environ.get("OPENREGISTER_API_KEY")
if not KEY:
    sys.exit("OPENREGISTER_API_KEY fehlt")
URL = "https://api.openregister.de/v1/search/company"

def search(body):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:500]}

cfg = json.load(open("firmen/queries.json"))
base = cfg["base"]
out = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": []}
for q in cfg["queries"]:
    body = {"filters": base + q.get("filters", []), "pagination": {"page": q.get("page", 1), "per_page": q.get("per_page", 1)}}
    status, data = search(body)
    entry = {"name": q["name"], "status": status, "body": body}
    if status == 200:
        entry["pagination"] = data.get("pagination")
        entry["sample"] = [{k: c.get(k) for k in ("company_id", "name", "legal_form", "purpose")} | {"city": (c.get("address") or {}).get("city")}
                           for c in data.get("results", [])[: q.get("keep", 5)]]
    else:
        entry["error"] = data.get("error")
    out["results"].append(entry)
    print(q["name"], status, (entry.get("pagination") or {}).get("total_results"), flush=True)
    if status in (401, 402):
        break
    time.sleep(1)
os.makedirs("data/firmen", exist_ok=True)
json.dump(out, open("data/firmen/results.json", "w"), ensure_ascii=False, indent=1)
