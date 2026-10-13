"""Scan public job boards (Greenhouse / Ashby / Lever APIs) of AI companies for
graduate / early-careers roles. Usage: python ats_scan.py companies.txt out.csv"""
import csv, json, re, sys, time, urllib.request

KW = re.compile(r"\b(graduate|grad\b|new grad|early[- ]career|intern(ship)?|apprentice|entry[- ]level|"
                r"university|campus|placement|residency|fellowship|junior|associate program|rotational)\b", re.I)
EXCL = re.compile(r"\b(senior|staff|principal|lead|head of|director|manager|recruiter)\b", re.I)


def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:
        return None


def jobs(slug):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if d and d.get("jobs") is not None:
        return "greenhouse", [(j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"]) for j in d["jobs"]]
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if d and d.get("jobs") is not None:
        return "ashby", [(j["title"], j.get("location", ""), j.get("jobUrl", "")) for j in d["jobs"]]
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if isinstance(d, list):
        return "lever", [(j["text"], (j.get("categories") or {}).get("location", ""), j.get("hostedUrl", "")) for j in d]
    return None, []


out = csv.writer(open(sys.argv[2], "w", newline=""))
out.writerow(["company", "ats", "total_open_roles", "early_roles", "title", "location", "url"])
for line in open(sys.argv[1]):
    if not line.strip() or line.startswith("#"):
        continue
    name, *slugs = [x.strip() for x in line.split("|")]
    ats, js = None, []
    for s in slugs:
        ats, js = jobs(s)
        if ats:
            break
    hits = [j for j in js if KW.search(j[0]) and not EXCL.search(j[0])]
    print(f"{name:28} {ats or 'not found':10} roles={len(js):4} early={len(hits)}", flush=True)
    if not hits:
        out.writerow([name, ats or "not found", len(js), 0, "", "", ""])
    for t, loc, u in hits:
        out.writerow([name, ats, len(js), len(hits), t, loc, u])
    time.sleep(0.5)
