# US brokerages with 35-75 agents: run report (2026-10-09)

**760 cold-callable leads** in `leads_verified.csv`. Each has a headcount verified from a
state regulator's own roster and an office phone number. Another **999** verified firms
are in `no_phone.csv` because no phone could be found and confirmed.

Nothing was guessed. Every headcount is a count of named, active individual licensees linked
to the firm in an official roster. Every phone comes from a logged source.

## Verified firms by state

| State | Firms with 35-75 verified | Leads with phone | High | Medium | Low | No phone | Dropped as duplicate |
|---|---|---|---|---|---|---|---|
| FL | 546 | 235 | 121 | 112 | 2 | 310 | 1 |
| CA | 349 | 77 | 29 | 41 | 7 | 272 | 0 |
| TX | 251 | 71 | 29 | 39 | 3 | 180 | 0 |
| NY | 184 | 88 | 39 | 48 | 1 | 95 | 1 |
| AZ | 139 | 121 | 41 | 80 | 0 | 9 | 9 |
| OH | 78 | 18 | 5 | 11 | 2 | 60 | 0 |
| CO | 66 | 64 | 29 | 35 | 0 | 2 | 0 |
| OR | 57 | 54 | 25 | 29 | 0 | 2 | 1 |
| NV | 56 | 24 | 9 | 14 | 1 | 32 | 0 |
| CT | 34 | 8 | 5 | 3 | 0 | 26 | 0 |
| WV | 11 | 0 | 0 | 0 | 0 | 11 | 0 |
| **Total** | **1771** | **760** | **332** | **412** | **16** | **999** | **12** |

What each state's headcount counts. Every count covers that state only, so a firm with offices
in several states is undercounted.

| State | Source | Who is counted |
|---|---|---|
| FL | https://www2.myfloridalicense.com/sto/file_download/extracts//REALESTATE2501LICENSE_1.csv | Current/Active sales associates + brokers + broker-sales whose employer licence is the firm |
| CA | https://secure.dre.ca.gov/datafile/CurrList.zip | Licensed salespersons + broker associates + corporate officers whose responsible broker is the firm |
| TX | https://data.texas.gov/api/views/s7ft-44qi/rows.csv?accessType=DOWNLOAD | ACTIVE sales agents sponsored by the firm licence + its designated broker (other brokers are not linked in TX data) |
| NY | https://data.ny.gov/api/views/yg7h-zjbf/rows.csv?accessType=DOWNLOAD | salespersons + all broker licence types whose business_name is the firm (firm key = business name, all NY offices combined) |
| AZ | https://services.azre.gov/PdbWeb/List/DownloadList/1 | Active real estate licensees whose EmployerLicNumber is the firm |
| OH | https://elicense3.com.ohio.gov/Lookup/GenerateRoster.aspx | ACTIVE salespersons (incl. management level) whose employer credential is the firm (brokers not linked in OH data) |
| CO | https://apps2.colorado.gov/dre/licensing/Lookup/GenerateRoster.aspx | Active associate + responsible brokers whose roster Entity Name is the firm |
| OR | https://orea.elicense.micropact.com/Lookup/GenerateRoster.aspx | ACTIVE brokers + principal brokers associated to the registered business name (RBN) licence |
| NV | https://red.nv.gov/uploadedFiles/rednvgov/Content/Administration/Public_Records/NRED-ACTIVE-SALESPERSONS.xlsx | active salespersons + broker-salespersons whose Company is the firm (firm key = company name) |
| CT | https://data.ct.gov/api/views/eqtn-rppv/rows.csv?accessType=DOWNLOAD | ACTIVE salespersons whose supervising broker licence is the firm (brokers are not linked to firms in CT data) |
| WV | https://rec.wv.gov/media/8/download?inline | salespersons + associate brokers + brokers on the active rosters under the CompanyNumber |

CT, OH and TX link only salespersons (plus the designated broker in TX) to firms. Their
associate brokers are not linked, so those counts are lower bounds. A firm is kept only if
its count falls inside 35-75.

Spot checks against the live regulator APIs matched exactly:
* CBRE Upstate NY: 28 + 24 + 1 = 53.
* Realty ONE Group Key: 65.
* Two Texas firms: 70 and 45 + 1.

## Phones

**Confidence split:** 332 high, 412 medium, 16 low.
* **High:** the firm's own website shows the number, or the regulator record and Overture agree.
* **Medium:** one source only (the regulator's firm record, or an Overture place matched on name + city/ZIP).
* **Low:** a weaker match, either Overture address + ZIP with a partial name match, or an exact
  statewide-unique name for firms whose roster gives no city.

**Which phone sources worked best** (number of leads each source contributed to):
* Overture Maps: 579
* firm website: 292
* regulator firm record: 221

Combinations used for the chosen number:
* Overture: 285
* Overture + firm website: 254
* regulator record: 143
* regulator record + Overture: 40
* regulator record + firm website: 38

How phones were matched and cleaned:
* **Overture match:** 758,025 US real-estate places in Overture Maps (release 2026-09-23.1).
  Whole-name similarity (token_sort ≥ 92) plus the same city or ZIP. For Texas, the same
  county (via the Census ZCTA→county file), because TREC's data has no address. Every score
  is in `phone_match_log.csv`.
* **Website check:** 636 firm websites (from the Overture match), about 1 request per second per
  host. tel: links are taken first, then numbers in the page text.
* **Regulator phone:** the office phone on the firm's licence record. Arizona, Colorado and
  Oregon publish one. Arizona's fax numbers are excluded.
* **Clean-up:** all numbers normalised to (XXX) XXX-XXXX. Invalid numbers, toll-free-only
  numbers and numbers labelled fax were dropped. Leads were deduped by phone and by name + city;
  the 12 dropped rows are in `duplicates_dropped.csv`.
* **Flags (67 rows, `phone_flag` column):**
  * Overture listing name includes extra words: 61
  * possible cell: 6

  No free source can tell a mobile line from a landline. These flags are heuristics, and
  unflagged rows can still be personal cells.

## Sources (all 50 states + DC, see `sources_checked.csv`)

**Used (11):** AZ, CA, CO, CT, FL, NV, NY, OH, OR, TX, WV.

**Could not be verified (40):**
* no free bulk roster found: AK, AR, DE, GA, HI, ID, IL, IN, MD, ME, MN, MS, MT, NC, ND, NE, NH, NM, OK, PA, SC, TN, UT, VT, WA, WI, WY
* blocks automated access: KS, MA, MI, NJ, RI
* paid: AL, IA, LA, SD
* login-walled: DC
* per-firm pages only (not automated): KY
* TLS certificate problem: MO
* no link between licensees and firms: VA

Phone sources tried and not used:
* **Foursquare OS Places:** now gated behind a Hugging Face login.
* **OpenStreetMap Overpass:** public endpoints reset or errored on a US-wide query.
* **TREC's license-holder search:** returns 403 to automated requests.

## Why 999 firms have no phone

Most rosters list the legal entity name (e.g. "S. CROWE, LLC", "CIDEJJKO INC") rather than
the trade name the office uses publicly (often a franchise brand). Matching on name + location
therefore fails for many firms. Matching on address alone mostly hit other businesses in the
same building, so it was restricted.

The likely way to recover more phones is a web search per firm (name + city). This
environment had no free search API that allows automated use, so it was not done.

## Re-running

`pipeline/run_all.sh` reruns everything. The Overture extract takes about 15 minutes; the
regulator downloads and roster generators about 5 minutes. Large raw files go to `work/`,
which is not committed.
