"""STEP 2: average number of employees from Companies House bulk accounts (iXBRL).

Downloads the monthly accounts zips for the last 24 months (plus this month's daily files),
parses only filings for companies kept in step 1, extracts the current-period
AverageNumberEmployeesDuringPeriod, keeps the most recent figure per company and then keeps
ONLY companies with 40-200. Companies with no figure are dropped.

Resumable: each zip is marked done in data/s2.state.json and its extracted facts are kept in
data/emp/<zip>.parquet; zips are deleted after processing.
Env: MONTHS=N (only the latest N monthly files, for testing), SIC=... (filter at the end).
"""
import datetime, io, os, re, sys, threading, queue, zipfile, urllib.request
from concurrent.futures import ProcessPoolExecutor
import duckdb, pyarrow as pa, pyarrow.parquet as pq
from common import *

log = get_logger("s2_employees")
EMPDIR = os.path.join(DATA, "emp")
os.makedirs(EMPDIR, exist_ok=True)
state = State("s2")

# ---------- which files (last 24 months + current month's dailies) ----------
def links(page):
    h = urllib.request.urlopen(CH + page, timeout=60).read().decode()
    return re.findall(r'href="([^"]+\.zip)"', h)

MON = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                    "September", "October", "November", "December"], 1)}
today = datetime.date.today()
start = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)  # first day of last full month
cut = datetime.date(start.year - 2 + (start.month == 12), (start.month % 12) + 1, 1)  # 24 months back
monthly = {}
for page in ("en_monthlyaccountsdata.html", "historicmonthlyaccountsdata.html"):
    for l in links(page):
        m = re.search(r"Accounts_Monthly_Data-([A-Za-z]+)(\d{4})\.zip", l)
        if m and m.group(1) in MON:  # skip yearly bundles e.g. JanuaryToDecember2010
            d = datetime.date(int(m.group(2)), MON[m.group(1)], 1)
            if cut <= d <= start:
                monthly[d] = CH + l
daily = [CH + l for l in links("en_accountsdata.html")
         if (m := re.search(r"Accounts_Bulk_Data-(\d{4})-(\d\d)-\d\d\.zip", l))
         and datetime.date(int(m.group(1)), int(m.group(2)), 1) > start]
files = [monthly[d] for d in sorted(monthly, reverse=True)]
if os.environ.get("MONTHS"):
    files = files[:int(os.environ["MONTHS"])]
else:
    files = sorted(daily, reverse=True) + files
log.info("accounts files to process: %d (monthly %s .. %s, dailies %d)", len(files),
         min(monthly).strftime("%b %Y"), max(monthly).strftime("%b %Y"), 0 if os.environ.get("MONTHS") else len(daily))

# ---------- company numbers from step 1 (always the full SIC set, so work is reusable) ----------
wanted = set(duckdb.sql(f"select company_number from '{os.path.join(DATA, 'companies.parquet')}'").fetchnumpy()["company_number"])
log.info("step-1 companies to look for: %d", len(wanted))

# ---------- iXBRL parsing ----------
FACT = re.compile(r'<ix:nonFraction\b([^>]*?name="[^"]*:AverageNumberEmployeesDuringPeriod"[^>]*)>(.*?)</ix:nonFraction>', re.S | re.I)
ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
CTX = re.compile(r'<xbrli:context\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</xbrli:context>', re.S | re.I)
END = re.compile(r'<xbrli:(?:endDate|instant)>\s*([\d-]+)\s*</xbrli:(?:endDate|instant)>', re.I)


def parse(html):
    """-> (employees:int, period_end:str) for the latest non-dimensional context, or None."""
    facts = FACT.findall(html)
    if not facts:
        return None
    ctx = {}
    for cid, body in CTX.findall(html):
        e = END.search(body)
        ctx[cid] = (e.group(1) if e else "", "segment>" in body.lower() or "explicitmember" in body.lower())
    best = None
    for attrs, inner in facts:
        a = dict(ATTR.findall(attrs))
        raw = re.sub(r"<[^>]+>", "", inner).strip().replace(",", "")
        if raw in ("-", "", "nil") and "zerodash" in a.get("format", "").lower():
            raw = "0"
        try:
            v = float(raw) * (10 ** int(a.get("scale", "0") or 0))
        except ValueError:
            continue
        if a.get("sign") == "-":
            v = -v
        end, dim = ctx.get(a.get("contextRef", ""), ("", True))
        key = (not dim, end)
        if best is None or key > best[0]:
            best = (key, v, end)
    if best is None:
        return None
    return int(round(best[1])), best[2]


NUM = re.compile(r"_([A-Z0-9]{8})_(\d{8})\.(?:html|xhtml|xml)$", re.I)


def handle_member(args):
    name, data = args
    try:
        r = parse(data.decode("utf-8", "replace"))
    except Exception as e:  # never crash the batch on one bad file
        return ("error", name, repr(e)[:200])
    return ("ok", name, r)


def process_zip(path, label):
    rows, errors, scanned = [], 0, 0
    jobs = []

    def collect(z, prefix=""):
        nonlocal scanned
        for n in z.namelist():
            if n.lower().endswith(".zip"):
                try:
                    collect(zipfile.ZipFile(io.BytesIO(z.read(n))), prefix + n + "/")
                except Exception as e:
                    log.error("%s: nested zip %s unreadable: %r", label, n, e)
                continue
            m = NUM.search(n)
            if not m:
                continue
            scanned += 1
            if m.group(1) in wanted:
                jobs.append((prefix + n, m.group(1), m.group(2), z.read(n)))
    collect(zipfile.ZipFile(path))
    with ProcessPoolExecutor(4) as ex:
        for (name, cn, made_up, _), res in zip(jobs, ex.map(handle_member, [(j[0], j[3]) for j in jobs], chunksize=64)):
            if res[0] == "error":
                errors += 1
                log.error("%s: parse error %s: %s", label, name, res[2])
            elif res[2]:
                emp, end = res[2]
                rows.append((cn, emp, end or f"{made_up[:4]}-{made_up[4:6]}-{made_up[6:]}", made_up, label, name))
    return rows, scanned, len(jobs), errors


def prefetch(urls, q):
    for u in urls:
        fn = u.rsplit("/", 1)[-1]
        if state.done(fn):
            continue
        dest = os.path.join(DATA, fn)
        ok = os.path.exists(dest) or download(u, dest, log)
        q.put((fn, dest if ok else None))
    q.put(None)


q = queue.Queue(maxsize=1)  # at most one zip waiting on disk while another is parsed
threading.Thread(target=prefetch, args=(files, q), daemon=True).start()
while (item := q.get()) is not None:
    fn, dest = item
    if dest is None:
        log.error("%s: download failed, will retry on next run", fn)
        continue
    try:
        rows, scanned, matched, errs = process_zip(dest, fn)
    except zipfile.BadZipFile as e:
        log.error("%s: bad zip (%r); deleting so the next run re-downloads", fn, e)
        os.remove(dest)
        continue
    pq.write_table(pa.table({k: [r[i] for r in rows] for i, k in enumerate(
        ["company_number", "employees", "period_end", "made_up_date", "source_zip", "source_file"])},
        schema=pa.schema([("company_number", pa.string()), ("employees", pa.int64()), ("period_end", pa.string()),
                          ("made_up_date", pa.string()), ("source_zip", pa.string()), ("source_file", pa.string())])),
        os.path.join(EMPDIR, fn.replace(".zip", ".parquet")))
    state.mark(fn)
    os.remove(dest)
    log.info("%s: filings=%d for step-1 companies=%d with employee figure=%d parse errors=%d",
             fn, scanned, matched, len(rows), errs)

# ---------- combine: most recent figure per company, keep 40-200 ----------
done = [os.path.join(EMPDIR, u.rsplit("/", 1)[-1].replace(".zip", ".parquet")) for u in files
        if state.done(u.rsplit("/", 1)[-1])]
if len(done) < len(files):
    log.warning("%d of %d files not processed yet (re-run to resume)", len(files) - len(done), len(files))
comp = os.path.join(DATA, f"companies{tag()}.parquet")
out = os.path.join(DATA, f"employees{tag()}.parquet")
lst = "[" + ",".join(f"'{p}'" for p in done) + "]"
duckdb.sql(f"""
copy (
  with f as (select * from read_parquet({lst})),
  latest as (select * from f qualify row_number() over (partition by company_number
                 order by period_end desc, made_up_date desc, source_zip desc) = 1)
  select c.*, l.employees, l.period_end, l.source_zip, l.source_file
  from '{comp}' c join latest l using (company_number)
) to '{out}' (format parquet)""")
n_comp = duckdb.sql(f"select count(*) from '{comp}'").fetchone()[0]
n_fig = duckdb.sql(f"select count(*) from '{out}'").fetchone()[0]
inr = os.path.join(DATA, f"employees_40_200{tag()}.parquet")
duckdb.sql(f"copy (select * from '{out}' where employees between {EMP_MIN} and {EMP_MAX}) to '{inr}' (format parquet)")
n_in = duckdb.sql(f"select count(*) from '{inr}'").fetchone()[0]
log.info("STEP2 counts: step-1 companies=%d with an employee figure=%d with 40-200 (kept)=%d [files used %d/%d]",
         n_comp, n_fig, n_in, len(done), len(files))
