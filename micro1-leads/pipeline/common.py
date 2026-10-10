"""Shared config, paths, logging and helpers for the micro1-leads pipeline."""
import logging, os, re, subprocess, json, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output")
LOGS = os.path.join(ROOT, "logs")
for d in (DATA, OUT, LOGS):
    os.makedirs(d, exist_ok=True)

SIC_CODES = ["62011", "62012", "62020", "62090", "63110", "63120", "69101", "69102", "69109", "69201",
             "69202", "69203", "70100", "70210", "70221", "70229", "73110", "73120", "74909", "78109",
             "78200", "82110", "82190", "82200", "82990", "64191", "64999", "65120", "66190", "66220",
             "52103", "52290", "49410", "86210", "86900", "87100", "88100"]
EMP_MIN, EMP_MAX = 40, 200
CH = "https://download.companieshouse.gov.uk/"


def sic_filter():
    """Optional SIC subset for test runs: env SIC=78109[,..]."""
    s = os.environ.get("SIC")
    return [x.strip() for x in s.split(",")] if s else SIC_CODES


def tag():
    """Suffix for output/intermediate files so a test run never clobbers a full run."""
    s = os.environ.get("SIC")
    return "_sic" + s.replace(",", "-") if s else ""


def get_logger(name):
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        fh = logging.FileHandler(os.path.join(LOGS, name + ".log"))
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(fh)
        log.addHandler(sh)
    return log


def download(url, dest, log=None):
    """Resumable download (curl -C -) with retries. Returns True on success."""
    for attempt in range(5):
        r = subprocess.run(["curl", "-sS", "-L", "--fail", "-C", "-", "--retry", "3", "-o", dest + ".part", url],
                           capture_output=True, text=True)
        if r.returncode == 0:
            os.replace(dest + ".part", dest)
            return True
        if log:
            log.warning("download %s attempt %d failed: %s", url, attempt + 1, r.stderr.strip()[:200])
        time.sleep(2 ** attempt)
    return False


class State:
    """Tiny JSON state file for resumability."""
    def __init__(self, name):
        self.path = os.path.join(DATA, name + ".state.json")
        self.d = json.load(open(self.path)) if os.path.exists(self.path) else {}

    def done(self, key):
        return self.d.get(key) == "done"

    def mark(self, key, val="done"):
        self.d[key] = val
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w"), indent=1)
        os.replace(tmp, self.path)


SUFFIX = r"\b(limited|ltd|llp|plc|l\.l\.p|p\.l\.c|the|uk|u\.k|group|holdings|services|company|co)\b"


def norm_name(s):
    """lowercase; drop Ltd/Limited/LLP/PLC/&/punctuation and a few generic words."""
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("'", ""))
    s = re.sub(SUFFIX, " ", s)
    s = re.sub(r"\band\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_pc(s):
    s = re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    return s[:-3] + " " + s[-3:] if len(s) >= 5 else s


def pc_district(pc):
    return norm_pc(pc).split(" ")[0] if pc else ""


def pc_area(pc):
    m = re.match(r"[A-Z]{1,2}", norm_pc(pc))
    return m.group(0) if m else ""


def duck():
    """DuckDB connection with httpfs loaded through this environment's HTTPS proxy."""
    import duckdb
    con = duckdb.connect()
    ext = os.path.join(DATA, "ext", "httpfs.duckdb_extension")
    if not os.path.exists(ext):
        os.makedirs(os.path.dirname(ext), exist_ok=True)
        v = duckdb.__version__
        subprocess.run(["curl", "-sS", "-o", ext + ".gz",
                        f"https://extensions.duckdb.org/v{v}/linux_amd64/httpfs.duckdb_extension.gz"], check=True)
        subprocess.run(["gunzip", "-f", ext + ".gz"], check=True)
    con.sql(f"INSTALL '{ext}'; LOAD httpfs;")
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if p:
        con.sql(f"SET http_proxy='{p.replace('http://', '')}'")
    con.sql("SET s3_region='us-west-2'")
    return con
