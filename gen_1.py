"""
MoonTech — Hits Link Generator  v14.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Single-file edition. All fixes from v13, proven v12 download engine.

Changes vs v12:
  ✓ Default 16 browsers / 48 DL streams / thread pool 12
  ✓ Stall threshold 0.5 MB/s (was 1.5) — only truly stuck files get killed
  ✓ Stall grace 90s, max 1 kill, 30 MB window guard — near-zero false kills
  ✓ bytes_acc is collections.deque(maxlen=2000) — no unbounded growth
  ✓ Shared counters protected by threading.Lock
  ✓ ETA byte-based — no more "10s forever" bug
  ✓ Telemetry log bug fixed (FileRecord unhashable)
  ✓ browser_worker / do_dl extracted as App methods
"""
import os, re, ctypes, asyncio, threading, tkinter as tk
import math, time, random, traceback, json, datetime, collections, io
from tkinter import filedialog, scrolledtext
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

import aiohttp
from playwright.async_api import async_playwright

# ── THEME ──────────────────────────────────────────────────────────────────────
BG      = "#080b12"
BG2     = "#0c1018"
BG3     = "#111520"
SURFACE = "#161c2a"
CARD    = "#161c2a"
BORDER  = "#1e2840"
BORDER2 = "#263350"
ACC     = "#00d4ff"
ACC2    = "#0099cc"
ACC3    = "#00ffb3"
GOLD    = "#f5a623"
TEXT    = "#e8f0ff"
TEXT2   = "#8899bb"
TEXT3   = "#3d506e"
OK      = "#00e676"
ERR     = "#ff4d6d"
WARN    = "#ffb547"
INFO    = "#00d4ff"
VERSION = "v14.0"

# ── TUNING ─────────────────────────────────────────────────────────────────────
DEFAULT_DL_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads", "datanodes")
RECV_CHUNK        = 4  * 1024 * 1024
WRITE_BUF         = 16 * 1024 * 1024
READ_BUFSZ        = 1  << 19
UI_HZ             = 8
LOG_HZ            = 10

# Stall detection — near-disabled. datanodes.to CDN assigns lanes per session:
# re-extracting returns the same slow lane. A slow file WILL finish. Only kill
# files genuinely stuck at < 0.5 MB/s, once, then let them complete regardless.
STALL_MIN_MBS          = 0.5
STALL_GRACE_S          = 90
STALL_CHECK_S          = 20
STALL_WIN_S            = 60
STALL_MAX_KILL         = 1
STALL_SAFE_PCT         = 0.80
STALL_MIN_BYTES_IN_WIN = 30 * 1024 * 1024
STALL_MIN_FILE_BYTES   = 50 * 1024 * 1024

# Early lane detection — disabled
EARLY_GRACE_S          = 999
EARLY_WIN_S            = 20
EARLY_MIN_MBS          = 1.8
EARLY_MIN_DATA         = 6 * 1024 * 1024
EARLY_MAX_KILLS        = 0

# Racing connections — open 2 connections per file, measure for 15s, keep the faster one.
# CDN assigns lane at TCP connect time → 2 connections = 2 lane draws → keep the winner.
RACE_ENABLED    = False  # disabled — doubles TCP connections, CDN throttles everything
RACE_MEASURE_S  = 15      # measure both connections for this many seconds
RACE_MIN_SIZE   = 32 * 1024 * 1024  # only race files >= 32 MB

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
]

LAUNCH_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-gpu", "--disable-extensions", "--disable-background-networking",
    "--disable-default-apps", "--disable-sync", "--no-first-run", "--no-zygote",
    "--mute-audio", "--hide-scrollbars", "--disable-breakpad",
    "--disable-component-update", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

BLOCKED_RES  = {"image", "media", "font", "ping", "stylesheet"}
BLOCKED_DOMS = {
    "google-analytics", "googletagmanager", "doubleclick", "googlesyndication",
    "facebook.net", "hotjar", "clarity.ms", "thewsere", "justinepulvino", "unridhoncho",
    "adsbygoogle", "googletag", "cloudflareinsights", "static.cloudflareinsights",
    "challenges.cloudflare", "cdn.jsdelivr", "fonts.googleapis", "fonts.gstatic",
    "downloadprotector.com", "oundhertobeconsist.org", "lootlabs",
}

# JS: detect dead/removed file pages — returns true if page shows an error
DEAD_LINK_JS = """() => {
    const txt = document.body?.innerText?.toLowerCase() || '';
    return txt.includes('file not found') || txt.includes('file was deleted')
        || txt.includes('file has been removed') || txt.includes('no file')
        || txt.includes('not be found') || txt.includes('unavailable');
}"""

# JS: remove ad overlay divs (z-index hijack on body > div)
REMOVE_OVERLAYS_JS = """() => {
    document.querySelectorAll('body > div').forEach(el => {
        const s = el.getAttribute('style') || '';
        if (s.includes('z-index') && !el.id && el.className === '') el.remove();
    });
}"""

FIND_BTN_JS = """(txt) => {
    let best=null,bsz=Infinity;
    for(const el of document.querySelectorAll('*')){
        let t='';
        for(const n of el.childNodes)if(n.nodeType===3)t+=n.textContent;
        t=t.trim().toLowerCase();
        if(t.includes(txt)){const r=el.getBoundingClientRect(),s=r.width*r.height;
        if(s>0&&s<bsz){best=el;bsz=s;}}
    }
    if(best){best.click();return true;}return false;
}"""

HAS_BTN_JS = """(txt) => {
    for(const el of document.querySelectorAll('*')){
        let t='';
        for(const n of el.childNodes)if(n.nodeType===3)t+=n.textContent;
        if(t.trim().toLowerCase().includes(txt))return true;
    }
    return false;
}"""

# ── FILENAME UTILS ─────────────────────────────────────────────────────────────
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def _sanitize_filename(name: str) -> str:
    """Strip characters that are invalid in Windows filenames."""
    name = _WIN_INVALID.sub("_", name).strip(". ")
    return name or "download"

# ── SHARED RESOURCES ───────────────────────────────────────────────────────────
_SESSION : aiohttp.ClientSession | None = None
_POOL    = ThreadPoolExecutor(max_workers=12, thread_name_prefix="dl_write")

def _sess() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        conn = aiohttp.TCPConnector(limit=0, limit_per_host=0, force_close=False,
                                    enable_cleanup_closed=True, ttl_dns_cache=600,
                                    keepalive_timeout=30)
        _SESSION = aiohttp.ClientSession(
            connector=conn, read_bufsize=READ_BUFSZ,
            timeout=aiohttp.ClientTimeout(total=7200, connect=20, sock_read=90))
    return _SESSION

async def _close_sess():
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close(); _SESSION = None

# ── PROXY POOL ────────────────────────────────────────────────────────────────
# Loaded from proxies.txt (same folder as gen.py).
# Format per line: ip:port:user:pass  OR  http://user:pass@ip:port
# Proxy sessions are kept per-proxy for connection reuse.

class ProxyPool:
    def __init__(self):
        self.proxies : list[dict] = []   # [{url, auth}]
        self._idx    = 0
        self._lock   = threading.Lock()
        self._sessions : dict[str, aiohttp.ClientSession] = {}

    def load(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        loaded = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    if line.startswith("http://") or line.startswith("https://") or line.startswith("socks"):
                        loaded.append({"url": line, "auth": None})
                    else:
                        parts = line.split(":")
                        if len(parts) == 4:
                            # ip:port:user:pass  OR  user:pass:ip:port
                            # Detect by checking if first part looks like IP
                            import re as _re
                            if _re.match(r'^\d+\.\d+\.\d+\.\d+$', parts[0]):
                                # ip:port:user:pass
                                ip, port, user, passwd = parts
                            else:
                                # user:pass:ip:port (less common)
                                user, passwd, ip, port = parts
                            url  = f"http://{ip}:{port}"
                            auth = aiohttp.BasicAuth(user, passwd)
                            loaded.append({"url": url, "auth": auth})
                        elif len(parts) == 2:
                            # ip:port (no auth)
                            ip, port = parts
                            loaded.append({"url": f"http://{ip}:{port}", "auth": None})
                except Exception:
                    continue
        self.proxies = loaded
        return len(loaded)

    def next(self) -> dict | None:
        """Round-robin proxy selection."""
        if not self.proxies:
            return None
        with self._lock:
            p = self.proxies[self._idx % len(self.proxies)]
            self._idx += 1
        return p

    def get_session(self, proxy: dict) -> aiohttp.ClientSession:
        """Get or create a dedicated aiohttp session for this proxy."""
        key = proxy["url"]
        if key not in self._sessions or self._sessions[key].closed:
            conn = aiohttp.TCPConnector(
                limit=0, limit_per_host=0, force_close=True,
                enable_cleanup_closed=True, ttl_dns_cache=300,
            )
            self._sessions[key] = aiohttp.ClientSession(
                connector=conn, read_bufsize=READ_BUFSZ,
                timeout=aiohttp.ClientTimeout(total=7200, connect=30, sock_read=120),
            )
        return self._sessions[key]

    async def close_all(self):
        for s in self._sessions.values():
            if not s.closed:
                try: await s.close()
                except Exception: pass
        self._sessions.clear()

_PROXY_POOL = ProxyPool()


# ── TELEMETRY ─────────────────────────────────────────────────────────────────

@dataclass
class FileRecord:
    url          : str
    filename     : str
    worker_id    : int   = -1
    stall_kills  : int   = 0
    queued_at    : float = 0.0
    extract_s    : float = 0.0
    dl_start     : float = 0.0
    dl_s         : float = 0.0
    file_bytes   : int   = 0
    status       : str   = "pending"
    error        : str   = ""
    avg_mbs      : float = 0.0
    queue_wait_s : float = 0.0
    notes        : list  = field(default_factory=list)

class Telemetry:
    def __init__(self, cfg: dict):
        self.cfg          = cfg
        self.t0           = time.monotonic()
        self.t_end        = 0.0
        self.files        : dict[str, FileRecord] = {}
        self.snapshots    : list[dict] = []
        self.stall_events : list[dict] = []
        self._lock        = threading.Lock()

    def reg(self, url: str, filename: str) -> FileRecord:
        rec = FileRecord(url=url, filename=filename, queued_at=time.monotonic())
        with self._lock: self.files[url] = rec
        return rec

    def snap(self, browsers, dls, qsize, ok, fail):
        self.snapshots.append({"ts": round(time.monotonic()-self.t0, 1),
            "browsers": browsers, "downloads": dls,
            "queue": qsize, "ok": ok, "fail": fail})

    def stall(self, filename, speed, done_bytes, action):
        self.stall_events.append({"ts": round(time.monotonic()-self.t0, 1),
            "file": filename, "speed_mbs": round(speed, 2),
            "done_mb": round(done_bytes/1e6, 1), "action": action})

    def finish(self): self.t_end = time.monotonic()

    def save(self, out_dir: str) -> tuple[str, str]:
        os.makedirs(out_dir, exist_ok=True)
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        lp   = os.path.join(out_dir, f"moontech_{ts}.log")
        jp   = os.path.join(out_dir, f"moontech_{ts}.json")
        el   = self.t_end - self.t0
        recs = list(self.files.values())
        ok_r = [r for r in recs if r.status == "ok"]
        dt   = sorted(r.dl_s for r in ok_r if r.dl_s > 0)
        med  = dt[len(dt)//2] if dt else 0.0
        # FIX: use id-based set — FileRecord is not hashable
        slow_ids = {id(r) for r in ok_r if r.dl_s > med * 2}

        buf = io.StringIO()
        def W(*parts): buf.write(" ".join(str(p) for p in parts) + "\n")

        W("="*72); W(f"MOONTECH {VERSION}  —  PERFORMANCE LOG"); W("="*72)
        W(f"Session  : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        W(f"Duration : {int(el//60)}m {int(el%60)}s  ({el:.1f}s)"); W()
        W("── CONFIG ──────────────────────────────────────────────────────────")
        for k, v in self.cfg.items(): W(f"  {k:<28} {v}")
        W()
        W("── SUMMARY ─────────────────────────────────────────────────────────")
        W(f"  Total links    : {len(recs)}")
        W(f"  Completed OK   : {len(ok_r)}")
        W(f"  Failed         : {len(recs)-len(ok_r)}")
        W(f"  Stall kills    : {sum(r.stall_kills for r in recs)}")
        if ok_r:
            tb = sum(r.file_bytes for r in ok_r)
            W(f"  Total data     : {tb/1e9:.2f} GB")
            W(f"  Session speed  : {tb/el/1e6:.1f} MB/s")
        if dt:
            W(f"  Median DL time : {med:.1f}s")
            W(f"  Slowest file   : {max(dt):.1f}s")
            W(f"  Fastest file   : {min(dt):.1f}s")
        W(f"  Slow (>2x median): {len(slow_ids)}"); W()
        W("── STALL EVENTS ────────────────────────────────────────────────────")
        if self.stall_events:
            for e in self.stall_events:
                W(f"  t={e['ts']:>6.1f}s  {e['file'][:44]:<44}  "
                  f"{e['speed_mbs']:.2f} MB/s  {e['done_mb']:.0f}MB  → {e['action']}")
        else: W("  None.")
        W()
        W("── PER-FILE TIMING ─────────────────────────────────────────────────")
        W(f"  {'#':<4} {'Filename':<48} {'Wkr':>3} {'Kll':>3} "
          f"{'QWait':>6} {'Extr':>6} {'DL':>7} {'Speed':>10} {'Status'}")
        W("  "+"-"*4+" "+"-"*48+" "+"-"*3+" "+"-"*3+" "+"-"*6+" "+"-"*6+" "+"-"*7+" "+"-"*10+" "+"-"*8)
        for i, r in enumerate(recs, 1):
            spd  = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "—"
            flag = " ⚠SLOW" if id(r) in slow_ids else ""
            W(f"  {i:<4} {r.filename[:48]:<48} {r.worker_id:>3} {r.stall_kills:>3} "
              f"{r.queue_wait_s:>6.1f} {r.extract_s:>6.1f} {r.dl_s:>7.1f} {spd:>10} {r.status}{flag}")
            for n in r.notes: W(f"       → {n}")
        W()
        W("── LAST 10 FILES ───────────────────────────────────────────────────")
        for r in recs[-10:]:
            spd = f"{r.avg_mbs:.1f} MB/s" if r.avg_mbs > 0 else "—"
            W(f"  {r.filename[:52]:<52}  DL={r.dl_s:.1f}s  {spd}"
              f"{'  ← SLOW' if id(r) in slow_ids else ''}")
        W()
        W("── CONCURRENCY ─────────────────────────────────────────────────────")
        W(f"  {'Time':>7}  {'Browsers':>8}  {'Downloads':>9}  {'Queue':>5}  {'OK':>5}  {'Fail':>5}")
        step = max(1, len(self.snapshots)//45)
        for s in self.snapshots[::step]:
            W(f"  {s['ts']:>6.1f}s  {s['browsers']:>8}  {s['downloads']:>9}  "
              f"{s['queue']:>5}  {s['ok']:>5}  {s['fail']:>5}")
        W()
        W("── ERRORS ──────────────────────────────────────────────────────────")
        errs = [r for r in recs if r.error]
        if errs:
            for r in errs: W(f"  {r.filename[:52]:<52}  {r.error}")
        else: W("  None.")
        W("="*72)

        with open(lp, "w", encoding="utf-8") as f: f.write(buf.getvalue())
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({
                "session": {"start": datetime.datetime.now().isoformat(),
                            "duration_s": round(el, 2), "config": self.cfg,
                            "total": len(recs), "ok": len(ok_r),
                            "fail": len(recs)-len(ok_r),
                            "stall_kills": sum(r.stall_kills for r in recs),
                            "median_dl_s": round(med, 2)},
                "files": [{k: round(v, 3) if isinstance(v, float) else v
                           for k, v in asdict(r).items()} for r in recs],
                "stall_events": self.stall_events,
                "concurrency": self.snapshots,
            }, f, indent=2)
        return lp, jp

# ── EXTRACTION ─────────────────────────────────────────────────────────────────

async def extract_fuckingfast(url: str) -> str | None:
    try:
        async with _sess().get(url, headers={"User-Agent": random.choice(USER_AGENTS)}) as r:
            m = re.search(r'https://(?:dl\.)?fuckingfast\.co/dl/[a-zA-Z0-9_-]+', await r.text())
            return m.group() if m else None
    except Exception: return None

async def extract_datanodes(context, url: str) -> tuple[str|None, str|None]:
    page         = await context.new_page()
    captured     = asyncio.Event()
    proxy_holder : list[str] = []

    async def on_route(route):
        u, rt = route.request.url, route.request.resource_type
        if rt in BLOCKED_RES or any(d in u for d in BLOCKED_DOMS):
            await route.abort(); return
        if "dlproxy" in u and len(u) > 50:
            proxy_holder.append(u); captured.set(); await route.abort(); return
        await route.continue_()

    await page.route("**/*", on_route)
    proxy_url = cookies_str = None

    async def poll(text: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if await page.evaluate(HAS_BTN_JS, text): return True
            except Exception: pass
            await asyncio.sleep(0.2)
        return False

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if not resp or resp.status >= 400: return None, None

        # Fast-fail on dead/removed file pages
        try:
            if await page.evaluate(DEAD_LINK_JS): return None, None
        except Exception: pass

        # Step 1: submit the landing form (op=download1)
        submitted = False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                done = await page.evaluate("""() => {
                    const form = document.getElementById('downloadForm');
                    if (!form) return false;
                    const inp = document.createElement('input');
                    inp.type='hidden'; inp.name='method_free'; inp.value='Free Download >>';
                    form.appendChild(inp); form.submit(); return true;
                }""")
                if done: submitted = True; break
            except Exception: pass
            await asyncio.sleep(0.3)
        if not submitted: return None, None

        # Wait for navigation after form submit
        try: await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception: pass

        # Fast-fail after form submit if file doesn't exist
        try:
            if await page.evaluate(DEAD_LINK_JS): return None, None
        except Exception: pass

        # Remove ad overlays so JS clicks reach real buttons
        try: await page.evaluate(REMOVE_OVERLAYS_JS)
        except Exception: pass

        # Step 2: click "Free Download" button (Vue SPA)
        if not await poll("free download", 15.0): return None, None
        await page.evaluate(FIND_BTN_JS, "free download")
        for _ in range(50):
            await asyncio.sleep(0.08)
            ads = [p for p in context.pages if p != page]
            if ads:
                for ap in ads:
                    try: await ap.close()
                    except Exception: pass
                break
        await asyncio.sleep(0.3)

        # Step 3: wait for countdown, then click "Start Download"
        if not await poll("start download", 90.0): return None, None
        try: await page.evaluate(REMOVE_OVERLAYS_JS)
        except Exception: pass
        await page.evaluate(FIND_BTN_JS, "start download")
        try: await asyncio.wait_for(captured.wait(), 15.0)
        except asyncio.TimeoutError:
            try: await page.evaluate(REMOVE_OVERLAYS_JS)
            except Exception: pass
            await page.evaluate(FIND_BTN_JS, "start download")
            try: await asyncio.wait_for(captured.wait(), 10.0)
            except asyncio.TimeoutError: pass

        if proxy_holder:
            proxy_url   = proxy_holder[0]
            cookies_str = "; ".join(
                f"{c['name']}={c['value']}" for c in await context.cookies())
    except Exception: pass
    finally:
        try: await page.close()
        except Exception: pass

    return proxy_url, cookies_str

# ── DOWNLOAD ──────────────────────────────────────────────────────────────────

class _StallKill(Exception): pass
class _RaceLoser(Exception): pass


async def _race_connection(
    proxy_url  : str,
    cookies    : str,
    race_id    : int,           # 0 or 1
    measure_s  : float,         # how long to measure
    speed_out  : list,          # [speed_mbs_conn0, speed_mbs_conn1]
    winner_evt : asyncio.Event, # set by the winner to stop the loser
    loser_evt  : asyncio.Event, # set by the loser to stop itself
    bytes_acc  : collections.deque,
    tmp_path   : str,
) -> tuple[bool, float]:
    """
    Opens one connection, measures speed for measure_s seconds,
    writes received data to tmp_path, reports speed in speed_out[race_id].
    Returns (True, bytes_downloaded) if this connection won or was elected winner,
    (False, bytes_downloaded) if this connection lost and was stopped.
    """
    loop   = asyncio.get_event_loop()
    hdrs   = {
        "User-Agent": random.choice(USER_AGENTS),
        "Cookie":     cookies,
        "Referer":    "https://datanodes.to/",
        "Connection": "keep-alive",
    }

    def _write(data: bytes):
        with open(tmp_path, "ab") as f:
            f.write(data)

    bytes_received = 0
    t0 = time.monotonic()

    try:
        async with _sess().get(proxy_url, headers=hdrs) as r:
            if r.status not in (200, 206):
                return False, 0

            buf: list[bytes] = []; bufsz = 0
            async for chunk in r.content.iter_chunked(RECV_CHUNK):
                if not chunk:
                    break
                if winner_evt.is_set() and race_id != 0:
                    # Other conn won — stop
                    return False, bytes_received
                if loser_evt.is_set():
                    return False, bytes_received

                now = time.monotonic()
                bytes_received += len(chunk)
                bytes_acc.append((now, len(chunk)))
                buf.append(chunk); bufsz += len(chunk)

                if bufsz >= WRITE_BUF:
                    data  = b"".join(buf); buf = []; bufsz = 0
                    await loop.run_in_executor(_POOL, _write, data)

                elapsed = now - t0
                if elapsed >= measure_s and not winner_evt.is_set():
                    # Report speed
                    speed_out[race_id] = bytes_received / elapsed / 1e6
                    # If both reported, elect winner
                    other = 1 - race_id
                    if speed_out[other] > 0:
                        my_speed    = speed_out[race_id]
                        other_speed = speed_out[other]
                        if my_speed >= other_speed:
                            winner_evt.set()   # I win
                        else:
                            loser_evt.set()    # I lose
                            return False, bytes_received
                    # else wait for other to report

            if buf:
                await loop.run_in_executor(_POOL, _write, b"".join(buf))

        return True, bytes_received

    except Exception:
        return False, bytes_received


async def download_file_raced(
    proxy_url    : str,
    cookies      : str,
    dest         : str,
    rec          : FileRecord,
    bytes_acc    : collections.deque,
    telem        : Telemetry,
    kill_evt     : asyncio.Event,
    kills_so_far : int,
    early_kills_so_far: int = 0,
    active_dls_ref    : list = None,
) -> tuple[bool, str, int]:
    """
    Race two connections for RACE_MEASURE_S seconds, keep the faster one,
    continue downloading with the winner. Falls back to single-stream on any error.
    """
    # Only race fresh downloads of big files
    tmp = dest + ".tmp"
    already_done = os.path.getsize(tmp) if os.path.exists(tmp) else 0

    if (not RACE_ENABLED
            or already_done > 0
            or (rec.file_bytes > 0 and rec.file_bytes < RACE_MIN_SIZE)
            or kills_so_far >= STALL_MAX_KILL):
        return await download_file(
            proxy_url, cookies, dest, rec, bytes_acc, telem, kill_evt,
            kills_so_far, early_kills_so_far, active_dls_ref)

    tmp0 = dest + ".race0"
    tmp1 = dest + ".race1"
    speed_out  = [0.0, 0.0]
    winner_evt = asyncio.Event()
    loser_evt  = asyncio.Event()

    # Run both connections simultaneously
    try:
        task0 = asyncio.create_task(
            _race_connection(proxy_url, cookies, 0, RACE_MEASURE_S,
                             speed_out, winner_evt, loser_evt, bytes_acc, tmp0))
        task1 = asyncio.create_task(
            _race_connection(proxy_url, cookies, 1, RACE_MEASURE_S,
                             speed_out, winner_evt, loser_evt, bytes_acc, tmp1))

        # Wait for both to finish their race phase
        done, pending = await asyncio.wait(
            [task0, task1],
            timeout=RACE_MEASURE_S + 10,
            return_when=asyncio.ALL_COMPLETED
        )
    except Exception:
        for t in [tmp0, tmp1]:
            try: os.remove(t)
            except: pass
        return await download_file(
            proxy_url, cookies, dest, rec, bytes_acc, telem, kill_evt,
            kills_so_far, early_kills_so_far, active_dls_ref)

    # Determine winner by speed
    spd0, spd1 = speed_out[0], speed_out[1]
    winner_spd  = max(spd0, spd1)
    winner_tmp  = tmp0 if spd0 >= spd1 else tmp1
    loser_tmp   = tmp1 if spd0 >= spd1 else tmp0

    # Clean up loser file
    try: os.remove(loser_tmp)
    except: pass

    if winner_spd == 0:
        # Both failed — fall back
        try: os.remove(winner_tmp)
        except: pass
        rec.notes.append("race both failed, falling back")
        return await download_file(
            proxy_url, cookies, dest, rec, bytes_acc, telem, kill_evt,
            kills_so_far, early_kills_so_far, active_dls_ref)

    winner_bytes = os.path.getsize(winner_tmp) if os.path.exists(winner_tmp) else 0
    loser_label  = f"conn{'1' if spd0>=spd1 else '0'}"
    rec.notes.append(
        f"race: conn0={spd0:.1f} MB/s conn1={spd1:.1f} MB/s → "
        f"{'conn0' if spd0>=spd1 else 'conn1'} wins"
    )

    # Rename winner tmp to standard .tmp and continue with single-stream resume
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        os.rename(winner_tmp, tmp)
    except Exception:
        try: os.remove(winner_tmp)
        except: pass
        return await download_file(
            proxy_url, cookies, dest, rec, bytes_acc, telem, kill_evt,
            kills_so_far, early_kills_so_far, active_dls_ref)

    # Continue download from where the winner left off (resume)
    return await download_file(
        proxy_url, cookies, dest, rec, bytes_acc, telem, kill_evt,
        kills_so_far, early_kills_so_far, active_dls_ref)


async def download_file(
    proxy_url        : str,
    cookies          : str,
    dest             : str,
    rec              : FileRecord,
    bytes_acc        : collections.deque,
    telem            : Telemetry,
    kill_evt         : asyncio.Event,
    kills_so_far     : int,
    early_kills_so_far: int = 0,   # separate counter for early lane kills
    active_dls_ref   : list = None, # [int] — shared active download count
) -> tuple[bool, str, int]:

    tmp  = dest + ".tmp"
    loop = asyncio.get_event_loop()
    detect       = kills_so_far < STALL_MAX_KILL
    early_detect = early_kills_so_far < EARLY_MAX_KILLS
    early_checked = False   # only do one early check per attempt

    def _write(f, data: bytes): f.write(data)

    for att in range(4):
        resume = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        ref = "https://fuckingfast.co/" if "fuckingfast" in proxy_url else "https://datanodes.to/"
        hdrs = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer":    ref,
            "Connection": "keep-alive",
        }
        if cookies: hdrs["Cookie"] = cookies
        if resume > 0: hdrs["Range"] = f"bytes={resume}-"

        # Pick proxy for this attempt — new proxy on each retry for variety
        proxy_cfg  = _PROXY_POOL.next()
        if proxy_cfg:
            dl_session  = _PROXY_POOL.get_session(proxy_cfg)
            dl_proxy    = proxy_cfg["url"]
            dl_proxy_auth = proxy_cfg["auth"]
        else:
            dl_session  = _sess()
            dl_proxy    = None
            dl_proxy_auth = None

        try:
            dl_t0 = time.monotonic()
            req_kwargs = dict(headers=hdrs)
            if dl_proxy:
                req_kwargs["proxy"]      = dl_proxy
                req_kwargs["proxy_auth"] = dl_proxy_auth

            async with dl_session.get(proxy_url, **req_kwargs) as r:
                if r.status == 416:
                    if os.path.exists(tmp): os.replace(tmp, dest)
                    rec.file_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
                    return True, "ok", 0
                if r.status not in (200, 206):
                    return False, f"HTTP {r.status}", resume
                if r.status == 200 and resume > 0: resume = 0

                file_size = int(r.headers.get("Content-Length", 0)) + resume
                if file_size > 0: rec.file_bytes = file_size

                effective_detect = detect and (file_size == 0 or file_size >= STALL_MIN_FILE_BYTES)
                mode = "ab" if resume > 0 else "wb"
                f = open(tmp, mode)
                speed_win  : collections.deque = collections.deque(maxlen=8000)
                downloaded = resume
                last_check = dl_t0
                early_checked = False

                try:
                    buf: list[bytes] = []; bufsz = 0
                    async for chunk in r.content.iter_chunked(RECV_CHUNK):
                        if not chunk: break
                        if kill_evt.is_set(): raise _StallKill()
                        now         = time.monotonic()
                        downloaded += len(chunk)
                        speed_win.append((now, len(chunk)))
                        bytes_acc.append((now, len(chunk)))
                        buf.append(chunk); bufsz += len(chunk)
                        if bufsz >= WRITE_BUF:
                            data  = b"".join(buf); buf = []; bufsz = 0
                            await loop.run_in_executor(_POOL, _write, f, data)

                        elapsed = now - dl_t0

                        # ── Early lane detection (25s) ────────────────────────
                        # Only on fresh downloads (no resume), only if many
                        # other downloads are still active, only once per attempt.
                        if (early_detect and not early_checked
                                and elapsed >= EARLY_GRACE_S
                                and resume == 0
                                and file_size >= STALL_MIN_FILE_BYTES):
                            early_checked = True
                            # Measure speed over the early window
                            cutoff = now - EARLY_WIN_S
                            win = [(t, b) for t, b in speed_win if t > cutoff]
                            win_bytes = sum(b for _, b in win)
                            if (win_bytes >= EARLY_MIN_DATA and len(win) > 1):
                                win_s = max(now - win[0][0], 1.0)
                                spd   = win_bytes / win_s / 1e6
                                # Only kill if other downloads are still active
                                # (so this file can get a fresh lane while
                                # other files keep the bandwidth busy)
                                active = active_dls_ref[0] if active_dls_ref else 0
                                if spd < EARLY_MIN_MBS and active >= 4:
                                    telem.stall(rec.filename, spd, downloaded,
                                        f"slow lane ({spd:.2f} MB/s at {elapsed:.0f}s) → early re-extract")
                                    kill_evt.set()
                                    raise _StallKill()

                        # ── Standard stall detection (90s) ───────────────────
                        if effective_detect and (now - last_check) >= STALL_CHECK_S:
                            last_check = now
                            if elapsed >= STALL_GRACE_S:
                                pct    = downloaded / file_size if file_size > 0 else 0.0
                                cutoff = now - STALL_WIN_S
                                while speed_win and speed_win[0][0] < cutoff:
                                    speed_win.popleft()
                                win_bytes = sum(b for _, b in speed_win)
                                if win_bytes >= STALL_MIN_BYTES_IN_WIN and pct < STALL_SAFE_PCT:
                                    win_s = max(now - speed_win[0][0], 1.0)
                                    spd   = win_bytes / win_s / 1e6
                                    if spd < STALL_MIN_MBS:
                                        telem.stall(rec.filename, spd, downloaded,
                                            f"slow ({spd:.2f} MB/s, {pct*100:.0f}%) → kill")
                                        kill_evt.set()
                                        raise _StallKill()

                    if buf:
                        bytes_acc.append((time.monotonic(), sum(len(b) for b in buf)))
                        await loop.run_in_executor(_POOL, _write, f, b"".join(buf))
                finally:
                    f.close()

            os.replace(tmp, dest)
            dl_s = max(time.monotonic() - dl_t0, 0.001)
            net  = downloaded - resume
            if net > 0: rec.avg_mbs = net / dl_s / 1e6
            rec.file_bytes = rec.file_bytes or downloaded
            return True, "ok", 0

        except _StallKill:
            return False, "stall_killed", downloaded
        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError):
            rec.notes.append(f"connection dropped att {att+1}")
            if att < 3: await asyncio.sleep(0.5*(att+1)); continue
            return False, "connection dropped", downloaded
        except asyncio.TimeoutError:
            rec.notes.append(f"timeout att {att+1}")
            if att < 3: await asyncio.sleep(1+att); continue
            return False, "timeout", downloaded
        except Exception as e:
            err = str(e)
            rec.notes.append(f"error att {att+1}: {err}")
            if att < 3 and ("ContentLengthError" in err or "not enough data" in err.lower()):
                await asyncio.sleep(0.5*(att+1)); continue
            return False, err, downloaded

    return False, "max retries", 0


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("MoonDownloader")
        self.geometry("1060x720")
        self.minsize(900, 580)
        self.configure(bg=BG)
        self.resizable(True, True)
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception: pass

        # ── State ──────────────────────────────────────────────────────────
        self._out_folder  = tk.StringVar(value=DEFAULT_DL_FOLDER)
        self._mode        = tk.StringVar(value="download")
        self._workers_var = tk.IntVar(value=16)
        self._dl_conc_var = tk.IntVar(value=48)
        self._retry_var   = tk.IntVar(value=3)
        self._link_count  = tk.StringVar(value="0 links")
        self._logo_img    = None

        self._lock       = threading.Lock()
        self._running    = False
        self._stop_flag  = False
        self._url_total  = 0; self._url_done = 0
        self._dl_total   = 0; self._dl_done  = 0
        self._ok         = 0; self._fail     = 0
        self._kills      = 0; self._browsers = 0
        self._dls        = 0
        self._bytes_acc  : collections.deque = collections.deque(maxlen=200000)
        self._t0         = 0.0

        self._log_buf  : list[tuple[str,str]] = []
        self._log_lock = threading.Lock()
        self._pulse_ph = 0.0
        self._pulse_on = False

        self._alive = True
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._pulse_tick()
        self._ui_loop()
        self._log_flush_loop()

    def _on_close(self):
        self._alive = False
        try: self.destroy()
        except Exception: pass

    # ── helpers ────────────────────────────────────────────────────────────
    def _inc(self, attr, delta=1):
        with self._lock: setattr(self, attr, getattr(self, attr) + delta)

    def _get(self, attr):
        with self._lock: return getattr(self, attr)

    def _load_logo(self, size=44):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if not os.path.exists(p): return None
        if PIL_OK:
            return ImageTk.PhotoImage(
                Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS))
        try:
            raw = tk.PhotoImage(file=p)
            return raw.subsample(max(1, raw.width()//size))
        except Exception: return None

    # ── BUILD ──────────────────────────────────────────────────────────────
    def _build(self):
        self._build_header()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=0, pady=0)
        self._build_left(body)
        self._build_right(body)
        self._build_footer()

    def _build_header(self):
        h = tk.Frame(self, bg=BG2, height=60)
        h.pack(fill="x")
        h.pack_propagate(False)

        # Left: logo + name
        left = tk.Frame(h, bg=BG2)
        left.pack(side="left", padx=20, pady=10)

        self._logo_img = self._load_logo(40)
        if self._logo_img:
            tk.Label(left, image=self._logo_img, bg=BG2, bd=0).pack(side="left", padx=(0,10))

        nameframe = tk.Frame(left, bg=BG2)
        nameframe.pack(side="left")
        tk.Label(nameframe, text="Moon", font=("Courier",16,"bold"),
                 fg=TEXT, bg=BG2).pack(side="left")
        tk.Label(nameframe, text="Downloader",
                 font=("Courier",16,"bold"), fg=ACC, bg=BG2).pack(side="left")
        tk.Label(nameframe, text=f"  {VERSION}",
                 font=("Courier",9), fg=TEXT3, bg=BG2).pack(side="left")

        # Right: status pill
        right = tk.Frame(h, bg=BG2)
        right.pack(side="right", padx=20)

        self._status_frame = tk.Frame(right, bg=SURFACE,
                                       highlightbackground=BORDER, highlightthickness=1)
        self._status_frame.pack(ipady=5, ipadx=12)
        inner = tk.Frame(self._status_frame, bg=SURFACE)
        inner.pack()

        self._dot_c = tk.Canvas(inner, width=8, height=8, bg=SURFACE, highlightthickness=0)
        self._dot_c.pack(side="left", padx=(0,6))
        self._dot = self._dot_c.create_oval(1,1,7,7, fill=TEXT3, outline="")
        self._status_var = tk.StringVar(value="IDLE")
        tk.Label(inner, textvariable=self._status_var,
                 font=("Courier",8,"bold"), fg=TEXT2, bg=SURFACE).pack(side="left")

        # Separator
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=BG, width=340)
        left.pack(side="left", fill="y", padx=(16,8), pady=14)
        left.pack_propagate(False)

        # Links input
        self._label(left, "INPUT LINKS")
        lf = tk.Frame(left, bg=BG3, highlightbackground=BORDER, highlightthickness=1)
        lf.pack(fill="x")
        self._links = scrolledtext.ScrolledText(
            lf, height=9, font=("Courier",9), bg=BG3, fg=TEXT2,
            insertbackground=ACC, relief="flat", bd=8, wrap="none",
            selectbackground=ACC2, selectforeground=TEXT)
        self._links.pack(fill="both")
        self._links.bind("<KeyRelease>", self._upd_count)

        br = tk.Frame(left, bg=BG); br.pack(fill="x", pady=(5,0))
        self._btn(br, "📂 Load .txt", self._load_file).pack(side="left")
        self._btn(br, "✕ Clear", self._clear_links).pack(side="left", padx=(6,0))
        tk.Label(br, textvariable=self._link_count,
                 font=("Courier",8), fg=ACC3, bg=BG).pack(side="right")

        # Output folder
        self._label(left, "OUTPUT FOLDER", top=12)
        ff = tk.Frame(left, bg=BG3, highlightbackground=BORDER, highlightthickness=1)
        ff.pack(fill="x")
        tk.Entry(ff, textvariable=self._out_folder, font=("Courier",8),
                 bg=BG3, fg=TEXT2, insertbackground=ACC, relief="flat",
                 bd=8, highlightthickness=0).pack(side="left", fill="x", expand=True)
        tk.Button(ff, text="…", command=self._pick_folder,
                  font=("Courier",9), bg=SURFACE, fg=TEXT2,
                  activebackground=BORDER, relief="flat", bd=0,
                  cursor="hand2", padx=10, pady=5).pack(side="right")

        # Mode toggle
        self._label(left, "MODE", top=12)
        mf = tk.Frame(left, bg=BG3, highlightbackground=BORDER, highlightthickness=1)
        mf.pack(fill="x")
        self._mode_btns = []
        self._mbtn(mf, "⬇  Download", "download").pack(side="left", fill="x", expand=True)
        self._mbtn(mf, "🔗  Links only", "links").pack(side="left", fill="x", expand=True)

        # Settings — compact
        self._label(left, "SETTINGS", top=12)
        sf = tk.Frame(left, bg=BG3, highlightbackground=BORDER, highlightthickness=1)
        sf.pack(fill="x")
        sinn = tk.Frame(sf, bg=BG3); sinn.pack(fill="x", padx=10, pady=8)

        rows = [
            ("Browsers", self._workers_var, 8, 32, 16, "rec. 16"),
            ("DL streams", self._dl_conc_var, 16, 64, 48, "rec. 48"),
            ("Retries", self._retry_var, 0, 5, 3, ""),
        ]
        for label, var, frm, to, opt, hint in rows:
            row = tk.Frame(sinn, bg=BG3); row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=("Courier",8), fg=TEXT2,
                     bg=BG3, width=10, anchor="w").pack(side="left")
            tk.Scale(row, from_=frm, to=to, orient="horizontal", variable=var,
                     font=("Courier",7), fg=TEXT3, bg=BG3, troughcolor=SURFACE,
                     activebackground=ACC, highlightthickness=0, bd=0,
                     sliderrelief="flat", showvalue=True).pack(side="left", fill="x", expand=True)
            if hint:
                tk.Label(row, text=hint, font=("Courier",7), fg=TEXT3,
                         bg=BG3).pack(side="right")

        # START button
        self._start_btn = tk.Button(
            left, text="▶   START", command=self._toggle,
            font=("Courier",12,"bold"), bg=ACC2, fg=BG,
            activebackground=ACC, activeforeground=BG,
            relief="flat", bd=0, cursor="hand2")
        self._start_btn.pack(fill="x", pady=(14,0), ipady=13)

    def _build_right(self, parent):
        right = tk.Frame(parent, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(0,16), pady=14)

        # ── Stats row ──────────────────────────────────────────────────────
        stats_row = tk.Frame(right, bg=BG)
        stats_row.pack(fill="x", pady=(0,10))

        self._stat_cards = {}
        cards = [
            ("speed",   "SPEED",     "—",      ACC),
            ("done",    "DONE",      "0/0",    OK),
            ("kills",   "KILLS",     "0",      WARN),
            ("eta",     "ETA",       "—",      TEXT2),
        ]
        for key, label, init, color in cards:
            c = tk.Frame(stats_row, bg=BG2,
                         highlightbackground=BORDER, highlightthickness=1)
            c.pack(side="left", fill="x", expand=True, padx=(0,6))
            inn = tk.Frame(c, bg=BG2); inn.pack(padx=10, pady=7)
            tk.Label(inn, text=label, font=("Courier",7), fg=TEXT3,
                     bg=BG2).pack(anchor="w")
            v = tk.StringVar(value=init)
            tk.Label(inn, textvariable=v, font=("Courier",13,"bold"),
                     fg=color, bg=BG2).pack(anchor="w")
            self._stat_cards[key] = v

        # ── Progress bars ──────────────────────────────────────────────────
        pb_frame = tk.Frame(right, bg=BG2,
                             highlightbackground=BORDER, highlightthickness=1)
        pb_frame.pack(fill="x", pady=(0,10))
        pb_inn = tk.Frame(pb_frame, bg=BG2); pb_inn.pack(fill="x", padx=12, pady=10)

        self._phase_var = tk.StringVar(value="—")
        tk.Label(pb_inn, textvariable=self._phase_var,
                 font=("Courier",8,"bold"), fg=GOLD, bg=BG2, anchor="w").pack(fill="x", pady=(0,6))

        for row_n, lbl, cv_a, bar_a, col in [
            (0, "URL", "_url_cv", "_url_bar", ACC2),
            (1, "DL ", "_dl_cv",  "_dl_bar",  ACC3),
        ]:
            row = tk.Frame(pb_inn, bg=BG2); row.pack(fill="x", pady=(0,3))
            tk.Label(row, text=lbl, font=("Courier",7), fg=TEXT3,
                     bg=BG2, width=3).pack(side="left")
            cv = tk.Canvas(row, height=6, bg=SURFACE, highlightthickness=0)
            cv.pack(side="left", fill="x", expand=True, padx=(4,0))
            bar = cv.create_rectangle(0,0,0,6, fill=col, width=0)
            setattr(self, cv_a, cv); setattr(self, bar_a, bar)
            cv.bind("<Configure>", lambda e: self._draw_bars())

        # ── Log ────────────────────────────────────────────────────────────
        log_header = tk.Frame(right, bg=SURFACE,
                               highlightbackground=BORDER, highlightthickness=1)
        log_header.pack(fill="x")
        lh_inn = tk.Frame(log_header, bg=SURFACE); lh_inn.pack(fill="x", padx=10, pady=5)

        for col in [OK, ERR, WARN]:
            c = tk.Canvas(lh_inn, width=8, height=8, bg=SURFACE, highlightthickness=0)
            c.pack(side="left", padx=(0,3))
            c.create_oval(1,1,7,7, fill=col, outline="")

        tk.Label(lh_inn, text="LIVE OUTPUT", font=("Courier",7,"bold"),
                 fg=TEXT3, bg=SURFACE).pack(side="left", padx=(6,0))
        tk.Button(lh_inn, text="CLEAR", command=self._clear_log,
                  font=("Courier",7), bg=SURFACE, fg=TEXT3,
                  activebackground=BORDER, relief="flat", bd=0,
                  cursor="hand2", padx=6).pack(side="right")

        log_body = tk.Frame(right, bg=BG2,
                             highlightbackground=BORDER, highlightthickness=1)
        log_body.pack(fill="both", expand=True)
        self._log_w = scrolledtext.ScrolledText(
            log_body, font=("Courier",9), bg=BG2, fg=TEXT2,
            insertbackground=ACC, relief="flat", bd=10,
            state="disabled", wrap="word",
            selectbackground=ACC2, selectforeground=TEXT)
        self._log_w.pack(fill="both", expand=True, padx=1, pady=1)
        for tag, col in [("ok",OK),("fail",ERR),("warn",WARN),
                         ("info",ACC),("dim",TEXT3),("retry",WARN),("kill",WARN)]:
            self._log_w.tag_config(tag, foreground=col)

    def _build_footer(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        f = tk.Frame(self, bg=BG2, height=24)
        f.pack(fill="x"); f.pack_propagate(False)
        tk.Label(f, text="MoonDownloader  ·  python · playwright · aiohttp",
                 font=("Courier",7), fg=TEXT3, bg=BG2).pack(side="left", padx=14, pady=4)
        tk.Label(f, text="datanodes.to  ·  fuckingfast.co",
                 font=("Courier",7), fg=TEXT3, bg=BG2).pack(side="right", padx=14, pady=4)

    # ── Widget helpers ─────────────────────────────────────────────────────
    def _label(self, p, t, top=0):
        tk.Label(p, text=t, font=("Courier",7,"bold"),
                 fg=TEXT3, bg=BG, anchor="w").pack(fill="x", pady=(top,3))

    def _btn(self, p, t, cmd):
        return tk.Button(p, text=t, command=cmd, font=("Courier",8),
                         bg=SURFACE, fg=TEXT2, activebackground=BORDER,
                         activeforeground=TEXT, relief="flat", bd=0,
                         cursor="hand2", padx=8, pady=4)

    def _mbtn(self, p, t, val):
        def toggle():
            self._mode.set(val)
            for b, v in self._mode_btns:
                active = v == self._mode.get()
                b.config(bg=ACC2 if active else BG3,
                         fg=BG if active else TEXT2)
        btn = tk.Button(p, text=t, command=toggle, font=("Courier",8),
                        relief="flat", bd=0, cursor="hand2", padx=6, pady=7,
                        bg=ACC2 if val=="download" else BG3,
                        fg=BG if val=="download" else TEXT2)
        self._mode_btns.append((btn, val)); return btn

    # ── Animation ──────────────────────────────────────────────────────────
    def _pulse_tick(self):
        if not self._alive: return
        if self._pulse_on:
            self._pulse_ph = (self._pulse_ph + 0.12) % (2 * math.pi)
            v = int(100 + 155 * (0.5 + 0.5 * math.sin(self._pulse_ph)))
            self._dot_c.itemconfig(self._dot, fill=f"#{0:02x}{v:02x}{min(v+20,255):02x}")
        else:
            self._dot_c.itemconfig(self._dot, fill=TEXT3)
        if self._alive: self.after(80, self._pulse_tick)

    def _draw_bars(self):
        with self._lock:
            url_done, url_total = self._url_done, self._url_total
            dl_done,  dl_total  = self._dl_done,  self._dl_total
        for cv, bar, done, total, col in [
            (self._url_cv, self._url_bar, url_done, url_total, ACC2),
            (self._dl_cv,  self._dl_bar,  dl_done,  dl_total,  ACC3),
        ]:
            w   = cv.winfo_width()
            pct = done / total if total else 0
            cv.coords(bar, 0, 0, max(int(w * pct), 0), 6)
            cv.itemconfig(bar, fill=col)

    # ── UI loop ────────────────────────────────────────────────────────────
    def _ui_loop(self):
        if self._get("_running"):
            with self._lock:
                el       = time.monotonic() - self._t0
                url_done = self._url_done; url_tot = self._url_total
                dl_done  = self._dl_done;  dl_tot  = self._dl_total
                ok       = self._ok; fail = self._fail
                kills    = self._kills; dls = self._dls

            ur = url_done / el if el > 0 else 0

            now  = time.monotonic()
            snap = list(self._bytes_acc)          # atomic copy — safe across threads
            cut  = now - 3.0
            recent = [(t, b) for t, b in snap if t > cut]
            if len(recent) > 1:
                span = max(now - recent[0][0], 0.05)
                mbs  = sum(b for _, b in recent) / span / 1_048_576
            else:
                mbs = 0.0

            # Byte-based ETA — uses full session history
            total_downloaded = sum(b for _, b in snap)
            files_remaining  = dl_tot - dl_done
            if mbs > 0.1 and files_remaining > 0 and dl_done > 0:
                avg_file_bytes  = total_downloaded / dl_done
                remaining_bytes = files_remaining * avg_file_bytes
                eta = min(remaining_bytes / (mbs * 1_048_576), 7200)
            else:
                eta = 0

            # Update stats cards
            spd_str = (f"{mbs:.1f} MB/s" if mbs >= 1 else f"{mbs*1024:.0f} KB/s") if mbs > 0 else "—"
            self._stat_cards["speed"].set(spd_str)
            self._stat_cards["done"].set(f"{dl_done}/{dl_tot}")
            self._stat_cards["kills"].set(str(kills))
            self._stat_cards["eta"].set(f"{int(eta//60)}m {int(eta%60)}s" if eta > 0 else "—")

            # Phase
            gb_dl = total_downloaded / 1e9
            gb_str = f"  ·  {gb_dl:.2f} GB" if gb_dl >= 0.01 else ""
            if url_done < url_tot:
                phase = f"Extracting [{url_done}/{url_tot}]  +  Downloading [{dl_done}/{dl_tot} done · {dls} active]{gb_str}"
            elif dl_done < dl_tot:
                phase = f"Downloading  [{dl_done}/{dl_tot} done  ·  {dls} active]{gb_str}"
            else:
                phase = f"Done{gb_str}"

            self._phase_var.set(phase)
            self._draw_bars()

        if self._alive: self.after(1000 // UI_HZ, self._ui_loop)

    # ── Log ────────────────────────────────────────────────────────────────
    _LOG_MAX_LINES = 2000

    def _log_flush_loop(self):
        with self._log_lock: msgs, self._log_buf = self._log_buf, []
        if msgs:
            self._log_w.config(state="normal")
            for msg, tag in msgs: self._log_w.insert("end", msg+"\n", tag)
            # Prune oldest lines to keep the widget fast
            lines = int(self._log_w.index("end-1c").split(".")[0])
            if lines > self._LOG_MAX_LINES:
                self._log_w.delete("1.0", f"{lines - self._LOG_MAX_LINES}.0")
            self._log_w.see("end"); self._log_w.config(state="disabled")
        if self._alive: self.after(1000 // LOG_HZ, self._log_flush_loop)

    def log(self, msg, tag=""):
        with self._log_lock: self._log_buf.append((msg, tag))

    # ── File/folder dialogs ────────────────────────────────────────────────
    def _load_file(self):
        p = filedialog.askopenfilename(
            filetypes=[("Text files","*.txt"),("All","*.*")])
        if p:
            with open(p,"r",encoding="utf-8",errors="replace") as f:
                self._links.delete("1.0","end")
                self._links.insert("1.0",f.read().strip())
            self._upd_count()

    def _clear_links(self):
        self._links.delete("1.0","end"); self._upd_count()

    def _upd_count(self, *_):
        n = sum(1 for l in self._links.get("1.0","end").splitlines() if l.strip())
        self._link_count.set(f"{n} link{'s' if n!=1 else ''}")

    def _pick_folder(self):
        d = filedialog.askdirectory()
        if d: self._out_folder.set(d)

    def _clear_log(self):
        with self._log_lock: self._log_buf.clear()
        self._log_w.config(state="normal")
        self._log_w.delete("1.0","end")
        self._log_w.config(state="disabled")

    def _set_status(self, t, active=False):
        self._status_var.set(t); self._pulse_on = active

    # ── Start / Stop ───────────────────────────────────────────────────────
    def _toggle(self):
        if self._get("_running"):
            with self._lock: self._stop_flag = True
            self._start_btn.config(text="⏹   STOPPING…", bg="#7a1010", fg=TEXT)
            return

        urls = [l.strip() for l in self._links.get("1.0","end").splitlines() if l.strip()]
        if not urls: self.log("⚠  No links.", "warn"); return

        with self._lock:
            self._running=True; self._stop_flag=False
            self._url_total=len(urls); self._url_done=0
            self._dl_total=len(urls);  self._dl_done=0
            self._ok=0; self._fail=0; self._kills=0
            self._browsers=0; self._dls=0
            self._bytes_acc.clear(); self._t0=time.monotonic()

        self._start_btn.config(text="⏹   STOP", bg="#cc1a1a", fg=TEXT)
        self._set_status("RUNNING", active=True)
        self._clear_log()

        try:
            os.makedirs(self._out_folder.get(), exist_ok=True)
        except OSError as e:
            self.log(f"✗  Cannot create output folder: {e}", "fail")
            with self._lock: self._running = False
            self._start_btn.config(text="▶   START", bg=ACC2, fg=BG)
            return

        n, d, r = self._workers_var.get(), self._dl_conc_var.get(), self._retry_var.get()

        proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.txt")
        n_proxies  = _PROXY_POOL.load(proxy_path)

        self.log(f"▶  {len(urls)} links  ·  {n} browsers  ·  {d} streams  ·  {r} retries  ·  {VERSION}", "info")
        if n_proxies > 0:
            self.log(f"   proxies: {n_proxies} loaded — rotating per download", "info")
        self.log(f"   stall < {STALL_MIN_MBS} MB/s  ·  grace {STALL_GRACE_S}s  ·  max {STALL_MAX_KILL} kill", "dim")

        threading.Thread(target=lambda: asyncio.run(self._run(urls,n,d,r)), daemon=True).start()

    # ── Async core (unchanged) ─────────────────────────────────────────────
    async def _do_dl(self, proxy_url, cookies, filename, orig_url, rec,
                     kill_counts, early_kill_counts, dl_sem, dest_folder, telem, mark_done_fn,
                     failed_urls, q, dl_times, dl_times_lock):
        async with dl_sem:
            self._inc("_dls")
            rec.dl_start = time.monotonic(); rec.status = "downloading"
            dest = os.path.join(dest_folder, filename)

            if os.path.exists(dest):
                self._inc("_ok")
                self.log(f"    ✓  Exists: {filename}", "ok")
                rec.status="ok"; rec.dl_s=0.0
                mark_done_fn(); self._inc("_dl_done"); self._inc("_dls",-1); return

            kc  = kill_counts.get(orig_url, 0)
            ekc = early_kill_counts.get(orig_url, 0)
            kill_evt = asyncio.Event()
            active_dls_ref = [self._get("_dls")]
            ok, msg, bytes_done = await download_file_raced(
                proxy_url, cookies, dest, rec, self._bytes_acc, telem, kill_evt, kc,
                early_kills_so_far=ekc, active_dls_ref=active_dls_ref)
            rec.dl_s = max(time.monotonic()-rec.dl_start, 0.001)

            if ok:
                async with dl_times_lock:
                    dl_times.append(rec.dl_s)
                self._inc("_ok")
                spd = f"  ({rec.avg_mbs:.1f} MB/s)" if rec.avg_mbs > 0 else ""
                self.log(f"    ✓  Saved: {filename}{spd}", "ok")
                rec.status="ok"; mark_done_fn(); self._inc("_dl_done")
            elif msg == "stall_killed":
                done_mb = bytes_done//(1<<20)
                if ekc < EARLY_MAX_KILLS and (time.monotonic()-rec.dl_start) < STALL_GRACE_S:
                    new_ekc = ekc + 1; early_kill_counts[orig_url] = new_ekc
                    self._inc("_kills"); rec.stall_kills += 1
                    self.log(f"    ⚡  Lane kill #{new_ekc}: {filename}  ({done_mb}MB)", "kill")
                else:
                    new_kc = kc + 1; kill_counts[orig_url] = new_kc
                    self._inc("_kills"); rec.stall_kills += 1
                    if new_kc <= STALL_MAX_KILL:
                        self.log(f"    ⚡  Kill #{new_kc}: {filename}  ({done_mb}MB) → re-extract", "kill")
                    else:
                        self.log(f"    ⚡  Kill #{new_kc}: {filename}  ({done_mb}MB) → continue", "warn")
                rec.queued_at=time.monotonic(); rec.status="pending"
                await q.put((orig_url, 1, rec))
                self._inc("_dls",-1); return
            else:
                self._inc("_fail"); failed_urls.append(orig_url)
                rec.status="fail"; rec.error=msg
                self.log(f"    ✗  {filename}: {msg}", "fail")
                mark_done_fn(); self._inc("_dl_done")

            self._inc("_dls",-1)

    async def _browser_worker(self, browser, wid, q, dl_sem, all_done, mark_done_fn,
                               kill_counts, early_kill_counts, all_tasks, tasks_lock,
                               output_links, failed_urls, dest_folder, mode, max_retries, telem,
                               dl_times, dl_times_lock):
        self._inc("_browsers")
        my_tasks = []
        try:
            while not self._get("_stop_flag"):
                if all_done.is_set() and q.empty(): break
                try:
                    url, attempt, rec = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError: continue

                rec.worker_id    = wid
                t_start          = time.monotonic()
                rec.queue_wait_s = t_start - rec.queued_at
                rec.status       = "extracting"
                filename = rec.filename
                short    = filename[:44]+("…" if len(filename)>44 else "")
                is_re    = rec.stall_kills > 0
                is_retry = attempt > 1
                suffix   = (" [re-extract]" if is_re else "")+(" [retry]" if is_retry else "")
                self.log(f"  → {short}{suffix}", "retry" if (is_re or is_retry) else "dim")

                success = False
                try:
                    parsed = urlparse(url)
                    if "fuckingfast.co" in parsed.netloc:
                        link = await extract_fuckingfast(url)
                        rec.extract_s = time.monotonic()-t_start
                        if not link:
                            self.log("    ✗  No link found", "fail")
                        elif mode == "links":
                            output_links.append(link); self._inc("_ok")
                            self.log(f"    ✓  {link[:70]}", "ok")
                            rec.status="ok"; success=True; mark_done_fn()
                        else:
                            self.log(f"    ↓  {filename}", "dim")
                            async def _task(pu=link, fn=filename, ou=url, r=rec):
                                await self._do_dl(pu, "", fn, ou, r, kill_counts, early_kill_counts,
                                                   dl_sem, dest_folder, telem, mark_done_fn,
                                                   failed_urls, q, dl_times, dl_times_lock)
                            t = asyncio.create_task(_task())
                            my_tasks.append(t)
                            async with tasks_lock: all_tasks.append(t)
                            success = True
                    elif "datanodes.to" in parsed.netloc:
                        # Fresh context per extraction = fresh cookies = fresh CDN session
                        ctx = await browser.new_context(
                            user_agent=USER_AGENTS[wid % len(USER_AGENTS)],
                            viewport={"width": 1280, "height": 800}, locale="en-US",
                            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
                        try:
                            proxy_url, cookies = await extract_datanodes(ctx, url)
                        finally:
                            try: await ctx.close()
                            except Exception: pass
                        rec.extract_s = time.monotonic()-t_start
                        if not proxy_url:
                            rec.notes.append("extraction failed")
                            self.log("    ✗  No URL extracted", "fail")
                        elif mode == "links":
                            output_links.append(proxy_url); self._inc("_ok")
                            self.log(f"    ✓  {proxy_url[:70]}", "ok")
                            rec.status="ok"; success=True; mark_done_fn()
                        else:
                            self.log(f"    ↓  {filename}", "dim")
                            async def _task(pu=proxy_url, co=cookies, fn=filename, ou=url, r=rec):
                                await self._do_dl(pu, co, fn, ou, r, kill_counts, early_kill_counts,
                                                   dl_sem, dest_folder, telem, mark_done_fn,
                                                   failed_urls, q, dl_times, dl_times_lock)
                            t = asyncio.create_task(_task())
                            my_tasks.append(t)
                            async with tasks_lock: all_tasks.append(t)
                            success = True
                except Exception as e:
                    rec.notes.append(f"exception: {e}")
                    self.log(f"    ✗  {e}", "fail")

                if not success and not is_re and attempt < max_retries and not self._get("_stop_flag"):
                    backoff = min(2**(attempt-1), 6)
                    self.log(f"    ↻  retry in {backoff}s", "warn")
                    await asyncio.sleep(backoff)
                    rec.queued_at = time.monotonic()
                    await q.put((url, attempt+1, rec))
                    q.task_done(); continue

                if not success and not is_re:
                    self._inc("_fail"); failed_urls.append(url)
                    rec.status="fail"; mark_done_fn()

                self._inc("_url_done"); q.task_done()

            if my_tasks:
                await asyncio.gather(*my_tasks, return_exceptions=True)
        finally:
            self._inc("_browsers",-1)

    async def _run(self, urls, n_workers, max_dl, max_retries):
        t0           = time.monotonic()
        q            = asyncio.Queue()
        dl_sem       = asyncio.Semaphore(max_dl)
        output_links : list[str] = []
        failed_urls  : list[str] = []
        all_tasks    : list      = []
        tasks_lock   = asyncio.Lock()
        kill_counts       : dict[str,int] = {}
        early_kill_counts : dict[str,int] = {}
        dest_folder  = self._out_folder.get()
        mode         = self._mode.get()
        n_done       = 0
        all_done     = asyncio.Event()
        dl_times     : list[float] = []
        dl_times_lock = asyncio.Lock()

        def mark_done():
            nonlocal n_done
            n_done += 1
            if n_done >= len(urls): all_done.set()

        cfg = {"browsers": n_workers, "dl_streams": max_dl, "retries": max_retries,
               "stall_min_mbs": STALL_MIN_MBS, "stall_grace_s": STALL_GRACE_S,
               "stall_max_kill": STALL_MAX_KILL, "stall_safe_pct": STALL_SAFE_PCT,
               "stall_win_guard_MB": STALL_MIN_BYTES_IN_WIN//(1<<20),
               "recv_chunk_MB": RECV_CHUNK//(1<<20), "write_buf_MB": WRITE_BUF//(1<<20),
               "socket_buf_KB": READ_BUFSZ//1024, "mode": mode, "total_links": len(urls)}
        telem = Telemetry(cfg)

        for url in urls:
            p = urlparse(url)
            raw_name = unquote(p.fragment or p.path.split("/")[-1]) or url
            rec = telem.reg(url, _sanitize_filename(raw_name))
            await q.put((url, 1, rec))

        snap_stop = asyncio.Event()
        async def snap_task():
            while not snap_stop.is_set():
                with self._lock:
                    b, d, ok, fail = self._browsers, self._dls, self._ok, self._fail
                telem.snap(b, d, q.qsize(), ok, fail)
                await asyncio.sleep(1.0)

        snap_t = asyncio.create_task(snap_task())

        async with async_playwright() as p:
            async def _launch(wid):
                b = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
                try:
                    await self._browser_worker(
                        b, wid, q, dl_sem, all_done, mark_done,
                        kill_counts, early_kill_counts, all_tasks, tasks_lock,
                        output_links, failed_urls, dest_folder, mode, max_retries, telem,
                        dl_times, dl_times_lock)
                finally:
                    try: await b.close()
                    except Exception: pass
            await asyncio.gather(*[asyncio.create_task(_launch(i)) for i in range(n_workers)])

        async with tasks_lock:
            stragglers = [t for t in all_tasks if not t.done()]
        if stragglers:
            self.log(f"  ⚠  {len(stragglers)} straggler tasks finishing...", "warn")
            await asyncio.gather(*stragglers, return_exceptions=True)

        snap_stop.set()
        await _close_sess()
        await _PROXY_POOL.close_all()
        telem.finish()

        base = os.path.dirname(os.path.abspath(__file__))
        try:
            lp, jp = telem.save(base)
            self.log(f"📊  {os.path.basename(lp)}", "info")
            self.log(f"📊  {os.path.basename(jp)}", "info")
        except Exception as e:
            self.log(f"⚠  Log save error: {e}", "warn")

        if output_links and mode == "links":
            with open(os.path.join(base,"output_links.txt"),"a",encoding="utf-8") as f:
                f.write("\n".join(output_links)+"\n")
            self.log("✓  Links → output_links.txt", "info")
        if failed_urls:
            with open(os.path.join(base,"failed_links.txt"),"w",encoding="utf-8") as f:
                f.write("\n".join(failed_urls)+"\n")
            self.log(f"⚠  {len(failed_urls)} failed → failed_links.txt", "warn")

        el = time.monotonic()-t0; m, s = divmod(int(el), 60)
        with self._lock: ok, fail, kills = self._ok, self._fail, self._kills
        self.log(f"\n✓  Done in {m}m {s}s  ·  ✓ {ok}  ✗ {fail}  ⚡ {kills} kills", "ok")
        self.after(0, self._on_done)

    def _on_done(self):
        with self._lock: self._running=False; self._stop_flag=False
        self._start_btn.config(text="▶   START", bg=ACC2, fg=BG)
        self._set_status("DONE", active=False)
        self._stat_cards["speed"].set("—")
        self._phase_var.set("Done")
        self._scan_tmp()

    def _scan_tmp(self):
        folder = self._out_folder.get()
        if not os.path.isdir(folder): return
        tmps = [f for f in os.listdir(folder) if f.endswith(".tmp")]
        if tmps:
            self.log(f"⚠  {len(tmps)} .tmp files — will resume on next run.", "warn")


if __name__ == "__main__":
    try:
        app = App()
        app.after(600, app._scan_tmp)
        app.mainloop()
    except Exception:
        crash = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_log.txt")
        with open(crash,"w",encoding="utf-8") as f: f.write(traceback.format_exc())
        raise
