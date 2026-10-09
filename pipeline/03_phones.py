"""Steps 3-4: find office phones for firms with verified headcount; clean, validate, dedupe.

Phone evidence, all free:
  A. Overture Maps places (US real-estate category), matched by either
     - NAME: normalised whole-name similarity (token_sort_ratio) >= 92 AND same city or ZIP
       (TX firms have no address in the roster: same county via Census ZCTA->county file).
       Whole-name similarity rejects agents' personal listings ("Jane Doe, Realtor - Firm").
     - NAME (statewide): identical normalised name (>= 2 tokens) with exactly one distinct
       phone among real-estate places in the state; only for firms whose roster has no city/ZIP
       (graded low).
     - ADDRESS: identical normalised street number + street name AND same ZIP, exactly one
       distinct phone at that address, AND partial name similarity >= 75 (graded low).
  R. Office phone on the regulator's own firm record (AZ, CO, OR rosters), fax numbers excluded.
  B. The firm's own website (from the Overture match): home/contact/about/team pages,
     tel: links first, then US-format numbers in text; ~1 req/s per host, retries.
Confidence: high = firm's own website shows the number, or two independent sources agree
(regulator record + Overture); medium = one source (regulator record, or Overture name match);
low = a weak match (Overture address-only match) not confirmed elsewhere. Every match score is logged in phone_match_log.csv.
Usage: python 03_phones.py FIRMS_CSV OVERTURE_PARQUET ZCTA_COUNTY_TXT OUT_DIR
"""
import csv, os, re, sys, time, datetime, urllib.request, urllib.parse, ssl
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import phonenumbers, pyarrow.parquet as pq
from rapidfuzz import fuzz

FIRMS, OVT, ZC, OUTDIR = sys.argv[1:5]
TODAY = datetime.date.today().isoformat()
THRESH = 92
STOP = (r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LP|LLP|PLLC|PA|PC|THE|AND|OF|"
        r"REALTY|REAL ESTATE|REALTORS?|GROUP|PROPERTIES|BROKERAGE|BROKERS?|ASSOCIATES|SERVICES|HOMES|NY|CT|TX|FL|CA)\b")
TOLL = {"800", "833", "844", "855", "866", "877", "888"}
SUFFIX = {"AVENUE": "AVE", "STREET": "ST", "ROAD": "RD", "BOULEVARD": "BLVD", "DRIVE": "DR", "LANE": "LN",
          "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY", "HIGHWAY": "HWY", "SUITE": "STE", "NORTH": "N",
          "SOUTH": "S", "EAST": "E", "WEST": "W", "CIRCLE": "CIR", "TRAIL": "TRL", "TERRACE": "TER",
          "SQUARE": "SQ", "TURNPIKE": "TPKE", "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "CENTER": "CTR"}
CITY_ABBR = {"BCH": "BEACH", "SPGS": "SPRINGS", "FT": "FORT", "ST": "SAINT", "PT": "PORT", "MT": "MOUNT",
             "HTS": "HEIGHTS", "GDNS": "GARDENS", "LK": "LAKE", "PK": "PARK", "VLG": "VILLAGE"}


def nname(s):
    s = re.sub(r"[^A-Z0-9& ]", " ", (s or "").upper().replace("'", ""))
    return re.sub(r"\s+", " ", re.sub(STOP, " ", s)).strip()


def ncity(s):
    return " ".join(CITY_ABBR.get(w, w) for w in re.sub(r"[^A-Z ]", " ", (s or "").upper()).split())


def nstreet(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    s = re.split(r"\b(STE|SUITE|UNIT|APT|FL|FLOOR|BLDG|RM|ROOM|#)\b", s)[0]
    w = [SUFFIX.get(t, t) for t in s.split()]
    if not w or not w[0].isdigit():
        return ""
    return " ".join(w[:4])  # number + up to 3 street tokens


def fmt(raw):
    """(XXX) XXX-XXXX or None; drops invalid + toll-free. Never alters digits."""
    try:
        n = phonenumbers.parse(str(raw), "US")
    except phonenumbers.NumberParseException:
        return None
    if n.country_code != 1 or not phonenumbers.is_valid_number(n):
        return None
    d = str(n.national_number)
    if len(d) != 10 or d[:3] in TOLL:
        return None
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


# ---- ZIP -> county (Census 2020 ZCTA-county relationship), for TX
zip_county = defaultdict(set)
for i, line in enumerate(open(ZC, encoding="utf-8-sig")):
    p = line.rstrip("\n").split("|")
    if i == 0 or not p[1]:
        continue
    if p[9].startswith("48"):  # Texas FIPS
        zip_county[p[1]].add(p[10].upper().replace(" COUNTY", ""))

# ---- Overture candidates by state
PERSONAL = re.compile(r"realtor|\bagent\b|broker associate|\|", re.I)
cands = defaultdict(list)
by_addr = defaultdict(list)
by_name = defaultdict(list)
for r in pq.read_table(OVT, columns=["id", "names", "phones", "websites", "addresses"]).to_pylist():
    a = (r["addresses"] or [{}])[0] or {}
    st = (a.get("region") or "").upper()[-2:]
    phones = [p for p in map(fmt, r["phones"] or []) if p]
    c = dict(id=r["id"], name=(r["names"] or {}).get("primary") or "", phones=phones,
             web=(r["websites"] or [None])[0], city=ncity(a.get("locality")), zip=(a.get("postcode") or "")[:5],
             addr=a.get("freeform") or "", raw_city=a.get("locality") or "")
    c["n"] = nname(c["name"])
    if not phones:
        continue
    cands[st].append(c)
    if c["n"] and not PERSONAL.search(c["name"]):
        by_name[(st, c["n"])].append(c)
    s = nstreet(c["addr"])
    if s and c["zip"]:
        by_addr[(st, s, c["zip"])].append(c)




def match(f):
    """returns (method, score, candidate) or None"""
    st = f["state"]
    names = [n for n in {nname(f["brokerage_name"]), nname(f.get("dba"))} if n]
    city, z, county = ncity(f["city"]), f["zip"], f.get("county", "")
    best = None
    for c in cands.get(st, []):
        if st == "TX" and not city:
            geo = county and county in zip_county.get(c["zip"], ())
        else:
            geo = (city and c["city"] == city) or (z and c["zip"] == z)
        if not geo:
            continue
        if not c["n"] or PERSONAL.search(c["name"]):
            continue
        s = max(fuzz.token_sort_ratio(n, c["n"]) for n in names if len(n) >= 4) if any(len(n) >= 4 for n in names) else 0
        if s >= THRESH and (best is None or s > best[1]):
            best = ("name", s, c)
    if best:
        return best
    # fallback: exact normalised name, unique (one distinct phone) across the whole state
    strong = [n for n in names if len(n.split()) >= 2]
    if strong and not city and not z:  # only when the roster gives no location to contradict it
        hits = [c for n in strong for c in by_name.get((st, n), [])]
        if hits and len({c["phones"][0] for c in hits}) == 1:
            return ("name_statewide_unique", 100, hits[0])
    s = nstreet(f.get("address"))
    if s and z:
        hits = by_addr.get((st, s, z), [])
        distinct = {h["phones"][0] for h in hits}
        if len(distinct) == 1:
            h = hits[0]
            sim = max(fuzz.partial_ratio(n, h["n"]) for n in names) if names and h["n"] else 0
            if sim >= 75 and not PERSONAL.search(h["name"]):
                return ("address", round(sim), h)
    return None


# ---- website scrape
CTX = ssl.create_default_context()
TEL = re.compile(r'href=["\']tel:([^"\']+)', re.I)
TXT = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d-])")
FAX = re.compile(r"fax|facsimile|\bf[:.]", re.I)
CELL = re.compile(r"cell|mobile|\bc[:.]|direct|text", re.I)


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def get(url, last={}):
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(3):
        wait = 1.0 - (time.time() - last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        last[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as resp:
                return resp.read(1_500_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410, 401):
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return None


def site_phones(web):
    """list of (phone, how, page, flagged_cell) from the firm's own pages, tel: first."""
    if not web:
        return []
    # https only: this environment's egress proxy carries HTTPS (CONNECT) traffic only
    web = re.sub(r"^(https?://)?", "https://", web.strip())
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(web))
    out = []
    for path in ("/", "/contact", "/contact-us", "/about", "/about-us"):
        html = get(base + path)
        if not html:
            continue
        for m in TEL.finditer(html):
            p = fmt(urllib.parse.unquote(m.group(1)))
            if p:
                ctx = html[max(0, m.start() - 80):m.start()]
                out.append((p, "tel", path, bool(CELL.search(ctx))))
        for m in TXT.finditer(re.sub(r"<[^>]+>", " ", html)):
            pass
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        for m in TXT.finditer(text):
            ctx = text[max(0, m.start() - 25):m.start()]
            if FAX.search(ctx):
                continue
            p = fmt(m.group())
            if p:
                out.append((p, "text", path, bool(CELL.search(ctx))))
        if out:
            break
    return out


firms = list(csv.DictReader(open(FIRMS)))
print("firms", len(firms), flush=True)
matches = {f["firm_key"]: match(f) for f in firms}
print("overture matches", sum(1 for m in matches.values() if m), flush=True)


import json
CACHE = os.path.join(os.path.dirname(os.path.abspath(OVT)), "site_cache.json")
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}


def work(f):
    m = matches[f["firm_key"]]
    web = m[2]["web"] if m else None
    if not web:
        return f["firm_key"], []
    if web not in cache:
        cache[web] = site_phones(web)
    return f["firm_key"], [tuple(x) for x in cache[web]]


with ThreadPoolExecutor(24) as ex:  # different hosts in parallel; per-host 1 req/s
    sites = dict(ex.map(work, firms))
json.dump(cache, open(CACHE, "w"))
print("websites checked", len(cache), flush=True)

leads, nophone, mlog = [], [], []
for f in firms:
    m = matches[f["firm_key"]]
    site = sites[f["firm_key"]]
    row = dict(brokerage_name=f["brokerage_name"], verified_headcount=f["verified_headcount"],
               headcount_source=f["headcount_source"], state=f["state"], city=f["city"],
               address=f["address"], website="", date_collected=TODAY)
    phone = conf = src = None
    flag = ""
    reg = fmt(f.get("reg_phone")) if f.get("reg_phone") else None
    if reg and reg == fmt(f.get("reg_fax") or ""):
        reg = None
    regsrc = f"regulator firm record ({f['headcount_source'].split(' ; ')[-1].split(' (')[0]})"
    ov = c = None
    site_set, main, ovsrc = [], None, ""
    if m:
        method, score, c = m
        ov = c["phones"][0]
        row["website"] = c["web"] or ""
        if not row["city"]:
            row["city"] = c["raw_city"].upper()
        if not row["address"]:
            row["address"] = c["addr"].upper() + " (from Overture)"
        site_set = [s_[0] for s_ in site]
        main = next((s_ for s_ in site if s_[1] == "tel" and not s_[3]), None) or next((s_ for s_ in site if not s_[3]), None)
        mlog.append([f["firm_key"], f["brokerage_name"], f["city"], f["zip"], f.get("county", ""), c["name"],
                     c["raw_city"], c["zip"], method, score, ov, ";".join(sorted(set(site_set)))])
        ovsrc = f"Overture Maps place {c['id']} ({method} match {score})"
    if reg and reg in site_set:
        phone, conf, src = reg, "high", f"{regsrc} + firm website {c['web']}"
    elif ov and ov in site_set:
        phone, conf, src = ov, "high", f"{ovsrc} + firm website {c['web']}"
    elif reg and ov and reg == ov:
        phone, conf, src = reg, "high", f"{regsrc} + {ovsrc}"
    elif main:
        phone, conf, src = main[0], "high", f"firm website {c['web']} ({main[1]} on {main[2]}); {ovsrc}"
    elif reg:
        phone, conf, src = reg, "medium", regsrc
    elif ov:
        phone, conf, src = ov, ("medium" if m[0] == "name" else "low"), ovsrc  # address / statewide-only = low
    if phone and phone in site_set and all(s_[3] for s_ in site if s_[0] == phone):
        flag = "possible cell (labelled cell/mobile/direct on site)"
    elif phone and phone == ov and conf != "high" and fuzz.token_sort_ratio(nname(f["brokerage_name"]), c["n"]) < 90:
        flag = "Overture listing name includes extra words (may be an individual agent's listing / personal cell)"
    if phone:
        row.update(phone=phone, phone_confidence=conf, phone_source=src, phone_flag=flag)
        leads.append(row)
    else:
        nophone.append(row)

# dedupe by phone and by name+city (keep higher confidence, then larger headcount)
rank = {"high": 0, "medium": 1, "low": 2}
seen_p, seen_n, ded, dropped = set(), set(), [], []
for r in sorted(leads, key=lambda r: (rank[r["phone_confidence"]], -int(r["verified_headcount"]))):
    k = (nname(r["brokerage_name"]), r["city"])
    if r["phone"] in seen_p or k in seen_n:
        dropped.append(r); continue
    seen_p.add(r["phone"]); seen_n.add(k); ded.append(r)

LEAD_COLS = ["brokerage_name", "phone", "phone_confidence", "verified_headcount", "headcount_source",
             "state", "city", "address", "website", "phone_source", "date_collected", "phone_flag"]
NP_COLS = ["brokerage_name", "verified_headcount", "headcount_source", "state", "city", "address", "website", "date_collected"]
ded.sort(key=lambda r: (r["state"], r["brokerage_name"]))
with open(os.path.join(OUTDIR, "leads_verified.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=LEAD_COLS); w.writeheader(); w.writerows(ded)
with open(os.path.join(OUTDIR, "no_phone.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=NP_COLS, extrasaction="ignore"); w.writeheader()
    w.writerows(sorted(nophone, key=lambda r: (r["state"], r["brokerage_name"])))
with open(os.path.join(OUTDIR, "duplicates_dropped.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=LEAD_COLS); w.writeheader(); w.writerows(dropped)
with open(os.path.join(OUTDIR, "phone_match_log.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["firm_key", "firm", "firm_city", "firm_zip", "firm_county", "overture_name", "overture_city",
                "overture_zip", "method", "score", "overture_phone", "website_phones"])
    w.writerows(mlog)
print(f"leads: {len(ded)}  no_phone: {len(nophone)}  dup dropped: {len(dropped)}")
