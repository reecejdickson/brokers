"""Fetch free rosters from Iron Data / Micropact 'eLicense' GenerateRoster pages (used by
CO DRE, OR REA, OH ...). Usage: python micropact_roster.py BASE_URL OUTDIR "label regex"
BASE_URL e.g. https://apps2.colorado.gov/dre/licensing/Lookup/GenerateRoster.aspx"""
import re, sys, os, html, http.cookiejar, urllib.request, urllib.parse

base, outdir, pat = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", "Mozilla/5.0")]


def form(h):
    return {html.unescape(m.group(1)): html.unescape(m.group(2)) for m in
            re.finditer(r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]*value="([^"]*)"', h)}


h = op.open(base, timeout=60).read().decode("utf-8", "replace")
data = form(h)
picked = []
for m in re.finditer(r'name="([^"]*ckbRoster(\d+))"[^>]*/>\s*(?:<[^>]+>\s*)*([^<]{3,120})', h):
    label = html.unescape(m.group(3)).replace("\xa0", " ").strip()
    if re.search(pat, label, re.I):
        data[m.group(1)] = "on"; picked.append(label)
btn = re.search(r'<input type="submit" name="([^"]*btnRosterContinue)" value="([^"]*)"', h)
data[btn.group(1)] = btn.group(2)
print("picked", picked)
r = op.open(base, urllib.parse.urlencode(data).encode(), timeout=600)
h2 = r.read().decode("utf-8", "replace")
open(os.path.join(outdir, "_step2.html"), "w").write(h2)
print(r.geturl())

# step 3: download each generated roster as CSV
dl = urllib.parse.urljoin(r.geturl(), "FileDownload.aspx")
for m in re.finditer(r'OpenFileDownloadWindow\((\d+),.*?</td><td>([^<]+)</td><td[^>]*>([^<]+)</td>', h2, re.S):
    idnt, name, n = m.group(1), m.group(2).strip(), m.group(3).strip()
    body = op.open(f"{dl}?Idnt={idnt}&Type=Comma", timeout=900).read()
    fn = os.path.join(outdir, re.sub(r"\W+", "_", name).strip("_") + ".csv")
    open(fn, "wb").write(body)
    print(name, n, len(body), "bytes ->", fn)
