"""Extract US real-estate places (name, phone, website, address) from Overture Maps
places (free, anonymous S3 over HTTPS). Downloads one part file at a time, filters
locally, deletes it. Usage: python 01_overture_extract.py RELEASE OUT.parquet TMPDIR
"""
import os, re, subprocess, sys, urllib.request
import pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc

release, out, tmp = sys.argv[1:4]
host = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com"
prefix = f"release/{release}/theme=places/type=place/"
xml = urllib.request.urlopen(f"{host}/?list-type=2&prefix={prefix}").read().decode()
keys = re.findall(r"<Key>([^<]+\.parquet)</Key>", xml)
cols = ["id", "names", "phones", "websites", "addresses", "basic_category", "taxonomy",
        "confidence", "operating_status"]
parts = []
for i, k in enumerate(keys):
    local = os.path.join(tmp, "part.parquet")
    subprocess.run(["curl", "-sS", "--retry", "5", "-o", local, f"{host}/{k}"], check=True)
    f = pq.ParquetFile(local)
    for rg in range(f.metadata.num_row_groups):
        t = f.read_row_group(rg, columns=cols)
        cat = pc.fill_null(t.column("basic_category"), "")
        tax = pc.fill_null(pc.struct_field(t.column("taxonomy").combine_chunks(), "primary"), "")
        m = pc.or_(pc.match_substring_regex(cat, "real_estate|property_management"),
                   pc.match_substring_regex(tax, "real_estate|property_management"))
        if pc.any(m).as_py():
            sub = t.filter(m)
            # keep US only (country of first address)
            fl = pc.list_flatten(sub.column("addresses").combine_chunks())
            ctry = pc.struct_field(fl, "country").to_pylist()
            offs = sub.column("addresses").combine_chunks().offsets.to_pylist()
            keep = [offs[j + 1] > offs[j] and ctry[offs[j]] == "US" for j in range(len(sub))]
            sub = sub.filter(pa.array(keep))
            if len(sub):
                parts.append(sub)
    os.remove(local)
    print(f"file {i+1}/{len(keys)} rows so far {sum(len(x) for x in parts)}", flush=True)
pq.write_table(pa.concat_tables(parts), out)
print("wrote", out)
