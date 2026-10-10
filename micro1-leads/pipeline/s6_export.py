"""STEPS 5-6: clean phones, add directors (if CH_API_KEY), export call-list.csv and no-phone.csv.

Phones: phonenumbers (GB). Only valid UK (+44) numbers are kept. phone_type:
  mobile                     07xxx mobiles
  landline                   geographic 01/02 numbers
  landline (non-geographic)  03/08/0800/VoIP numbers (office lines, not mobiles)
Duplicates by phone number are removed (first kept: higher match tier, then more employees).
Sorted mobiles first, then employees (largest first)."""
import base64, csv, json, os, time, urllib.request, urllib.error
import pyarrow.parquet as pq, phonenumbers
from phonenumbers import PhoneNumberType as T
from common import *

log = get_logger("s6_export")
comp = {c["company_number"]: c for c in pq.read_table(os.path.join(DATA, f"employees_40_200{tag()}.parquet")).to_pylist()}
cands = []
for tier, fn in ((0, f"matched{tag()}.parquet"), (1, f"web{tag()}.parquet")):
    p = os.path.join(DATA, fn)
    if os.path.exists(p):
        cands += [dict(r, tier=tier) for r in pq.read_table(p).to_pylist()]


def clean(raw):
    try:
        n = phonenumbers.parse(raw, "GB")
    except phonenumbers.NumberParseException:
        return None, None
    if n.country_code != 44 or not phonenumbers.is_valid_number(n):
        return None, None
    t = phonenumbers.number_type(n)
    kind = ("mobile" if t == T.MOBILE else "landline" if t in (T.FIXED_LINE, T.FIXED_LINE_OR_MOBILE)
            else "landline (non-geographic)")
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.NATIONAL), kind


# ---- directors (optional, Companies House public data API) ----
key = os.environ.get("CH_API_KEY")
dcache_path = os.path.join(DATA, "directors_cache.json")
dcache = json.load(open(dcache_path)) if os.path.exists(dcache_path) else {}


def directors(cn):
    if not key:
        return ""
    if cn in dcache:
        return dcache[cn]
    req = urllib.request.Request(f"https://api.company-information.service.gov.uk/company/{cn}/officers?items_per_page=100",
                                 headers={"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode()})
    for attempt in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=20))
            names = [o["name"] for o in d.get("items", []) if o.get("officer_role") == "director" and not o.get("resigned_on")]
            dcache[cn] = "; ".join(names)
            time.sleep(0.5)  # CH limit: 600 requests / 5 minutes
            return dcache[cn]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(60); continue
            log.error("directors %s: HTTP %s", cn, e.code); return ""
        except Exception as e:
            log.error("directors %s: %r", cn, e); time.sleep(2)
    return ""


rows, seen, invalid, dup = [], set(), 0, 0
for r in sorted(cands, key=lambda r: (r["tier"], -comp[r["company_number"]]["employees"])):
    c = comp[r["company_number"]]
    if c["company_number"] in {x["company_number"] for x in rows}:
        continue
    phone, kind = clean(r["phone_raw"])
    if not phone:
        invalid += 1
        log.info("dropped invalid/non-UK number %r for %s", r["phone_raw"], c["company_number"]); continue
    if phone in seen:
        dup += 1; continue
    seen.add(phone)
    rows.append({"company_name": c["name"], "company_number": c["company_number"], "phone": phone, "phone_type": kind,
                 "employees": c["employees"], "sic_code": "; ".join(c["sic_list"]), "address": c["address"],
                 "website": r["website"] or "", "director_names": "", "phone_source": r["phone_source"],
                 "match_confidence": r["match_confidence"], "employees_period_end": c["period_end"],
                 "employees_source": f"Companies House accounts {c['source_zip']} / {c['source_file']}"})
for r in rows:
    r["director_names"] = directors(r["company_number"])
json.dump(dcache, open(dcache_path, "w"))
rows.sort(key=lambda r: (r["phone_type"] != "mobile", -r["employees"]))
cols = list(rows[0].keys()) if rows else ["company_name", "company_number", "phone", "phone_type", "employees", "sic_code",
                                          "address", "website", "director_names", "phone_source", "match_confidence"]
with open(os.path.join(OUT, f"call-list{tag()}.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

have = {r["company_number"] for r in rows}
web = {}
wc = os.path.join(DATA, "web_cache.jsonl")
if os.path.exists(wc):
    for line in open(wc):
        d = json.loads(line); web[d["company_number"]] = d.get("website") or ""
np_rows = [{"company_name": c["name"], "company_number": n, "employees": c["employees"], "sic_code": "; ".join(c["sic_list"]),
            "address": c["address"], "website_found": web.get(n, ""), "employees_period_end": c["period_end"]}
           for n, c in comp.items() if n not in have]
np_rows.sort(key=lambda r: -r["employees"])
with open(os.path.join(OUT, f"no-phone{tag()}.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(np_rows[0].keys()) if np_rows else ["company_name"]); w.writeheader(); w.writerows(np_rows)
types = {}
for r in rows:
    types[r["phone_type"]] = types.get(r["phone_type"], 0) + 1
log.info("STEP5/6 counts: candidates=%d invalid dropped=%d duplicate phones dropped=%d call-list rows=%d %s no-phone rows=%d directors=%s",
         len(cands), invalid, dup, len(rows), types, len(np_rows), "added" if key else "skipped (CH_API_KEY not set)")
