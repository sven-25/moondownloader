# MoonDownloader v14.0

Fast bulk downloader for **datanodes.to** and **fuckingfast.co** with GUI interface.

Uses Playwright browser automation for link extraction and async HTTP (aiohttp) for concurrent downloading.

**Best tested:** ~106 MB/s on a 1 Gbps line (22 GB / 43 files in 3m 30s)

---

## Quick Start (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/)
2. Double-click **`avvia.bat`** — it handles everything automatically

That's it. First run installs dependencies and Chromium browser (~150 MB one-time download).

## Manual Setup

```bash
pip install -r requirements.txt
playwright install chromium
python gen_1.py
```

## CLI Version

```bash
python gen_cli.py <url1> <url2> ... -o <output_folder>
```

## Features

- Concurrent browser extraction (8–32 parallel browsers)
- Concurrent downloads (16–64 streams)
- Stall detection & automatic retry
- Resume interrupted downloads (`.tmp` files)
- Links-only mode (extract without downloading)
- Per-session telemetry logs (`.log` + `.json`)

## Settings

| Setting | Range | Default | Notes |
|---------|-------|---------|-------|
| Browsers | 8–32 | 16 | Parallel extraction workers |
| DL Streams | 16–64 | 48 | Concurrent download connections |
| Retries | 0–5 | 3 | Per-URL retry attempts |

## Optional Files

Place next to `gen_1.py`:

- `proxies.txt` — proxy list, one per line: `ip:port:user:pass` or `http://user:pass@ip:port`
- `logo.png` — custom header logo (auto-scaled to 44x44)

## Output Files

| File | Description |
|------|-------------|
| `moontech_*.log` | Human-readable performance report |
| `moontech_*.json` | Per-file metrics (machine-readable) |
| `output_links.txt` | Extracted direct links (Links-only mode) |
| `failed_links.txt` | URLs that failed all retries |

## Requirements

- Windows 10/11
- Python 3.10+
- ~150 MB disk for Chromium browser
