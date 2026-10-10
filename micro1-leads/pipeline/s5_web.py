"""STEP 4: fill phone gaps from the company's own website (confirmed 40-200 companies only).

Website discovery (no paid APIs):
  a) websites attached to Overture places whose normalised name equals the company's;
  b) domains derived from the company name (name.co.uk / name.com / name.uk / hyphenated).
  A site is accepted ONLY if one of its pages shows the company's registration number
  (UK companies must state it on their websites) or its full registered name together
  with the registered postcode.
Search engines are not scraped (their terms forbid automated queries; DuckDuckGo returns a
bot challenge) and Yell.com returns 403 to automated access; both are logged as skipped.
Phones: homepage, /contact, /contact-us (+ /about, /privacy only for verification);
tel: links, JSON-LD "telephone", then UK number patterns in visible text.
Pages that return no phone are re-rendered with Playwright (headless Chromium).
Politeness: 1 request/second per host, 5 workers. Resumable via data/web_cache.jsonl."""
import json, os, re, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import duckdb, pyarrow as pa, pyarrow.parquet as pq, requests, phonenumbers
from common import *

log = get_logger("s5_web")
comp = pq.read_table(os.path.join(DATA, f"employees_40_200{tag()}.parquet")).to_pylist()
matched = {r["company_number"] for r in pq.read_table(os.path.join(DATA, f"matched{tag()}.parquet")).to_pylist()}
todo = [c for c in comp if c["company_number"] not in matched]
CACHE = os.path.join(DATA, "web_cache.jsonl")
cache = {}
if os.path.exists(CACHE):
    for line in open(CACHE):
        try:
            d = json.loads(line); cache[d["company_number"]] = d
        except ValueError:
            pass
log.info("companies without a step-3 phone: %d (already cached: %d)", len(todo), sum(c["company_number"] in cache for c in todo))

# Overture websites by normalised name
site_by_name = {}
for name, webs in duckdb.sql(f"SELECT name, websites FROM '{os.path.join(DATA, 'places_overture_gb.parquet')}' WHERE len(websites) > 0").fetchall():
    site_by_name.setdefault(norm_name(name), set()).update(w for w in webs if w)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
_last, _lock = {}, threading.Lock()
sess = requests.Session()
sess.headers["User-Agent"] = UA


def get(url):
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(2):
        with _lock:
            wait = 1.0 - (time.time() - _last.get(host, 0))
            _last[host] = time.time() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)
        try:
            r = sess.get(url, timeout=12, allow_redirects=True)
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                return r.url, r.text[:2_000_000]
            if r.status_code in (403, 404, 410, 401, 451):
                return None
        except requests.RequestException:
            if attempt:
                return None
            time.sleep(2)
    return None


def candidates(c):
    out = []
    for w in sorted(site_by_name.get(norm_name(c["name"]), ())):
        out.append(re.sub(r"^(https?://)?", "https://", w.strip()))
    base = re.sub(r"\b(limited|ltd|llp|plc)\b", " ", c["name"].lower().replace("&", " and "))
    toks = re.findall(r"[a-z0-9]+", base)
    if toks:
        joined, hyph = "".join(toks), "-".join(toks)
        no_generic = "".join(t for t in toks if t not in ("the", "and", "uk", "group", "services"))
        for stem in dict.fromkeys([joined, hyph, no_generic]):
            if 3 <= len(stem) <= 40:
                for tld in (".co.uk", ".com", ".uk"):
                    out.append(f"https://www.{stem}{tld}/")
    return list(dict.fromkeys(out))[:10]


def verifies(html, c):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()
    num = c["company_number"].lower()
    nums = {num, num.lstrip("0")} if num.isdigit() else {num}
    if any(re.search(rf"(?<![0-9a-z]){re.escape(n)}(?![0-9])", t) for n in nums if len(n) >= 6):
        return "company number on site"
    pc = (c["postcode"] or "").lower()
    if c["name"].lower() in t and pc and pc in t:
        return "registered name + postcode on site"
    return None


TEL = re.compile(r'href=["\']tel:([^"\']+)', re.I)
JSONLD = re.compile(r'"telephone"\s*:\s*"([^"]+)"', re.I)
UKPAT = re.compile(r"(?<![\d+])(?:\+44\s?\(?0?\)?\s?|\(?0)\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)")
FAX = re.compile(r"fax|facsimile|\bf[:.]", re.I)


def phones_in(html):
    found = []
    for m in TEL.findall(html):
        found.append((urllib.parse.unquote(m), "tel: link"))
    for m in JSONLD.findall(html):
        found.append((m, 'JSON-LD "telephone"'))
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for m in UKPAT.finditer(text):
        if not FAX.search(text[max(0, m.start() - 20):m.start()]):
            found.append((m.group(), "UK number pattern in page text"))
    good = []
    for raw, how in found:
        try:
            n = phonenumbers.parse(raw, "GB")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(n) and n.country_code == 44:
            good.append((phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164), how))
    return good


_pw = threading.local()


def render(url):
    """Playwright fallback for JS-rendered pages (one browser per worker thread)."""
    if getattr(_pw, "failed", False):
        return None
    try:
        if not getattr(_pw, "browser", None):
            from playwright.sync_api import sync_playwright
            _pw.p = sync_playwright().start()
            proxy = os.environ.get("HTTPS_PROXY")
            exe = next((p for p in (os.environ.get("CHROMIUM_PATH", ""),
                                    *sorted(__import__("glob").glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")))
                        if p and os.path.exists(p)), None)
            try:
                _pw.browser = _pw.p.chromium.launch(headless=True, executable_path=exe,
                                                    proxy={"server": proxy} if proxy else None)
            except Exception:
                _pw.failed = True
                _pw.p.stop()
                raise
        page = _pw.browser.new_page(user_agent=UA)
        page.goto(url, timeout=25000, wait_until="networkidle")
        html = page.content()
        page.close()
        return html
    except Exception as e:
        log.warning("playwright %s: %r", url, str(e)[:150])
        return None


def work(c):
    res = dict(company_number=c["company_number"], website=None, verified_by=None, phone=None, how=None, page=None,
               tried=[])
    for cand in candidates(c):
        r = get(cand)
        res["tried"].append(cand)
        if not r:
            continue
        final, html = r
        root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(final))
        pages = {root + "/": html}
        why = verifies(html, c)
        for path in ("/contact", "/contact-us", "/about", "/privacy-policy", "/privacy"):
            if why and path in ("/about", "/privacy-policy", "/privacy"):
                break
            g = get(root + path)
            if g:
                pages[root + path] = g[1]
                why = why or verifies(g[1], c)
        if not why:
            continue
        res.update(website=root, verified_by=why)
        hits = []
        for u in (root + "/contact", root + "/contact-us", root + "/"):
            if u in pages:
                hits += [(p, how, u) for p, how in phones_in(pages[u])]
        if not hits:  # JS-rendered site
            for u in (root + "/contact", root + "/contact-us", root + "/"):
                if u in pages:
                    h = render(u)
                    if h:
                        hits += [(p, how + " (rendered)", u) for p, how in phones_in(h)]
                        if hits:
                            break
        if hits:
            rank = {"tel: link": 0, 'JSON-LD "telephone"': 1}
            counts = {}
            for p, *_ in hits:
                counts[p] = counts.get(p, 0) + 1
            best = sorted(hits, key=lambda h: (rank.get(h[1].replace(" (rendered)", ""), 2), -counts[h[0]]))[0]
            res.update(phone=best[0], how=best[1], page=best[2])
        break
    return res


log.info("Yell.com skipped: returns HTTP 403 to automated requests (and its terms prohibit scraping)")
pending = [c for c in todo if c["company_number"] not in cache]
lock = threading.Lock()
with open(CACHE, "a") as fh, ThreadPoolExecutor(5) as ex:
    for i, res in enumerate(ex.map(work, pending), 1):
        with lock:
            fh.write(json.dumps(res) + "\n"); fh.flush()
            cache[res["company_number"]] = res
        if i % 50 == 0:
            log.info("progress %d/%d", i, len(pending))

rows = []
for c in todo:
    r = cache.get(c["company_number"], {})
    if r.get("phone"):
        rows.append(dict(company_number=c["company_number"], phone_raw=r["phone"], website=r["website"],
                         phone_source=f"company website {r['page']} ({r['how']})",
                         match_confidence=f"high (website verified: {r['verified_by']})"))
schema = pa.schema([(k, pa.string()) for k in ["company_number", "phone_raw", "website", "phone_source", "match_confidence"]])
pq.write_table(pa.Table.from_pylist(rows, schema=schema), os.path.join(DATA, f"web{tag()}.parquet"))
sites = sum(1 for c in todo if cache.get(c["company_number"], {}).get("website"))
log.info("STEP4 counts: companies without phone=%d verified websites found=%d phones found=%d", len(todo), sites, len(rows))
