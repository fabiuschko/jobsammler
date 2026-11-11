"""Firmen-Finder: zufällige Stichprobe aus den behaltenen Branchengruppen ziehen.
Liest firmen/sample.json (Gruppen, Anzahl je Gruppe, Runde), schreibt data/firmen/sample_r<Runde>.json."""
import json, os, random, sys, time, urllib.request, urllib.error
KEY = os.environ.get("OPENREGISTER_API_KEY") or sys.exit("OPENREGISTER_API_KEY fehlt")
URL = "https://api.openregister.de/v1/search/company"
def search(body):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, {"error": e.read().decode(errors="replace")[:300]}
cfg = json.load(open("firmen/sample.json"))
rnd = random.Random(cfg["seed"])
out = {"round": cfg["round"], "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "companies": [], "log": []}
for g in cfg["groups"]:
    n = g["n"]; pages = max(1, g["total"] // n); page = rnd.randint(1, pages)
    body = {"filters": cfg["base"] + [{"field": "primary_industry_codes", "values": g["codes"]}], "pagination": {"page": page, "per_page": n}}
    st, data = search(body)
    out["log"].append({"group": g["name"], "status": st, "page": page, "pagination": data.get("pagination"), "error": data.get("error")})
    print(g["name"], st, page, flush=True)
    for c in data.get("results", []) if st == 200 else []:
        a = c.get("address") or {}
        out["companies"].append({"id": c.get("company_id"), "name": c.get("name"), "legal_form": c.get("legal_form"),
            "purpose": c.get("purpose"), "address": a.get("formatted_value"), "group": g["name"]})
    if st in (401, 402): break
    time.sleep(1)
os.makedirs("data/firmen", exist_ok=True)
json.dump(out, open(f"data/firmen/sample_r{cfg['round']}.json", "w"), ensure_ascii=False, indent=1)
