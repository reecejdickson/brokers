"""STEP 1: Companies House Basic Company Data -> active companies in target SIC codes.
Output: data/companies{tag}.parquet (company_number, name, postcode, address, sic)."""
import os, re, zipfile, urllib.request
import duckdb
from common import *

log = get_logger("s1_companies")
page = urllib.request.urlopen(CH + "en_output.html", timeout=60).read().decode()
fn = re.search(r'href="(BasicCompanyDataAsOneFile-[\d-]+\.zip)"', page).group(1)
zpath, csvdir = os.path.join(DATA, fn), os.path.join(DATA, "basic")
out = os.path.join(DATA, f"companies{tag()}.parquet")
if not os.path.exists(zpath):
    log.info("downloading %s", fn)
    assert download(CH + fn, zpath, log), "Basic Company Data download failed"
os.makedirs(csvdir, exist_ok=True)
z = zipfile.ZipFile(zpath)
csvname = z.namelist()[0]
csvpath = os.path.join(csvdir, csvname)
if not os.path.exists(csvpath):
    z.extract(csvname, csvdir)
sics = sic_filter()
con = duckdb.connect()
cols = con.sql(f"select * from read_csv('{csvpath}', all_varchar=true, sample_size=1000) limit 0").columns
log.info("columns: %s", [c for c in cols][:60])
sic_cols = [c for c in cols if c.strip().startswith("SICCode.SicText")]
sic_expr = " , ".join(f'"{c}"' for c in sic_cols)
q = f"""
with base as (
  select trim("{[c for c in cols if c.strip()=='CompanyNumber'][0]}") as company_number,
         trim("CompanyName") as name,
         trim("CompanyStatus") as status,
         trim("RegAddress.PostCode") as postcode,
         concat_ws(', ', nullif(trim("RegAddress.AddressLine1"),''), nullif(trim("RegAddress.AddressLine2"),''),
                   nullif(trim("RegAddress.PostTown"),''), nullif(trim("RegAddress.County"),''),
                   nullif(trim("RegAddress.PostCode"),'')) as address,
         trim("RegAddress.PostTown") as town,
         list_filter([{sic_expr}], x -> x is not null and trim(x) <> '') as sic_list
  from read_csv('{csvpath}', all_varchar=true)
)
select company_number, name, postcode, town, address,
       list_transform(sic_list, x -> trim(split_part(x, ' - ', 1))) as sic_codes,
       sic_list
from base
where status = 'Active'
  and len(list_intersect(list_transform(sic_list, x -> trim(split_part(x, ' - ', 1))), {sics})) > 0
"""
con.sql(f"copy ({q}) to '{out}' (format parquet)")
total = con.sql(f"select count(*) from read_csv('{csvpath}', all_varchar=true)").fetchone()[0]
active = con.sql(f"select count(*) from read_csv('{csvpath}', all_varchar=true) where trim(\"CompanyStatus\")='Active'").fetchone()[0]
kept = con.sql(f"select count(*) from '{out}'").fetchone()[0]
log.info("STEP1 counts: all companies=%d active=%d kept (active + target SIC %s)=%d -> %s",
         total, active, "all 37" if len(sics) > 1 else sics[0], kept, out)
