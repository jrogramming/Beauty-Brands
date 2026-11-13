# ♠ &nbsp; Cosmetic Brand Color Scraper &nbsp; ♠

<p align="center">
  <em>Automated cosmetic brand color data collection using Python and web scraping.</em>
</p>

---

### &nbsp; ♜ &nbsp; ABOUT THE PROJECT

A web scraping project that collects cosmetic brand color palettes from [Brand Color Code](https://www.brandcolorcode.com/company/cosmetic-manufacturers) and exports the results into a structured CSV dataset.

Each brand can have between 1–8 associated colors, with corresponding color specifications and industry classifications.

**Purpose:**
- Crawling paginated brand directories to collect brand names and URLs.
- Extracting color palettes from individual brand pages.
- Parsing HEX, RGB, CMYK, and Pantone color specifications.
- Identifying estimated color values using source-provided asterisks.
- Extracting industry classifications from breadcrumb navigation.
- Cleaning, validating, and deduplicating color records.
- Building a structured CSV dataset for downstream analysis.

---

### &nbsp; ♥ &nbsp; HOW IT WORKS

The scraping process follows five steps:

| Step | Operation | Purpose |
|:---:|:---|:---|
| 01 | `crawl_directory()` | Collect brand names and URLs across paginated directories. |
| 02 | `fetch()` | Retrieve individual brand pages using HTTP requests. |
| 03 | `parse_breadcrumb()` | Extract brand and industry classifications. |
| 04 | `parse_colors()` | Extract and clean HEX, RGB, CMYK, and Pantone values. |
| 05 | `write_csv()` | Export the collected records into a structured CSV. |

**The scraping pipeline:**

```text
Brand Directory
      |
      v
Crawl Paginated Listings
      |
      v
Collect Brand URLs
      |
      v
Fetch Individual Brand Pages
      |
      v
Extract Breadcrumbs & Color Tables
      |
      v
Clean & Validate Color Values
      |
      v
Save JSONL Cache
      |
      v
Export CSV Dataset
```

---

### &nbsp; ♦ &nbsp; GETTING STARTED

**01. Clone the repository**

```bash
git clone <repository-url>
cd <repository-folder>
```

**02. Install dependencies**

```bash
pip install requests beautifulsoup4 lxml
```

**03. Run the scraper**

For a full scrape:

```bash
python bcc_scraper.py
```

For a more conservative request rate:

```bash
python bcc_scraper.py --workers 1 --delay 3
```

**04. Test on a smaller dataset**

```bash
python bcc_scraper.py --limit 3
```

**05. Debug an individual brand page**

```bash
python bcc_scraper.py --debug-url https://www.brandcolorcode.com/gelish
```

---

### &nbsp; ♣ &nbsp; UNDERSTANDING THE CODE

**Web Requests — `fetch()`**

Uses `requests` to retrieve website HTML.

- `requests.Session()` maintains reusable HTTP sessions.
- `HTTPAdapter()` configures connection retries.
- `Retry()` handles HTTP errors, including 429 (Too Many Requests).
- Exponential backoff and `Retry-After` headers help manage failed requests.
- Configurable delays help control request frequency.

**Directory Extraction — `crawl_directory()`**

- Iterates through paginated manufacturer directories.
- `BeautifulSoup` parses HTML content.
- CSS selectors (`h2 a[href]`) identify brand links.
- URL canonicalization removes query parameters and fragments.
- Reserved paths and duplicate URLs are filtered out.

**Brand Classification — `parse_breadcrumb()`**

Extracts breadcrumb navigation to identify the brand's industry.

- Attempts to locate HTML breadcrumb elements first.
- Falls back to JSON-LD structured data if necessary.
- Uses the parent breadcrumb category as the industry classification.

**Color Extraction — `parse_colors()`**

Extracts individual color specifications from brand pages.

- `_parse_colors_from_rows()` identifies color values from HTML tables.
- `_parse_colors_from_text()` provides a fallback when table markup is unavailable.
- `_make_color()` standardizes extracted values.
- `clean_value()` removes formatting artifacts and identifies estimated values.
- Duplicate color records are removed.

**Color Validation**

- HEX values are normalized into uppercase six-digit codes.
- RGB values are checked against their corresponding HEX values.
- CMYK and Pantone values are checked for missing information.
- Estimated values are tracked separately using Boolean indicators.

**Data Persistence — `load_cache()` & `write_csv()`**

Uses JSONL caching to preserve progress between runs.

- Each successfully scraped brand is appended to `brands.jsonl`.
- Previously completed brands are skipped when resuming.
- Failed URLs are recorded in `errors.log` for troubleshooting.
- CSV output is regenerated using all successfully cached records.

---

### &nbsp; ♠ &nbsp; DATA STRUCTURE

The exported CSV contains one row per color, with brand-level information repeated across its associated colors.

| Column | Description |
|:---|:---|
| `brand_name` | Cosmetic brand name. |
| `brand_url` | Source brand page URL. |
| `num_colors` | Total unique colors identified for the brand. |
| `industry` | Industry category extracted from breadcrumbs. |
| `color_index` | Sequential color number within the brand. |
| `hex` | HEX color code. |
| `rgb` | RGB color values. |
| `cmyk` | CMYK color values. |
| `pantone` | Pantone color reference. |
| `cmyk_estimated` | Whether the CMYK value is marked as estimated. |
| `pantone_estimated` | Whether the Pantone value is marked as estimated. |

---

### &nbsp; ♥ &nbsp; REPOSITORY STRUCTURE

```text
.
├── bcc_scraper.py
├── cosmetic_brand_colors.csv
└── bcc_data/
    ├── brands.jsonl
    ├── brand_urls.json
    └── errors.log
```

| File | Purpose |
|:---|:---|
| `bcc_scraper.py` | Main scraping and data processing script. |
| `cosmetic_brand_colors.csv` | Exported color dataset. |
| `brands.jsonl` | Resumable cache of successfully scraped brands. |
| `brand_urls.json` | Cached directory of brand names and URLs. |
| `errors.log` | Log of failed brand requests. |

---

### &nbsp; ♦ &nbsp; CONFIGURATION

| Argument | Default | Description |
|:---|:---|:---|
| `--out` | `cosmetic_brand_colors.csv` | CSV output path. |
| `--workdir` | `bcc_data` | Cache and log directory. |
| `--workers` | `4` | Number of concurrent scraping workers. |
| `--delay` | `0.5` | Delay between requests per worker, in seconds. |
| `--limit` | None | Maximum number of unscraped brands to process. |
| `--fresh` | False | Clear existing cache and directory list. |
| `--refresh-directory` | False | Re-crawl the brand directory. |
| `--debug-url` | None | Scrape and inspect one individual brand page. |

**Note:** The website may return HTTP 429 errors when requests are too frequent. Reducing workers and increasing the delay can help avoid rate limiting. Existing cache data is preserved when resuming a normal run.

---

### &nbsp; ♣ &nbsp; TECHNOLOGIES

| Technology | Application |
|:---|:---|
| Python | Core programming language. |
| Requests | HTTP requests and session management. |
| BeautifulSoup4 | HTML parsing and data extraction. |
| lxml | HTML parsing backend. |
| JSON / JSONL | Intermediate data storage and resumable caching. |
| CSV | Structured dataset export. |
| ThreadPoolExecutor | Concurrent brand-page processing. |
| Regular Expressions | Color value extraction, cleaning, and validation. |

---

<p align="center">
  <sub>♠ &nbsp; Happy scraping! &nbsp; ♠</sub>
</p>