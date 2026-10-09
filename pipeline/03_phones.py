"""Steps 3-4: find office phones for firms with verified headcount, clean, validate, dedupe.

Sources, in order:
  A. Overture Maps places (free, open data) - match on normalised name + same city or ZIP,
     strict fuzzy threshold (token_set_ratio >= 92), score logged.
  B. Firm's own website (home/contact/about/team pages): tel: links first, then US-format
     numbers in text. ~1 request/second per site, retries on errors.
Confidence: high = firm's own site, or two sources agree; medium = one directory
(Overture); low = one weak source.
Usage: python 03_phones.py FIRMS_CSV OVERTURE_PARQUET OUT_DIR
"""
import csv, os, re, sys, time, datetime, urllib.request, urllib.parse
from collections import defaultdict
import phonenumbers, pyarrow.parquet as pq
from rapidfuzz import fuzz

FIRMS, OVT, OUTDIR = sys.argv[1:4]
TODAY = datetime.date.today().isoformat()
THRESH = 92
STOP = r"\b(LLC|L L C|INC|CORP|CORPORATION|CO|COMPANY|LTD|LP|LLP|PLLC|PC|THE|AND|&|REALTY|REAL ESTATE|REALTORS?|GROUP|PROPERTIES|BROKERAGE|ASSOCIATES)\b"
TOLL = {"800", "833", "844", "855", "866", "877", "888"}


def nname(s):
    s = re.sub(r"[^A-Z0-9& ]", " ", (s or "").upper())
    return re.sub(r"\s+", " ", re.sub(STOP, " ", s)).strip()


def fmt(raw):
    """(XXX) XXX-XXXX or None; drops invalid and toll-free numbers. Never alters digits."""
    try:
        n = phonenumbers.parse(raw, "US")
    except phonenumbers.NumberParseException:
        return None
    if n.country_code != 1 or not phonenumbers.is_valid_number(n):
        return None
    d = str(n.national_number)
    if len(d) != 10 or d[:3] in TOLL:
        return None
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


# ---- Overture index: (state) -> list of candidates
ovt = defaultdict(list)
t = pq.read_table(OVT, columns=["names", "phones", "websites", "addresses"]).to_pylist()
for r in t:
    a = (r["addresses"] or [{}])[0] or {}
    st = (a.get("region") or "").upper()[-2:]
    ovt[st].append(dict(name=(r["names"] or {}).get("primary") or "", n=nname((r["names"] or {}).get("primary")),
                        phones=r["phones"] or [], web=(r["websites"] or [None])[0],
                        city=(a.get("locality") or "").upper(), zip=(a.get("postcode") or "")[:5],
                        addr=a.get("freeform") or ""))


def overture_match(f):
    best = None
    fn = nname(f["brokerage_name"])
    if not fn:
        return None
    for c in ovt.get(f["state"], []):
        geo_ok = (f["city"] and c["city"] == f["city"]) or (f["zip"] and c["zip"] == f["zip"])
        if not geo_ok or not c["phones"]:
            continue
        s = fuzz.token_set_ratio(fn, c["n"])
        if s >= THRESH and (best is None or s > best[0]):
            best = (s, c)
    return best


_last = {}


def get(url):
    host = urllib.parse.urlparse(url).netloc
    wait = 1.0 - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        try:
            _last[host] = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research; contact via site)"})
            return urllib.request.urlopen(req, timeout=20).read(2_000_000).decode("utf-8", "replace")
        except Exception:
            time.sleep(2 ** (attempt + 1))
    return None


TEL = re.compile(r'href=["\']tel:([^"\']+)', re.I)
TXT = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
FAX = re.compile(r"fax|facsimile", re.I)


def site_phones(web):
    found = []
    for path in ("", "/contact", "/contact-us", "/about", "/team", "/our-team", "/agents"):
        html = get(urllib.parse.urljoin(web, path))
        if not html:
            continue
        for m in TEL.findall(html):
            p = fmt(m)
            if p:
                found.append((p, "tel", path))
        for m in TXT.finditer(html):
            ctx = html[max(0, m.start() - 40):m.start()]
            if FAX.search(ctx):
                continue  # fax line
            p = fmt(m.group())
            if p:
                found.append((p, "text", path))
        if found:
            break
    return found


firms = list(csv.DictReader(open(FIRMS)))
leads, nophone, matchlog = [], [], []
for f in firms:
    m = overture_match(f)
    ov_phone = ov_web = None
    if m:
        score, c = m
        ov_phone = next((p for p in map(fmt, c["phones"]) if p), None)
        ov_web = c["web"]
        matchlog.append([f["brokerage_name"], f["state"], f["city"], c["name"], c["city"], c["zip"], score, ov_phone])
        f.setdefault("website", ov_web or "")
        if not f.get("address"):
            f["address"] = c["addr"]
    site = site_phones(ov_web) if ov_web else []
    site_phone = site[0][0] if site else None
    phone, conf, src = None, None, None
    if site_phone and ov_phone and site_phone == ov_phone:
        phone, conf, src = site_phone, "high", f"firm website ({ov_web}) + Overture Maps (match {m[0]})"
    elif site_phone:
        phone, conf, src = site_phone, "high", f"firm website ({ov_web}{site[0][2]})"
    elif ov_phone:
        phone, conf, src = ov_phone, "medium", f"Overture Maps places (fuzzy match {m[0]})"
    row = dict(brokerage_name=f["brokerage_name"], verified_headcount=f["verified_headcount"],
               headcount_source=f["headcount_source"], state=f["state"], city=f["city"],
               address=f.get("address", ""), website=f.get("website", ""), date_collected=TODAY)
    if phone:
        leads.append(dict(row, phone=phone, phone_confidence=conf, phone_source=src))
    else:
        nophone.append(row)

# dedupe by phone and by name+city
seen_p, seen_n, ded = set(), set(), []
for r in sorted(leads, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["phone_confidence"]]):
    k = (nname(r["brokerage_name"]), r["city"])
    if r["phone"] in seen_p or k in seen_n:
        continue
    seen_p.add(r["phone"]); seen_n.add(k); ded.append(r)

LEAD_COLS = ["brokerage_name", "phone", "phone_confidence", "verified_headcount", "headcount_source",
             "state", "city", "address", "website", "phone_source", "date_collected"]
with open(os.path.join(OUTDIR, "leads_verified.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=LEAD_COLS); w.writeheader(); w.writerows(ded)
with open(os.path.join(OUTDIR, "no_phone.csv"), "w", newline="") as fh:
    cols = [c for c in LEAD_COLS if c not in ("phone", "phone_confidence", "phone_source")]
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(nophone)
with open(os.path.join(OUTDIR, "phone_match_log.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["firm", "state", "firm_city", "overture_name", "overture_city", "overture_zip", "score", "phone"])
    w.writerows(matchlog)
print(f"leads: {len(ded)}  no_phone: {len(nophone)}")
