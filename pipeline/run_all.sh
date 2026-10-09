#!/usr/bin/env bash
# Full pipeline. WORK holds large downloads (not committed); output/ holds deliverables.
# Needs outbound HTTPS to the regulator hosts in output/sources_checked.csv, Overture's
# S3 bucket, census.gov, and (for the website check) general internet access.
set -euo pipefail
cd "$(dirname "$0")/.."
WORK=${WORK:-./work}; mkdir -p "$WORK/ovt" "$WORK/raw" output
pip install -q pyarrow rapidfuzz phonenumbers xlrd openpyxl
[ -f "$WORK/overture_re.parquet" ] || python3 pipeline/01_overture_extract.py "${OVERTURE_RELEASE:-2026-09-23.1}" "$WORK/overture_re.parquet" "$WORK/ovt"
[ -f "$WORK/zcta_county.txt" ] || curl -sS -o "$WORK/zcta_county.txt" https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt
python3 pipeline/02_headcount.py "$WORK/raw" output/firms_headcount_35_75.csv output/headcount_source_log.csv
python3 pipeline/03_phones.py output/firms_headcount_35_75.csv "$WORK/overture_re.parquet" "$WORK/zcta_county.txt" output
