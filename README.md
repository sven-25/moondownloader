<div align="center">

# 🌙 Moon Downloader

### **v14.1**

**Lightning-fast bulk file downloader** for datanodes.to & fuckingfast.co

Built with Python · Playwright · aiohttp

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/LeyckerS/moondownloader)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](https://github.com/LeyckerS/moondownloader)

---

<br>

> **Best tested: `~250 MB/s` on a 2.5 Gbps fiber — 23.5 GB across 47 files in ~3 minutes**

<br>

</div>

---

## 📸 Screenshots

<div align="center">

<table>
<tr>
<td align="center"><b>🖥️ Interface</b></td>
<td align="center"><b>✅ Download Complete</b></td>
</tr>
<tr>
<td><img src="screenshot_idle.png" width="500"/></td>
<td><img src="screenshot_done.png" width="500"/></td>
</tr>
</table>

</div>

---

## ⚡ Features

<table>
<tr>
<td width="50%">

### 🚀 Performance
- **8–32 parallel browsers** for extraction
- **16–64 concurrent download streams**
- Stall detection & automatic lane kills
- Resume interrupted downloads (`.tmp`)

</td>
<td width="50%">

### 🛡️ Reliability
- Per-URL retry with exponential backoff
- Dead link detection (instant fail, no wasted time)
- Ad overlay bypass & popup dismissal
- Per-session telemetry logs

</td>
</tr>
<tr>
<td width="50%">

### 🎯 Providers
- **fuckingfast.co** — regex extraction, no browser needed
- **datanodes.to** — full browser automation flow
- Automatic provider detection from URL

</td>
<td width="50%">

### 🔧 Modes
- **Download** — extract & download files
- **Links only** — extract direct URLs to file
- CLI version included (`gen_cli.py`)

</td>
</tr>
</table>

---

## 🚀 Quick Start

### One-click launch (Windows)

1. Install **[Python 3.10+](https://www.python.org/downloads/)** — check ✅ *"Add Python to PATH"*
2. Double-click **`avvia.bat`**
3. Done. First run auto-installs everything.

### Manual setup

```bash
pip install -r requirements.txt
playwright install chromium
python gen_1.py
```

### CLI version

```bash
python gen_cli.py <url1> <url2> ... -o <output_folder>
```

---

## ⚙️ Settings

| Setting | Range | Default | Description |
|:--------|:-----:|:-------:|:------------|
| **Browsers** | 8 – 32 | 16 | Parallel browser workers for link extraction |
| **DL Streams** | 16 – 64 | 48 | Concurrent download connections |
| **Retries** | 0 – 5 | 3 | Retry attempts per failed URL |

> **Tip:** For 40+ file sessions, 16 browsers / 48 streams is the sweet spot. For 200+ files, push browsers toward 32.

---

## 📂 Output Files

| File | Description |
|:-----|:------------|
| `moontech_*.log` | Human-readable performance report |
| `moontech_*.json` | Per-file metrics (machine-readable) |
| `output_links.txt` | Extracted direct links (Links-only mode) |
| `failed_links.txt` | URLs that failed all retries |

---

## 📁 Optional Files

Place in the same folder as `gen_1.py`:

| File | Purpose |
|:-----|:--------|
| `proxies.txt` | Proxy list — `ip:port:user:pass` or `http://user:pass@ip:port` |
| `logo.png` | Custom header logo (auto-scaled to 44×44) |

---

## 🏗️ Architecture

Single-file application (~1500 lines), structured in layers:

```
gen_1.py
├── Global config          — theme, tuning constants, user agents
├── Resource singletons    — aiohttp session pool, proxy rotation
├── Extraction layer       — fuckingfast (regex) + datanodes (Playwright)
├── Download engine        — Range resume, stall detection, lane kills
├── Telemetry              — 1 Hz snapshots, .log + .json output
├── GUI (tkinter)          — live stats, dual progress bars, color log
└── Async orchestration    — queue-based workers, semaphore concurrency
```

---

## 📋 Requirements

- **OS:** Windows 10 / 11
- **Python:** 3.10+
- **Disk:** ~150 MB for Chromium browser
- **Packages:** `aiohttp`, `playwright`, `pillow`

---

<div align="center">

**Made with 🖤 and cold coffee**

</div>
