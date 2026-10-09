"""Step 2: download free regulator rosters that link licensees to firms, count ACTIVE
individual licensees per firm, keep firms with 35-75 (inclusive).

Each adapter yields normalised person rows; firms are never counted as their own
licensee. A count covers only that source's jurisdiction.
Usage: python 02_headcount.py RAW_DIR OUT_CSV LOG_CSV
"""
import csv, io, json, os, re, subprocess, sys, zipfile, datetime
from collections import defaultdict

RAW, OUT, LOG = sys.argv[1:4]
os.makedirs(RAW, exist_ok=True)
TODAY = datetime.date.today().isoformat()


def fetch(url, dest):
    """curl with retries; returns (ok, message). Never fabricates data on failure."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True, "cached"
    r = subprocess.run(["curl", "-sS", "-L", "--fail", "--retry", "4", "--retry-delay", "2",
                        "-m", "1800", "-o", dest, url], capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(dest):
            os.remove(dest)
        return False, r.stderr.strip()[:200]
    return True, "downloaded"


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def pick(row, *cands):
    keys = {k.lower().strip(): k for k in row}
    for c in cands:
        if c in keys:
            return row[keys[c]]
    raise KeyError(f"none of {cands} in columns {list(row)[:30]}")


# ---------- adapters: each returns list of dicts ----------
# person_id, firm_key, firm_name, address, city, state, zip

def ny(path):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
        lt = norm(pick(r, "license type", "license_type"))
        if not lt or any(x in lt for x in ("CORPORAT", "PARTNERSHIP", "LLC", "TRADE NAME", "OFFICE", "BRANCH")):
            continue  # business records are not people
        firm = norm(pick(r, "business name", "business_name"))
        if not firm:
            continue
        exp = pick(r, "license expiration date", "license_expiration_date")
        out.append(dict(person_id=pick(r, "license number", "license_number"), firm_key=firm,
                        firm_name=firm, address=norm(pick(r, "business address 1", "business_address_1")),
                        city=norm(pick(r, "business city", "business_city")), state="NY",
                        zip=(pick(r, "business zip", "business_zip") or "")[:5], exp=exp))
    return out  # dataset only contains ACTIVE licenses


def ct(path):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
        b = norm(pick(r, "broker credential", "broker_credential"))
        if not b:
            continue
        out.append(dict(person_id=pick(r, "salesperson credential", "salesperson_credential"),
                        firm_key=b, firm_name=norm(pick(r, "broker name", "broker_name")),
                        address="", city="", state="CT", zip=""))
    return out  # active salespersons only; supervising brokers themselves not listed


FL_COLS = ["board", "occ_code", "name", "dba", "rank", "addr1", "addr2", "addr3", "city", "state",
           "zip", "county", "lic_no", "primary_status", "secondary_status", "orig_date",
           "status_date", "exp_date", "alt_lic", "self_prop", "employer_name", "employer_lic"]


def fl(path):
    out = []
    for row in csv.reader(open(path, encoding="latin-1")):
        if len(row) < len(FL_COLS):
            continue
        r = dict(zip(FL_COLS, row))
        if norm(r["primary_status"]) != "C" or norm(r["secondary_status"]) != "A":
            continue  # keep Current + Active only
        if norm(r["rank"]) not in ("SL", "BK", "SL ", "BL"):  # sales associate / broker (individual ranks)
            continue
        emp = norm(r["employer_lic"])
        if not emp:
            continue
        out.append(dict(person_id=r["lic_no"], firm_key=emp, firm_name=norm(r["employer_name"]),
                        address="", city="", state="FL", zip=""))
    return out


def tx(path):
    out = []
    for r in json.load(open(path)):
        lt = norm(r.get("license_type", ""))
        st = norm(r.get("license_status", r.get("status", "")))
        if "ACTIVE" not in st or lt.startswith("BROKER COMPANY") or "COMPANY" in lt or "BUSINESS" in lt:
            continue
        firm = norm(r.get("related_license_number") or r.get("sponsor_license_number") or "")
        if not firm:
            continue
        out.append(dict(person_id=r.get("license_number"), firm_key=firm,
                        firm_name=norm(r.get("related_license_name") or r.get("sponsor_name") or ""),
                        address="", city="", state="TX", zip=""))
    return out


def ca(path):
    out = []
    z = zipfile.ZipFile(path) if path.endswith(".zip") else None
    fh = io.TextIOWrapper(z.open(z.namelist()[0]), encoding="latin-1") if z else open(path, encoding="latin-1")
    for r in csv.DictReader(fh):
        if norm(pick(r, "lic_status")) != "LICENSED" or norm(pick(r, "lic_type")) not in ("SALESPERSON", "BROKER"):
            continue
        rel = norm(pick(r, "related_lic_number"))
        if not rel:
            continue
        out.append(dict(person_id=pick(r, "lic_number"), firm_key=rel,
                        firm_name=norm(pick(r, "related_lastname_primary")),
                        address="", city=norm(pick(r, "city")), state="CA", zip=""))
    return out


# NY/CT/TX URLs are the Socrata export endpoints of the datasets named in
# output/sources_checked.csv. FL and CA direct file paths could NOT be confirmed in this
# run (hosts blocked); confirm them on the landing pages before running:
#   FL https://www2.myfloridalicense.com/real-estate-commission/public-records/
#   CA https://www.dre.ca.gov/Licensees/ExamineeLicenseeListDataFiles.html
SOURCES = [
    ("NY", "https://data.ny.gov/api/views/yg7h-zjbf/rows.csv?accessType=DOWNLOAD", "ny.csv", ny),
    ("CT", "https://data.ct.gov/api/views/6tja-6vdt/rows.csv?accessType=DOWNLOAD", "ct.csv", ct),
    ("TX", "https://data.texas.gov/resource/s7ft-44qi.json?$limit=2000000", "tx.json", tx),
    ("FL", "https://www2.myfloridalicense.com/sto/file_download/extracts/RE_licensee_all.csv", "fl.csv", fl),  # UNCONFIRMED path,
    ("CA", "https://secure.dre.ca.gov/datafile/CurrList.zip", "ca.zip", ca),  # UNCONFIRMED path,
]

log = csv.writer(open(LOG, "w", newline=""))
log.writerow(["state", "url", "status", "message", "persons", "firms_35_75", "date"])
rows_out = []
for st, url, fn, fn_parse in SOURCES:
    ok, msg = fetch(url, os.path.join(RAW, fn))
    if not ok:
        log.writerow([st, url, "download_failed", msg, 0, 0, TODAY]); continue
    try:
        people = fn_parse(os.path.join(RAW, fn))
    except Exception as e:  # schema drift -> report, never guess
        log.writerow([st, url, "parse_failed", repr(e)[:200], 0, 0, TODAY]); continue
    firms = defaultdict(lambda: {"ids": set(), "names": defaultdict(int), "addr": defaultdict(int)})
    for p in people:
        f = firms[p["firm_key"]]
        f["ids"].add(p["person_id"]); f["names"][p["firm_name"]] += 1
        f["addr"][(p["address"], p["city"], p["zip"])] += 1
    n = 0
    for key, f in firms.items():
        c = len(f["ids"])
        if 35 <= c <= 75:
            n += 1
            addr, city, z = max(f["addr"], key=f["addr"].get)
            rows_out.append(dict(firm_key=key, brokerage_name=max(f["names"], key=f["names"].get),
                                 verified_headcount=c, headcount_source=url, state=st,
                                 city=city, zip=z, address=addr, date_collected=TODAY))
    log.writerow([st, url, "ok", msg, len(people), n, TODAY])

with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["firm_key", "brokerage_name", "verified_headcount",
                                       "headcount_source", "state", "city", "zip", "address", "date_collected"])
    w.writeheader(); w.writerows(rows_out)
print(f"firms with verified headcount 35-75: {len(rows_out)}")
