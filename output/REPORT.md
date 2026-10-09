# US brokerages with 35-75 agents: run report (2026-10-09)

## Result: 0 leads. The pipeline could not verify any headcount from this environment.

| Output | Rows |
|---|---|
| `leads_verified.csv` | **0** |
| `no_phone.csv` | **0** |
| `sources_checked.csv` | 51 (50 states + DC) |
| `headcount_source_log.csv` | 5 roster downloads tried, 5 refused |

Nothing was guessed, estimated or filled in. The hard rule says to drop any firm whose
headcount can't be verified. No regulator roster or firm website could be fetched, so
every firm was dropped.

## Step 0: access check (what could and could not be reached)

The container sends all traffic through an egress proxy that enforces the environment's
network policy. That proxy refused **every** host needed to verify a headcount: the
CONNECT request got HTTP 403, which is a policy denial and not the site blocking us.

| Reachable | Refused (403 at proxy) |
|---|---|
| Overture Maps S3 (`overturemaps-us-west-2.s3.us-west-2.amazonaws.com`) | `data.ny.gov`, `data.ct.gov`, `data.texas.gov` (Socrata open data) |
| PyPI, npm, GitHub | `www2.myfloridalicense.com`, `www.dre.ca.gov`, `secure.dre.ca.gov` |
| | `apps2.colorado.gov`, `orea.elicense.micropact.com`, `elicense3.com.ohio.gov`, `pr.mo.gov`, `red.nv.gov`, `rec.wv.gov`, `www.dpor.virginia.gov`, `www.michigan.gov`, `services.azre.gov`, `oop.ky.gov`, `www.krec.ks.gov`, `www.nj.gov`, `dbr.ri.gov`, `lrec.gov` |
| | every firm website tried, plus Google, Bing, DuckDuckGo, OpenStreetMap/Overpass, Foursquare OS Places, archive.org, Kaggle, Common Crawl, `catalog.data.gov` |

The WebFetch tool could not resolve DNS for any of these hosts either. Web search did
work, but it only returns summaries, not datasets. It was used for Step 1 only.

## Step 1: which jurisdictions publish usable data (`sources_checked.csv`)

* **Free, and confirmed to link each licensee to a firm (12):** NY (data.ny.gov `yg7h-zjbf`,
  business name per licensee), CT (data.ct.gov `6tja-6vdt`, salesperson to broker),
  TX (data.texas.gov `s7ft-44qi`, sponsoring broker), FL (DBPR weekly CSV with employer
  name and licence number), CA (DRE licensee file, `related_*` = responsible broker),
  MI (LARA "Real Estate Active Employees" report), MO (directory with affiliation address),
  KY (per-firm pages listing affiliated licensees), NJ (company search lists active licensees),
  AZ (company search lists employees). AL and AR also link licensees to firms, but their
  bulk lists are paid or search-only.
* **Free download exists, but the firm link is unconfirmed (14):** CO, KS, LA, MA, NV, OH, OR,
  RI, VT, VA, WV, IL, IA, SD (IA and SD are paid).
* **No free bulk roster found, or login-walled (25):** AK, DE, DC, GA, HI, ID, IN, ME, MD, MN, MS,
  MT, NE, NH, NM, NC, ND, OK, PA, SC, TN, UT, WA, WI, WY.

The `links_licensees_to_firms` column uses `unknown` where a free file exists but its
columns could not be checked because the host was blocked. Forcing that to yes/no would
have meant guessing.

## Steps 2-4: what ran

* **Headcount (`pipeline/02_headcount.py`)**: adapters for NY, CT, TX, FL and CA. Each counts
  active *individual* licensees per firm, skips the firms' own business records, and keeps
  35-75. All 5 downloads were refused, so 0 firms.
* **Phones (`pipeline/03_phones.py`)**:
  * Overture Maps places: strict fuzzy name match (token_set ≥ 92) plus the same city or ZIP, with the score logged.
  * Firm website: tel: links first, then US numbers in the text with fax lines skipped, about 1 request per second per host, with retries.
  * Clean-up: numbers normalised to `(XXX) XXX-XXXX`; invalid and toll-free numbers dropped; dedupe by phone and by name + city.
  * Confidence: high = the firm's own site, or the site and Overture agree; medium = Overture only.
* **Overture extract (`pipeline/01_overture_extract.py`)**: this step *did* run, using the
  2026-09-23.1 release. See the counts below. It is ready to supply phones once headcounts exist.

Overture extract, release 2026-09-23.1, US only, category real_estate / property_management:
**758,025 places**. 649,906 have a phone, 609,398 have a website, and they span 55
state/territory codes (top states: CA 98,902, FL 86,994, TX 66,913, NY 36,918, GA 25,384).
The file is about 100 MB, so it is not committed; `run_all.sh` rebuilds it in about 15 minutes.

## Breakdowns asked for in the brief

* Verified firms by state: none in any state.
* Firms with phones: 0. Confidence split: n/a.
* Phone sources that worked best: n/a. No firm reached the phone stage. Overture is the
  only phone source reachable from this environment.
* States that could not be verified: all 51. 25 have no free roster that links licensees
  to firms. The other 26 have one, but the host was blocked in this run.
* Personal-cell flagging: no free source can tell a mobile line from a landline. Without
  a paid carrier lookup, rows can only be flagged with a heuristic: a phone that is shared
  with an individual agent's own listing.

## To get real numbers

Allow these hosts in the environment's network settings (Network access → Allowed
domains, or a broader level), then run `pipeline/run_all.sh`:

`data.ny.gov`, `data.ct.gov`, `data.texas.gov`, `www2.myfloridalicense.com`, `www.dre.ca.gov`,
`secure.dre.ca.gov`, plus full internet access for the firm-website phone check and for
the roster states with per-firm pages (KY, NJ, AZ, MI, MO).

The FL and CA direct file paths in `02_headcount.py` are marked UNCONFIRMED and must be
checked on their landing pages first. Because none of the rosters could be downloaded,
the adapters' column mappings are untested against real files. Parse failures are logged
rather than guessed around.
