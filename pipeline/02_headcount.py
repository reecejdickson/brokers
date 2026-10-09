"""Step 2: download free regulator rosters that link licensees to firms, count ACTIVE
individual licensees (people, not firm records) per firm, keep firms with 35-75.

A count covers only that source's jurisdiction (multi-state firms may be undercounted).
Usage: python 02_headcount.py RAW_DIR OUT_CSV LOG_CSV
"""
import csv, io, os, re, subprocess, sys, zipfile, datetime
from collections import Counter, defaultdict

RAW, OUT, LOG = sys.argv[1:4]
os.makedirs(RAW, exist_ok=True)
TODAY = datetime.date.today().isoformat()
csv.field_size_limit(10**8)


def fetch(url, fn):
    dest = os.path.join(RAW, fn)
    if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
        r = subprocess.run(["curl", "-sS", "-L", "--fail", "--retry", "4", "--retry-delay", "2",
                            "-m", "3600", "-A", "Mozilla/5.0", "-o", dest, url], capture_output=True, text=True)
        if r.returncode != 0:
            if os.path.exists(dest):
                os.remove(dest)
            raise RuntimeError("download failed: " + r.stderr.strip()[:200])
    return dest


def N(s):
    return re.sub(r"\s+", " ", (s or "").replace("\x00", "").strip().upper())


class Firms:
    """firm_key -> people set + descriptive fields."""
    def __init__(self):
        self.people = defaultdict(set)
        self.info = {}
        self.addr_votes = defaultdict(Counter)

    def add(self, key, person, name=None):
        self.people[key].add(person)
        if name and key not in self.info:
            self.info[key] = dict(name=name)

    def set_info(self, key, **kw):
        self.info.setdefault(key, {}).update({k: v for k, v in kw.items() if v})


# ---------------- NY: data.ny.gov yg7h-zjbf (active licenses only) ----------------
def ny():
    url = "https://data.ny.gov/api/views/yg7h-zjbf/rows.csv?accessType=DOWNLOAD"
    f = Firms()
    offices = {}
    for r in csv.DictReader(open(fetch(url, "ny.csv"), encoding="utf-8", errors="replace")):
        r = {k.strip().lower().replace(" ", "_"): v for k, v in r.items()}
        lt, firm = N(r["license_type"]), N(r["business_name"])
        if not firm:
            continue
        addr = (N(r.get("business_address_1")), N(r.get("business_city")), N(r.get("business_state")),
                (r.get("business_zip") or "")[:5])
        if lt == "REAL ESTATE PRINCIPAL OFFICE":
            offices.setdefault(firm, addr); continue
        if lt == "REAL ESTATE BRANCH OFFICE":
            continue
        f.add(firm, r["license_number"], firm)
        f.addr_votes[firm][addr] += 1
    for k in f.people:
        a = offices.get(k) or f.addr_votes[k].most_common(1)[0][0]
        f.set_info(k, address=a[0], city=a[1], addr_state=a[2], zip=a[3])
    return f, url, "people = salespersons + all broker licence types whose business_name is the firm (firm key = business name, all NY offices combined)"


# ---------------- CT: data.ct.gov eqtn-rppv + fwpc-pgqj ----------------
def ct():
    u1 = "https://data.ct.gov/api/views/eqtn-rppv/rows.csv?accessType=DOWNLOAD"
    u2 = "https://data.ct.gov/api/views/fwpc-pgqj/rows.csv?accessType=DOWNLOAD"
    f = Firms()
    for r in csv.DictReader(open(fetch(u1, "ct_sales.csv"), encoding="utf-8", errors="replace")):
        r = {k.strip().lower().replace(" ", "_"): v for k, v in r.items()}
        b = N(r.get("broker_license"))
        if N(r["status"]) != "ACTIVE" or not b:
            continue
        f.add(b, r["fullcredentialcode"], N(r.get("supervising_broker")))
    for r in csv.DictReader(open(fetch(u2, "ct_brokers.csv"), encoding="utf-8", errors="replace")):
        r = {k.strip().lower().replace(" ", "_"): v for k, v in r.items()}
        k = N(r["fullcredentialcode"])
        if k in f.people:
            f.set_info(k, name=N(r.get("businessname")) or N(r.get("dba")) or N(r["name"]),
                       city=N(r.get("city")), addr_state=N(r.get("state")), zip=(r.get("zip") or "")[:5])
    return f, u1 + " ; " + u2, "people = ACTIVE salespersons whose supervising broker licence is the firm (brokers are not linked to firms in CT data)"


# ---------------- TX: data.texas.gov s7ft-44qi ----------------
def tx():
    url = "https://data.texas.gov/api/views/s7ft-44qi/rows.csv?accessType=DOWNLOAD"
    f = Firms()
    for r in csv.DictReader(open(fetch(url, "tx.csv"), encoding="utf-8", errors="replace")):
        r = {k.strip().lower().replace(" ", "_"): v for k, v in r.items()}
        lt, st, rel = N(r["license_type"]), N(r["status"]), N(r["related_license_number"])
        active = st == "ACTIVE" or st.endswith("- ACTIVE")
        if not active:
            continue
        if lt == "SALES AGENT" and rel:
            f.add(rel, r["license_number"], N(r["related_license_full_name"]))
        elif lt == "BROKER COMPANY":
            k = N(r["license_number"])
            f.set_info(k, name=N(r["full_name"]), county=N(r["county"]))
            if rel:  # the company's designated broker
                f.add(k, rel)
        elif lt == "BROKER INDIVIDUAL":
            f.set_info(N(r["license_number"]), county=N(r["county"]))
    return f, url, "people = ACTIVE sales agents sponsored by the firm licence + its designated broker (other brokers are not linked in TX data)"


# ---------------- FL: DBPR extracts ----------------
FLC = ["occ", "name", "dba", "rank", "a1", "a2", "a3", "city", "state", "zip", "county", "lic",
       "pstat", "sstat", "orig", "eff", "exp", "alt", "selfprop", "emp_name", "emp_lic"]


def fl():
    u1 = "https://www2.myfloridalicense.com/sto/file_download/extracts//REALESTATE2501LICENSE_1.csv"
    u2 = "https://www2.myfloridalicense.com/sto/file_download/extracts//RealEstateCorpLicense.csv"
    f = Firms()
    for row in csv.reader(open(fetch(u1, "fl_sales.csv"), encoding="latin-1")):
        r = dict(zip(FLC, row))
        if N(r.get("pstat")) != "CURRENT" or N(r.get("sstat")) != "ACTIVE" or not N(r.get("emp_lic")):
            continue
        f.add(N(r["emp_lic"]), r["alt"] or r["lic"], N(r["emp_name"]))
    for row in csv.reader(open(fetch(u2, "fl_corp.csv"), encoding="latin-1")):
        r = dict(zip(FLC, row))
        k = N(r.get("lic"))
        if k in f.people:
            f.set_info(k, name=N(r["name"]), dba=N(r["dba"]), address=N(r["a1"]), city=N(r["city"]),
                       addr_state=N(r["state"]), zip=(r["zip"] or "")[:5])
    return f, u1 + " ; " + u2, "people = Current/Active sales associates + brokers + broker-sales whose employer licence is the firm"


# ---------------- CA: DRE licensee list + broker associate list ----------------
def ca():
    u1 = "https://secure.dre.ca.gov/datafile/CurrList.zip"
    u2 = "https://secure.dre.ca.gov/datafile/broker_associates_list.xls"
    f = Firms()
    zf = zipfile.ZipFile(fetch(u1, "ca.zip"))
    rows = list(csv.DictReader(io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="latin-1")))
    entity = {}
    for r in rows:
        if r["lic_status"] == "Licensed" and r["lic_type"] in ("Corporation", "Broker"):
            entity[r["lic_number"]] = r
    for r in rows:
        if r["lic_status"] != "Licensed":
            continue  # 'Licensed NBA' = no employing broker
        if r["lic_type"] == "Salesperson" and r["related_lic_type"] in ("Corporation", "Broker") and r["related_lic_number"]:
            f.add(r["related_lic_number"], r["lic_number"])
        elif r["lic_type"] == "Officer" and r["related_lic_type"] == "Corporation":
            # officer row: lic_number is the officer's broker licence, related = corporation
            f.add(r["related_lic_number"], r["lic_number"])
    # broker associates (xls has >65k-row quirks; read with patched xlrd)
    import xlrd, xlrd.sheet as S
    orig = S.Sheet.__init__
    def init(self, *a, **k):
        orig(self, *a, **k); self.utter_max_rows = 10**7
    S.Sheet.__init__ = init
    sh = xlrd.open_workbook(fetch(u2, "ca_ba.xls"), ragged_rows=True, logfile=open(os.devnull, "w")).sheet_by_index(0)
    hdr = [str(c).strip("\x00 ") for c in sh.row_values(0)]
    for i in range(1, sh.nrows):
        r = dict(zip(hdr, [str(c).strip("\x00 ") for c in sh.row_values(i)]))
        if r.get("rb_license_id") and r.get("ba_license_id"):
            f.add(r["rb_license_id"], r["ba_license_id"])
    for k in f.people:
        e = entity.get(k)
        if e:
            name = e["lastname_primary"] if e["lic_type"] == "Corporation" else \
                f"{e['firstname_secondary']} {e['lastname_primary']}".strip()
            f.set_info(k, name=N(name), address=N(e["address_1"]), city=N(e["city"]),
                       addr_state=N(e["state"]), zip=(e["zip_code"] or "")[:5])
    return f, u1 + " ; " + u2, "people = Licensed salespersons + broker associates + corporate officers whose responsible broker is the firm"


def xlsx_rows(path):
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True).active
    it = ws.iter_rows(values_only=True)
    hdr = [str(h or "").strip() for h in next(it)]
    for row in it:
        yield {h: ("" if v is None else str(v).replace("\t", "").strip()) for h, v in zip(hdr, row)}


def micropact(base, sub, pat):
    d = os.path.join(RAW, sub)
    if not os.path.isdir(d) or not any(f.endswith(".csv") for f in os.listdir(d)):
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "micropact_roster.py"), base, d, pat],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("roster generation failed: " + r.stderr[-300:])
    return d


# ---------------- CO: DORA Division of Real Estate rosters ----------------
def co():
    base = "https://apps2.colorado.gov/dre/licensing/Lookup/GenerateRoster.aspx"
    d = micropact(base, "co", "Real Estate Companies|Associate Brokers|Responsible Brokers")
    f = Firms()
    for fn in ("Active_Associate_Brokers.csv", "Active_Responsible_Brokers.csv"):
        for r in csv.DictReader(open(os.path.join(d, fn), encoding="latin-1")):
            ent = N(r.get("Entity Name"))
            if ent and N(r.get("Status")) == "ACTIVE":
                f.add(ent, r["Credential Type Prefix"] + r["Credential Number"], ent)
    for r in csv.DictReader(open(os.path.join(d, "Active_Real_Estate_Companies.csv"), encoding="latin-1")):
        k = N(r["Entity Name"])
        if k in f.people:
            f.set_info(k, name=k, dba=N(r.get("DBA")), address=N(r.get("Address Line 1")), city=N(r.get("City")),
                       addr_state=N(r.get("State")), zip=(r.get("ZipCode") or "")[:5], reg_phone=r.get("Phone", ""))
    return f, base + " (Active Associate Brokers + Active Responsible Brokers + Active Real Estate Companies rosters)", \
        "people = Active associate + responsible brokers whose roster Entity Name is the firm"


# ---------------- OR: Real Estate Agency eLicense rosters ----------------
def or_():
    base = "https://orea.elicense.micropact.com/Lookup/GenerateRoster.aspx"
    d = micropact(base, "or", "^Active Businesses|^Active Individuals")
    f = Firms()
    rows = list(csv.reader(open(os.path.join(d, "Active_Individuals.csv"), encoding="latin-1")))[1:]
    for x in rows:
        if len(x) > 25 and x[22].strip().upper() == "ACTIVE" and x[17].strip() in ("B", "PB") and x[25].strip():
            f.add(x[25].strip(), x[18].strip(), N(x[23]))
    for x in list(csv.reader(open(os.path.join(d, "Active_Businesses.csv"), encoding="latin-1")))[1:]:
        k = x[15].strip() if len(x) > 15 else ""
        if k in f.people:
            f.set_info(k, name=N(x[0]), address=N(x[2]), city=N(x[6]), addr_state=N(x[8]), zip=x[9][:5],
                       reg_phone=x[12])
    return f, base + " (Active Individuals + Active Businesses rosters)", \
        "people = ACTIVE brokers + principal brokers associated to the registered business name (RBN) licence"


# ---------------- OH: Division of Real Estate roster ----------------
def oh():
    base = "https://elicense3.com.ohio.gov/Lookup/GenerateRoster.aspx"
    d = micropact(base, "oh", "Real Estate")
    f = Firms()
    rows = list(csv.DictReader(open(os.path.join(d, "Real_Estate_and_Profession_Licensing.csv"), encoding="latin-1")))
    for r in rows:
        if r["Status"] == "ACTIVE" and "Salesperson" in r["Credential Type"] and r["Employer/Supervisor Credential"]:
            f.add(r["Employer/Supervisor Credential"], r["Credential"], N(r["Employer/Supervisor Name"]))
    for r in rows:
        k = r["Credential"]
        if k in f.people:
            f.set_info(k, name=N(r["Company Name"]) or f.info.get(k, {}).get("name"),
                       dba=N(r.get("Employer/Supervisor DBA")), address=N(r["Address1"]), city=N(r["City"]),
                       addr_state=N(r["State"]), zip=(r["Zip Code"] or "")[:5])
    return f, base + " (Real Estate and Profession Licensing roster)", \
        "people = ACTIVE salespersons (incl. management level) whose employer credential is the firm (brokers not linked in OH data)"


# ---------------- NV: Real Estate Division active licensee lists ----------------
def nv():
    b = "https://red.nv.gov/uploadedFiles/rednvgov/Content/Administration/Public_Records/"
    f = Firms()
    for fn in ("NRED-ACTIVE-SALESPERSONS.xlsx", "NRED-ACTIVE-BROKER-SALESPERSONS.xlsx"):
        for r in xlsx_rows(fetch(b + fn, "nv_" + fn)):
            k = N(r["Company"])
            if k:
                f.add(k, r["License No"], k)
                f.addr_votes[k][(N(r["Address"]), N(r["City"]), N(r["State"]), r["Zip Code"][:5])] += 1
    for k in f.people:
        a = f.addr_votes[k].most_common(1)[0][0]
        f.set_info(k, address=a[0], city=a[1], addr_state=a[2], zip=a[3])
    return f, b + "NRED-ACTIVE-SALESPERSONS.xlsx ; NRED-ACTIVE-BROKER-SALESPERSONS.xlsx", \
        "people = active salespersons + broker-salespersons whose Company is the firm (firm key = company name)"


# ---------------- AZ: ADRE public database lists ----------------
def az():
    u1, u2 = "https://services.azre.gov/PdbWeb/List/DownloadList/1", "https://services.azre.gov/PdbWeb/List/DownloadList/2"
    f = Firms()
    for r in csv.DictReader(open(fetch(u1, "az_individual.csv"), encoding="latin-1")):
        if r["LicStatus"].strip() == "Active" and r["LicCategory"].strip() == "Real Estate" and r["EmployerLicNumber"].strip():
            k = r["EmployerLicNumber"].strip()
            f.add(k, r["LicNumber"].strip(), N(r["EmployerDBAName"]) or N(r["EmployerLegalName"]))
            f.set_info(k, reg_phone=r["EmployerPhone"], reg_fax=r["EmployerFax"])
    for r in csv.DictReader(open(fetch(u2, "az_entity.csv"), encoding="latin-1")):
        k = r["LicNumber"].strip()
        if k in f.people:
            f.set_info(k, name=N(r["LegalName"]), dba=N(r["DBAName"]), address=N(r["Address1"]), city=N(r["City"]),
                       addr_state=N(r["State"]), zip=r["Zip"][:5], reg_phone=r["Phone"], reg_fax=r["Fax"])
    return f, u1 + " ; " + u2, "people = Active real estate licensees whose EmployerLicNumber is the firm"


# ---------------- WV: Real Estate Commission active rosters ----------------
def wv():
    urls = {"Salesperson": "https://rec.wv.gov/media/8/download?inline",
            "Associate Broker": "https://rec.wv.gov/media/9/download?inline",
            "Broker": "https://rec.wv.gov/media/10/download?inline"}
    f = Firms()
    for t, u in urls.items():
        for r in xlsx_rows(fetch(u, "wv_" + t.replace(" ", "_") + ".xlsx")):
            k = r["CompanyNumber"]
            if not k:
                continue
            f.add(k, r["LicenseNumber"], N(r["CompanyName"]))
            f.set_info(k, address=N(r["CompanyStreet"]), city=N(r["CompanyCity"]),
                       addr_state=N(r["CompanyState"]), zip=r["CompanyZip"][:5])
    return f, " ; ".join(urls.values()), "people = salespersons + associate brokers + brokers on the active rosters under the CompanyNumber"


ADAPTERS = [("NY", ny), ("CT", ct), ("TX", tx), ("FL", fl), ("CA", ca), ("CO", co), ("OR", or_),
            ("OH", oh), ("NV", nv), ("AZ", az), ("WV", wv)]
only = os.environ.get("STATES")
log = csv.writer(open(LOG, "w", newline=""))
log.writerow(["state", "source", "status", "message", "firms_total", "firms_35_75", "count_definition", "date"])
out = []
for st, fn in ADAPTERS:
    if only and st not in only.split(","):
        continue
    try:
        f, src, definition = fn()
    except Exception as e:  # never guess: record the failure
        log.writerow([st, "", "failed", repr(e)[:300], 0, 0, "", TODAY]); print(st, "FAILED", e); continue
    n = 0
    for k, ppl in f.people.items():
        c = len(ppl)
        if 35 <= c <= 75:
            i = f.info.get(k, {})
            if not i.get("name"):
                continue
            n += 1
            out.append(dict(firm_key=f"{st}:{k}", brokerage_name=i["name"], dba=i.get("dba", ""),
                            verified_headcount=c, headcount_source=src, count_definition=definition,
                            state=st, address=i.get("address", ""), city=i.get("city", ""),
                            addr_state=i.get("addr_state", ""), zip=i.get("zip", ""), county=i.get("county", ""),
                            reg_phone=i.get("reg_phone", ""), reg_fax=i.get("reg_fax", ""),
                            date_collected=TODAY))
    log.writerow([st, src, "ok", "", len(f.people), n, definition, TODAY])
    print(st, "firms", len(f.people), "in range", n, flush=True)

cols = ["firm_key", "brokerage_name", "dba", "verified_headcount", "headcount_source", "count_definition",
        "state", "address", "city", "addr_state", "zip", "county", "reg_phone", "reg_fax", "date_collected"]
with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)
print("total firms 35-75:", len(out))
