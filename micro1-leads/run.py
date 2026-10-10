"""Run the micro1-leads pipeline end to end (every step is resumable; re-run to continue).

  python run.py                 # all 37 SIC codes
  SIC=78109 python run.py       # one SIC code (outputs suffixed _sic78109)
  python run.py --from 4        # start from a later step

Optional env: CH_API_KEY (director names), HF_TOKEN (Foursquare OS Places), MONTHS=N (test on
the latest N monthly accounts files only)."""
import os, subprocess, sys

STEPS = [("1", "s1_companies.py"), ("2", "s2_employees.py"), ("3", "s3_places.py"), ("3b", "s4_match.py"),
         ("4", "s5_web.py"), ("5-6", "s6_export.py")]
start = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "1"
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline")
go = False
for n, script in STEPS:
    go = go or n == start
    if not go:
        continue
    print(f"=== step {n}: {script}", flush=True)
    r = subprocess.run([sys.executable, script], cwd=here)
    if r.returncode != 0:
        sys.exit(f"step {n} failed (see logs/); fix and re-run with --from {n}")
