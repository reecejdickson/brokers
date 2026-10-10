"""STEP 3b: bulk phone matching of confirmed 40-200 companies to open place data.

Name normalisation: lowercase, drop Ltd/Limited/LLP/PLC/&/punctuation (+ a few generic words).
Accepted matches (high confidence only):
  exact_postcode  normalised name identical AND full postcode identical
  exact_district  normalised name identical AND same postcode district (e.g. "M1")
  exact_area      normalised name identical within the same postcode area (e.g. "M")
  fuzzy_area      token_sort_ratio >= 96 within the same postcode area, normalised name >= 12
                  chars; in both area cases the best candidate must beat any runner-up with a
                  different phone by >= 3 points
Anything weaker is rejected. Output data/matched{tag}.parquet."""
import os
from collections import defaultdict
import duckdb, pyarrow as pa, pyarrow.parquet as pq
from rapidfuzz import fuzz, process
from common import *

log = get_logger("s4_match")
comp = pq.read_table(os.path.join(DATA, f"employees_40_200{tag()}.parquet")).to_pylist()
srcs = [("overture", os.path.join(DATA, "places_overture_gb.parquet")),
        ("foursquare", os.path.join(DATA, "places_fsq_gb.parquet"))]
by_exact, by_area = defaultdict(list), defaultdict(list)
for sname, path in srcs:
    if not os.path.exists(path):
        log.info("%s places not available, skipped", sname); continue
    for p in duckdb.sql(f"SELECT id, name, phones, websites, postcode, address FROM '{path}' WHERE len(phones) > 0").fetchall():
        pid, name, phones, webs, pc, addr = p
        n = norm_name(name)
        if len(n) < 3 or not pc:
            continue
        rec = dict(src=sname, id=pid, name=name, n=n, phone=phones[0], website=(webs or [None])[0],
                   pc=norm_pc(pc), district=pc_district(pc), addr=addr)
        by_exact[n].append(rec)
        by_area[pc_area(pc)].append(rec)
log.info("indexed places with phone+postcode: %d", sum(len(v) for v in by_area.values()))

rows, counts = [], defaultdict(int)
for c in comp:
    n, pc = norm_name(c["name"]), norm_pc(c["postcode"])
    dist, area = pc_district(pc), pc_area(pc)
    hit = None
    if n:
        ex = by_exact.get(n, [])
        same_pc = [r for r in ex if r["pc"] == pc]
        same_d = [r for r in ex if r["district"] == dist]
        if same_pc:
            hit = (same_pc[0], "exact_postcode", 100.0)
        elif same_d and len({r["phone"] for r in same_d}) == 1:
            hit = (same_d[0], "exact_district", 100.0)
        elif len(n) >= 5 and area:
            cands = by_area.get(area, [])
            res = process.extract(n, [r["n"] for r in cands], scorer=fuzz.token_sort_ratio, limit=5, score_cutoff=90)
            if res and (res[0][1] == 100 or (res[0][1] >= 96 and len(n) >= 12)):
                best = cands[res[0][2]]
                rivals = [cands[i] for _, s, i in res[1:] if s > res[0][1] - 3 and cands[i]["phone"] != best["phone"]]
                if not rivals:
                    hit = (best, "exact_area" if res[0][1] == 100 else "fuzzy_area", round(res[0][1], 1))
    if hit:
        r, kind, score = hit
        counts[kind] += 1
        rows.append(dict(company_number=c["company_number"], phone_raw=r["phone"], website=r["website"],
                         phone_source=f"{r['src']} place {r['id']} ('{r['name']}', {r['pc']})",
                         match_confidence=f"high ({kind}, score {score})"))
out = os.path.join(DATA, f"matched{tag()}.parquet")
schema = pa.schema([(k, pa.string()) for k in ["company_number", "phone_raw", "website", "phone_source", "match_confidence"]])
pq.write_table(pa.Table.from_pylist(rows, schema=schema), out)
log.info("STEP3 counts: confirmed 40-200 companies=%d high-confidence phone matches=%d (%s)",
         len(comp), len(rows), dict(counts))
