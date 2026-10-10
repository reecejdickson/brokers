# micro1-leads

Call list of UK companies with a **confirmed 40-200 average employees** (from their own
Companies House accounts) and a phone number, in office-based, process-heavy SIC codes.
Free sources only. Python + DuckDB + Playwright.

```
pip install duckdb pyarrow rapidfuzz phonenumbers requests playwright
python run.py                    # full run (resumable)
SIC=78109 python run.py          # one SIC code
```

| Step | Script | What it does |
|---|---|---|
| 1 | `s1_companies.py` | Basic Company Data → Active companies with a target SIC code |
| 2 | `s2_employees.py` | 24 months of bulk accounts (iXBRL) → latest `AverageNumberEmployeesDuringPeriod`; keep 40-200 only |
| 3 | `s3_places.py` | UK rows of Overture Maps places via DuckDB (Foursquare OS Places if `HF_TOKEN` is set) |
| 3b | `s4_match.py` | name + postcode matching; high-confidence matches only |
| 4 | `s5_web.py` | gaps: company website (verified by company number on site) → tel:/JSON-LD/UK numbers, Playwright fallback |
| 5-6 | `s6_export.py` | phonenumbers validation, mobile/landline, dedupe, directors (`CH_API_KEY`), CSVs |

Outputs: `output/call-list.csv`, `output/no-phone.csv`. Logs: `logs/`. Raw/intermediate data: `data/` (not committed).

## Latest full run (2026-10-10)

| Step | Count |
|---|---|
| 1. Active companies in the 37 SIC codes | 1,317,830 |
| 2. With an employee figure in accounts filed in the last 24 months (iXBRL) | 851,350 |
| 2. Confirmed 40-200 employees (kept) | 8,408 |
| 3. High-confidence Overture phone matches | 2,410 |
| 4. Remaining 5,998: verified company websites / phones found | 859 / 707 |
| 5. Invalid numbers dropped / duplicate phones dropped | 7 / 20 |
| **call-list.csv** (92 mobile, 2,679 landline, 319 non-geographic) | **3,090** |
| **no-phone.csv** | **5,318** |

Not used: Foursquare OS Places (needs `HF_TOKEN`), Yell.com (403 to automated access),
search engines (no permitted free automated search). Directors need `CH_API_KEY`.
