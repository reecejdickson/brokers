#!/usr/bin/env bash
# Full pipeline. WORK holds large downloads (not committed); output/ holds deliverables.
set -euo pipefail
cd "$(dirname "$0")/.."
WORK=${WORK:-./work}; mkdir -p "$WORK/ovt" "$WORK/raw" output
pip install -q pyarrow rapidfuzz phonenumbers
[ -f "$WORK/overture_re.parquet" ] || python3 pipeline/01_overture_extract.py "${OVERTURE_RELEASE:-2026-09-23.1}" "$WORK/overture_re.parquet" "$WORK/ovt"
python3 pipeline/02_headcount.py "$WORK/raw" output/firms_headcount_35_75.csv output/headcount_source_log.csv
python3 pipeline/03_phones.py output/firms_headcount_35_75.csv "$WORK/overture_re.parquet" output
