#!/usr/bin/env python3
"""Scrape cosmetic-manufacturer brand colors from brandcolorcode.com into a CSV.

Flow
  1. Crawl the paginated directory (/page/N until a page yields no new brands).
  2. Fetch every brand page, parse breadcrumb + one color block per table.
  3. Append each finished brand to a JSONL cache (resumable), then build the CSV.

Usage
  python bcc_scraper.py                      # full run (resumes automatically)
  python bcc_scraper.py --limit 3            # test on the first 3 unscraped brands
  python bcc_scraper.py --debug-url https://www.brandcolorcode.com/gelish
  python bcc_scraper.py --fresh              # ignore cache and start over

Dependencies: requests, beautifulsoup4, lxml
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://www.brandcolorcode.com"
START_URL = f"{BASE}/company/cosmetic-manufacturers" # substitude with the industry of choice
HOSTS = {"www.brandcolorcode.com", "brandcolorcode.com"}
# First path segments that are site pages, not brand pages.
RESERVED = {"company", "c", "popular", "about", "contact", "submit", "privacy",
            "terms-of-service", "color", "images", "media", "page", "wp-content"}

MAX_DIRECTORY_PAGES = 100  # safety stop

USER_AGENT = "Mozilla/5.0 (compatible; bcc-scraper/1.0)"

CSV_COLUMNS = ["brand_name", "brand_url", "num_colors", "industry", "color_index",
               "hex", "rgb", "cmyk", "pantone", "cmyk_estimated", "pantone_estimated"]

log = logging.getLogger("bcc")

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
_local = threading.local()


def get_session() -> requests.Session:
    """One Session per thread (requests.Session is not guaranteed thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        retry = Retry(total=4, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",), respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
        _local.session = s
    return s


def fetch(url: str, delay: float = 0.0, timeout: int = 30) -> str | None:
    """Return page HTML, or None on 404. Raises on other failures after retries."""
    if delay:
        time.sleep(delay)
    r = get_session().get(url, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


# --------------------------------------------------------------------------- #
# Directory parsing
# --------------------------------------------------------------------------- #
def _canonical(href: str) -> str:
    return urljoin(BASE + "/", href).split("#")[0].split("?")[0].rstrip("/")


def parse_directory(html: str) -> list[tuple[str, str]]:
    """Return [(brand_name, brand_url)] from one directory page (each brand is an h2 link)."""
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    for a in soup.select("h2 a[href]"):
        url = _canonical(a["href"])
        p = urlparse(url)
        segs = [s for s in p.path.split("/") if s]
        if p.netloc not in HOSTS or len(segs) != 1 or segs[0] in RESERVED or url in seen:
            continue
        seen.add(url)
        out.append((a.get_text(" ", strip=True), url))
    return out


def crawl_directory(delay: float) -> list[dict]:
    brands: dict[str, str] = {}
    page = 1
    while page <= MAX_DIRECTORY_PAGES:
        url = START_URL if page == 1 else f"{START_URL}/page/{page}"
        html = fetch(url, delay)
        if html is None:
            break
        new = [(n, u) for n, u in parse_directory(html) if u not in brands]
        log.info("Directory page %d: %d new brands", page, len(new))
        if not new:
            break
        brands.update({u: n for n, u in new})
        page += 1
    else:
        log.warning("Stopped at MAX_DIRECTORY_PAGES=%d", MAX_DIRECTORY_PAGES)
    return [{"name": n, "url": u} for u, n in brands.items()]


# --------------------------------------------------------------------------- #
# Brand page parsing
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _breadcrumb_from_html(soup: BeautifulSoup) -> list[str]:
    candidates = []
    container = soup.select_one('[class*="breadcrumb" i], nav[aria-label*="readcrumb"]')
    if container:
        candidates.append(container)
    # Fallback: an ordered list whose first item is "Home".
    for ol in soup.find_all("ol"):
        first = ol.find("li")
        if first and first.get_text(strip=True).lower() == "home":
            candidates.append(ol)
    for c in candidates:
        items = [li.get_text(" ", strip=True) for li in c.find_all("li")]
        items = [i for i in items if i]
        if len(items) >= 2:
            return items
    return []


def _find_breadcrumb_list(obj):
    if isinstance(obj, dict):
        t = obj.get("@type")
        if t == "BreadcrumbList" or (isinstance(t, list) and "BreadcrumbList" in t):
            return obj
        for v in obj.values():
            r = _find_breadcrumb_list(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_breadcrumb_list(v)
            if r:
                return r
    return None


def _breadcrumb_from_jsonld(soup: BeautifulSoup) -> list[str]:
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except ValueError:
            continue
        bl = _find_breadcrumb_list(data)
        if not bl:
            continue
        els = [e for e in (bl.get("itemListElement") or []) if isinstance(e, dict)]
        els.sort(key=lambda e: e.get("position", 0))
        names = []
        for e in els:
            item = e.get("item")
            n = e.get("name") or (item.get("name") if isinstance(item, dict) else None)
            if n:
                names.append(str(n).strip())
        if len(names) >= 2:
            return names
    return []


def parse_breadcrumb(soup: BeautifulSoup) -> list[str]:
    return _breadcrumb_from_html(soup) or _breadcrumb_from_jsonld(soup)


# Row labels, matched against the WHOLE cell text so a brand called "Pantone ..." can't match.
LABELS = {
    "hex": re.compile(r"hex(?: code)?", re.I),
    "rgb": re.compile(r"rgb(?: values?)?", re.I),
    "cmyk": re.compile(r"cmyk(?: values?)?", re.I),
    "pantone": re.compile(r"pantone\W*(?:\(pms\)\W*)?", re.I),
}
MISSING = {"", "-", "–", "—", "n/a", "na", "none", "null"}


def _label_key(text: str) -> str | None:
    t = text.strip()
    for k, rx in LABELS.items():
        if rx.fullmatch(t):
            return k
    return None


def clean_value(raw: str) -> tuple[str, bool]:
    """Strip whitespace, the copy-icon text and the trailing '*'. Returns (value, had_asterisk)."""
    v = re.sub(r"\s+", " ", raw or "").strip()
    v = re.sub(r"\s*content_copy\s*$", "", v).strip()
    est = v.endswith("*")
    v = v.rstrip("*").strip()
    if v.lower() in MISSING:
        v = ""
    return v, est


def _hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _make_color(raw: dict[str, str]) -> dict:
    hex_v, _ = clean_value(raw.get("hex", ""))
    m = re.search(r"#?\b([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b", hex_v)
    hex_n = "#" + m.group(1).upper() if m else hex_v

    rgb_v, _ = clean_value(raw.get("rgb", ""))
    nums = re.findall(r"\d+", rgb_v)
    rgb_n = ", ".join(nums) if len(nums) == 3 else rgb_v

    cmyk_v, cmyk_est = clean_value(raw.get("cmyk", ""))
    nums = re.findall(r"\d+(?:\.\d+)?", cmyk_v)
    cmyk_n = ", ".join(nums) if len(nums) == 4 else cmyk_v

    pan_v, pan_est = clean_value(raw.get("pantone", ""))
    return {"hex": hex_n, "rgb": rgb_n, "cmyk": cmyk_n, "pantone": pan_v,
            "cmyk_estimated": bool(cmyk_v and cmyk_est),
            "pantone_estimated": bool(pan_v and pan_est)}


def _parse_colors_from_rows(soup: BeautifulSoup) -> list[dict]:
    """Walk every table row in document order; a 'Hex code' row starts a new color."""
    raws: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for i, txt in enumerate(cells[:-1]):
            key = _label_key(txt)
            if not key:
                continue
            if key == "hex":
                if cur is not None:
                    raws.append(cur)
                cur = {}
            elif cur is None:
                cur = {}
            cur.setdefault(key, cells[i + 1])
            break
    if cur is not None:
        raws.append(cur)
    return [_make_color(r) for r in raws]


def _parse_colors_from_text(soup: BeautifulSoup) -> list[dict]:
    """Fallback if the markup isn't tables: split page text on 'Hex code' labels."""
    text = soup.get_text("\n", strip=True)
    colors = []
    for chunk in re.split(r"(?im)^hex(?: code)?$", text)[1:]:
        lines = [l.strip() for l in chunk.split("\n") if l.strip() and l.strip() != "content_copy"]
        if not lines:
            continue
        raw = {"hex": lines[0]}
        for i, l in enumerate(lines[:-1]):
            k = _label_key(l)
            if k and k != "hex" and k not in raw and _label_key(lines[i + 1]) is None:
                raw[k] = lines[i + 1]
        colors.append(_make_color(raw))
    return colors


def parse_colors(soup: BeautifulSoup) -> list[dict]:
    colors = _parse_colors_from_rows(soup) or _parse_colors_from_text(soup)
    seen, unique = set(), []
    for c in colors:  # drop exact duplicate colors within a brand
        key = (c["hex"], c["rgb"], c["cmyk"], c["pantone"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def parse_brand_page(html: str, fallback_name: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    crumbs = parse_breadcrumb(soup)
    colors = parse_colors(soup)
    warnings: list[str] = []

    name = fallback_name
    if not name:
        h1 = soup.find("h1")
        name = re.sub(r"\s*Brand Color Codes\s*$", "", h1.get_text(" ", strip=True)) if h1 else ""
    industry = crumbs[-2] if len(crumbs) >= 2 else ""

    if not crumbs:
        warnings.append("breadcrumb not found")
    elif _norm(crumbs[-1]) != _norm(name):
        warnings.append(f"breadcrumb ends with {crumbs[-1]!r}, expected brand {name!r}")
    if not colors:
        warnings.append("no colors found")
    elif len(colors) > 8:
        warnings.append(f"{len(colors)} colors (expected 1-8)")
    for i, c in enumerate(colors, 1):
        if not re.fullmatch(r"#[0-9A-F]{6}", c["hex"]):
            warnings.append(f"color {i}: unexpected hex {c['hex']!r}")
            continue
        want = _hex_to_rgb(c["hex"])
        got = tuple(int(x) for x in re.findall(r"\d+", c["rgb"])) if c["rgb"] else None
        if got and want and got != want:
            warnings.append(f"color {i}: rgb {c['rgb']} != hex {c['hex']}")
        if not c["rgb"] or not c["cmyk"] or not c["pantone"]:
            warnings.append(f"color {i}: missing rgb/cmyk/pantone")
    return {"brand_name": name, "brand_url": url, "industry": industry,
            "breadcrumb": crumbs, "colors": colors, "warnings": warnings}


def scrape_brand(name: str, url: str, delay: float) -> dict:
    html = fetch(url, delay)
    if html is None:
        raise RuntimeError("404 not found")
    return parse_brand_page(html, name, url)


# --------------------------------------------------------------------------- #
# Persistence (resume) and CSV export
# --------------------------------------------------------------------------- #
def load_cache(path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["brand_url"]] = rec
            except (ValueError, KeyError):
                log.warning("Skipping corrupt cache line %d", n)  # e.g. cut off mid-write
    return done


def build_rows(records: list[dict]) -> list[dict]:
    rows = []
    for rec in records:
        colors = rec["colors"]
        base = {"brand_name": rec["brand_name"], "brand_url": rec["brand_url"],
                "num_colors": len(colors), "industry": rec["industry"]}
        if not colors:  # keep the brand visible rather than dropping it
            rows.append({**base, "color_index": "", "hex": "", "rgb": "", "cmyk": "",
                         "pantone": "", "cmyk_estimated": "", "pantone_estimated": ""})
        for i, c in enumerate(colors, 1):
            rows.append({**base, "color_index": i, **c})
    return rows


def write_csv(records: list[dict], path: Path) -> int:
    rows = build_rows(records)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows accents
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    return len(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="cosmetic_brand_colors.csv", help="CSV output path")
    ap.add_argument("--workdir", default="bcc_data", help="folder for cache and logs")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds to sleep before each request")
    ap.add_argument("--limit", type=int, help="only scrape the first N unscraped brands (testing)")
    ap.add_argument("--fresh", action="store_true", help="ignore existing cache")
    ap.add_argument("--refresh-directory", action="store_true", help="re-crawl the brand list")
    ap.add_argument("--debug-url", help="fetch and parse one brand page, print JSON, exit")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    if args.debug_url:
        rec = scrape_brand("", _canonical(args.debug_url), 0)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    cache_path, dir_path, err_path = work / "brands.jsonl", work / "brand_urls.json", work / "errors.log"
    if args.fresh:
        cache_path.unlink(missing_ok=True)
        dir_path.unlink(missing_ok=True)

    # 1. Brand list
    if dir_path.exists() and not args.refresh_directory:
        brands = json.loads(dir_path.read_text(encoding="utf-8"))
        log.info("Loaded %d brands from %s", len(brands), dir_path)
    else:
        brands = crawl_directory(args.delay)
        dir_path.write_text(json.dumps(brands, indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("Directory crawl found %d brands", len(brands))
    if not brands:
        log.error("No brands found; the directory markup may have changed.")
        return 1

    # 2. Scrape what's left
    done = load_cache(cache_path)
    todo = [b for b in brands if b["url"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    log.info("%d done, %d to scrape", len(done), len(todo))

    failed: list[str] = []
    ex = ThreadPoolExecutor(max_workers=args.workers)
    try:
        with cache_path.open("a", encoding="utf-8") as cache:
            futures = {ex.submit(scrape_brand, b["name"], b["url"], args.delay): b for b in todo}
            for n, fut in enumerate(as_completed(futures), 1):
                b = futures[fut]
                try:
                    rec = fut.result()
                except Exception as e:  # noqa: BLE001 - log and continue; retried on next run
                    failed.append(b["url"])
                    log.error("[%d/%d] FAILED %s: %s", n, len(todo), b["name"], e)
                    with err_path.open("a", encoding="utf-8") as ef:
                        ef.write(f"{time.strftime('%F %T')}\t{b['url']}\t{e!r}\n")
                    continue
                cache.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cache.flush()
                done[rec["brand_url"]] = rec
                log.info("[%d/%d] %s: %d colors", n, len(todo), rec["brand_name"], len(rec["colors"]))
                for w in rec["warnings"]:
                    log.warning("    %s: %s", rec["brand_name"], w)
    except KeyboardInterrupt:
        log.warning("Interrupted; progress is saved. Re-run to resume.")
        ex.shutdown(wait=False, cancel_futures=True)
    else:
        ex.shutdown(wait=True)

    # 3. Export everything scraped so far, in directory order
    records = [done[b["url"]] for b in brands if b["url"] in done]
    nrows = write_csv(records, Path(args.out))
    log.info("Wrote %d rows for %d/%d brands to %s", nrows, len(records), len(brands), args.out)
    if failed:
        log.warning("%d brands failed (see %s). Re-run to retry them.", len(failed), err_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
