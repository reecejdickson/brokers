"""STEP 3a: UK rows of open place datasets with DuckDB.
 - Overture Maps places (latest release, anonymous S3): bbox-filtered to the UK, country GB.
 - Foursquare OS Places: gated on Hugging Face (free account + accepted terms). Used only if
   HF_TOKEN is set; otherwise skipped and logged.
Outputs data/places_overture_gb.parquet, data/places_fsq_gb.parquet (if available)."""
import os, re, urllib.request
from common import *

log = get_logger("s3_places")
con = duck()
xml = urllib.request.urlopen("https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/?list-type=2&prefix=release/&delimiter=/").read().decode()
rel = sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", xml))[-1]
host = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com"
keys = re.findall(r"<Key>([^<]+\.parquet)</Key>", urllib.request.urlopen(
    f"{host}/?list-type=2&prefix=release/{rel}/theme=places/type=place/").read().decode())
src = "[" + ",".join(f"'{host}/{k}'" for k in keys) + "]"
out = os.path.join(DATA, "places_overture_gb.parquet")
if not os.path.exists(out):
    log.info("Overture release %s schema:\n%s", rel, con.sql(f"DESCRIBE SELECT * FROM read_parquet({src})"))
    con.sql(f"""
    COPY (
      SELECT id, names.primary AS name, phones, websites, addresses[1].freeform AS address,
             addresses[1].locality AS town, addresses[1].postcode AS postcode,
             basic_category, taxonomy.primary AS category, confidence
      FROM read_parquet({src})
      WHERE bbox.xmin BETWEEN -8.7 AND 1.9 AND bbox.ymin BETWEEN 49.8 AND 60.9
        AND addresses[1].country = 'GB'
    ) TO '{out}' (FORMAT parquet)""")
n, ph, pc = con.sql(f"SELECT count(*), count(*) FILTER (len(phones)>0), count(*) FILTER (len(phones)>0 AND postcode IS NOT NULL) FROM '{out}'").fetchone()
log.info("Overture %s GB places=%d with phone=%d with phone+postcode=%d", rel, n, ph, pc)

fout = os.path.join(DATA, "places_fsq_gb.parquet")
tok = os.environ.get("HF_TOKEN")
if os.path.exists(fout):
    log.info("Foursquare GB already extracted")
elif not tok:
    log.warning("Foursquare OS Places skipped: dataset is gated on Hugging Face; set HF_TOKEN "
                "(free account, accept terms at huggingface.co/datasets/foursquare/fsq-os-places) to include it")
else:
    import json
    api = "https://huggingface.co/api/datasets/foursquare/fsq-os-places/tree/main/release"
    req = lambda u: json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": f"Bearer {tok}"})))
    dt = sorted(x["path"] for x in req(api) if "dt=" in x["path"])[-1]
    parts = [x["path"] for x in req(f"https://huggingface.co/api/datasets/foursquare/fsq-os-places/tree/main/{dt}/places/parquet")]
    con.sql(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{tok}')")
    urls = ",".join(f"'hf://datasets/foursquare/fsq-os-places/{p}'" for p in parts)
    log.info("Foursquare %s schema:\n%s", dt, con.sql(f"DESCRIBE SELECT * FROM read_parquet([{urls}])"))
    con.sql(f"""COPY (SELECT fsq_place_id AS id, name, [tel] AS phones, [website] AS websites, address,
                 locality AS town, postcode, fsq_category_labels AS category
               FROM read_parquet([{urls}]) WHERE country = 'GB' AND date_closed IS NULL) TO '{fout}' (FORMAT parquet)""")
    log.info("Foursquare GB places=%d", con.sql(f"SELECT count(*) FROM '{fout}'").fetchone()[0])
