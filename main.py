# -*- coding: utf-8 -*-
"""
Google One Plan Injector

  python main.py

Automatically locates adb, connects to an emulator/phone, and provisions
frida-server and module_521.apk when missing.
Prerequisites on target device: Root access, signed-in Google account, and Google One installed.
Selecting a plan opens the Google Play checkout sheet; subscriptions are never confirmed automatically.

Renew/stacking: press `s` in the menu to toggle stacking (`STACK=true` in frida.js),
which stacks the new offer onto an existing Google One subscription instead of creating a new one.
"""
from __future__ import annotations

import argparse
import glob
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BIN = REPO / "bin"
JS_PATH = HERE / "frida.js"
PLANS_PATH = HERE / "plans.json"
SCAN_PATH = HERE / "sku_scan.json"
REPORT_PATH = HERE / "g1-sku-report.html"

PKG = "com.google.android.apps.subscriptions.red"
ACT = PKG + "/com.google.android.apps.subscriptions.red.main.MainActivity"
DEX_REMOTE = "/data/local/tmp/module_521.apk"
FRIDA_REMOTE = "/data/local/tmp/frida-server"

ABI_ASSET = {
    "arm64-v8a": "android-arm64",
    "armeabi-v7a": "android-arm",
    "x86_64": "android-x86_64",
    "x86": "android-x86",
}

GROUPS = [
    ("Plus", [
        "helium_12m", "helium_6m", "helium_3m", "helium_1m", "helium_3m_75",
        "plus_6m", "plus_3m", "plus_2tb_6m", "plus_2tb_85",
        "yt_1m", "yt_family", "yt_lite",
    ]),
    ("Pro", [
        "pro_12m", "pro_6m", "pro_6m_2", "pro_6m_ss", "pro_play_3m",
        "pro_3m_75",
        "pro10_1m_90", "pro10_3m_85", "pro10_base",
    ]),
    ("Ultra 5x", [
        "ultra20_1m_eft", "ultra20_3m_90", "ultra20_3m_80", "ultra20_3m_75", "ultra20_base",
    ]),
    ("Ultra 20x", [
        "ultra_1m_eft", "ultra_1m_90", "ultra_1m_75", "ultra_3m_90", "ultra_3m_80", "ultra_3m_75", "ultra_30tb",
    ]),
]


def _fix_stdio():
    if sys.platform != "win32":
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def info(msg):
    print(f"[*] {msg}", flush=True)


def ok(msg):
    print(f"[+] {msg}", flush=True)


def warn(msg):
    print(f"[!] {msg}", flush=True)


def fail(msg):
    print(f"[x] {msg}", flush=True)


EMU_PORTS = (
    7555, 7556, 16384, 16416, 16448, 16480, 16385,
    5555, 5554, 5556, 5560, 62001, 21503,
)


def find_adb_exe():
    found = shutil.which("adb")
    if found:
        return found
    extras = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SDK"):
        root = os.environ.get(key)
        if root:
            extras.append(str(Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")))
    extras += [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        r"C:\Android\sdk\platform-tools\adb.exe",
        r"D:\SDK\platform-tools\adb.exe",
        r"E:\SDK\platform-tools\adb.exe",
    ]
    for drive in ("C", "D", "E", "F"):
        extras.append(rf"{drive}:\SDK\platform-tools\adb.exe")
        extras += glob.glob(rf"{drive}:\Program Files\Netease\*\*\adb.exe")
        extras += glob.glob(rf"{drive}:\Program Files\MuMu*\**\adb.exe", recursive=True)
    for p in extras:
        if p and os.path.isfile(p):
            return p
    return "adb"


class Adb:
    def __init__(self, serial=""):
        self.exe = find_adb_exe()
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or ""

    def _cmd(self, args, serial=None, timeout=None):
        cmd = [self.exe]
        s = self.serial if serial is None else serial
        if s:
            cmd += ["-s", s]
        return subprocess.run(
            cmd + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )

    def run(self, args, serial=None):
        return self._cmd(args, serial=serial)

    def shell(self, *parts):
        r = self._cmd(["shell", *parts])
        return ((r.stdout or "") + (r.stderr or "")).strip()

    def devices(self):
        rows = []
        out = self._cmd(["devices"], serial="").stdout
        for ln in out.splitlines()[1:]:
            bits = ln.split()
            if len(bits) >= 2 and bits[-1] == "device":
                rows.append(bits[0])
        return rows

    def _restart_server(self):
        info("Restarting adb server")
        self._cmd(["kill-server"], serial="")
        time.sleep(0.8)
        self._cmd(["start-server"], serial="")
        time.sleep(1.0)

    def _connect_emulators(self):
        for port in EMU_PORTS:
            r = self._cmd(["connect", f"127.0.0.1:{port}"], serial="")
            text = ((r.stdout or "") + (r.stderr or "")).strip()
            if "connected" in text.lower() or "already" in text.lower():
                ok(f"adb connect 127.0.0.1:{port}")

    def _fingerprint(self, serial):
        r = self._cmd(["shell", "settings", "get", "secure", "android_id"], serial=serial)
        aid = (r.stdout or "").strip()
        if aid and aid not in ("null", "error"):
            return aid
        r = self._cmd(["shell", "getprop", "ro.serialno"], serial=serial)
        return (r.stdout or "").strip() or serial

    def _dedupe(self, serials):
        best = {}
        order = []
        for s in serials:
            key = self._fingerprint(s)
            if key not in best:
                best[key] = s
                order.append(key)
                continue
            cur = best[key]
            prefer = (s.startswith("emulator-") and not cur.startswith("emulator-")) or (
                cur.startswith("127.0.0.1:") and not s.startswith("127.0.0.1:")
            )
            if prefer:
                best[key] = s
        return [best[k] for k in order]

    def _score(self, serial):
        score = 0
        old = self.serial
        self.serial = serial
        try:
            if "uid=0" in (self.shell("id") + self.shell("su", "-c", "id") + self.shell("su", "0", "id")):
                score += 10
            if "package:" in self.shell("pm", "path", PKG):
                score += 5
            if google_accounts():
                score += 3
        finally:
            self.serial = old
        return score

    def discover(self):
        info(f"adb: {self.exe}")
        rows = self.devices()
        if not rows:
            self._restart_server()
            rows = self.devices()
        if not rows:
            info("No online devices found; probing local emulator ports (MuMu / LDPlayer / Nox)")
            self._connect_emulators()
            time.sleep(0.6)
            rows = self.devices()
        if not rows:
            info("Waiting for device to come online ...")
            try:
                self._cmd(["wait-for-device"], serial="", timeout=15)
            except subprocess.TimeoutExpired:
                pass
            time.sleep(1.0)
            self._connect_emulators()
            rows = self.devices()
        rows = self._dedupe(rows)
        if not rows:
            fail("No available adb devices. Start an emulator or connect a phone with USB debugging enabled.")
            sys.exit(1)
        return rows

    def pick(self):
        rows = self.discover()
        if self.serial and self.serial in rows:
            ok(f"Device {self.serial}")
            return self.serial
        if self.serial and self.serial not in rows:
            warn(f"Specified device {self.serial} is offline; selecting automatically")
        if len(rows) == 1:
            self.serial = rows[0]
        else:
            scores = {s: self._score(s) for s in rows}
            ranked = sorted(rows, key=lambda s: scores[s], reverse=True)
            self.serial = ranked[0]
            info("Multiple devices detected; selecting the highest-scoring device:")
            for s in ranked:
                info(f"  {s}  score={scores[s]}")
        os.environ["ANDROID_SERIAL"] = self.serial
        ok(f"Device {self.serial}")
        return self.serial


adb = Adb()


def parse_plans(js_text):
    """Parse PLANS from frida.js so the menu stays in sync with the hook."""
    block = re.search(r"var PLANS = \{([\s\S]*?)\n\};", js_text)
    if not block:
        return {}
    plans = {}
    for m in re.finditer(
        r"(\w+)\s*:\s*\{\s*sku:\s*\"([^\"]+)\"\s*,\s*offer:\s*\"([^\"]*)\"\s*,\s*note:\s*\"([^\"]*)\"",
        block.group(1),
    ):
        plans[m.group(1)] = {"sku": m.group(2), "offer": m.group(3), "note": m.group(4)}
    return plans


def load_catalog(plans_path=PLANS_PATH, js_path=JS_PATH):
    """Load plans and group ordering from plans.json, falling back to frida.js."""
    if plans_path.is_file():
        data = json.loads(plans_path.read_text(encoding="utf-8"))
        plans = data.get("plans") or {}
        raw_groups = data.get("groups") or []
        groups_cfg = [(g["title"], g["plans"]) for g in raw_groups if g.get("title") and g.get("plans")]
        return plans, groups_cfg or GROUPS
    return parse_plans(js_path.read_text(encoding="utf-8")), GROUPS


def grouped_plans(plans, groups_cfg=GROUPS):
    used = set()
    groups = []
    for title, ids in groups_cfg:
        items = [(pid, plans[pid]) for pid in ids if pid in plans]
        if items:
            groups.append((title, items))
            used.update(pid for pid, _ in items)
    extra = [(pid, plans[pid]) for pid in plans if pid not in used]
    if extra:
        groups.append(("Other", extra))
    return groups


def flatten_menu(groups):
    rows = []
    for title, items in groups:
        for pid, spec in items:
            rows.append((title, pid, spec))
    return rows


def print_menu(plans, stack, addon, auto_remove=False, groups_cfg=GROUPS):
    groups = grouped_plans(plans, groups_cfg=groups_cfg)
    rows = flatten_menu(groups)
    mode = "Renew / Stack (STACK=true, stack onto active subscription)" if stack else "New Purchase (STACK=false)"
    print()
    print("=" * 64)
    print("  Google One Plan Injector")
    print(f"  Mode:        {mode}")
    print(f"  Add-on:      {addon or 'None'}")
    print(f"  Auto-remove: {'ON' if auto_remove else 'OFF'}")
    print("=" * 64)
    n = 1
    index = {}
    for title, items in groups:
        print(f"\n  [{title}]")
        for pid, spec in items:
            print(f"  {n:2d}. {pid:<16} {spec['note']}")
            index[str(n)] = pid
            index[pid] = pid
            n += 1
    print()
    print("  Enter plan number or plan ID to launch checkout")
    print("  s  Toggle renew/stack mode (requires an active Google One subscription)")
    print("  t  Stacked payment chain (purchase & stack multiple plans on one account)")
    print("  a  Configure YouTube add-on plan")
    print("  r  Toggle auto-remove Google account after payment succeeds")
    print("  p  Loop through all Google accounts on device (auto-skip/remove if subscribed)")
    print("  o  Audit all plan offer availability & pricing (--check-offers)")
    print("  h  Generate local HTML SKU & offer report (g1-sku-report.html)")
    print("  x  Remove Google accounts from device now")
    print("  e  Re-check and repair environment")
    print("  g  Restart Google One and re-inject Frida")
    print("  l  Re-list plans")
    print("  q  Quit")
    print()
    return index, rows


# ── environment ──────────────────────────────────────────────────────────────

def host_frida_version():
    try:
        import frida
        return getattr(frida, "__version__", "") or ""
    except Exception:
        pass
    exe = shutil.which("frida")
    if not exe:
        return ""
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or r.stderr or "").strip().split()[0]


def ensure_host_frida():
    ver = host_frida_version()
    if ver:
        ok(f"Host frida {ver}")
        return ver
    warn("frida not found on host, running pip install frida-tools ...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "frida-tools"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        fail((r.stderr or r.stdout or "pip failed").strip()[:400])
        sys.exit(1)
    ver = host_frida_version()
    if not ver:
        fail("Installed frida-tools, but frida is still not found. Check PATH.")
        sys.exit(1)
    ok(f"Host frida {ver}")
    return ver


def device_abi():
    abi = adb.shell("getprop", "ro.product.cpu.abi") or ""
    return abi.splitlines()[0].strip() if abi else ""


def adb_is_root():
    return "uid=0" in adb.shell("id")


def has_root():
    if adb_is_root():
        return True
    if "uid=0" in adb.shell("su", "-c", "id"):
        return True
    if "uid=0" in adb.shell("su", "0", "id"):
        return True
    adb.run(["root"])
    time.sleep(0.8)
    return adb_is_root()


def google_accounts():
    out = adb.shell("dumpsys", "account")
    names = re.findall(r"Account \{name=([^,}]+),\s*type=com\.google", out or "")
    if names:
        return [n.strip() for n in names]
    names = re.findall(r"type=com\.google[^\n]*name=([^\s,\}]+)", out or "")
    return [n.strip() for n in names]


def su_sh(cmd):
    if adb_is_root():
        r = adb.run(["shell", cmd])
        return ((r.stdout or "") + (r.stderr or "")).strip()
    r = adb.run(["shell", "su", "-c", cmd])
    text = ((r.stdout or "") + (r.stderr or "")).strip()
    bad = r.returncode != 0 or "inaccessible" in text or "not found" in text.lower()
    if bad:
        r = adb.run(["shell", "su", "0", "sh", "-c", cmd])
        text = ((r.stdout or "") + (r.stderr or "")).strip()
    return text


def remote_exists(path):
    out = adb.shell("ls", path)
    return "No such" not in out and path.split("/")[-1] in out


def ensure_module():
    if remote_exists(DEX_REMOTE):
        ok(f"module_521.apk already present at {DEX_REMOTE}")
        return
    info("Device is missing module_521.apk; unpacking embedded dex and pushing ...")
    try:
        from module_b64 import MODULE_B64
    except Exception as e:
        fail(f"Failed to read module_b64.py: {e}")
        sys.exit(1)
    import base64
    tmp = Path(tempfile.gettempdir()) / "module_521.apk"
    tmp.write_bytes(base64.b64decode(MODULE_B64))
    r = adb.run(["push", str(tmp), DEX_REMOTE])
    print((r.stdout or r.stderr or "").strip(), flush=True)
    adb.shell("chmod", "644", DEX_REMOTE)
    if not remote_exists(DEX_REMOTE):
        fail("Failed to push module_521.apk")
        sys.exit(1)
    ok("module_521.apk pushed")


def _xz_to_file(xz_path, dest):
    data = lzma.open(xz_path).read()
    dest.write_bytes(data)
    return dest


def local_frida_server(abi_tag):
    """Find a host-side frida-server binary matching the device ABI."""
    named = [
        HERE / "assets" / f"frida-server-{abi_tag}",
        BIN / f"frida-server-{abi_tag}",
    ]
    for p in named:
        if p.is_file() and p.stat().st_size > 1024 * 1024:
            return p
    generic = BIN / "frida-server"
    if abi_tag == "android-x86_64" and generic.is_file() and generic.stat().st_size > 1024 * 1024:
        return generic
    cache = HERE / "assets"
    cache.mkdir(exist_ok=True)
    unpacked = cache / f"frida-server-{abi_tag}"
    if unpacked.is_file() and unpacked.stat().st_size > 1024 * 1024:
        return unpacked
    xz_candidates = [
        HERE / "assets" / f"frida-server-{abi_tag}.xz",
        BIN / f"frida-server-{abi_tag}.xz",
    ]
    if abi_tag == "android-x86_64":
        xz_candidates.append(BIN / "frida-server-x86_64.xz")
    for xz in xz_candidates:
        if not xz.is_file():
            continue
        info(f"Extracting {xz.name} ...")
        return _xz_to_file(xz, unpacked)
    return None


def download_frida_server(ver, abi_tag):
    cache = HERE / "assets"
    cache.mkdir(exist_ok=True)
    name = f"frida-server-{ver}-{abi_tag}.xz"
    url = f"https://github.com/frida/frida/releases/download/{ver}/{name}"
    xz = cache / name
    info(f"Downloading {url}")
    try:
        urllib.request.urlretrieve(url, xz)
    except Exception as e:
        fail(f"Download failed: {e}")
        return None
    dest = cache / f"frida-server-{ver}-{abi_tag}"
    return _xz_to_file(xz, dest)


def remote_frida_version():
    if not remote_exists(FRIDA_REMOTE):
        return ""
    out = adb.shell(FRIDA_REMOTE, "--version") or su_sh(f"{FRIDA_REMOTE} --version")
    m = re.search(r"(\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else ""


def start_remote_frida():
    su_sh(f"chmod 755 {FRIDA_REMOTE}")
    su_sh("killall -9 frida-server")
    time.sleep(0.5)
    if adb_is_root():
        adb.run(["shell", f"chmod 755 {FRIDA_REMOTE} && setsid {FRIDA_REMOTE} -D </dev/null >/dev/null 2>&1 &"])
        time.sleep(1.5)
    else:
        adb.run(["shell", "su", "-c", f"chmod 755 {FRIDA_REMOTE}; {FRIDA_REMOTE} -D &"])
        time.sleep(1.5)
    running = (adb.shell("pidof", "frida-server") or "").strip()
    if not running and not adb_is_root():
        adb.run(["shell", "su", "0", "sh", "-c", f"{FRIDA_REMOTE} -D &"])
        time.sleep(1.5)
        running = (adb.shell("pidof", "frida-server") or "").strip()
    return running


def ensure_frida_server(ver):
    abi = device_abi()
    abi_tag = ABI_ASSET.get(abi, "")
    if not abi_tag:
        fail(f"Unrecognized device ABI: {abi!r}")
        sys.exit(1)
    info(f"Device ABI {abi} -> {abi_tag}")

    remote_ver = remote_frida_version()
    running = (adb.shell("pidof", "frida-server") or "").strip()
    if running and remote_ver == ver:
        ok(f"frida-server {remote_ver} running (pid={running.split()[0]})")
        return

    if remote_ver and remote_ver != ver:
        warn(f"Device frida-server {remote_ver} differs from host {ver}; replacing")

    local = None
    cache = HERE / "assets" / f"frida-server-{ver}-{abi_tag}"
    if cache.is_file() and cache.stat().st_size > 1024 * 1024:
        local = cache
    if local is None:
        local = download_frida_server(ver, abi_tag)
    if local is None:
        local = local_frida_server(abi_tag)
    if local is None:
        fail("Unable to prepare frida-server. Place the matching ABI binary in assets/ or ../bin/")
        sys.exit(1)

    if running:
        su_sh("killall -9 frida-server")
        time.sleep(0.5)

    info(f"push {local.name} ({ver}) -> {FRIDA_REMOTE}")
    r = adb.run(["push", str(local), FRIDA_REMOTE])
    print((r.stdout or r.stderr or "").strip(), flush=True)

    running = start_remote_frida()
    if not running:
        fail("Failed to start frida-server. Root access is required.")
        sys.exit(1)
    ok(f"frida-server {ver} started (pid={running.split()[0]})")


def ensure_google_one():
    path = adb.shell("pm", "path", PKG)
    if "package:" in path:
        ok("Google One installed")
        return
    fail("Google One (com.google.android.apps.subscriptions.red) is not installed on device")
    fail("Please install Google One from the Play Store first.")
    sys.exit(1)


def ensure_google_account():
    accts = google_accounts()
    if accts:
        ok("Google accounts: " + ", ".join(accts))
        return accts
    fail("No Google account is signed in on the device. Please sign in via Settings or Play Store.")
    sys.exit(1)


def ensure_root():
    if has_root():
        ok("Device is rooted")
        return
    fail("Device is not rooted (su / adb root failed). Please enable root first.")
    sys.exit(1)


def print_env(ver):
    accts = google_accounts()
    print()
    print("-" * 64)
    print("  Environment")
    print("-" * 64)
    print(f"  Device        {adb.serial}")
    print(f"  ABI           {device_abi() or '?'}")
    print(f"  Root          {'yes' if has_root() else 'no'}")
    print(f"  Google Accts  {', '.join(accts) if accts else 'none'}")
    print(f"  Host frida    {ver or 'none'}")
    print(f"  frida-server  {(adb.shell('pidof', 'frida-server') or 'stopped').split()[0]}")
    print(f"  module_521    {'present' if remote_exists(DEX_REMOTE) else 'missing'}")
    g1 = "installed" if "package:" in adb.shell("pm", "path", PKG) else "missing"
    print(f"  Google One    {g1}")
    print("-" * 64)


def ensure_env():
    adb.pick()
    ensure_root()
    ensure_google_account()
    ensure_google_one()
    ver = ensure_host_frida()
    ensure_module()
    ensure_frida_server(ver)
    print_env(ver)
    return ver


# ── Google One + Frida session ───────────────────────────────────────────────

def g1_pid():
    return (adb.shell("pidof", PKG) or "").split()[:1]


def wake():
    adb.shell("input", "keyevent", "224")
    adb.shell("wm", "dismiss-keyguard")


def start_google_one():
    wake()
    adb.shell("am", "force-stop", PKG)
    time.sleep(1.2)
    adb.shell("am", "start", "-n", ACT)
    adb.shell("monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    pid = ""
    for _ in range(40):
        time.sleep(0.35)
        got = g1_pid()
        if got:
            pid = got[0]
            break
    if not pid:
        fail("Google One failed to launch")
        sys.exit(1)
    time.sleep(2.5)
    pid = (g1_pid() or [pid])[0]
    ok(f"Google One pid={pid}")
    return int(pid)


def post_frida_bridge(script, name):
    """Frida 17 splits out the Java bridge; supply it when Java.perform requests it."""
    try:
        import frida_tools
        root = Path(frida_tools.__file__).resolve().parent / "bridges"
    except Exception as e:
        fail(f"Could not locate frida_tools/bridges: {e}")
        return False
    stem = (name or "java").lower()
    for p in root.glob("*.js"):
        if p.stem.lower() == stem:
            script.post({
                "type": "frida:bridge-loaded",
                "filename": p.name,
                "source": p.read_text(encoding="utf-8"),
            })
            return True
    fail(f"No Frida bridge named {stem}")
    return False


def get_frida_device():
    import frida
    mgr = frida.get_device_manager()
    for d in mgr.enumerate_devices():
        if d.id == adb.serial:
            return d
    return frida.get_usb_device(timeout=8)


def _embed_java_bridge(js, quiet=False):
    try:
        import frida_tools
        java_js = Path(frida_tools.__file__).resolve().parent / "bridges" / "java.js"
        if java_js.is_file():
            if not quiet:
                info("Embedded frida-java-bridge")
            return java_js.read_text(encoding="utf-8") + "\n;var Java = bridge;\n" + js
    except Exception as e:
        if not quiet:
            warn(f"Failed to embed Java bridge: {e}")
    return js


def db_sub_snapshot(email):
    """Return sorted snapshot of (doc_id, subs_valid_until_time, purchase_token) for email in library.db."""
    if not email:
        return ()
    safe_email = email.replace("'", "''")
    sql = (
        "select doc_id, subs_valid_until_time, inapp_purchase_data from ownership "
        f"where account='{safe_email}' and (doc_id like '%g1.%' or doc_id like 'subs:com.google.android.apps.subscriptions.red:%');"
    )
    out = su_sh(f'sqlite3 /data/data/com.android.vending/databases/library.db "{sql}"')
    if not out or "Error:" in out or "not found" in out.lower() or "inaccessible" in out.lower():
        return ()
    rows = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln or "|" not in ln:
            continue
        parts = ln.split("|", 2)
        doc = parts[0].strip()
        until = parts[1].strip() if len(parts) > 1 else ""
        data = parts[2] if len(parts) > 2 else ""
        m = re.search(r'"purchaseToken"\s*:\s*"([^"]+)"', data)
        tok = m.group(1) if m else ""
        rows.append((doc, until, tok))
    return tuple(sorted(rows))


def latest_db_token(snapshot):
    """Pick the most recent valid purchaseToken from a db_sub_snapshot tuple."""
    best_tok = ""
    best_until = -1
    for _doc, until, tok in snapshot or ():
        if not tok:
            continue
        try:
            u = int(until) if until else 0
        except ValueError:
            u = 0
        if u >= best_until:
            best_until = u
            best_tok = tok
    return best_tok


def db_has_g1_sub(email):
    """Check Play Store library.db for an active Google One subscription on email."""
    safe_email = email.replace("'", "''")
    sql = (
        "select doc_id, subs_valid_until_time from ownership "
        f"where account='{safe_email}' and (doc_id like '%g1.%' or doc_id like 'subs:com.google.android.apps.subscriptions.red:%');"
    )
    out = su_sh(f'sqlite3 /data/data/com.android.vending/databases/library.db "{sql}"')
    if not out or "Error:" in out or "not found" in out.lower() or "inaccessible" in out.lower():
        return False
    now_ms = int(time.time() * 1000)
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln or "|" not in ln:
            continue
        _doc, until = ln.split("|", 1)
        until = until.strip()
        if not until:
            return True
        try:
            if int(until) > now_ms:
                return True
        except ValueError:
            return True
    return False


def remove_google_accounts(device=None, target_emails=None):
    """Remove specified (or all) com.google accounts via system_server AccountManagerService."""
    before = google_accounts()
    if not before:
        info("No Google accounts currently present on device")
        return True
    targets = set(target_emails) if target_emails else None
    removing = [a for a in before if targets is None or a in targets]
    if not removing:
        info("Target Google account not found on device")
        return True
    info("Removing Google account(s): " + ", ".join(removing))
    dev = device or get_frida_device()
    done = threading.Event()
    results = []
    errors = []

    js = _embed_java_bridge("""
Java.perform(function () {
  var targets = __TARGETS__;
  try {
    var AccountManager = Java.use("android.accounts.AccountManager");
    var AMS = Java.use("com.android.server.accounts.AccountManagerService");
    var ams = AMS.getSingleton();
    var ctx = ams.mContext.value;
    var am = AccountManager.get(ctx);
    var accs = am.getAccountsByType("com.google");
    var key = AccountManager.KEY_BOOLEAN_RESULT.value;
    for (var i = 0; i < accs.length; i++) {
      var acc = accs[i];
      var name = String(acc.name.value);
      if (targets !== null && targets.indexOf(name) < 0) continue;
      var removed = false;
      try {
        var fut = am.removeAccount(acc, null, null, null);
        var res = fut.getResult();
        if (res && res.getBoolean(key, false)) removed = true;
      } catch (e1) {}
      if (!removed) {
        try {
          ams.removeAccountInternal(acc);
          removed = true;
        } catch (e2) {
          send({ ev: "rm_err", name: name, m: String(e2) });
        }
      }
      send({ ev: "rm_item", name: name, ok: removed });
    }
  } catch (e) {
    send({ ev: "rm_err", m: String(e) });
  }
  send({ ev: "rm_done" });
});
""".replace("__TARGETS__", json.dumps(list(targets)) if targets is not None else "null"), quiet=True)

    def on_msg(msg, _data):
        if msg.get("type") == "error":
            errors.append(msg.get("description") or str(msg))
            done.set()
            return
        if msg.get("type") != "send":
            return
        p = msg.get("payload") or {}
        if not isinstance(p, dict):
            return
        ev = p.get("ev")
        if ev == "rm_item":
            results.append((p.get("name"), bool(p.get("ok"))))
        elif ev == "rm_err":
            errors.append(p.get("m") or "unknown error")
        elif ev == "rm_done":
            done.set()

    sys_sess = None
    sys_script = None
    try:
        sys_sess = dev.attach("system_server")
        sys_script = sys_sess.create_script(js)
        sys_script.on("message", on_msg)
        sys_script.load()
        done.wait(timeout=15)
    except Exception as e:
        errors.append(str(e))
    finally:
        try:
            if sys_script:
                sys_script.unload()
        except Exception:
            pass
        try:
            if sys_sess:
                sys_sess.detach()
        except Exception:
            pass

    for err in errors:
        warn(f"Account removal error: {err}")
    for name, removed in results:
        if removed:
            ok(f"Removed Google account: {name}")
        else:
            warn(f"Account removal not confirmed: {name}")

    time.sleep(0.8)
    after = google_accounts()
    remaining_targets = [a for a in after if targets is None or a in targets]
    if not remaining_targets:
        if not after:
            ok("No Google accounts remaining on device")
        else:
            ok("Target account(s) removed; remaining: " + ", ".join(after))
        return True
    warn("Target Google account(s) still present: " + ", ".join(remaining_targets))
    return False


def account_play_country(email):
    """Read cached Play Store country for an account from finsky.xml."""
    safe = email.replace('"', '\\"')
    out = su_sh(f'grep -F "account-play-country{safe}" /data/data/com.android.vending/shared_prefs/finsky.xml 2>/dev/null')
    m = re.search(r">([^<]+)<", out or "")
    return m.group(1).strip() if m else "?"


class AuditLogger:
    """Append structured JSONL event records when --log-file is configured."""

    def __init__(self, path=""):
        self.path = Path(path) if path else None

    def write(self, **fields):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class SkuScanner:
    """Persist observed Play Billing SKU/offer pairs (adapted from g1-acq)."""

    LIMIT = 300
    RECENT_LIMIT = 10

    def __init__(self, path=SCAN_PATH, plans=None):
        self.path = Path(path) if path else None
        self.known_pairs = {
            (spec.get("sku", ""), spec.get("offer", ""))
            for spec in (plans or {}).values()
        }

    def load(self):
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("schemaVersion") == 1:
                    return data
            except Exception:
                pass
        return {
            "schemaVersion": 1,
            "updatedAt": "",
            "scannedResponses": 0,
            "matchedResponses": 0,
            "limit": self.LIMIT,
            "truncated": False,
            "recentEvents": [],
            "skus": [],
        }

    def clear(self):
        state = {
            "schemaVersion": 1,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scannedResponses": 0,
            "matchedResponses": 0,
            "limit": self.LIMIT,
            "truncated": False,
            "recentEvents": [],
            "skus": [],
        }
        if self.path:
            self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def record(self, items, source="query"):
        state = self.load()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        by_key = {}
        order = []
        for row in state.get("skus", [])[:self.LIMIT]:
            key = f"{row.get('sku', '')}|{row.get('offer', '')}"
            if key in by_key or not row.get("sku"):
                continue
            by_key[key] = dict(row)
            order.append(key)

        added = 0
        omitted = 0
        seen_in_batch = set()
        truncated = bool(state.get("truncated"))
        for it in items or []:
            sku = str(it.get("sku") or "").strip()
            offer = str(it.get("offer") or "").strip()
            if not sku:
                continue
            key = f"{sku}|{offer}"
            in_plans = (sku, offer) in self.known_pairs
            if key not in by_key:
                if len(order) >= self.LIMIT:
                    truncated = True
                    if key not in seen_in_batch:
                        omitted += 1
                    seen_in_batch.add(key)
                    continue
                by_key[key] = {
                    "sku": sku,
                    "offer": offer,
                    "title": str(it.get("title") or ""),
                    "price": str(it.get("price") or ""),
                    "in_plans": in_plans,
                    "count": 0,
                    "firstSeen": now,
                    "lastSeen": now,
                }
                order.append(key)
                added += 1
            seen_in_batch.add(key)
            entry = by_key[key]
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["lastSeen"] = now
            entry["in_plans"] = in_plans
            if it.get("title"):
                entry["title"] = str(it.get("title"))
            if it.get("price"):
                entry["price"] = str(it.get("price"))

        events = list(state.get("recentEvents", []))[-self.RECENT_LIMIT:]
        events.append({
            "at": now,
            "source": source,
            "candidates": len(seen_in_batch),
            "added": added,
            "omitted": omitted,
        })
        state["updatedAt"] = now
        state["scannedResponses"] = int(state.get("scannedResponses", 0)) + 1
        state["matchedResponses"] = int(state.get("matchedResponses", 0)) + (1 if seen_in_batch else 0)
        state["truncated"] = truncated
        state["recentEvents"] = events[-self.RECENT_LIMIT:]
        state["skus"] = [by_key[k] for k in sorted(order)]
        if self.path:
            self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state


def render_sku_report_html(state, plans=None, out_path=REPORT_PATH):
    """Render a standalone interactive HTML report (inspired by g1-acq/g1-sku-report.js)."""
    import html as _html
    skus = state.get("skus", [])
    new_count = sum(1 for s in skus if not s.get("in_plans"))
    events = list(reversed(state.get("recentEvents", [])))
    fallback_rows = [
        (pid, ", ".join(spec["fallback"] if isinstance(spec["fallback"], list) else [spec["fallback"]]))
        for pid, spec in (plans or {}).items()
        if spec.get("fallback")
    ]
    rows_html = "\n".join(
        "<tr>"
        f"<td><code>{_html.escape(s.get('sku', ''))}</code></td>"
        f"<td><code>{_html.escape(s.get('offer', '') or '-')}</code></td>"
        f"<td>{_html.escape(s.get('price', '') or '-')}</td>"
        f"<td>{'<span class=\"badge known\">KNOWN</span>' if s.get('in_plans') else '<span class=\"badge new\">NEW</span>'}</td>"
        f"<td>{int(s.get('count', 0))}</td>"
        f"<td>{_html.escape(s.get('lastSeen', ''))}</td>"
        "</tr>"
        for s in skus
    )
    events_html = "\n".join(
        "<tr>"
        f"<td>{_html.escape(e.get('at', ''))}</td>"
        f"<td>{_html.escape(e.get('source', ''))}</td>"
        f"<td>{int(e.get('candidates', 0))}</td>"
        f"<td>{int(e.get('added', 0))}</td>"
        f"<td>{int(e.get('omitted', 0))}</td>"
        "</tr>"
        for e in events
    )
    fb_html = "\n".join(
        f"<tr><td><code>{_html.escape(pid)}</code></td><td><code>{_html.escape(fb)}</code></td></tr>"
        for pid, fb in fallback_rows
    )
    json_blob = json.dumps(state, ensure_ascii=False, indent=2).replace("</", "<\\/")
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Google One SKU &amp; Offer Report</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
.wrap{{max-width:1100px;margin:0 auto}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px}}
.card b{{display:block;font-size:24px;margin-top:4px;color:#38bdf8}}
.bar{{display:flex;gap:10px;align-items:center;margin:16px 0;flex-wrap:wrap}}
input{{flex:1;min-width:220px;padding:9px 12px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#f8fafc}}
button{{padding:9px 14px;border-radius:8px;border:0;background:#0284c7;color:#fff;cursor:pointer;font-weight:600}}
table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden;margin-bottom:24px}}
th,td{{padding:9px 12px;border-bottom:1px solid #334155;text-align:left;font-size:13px}}
th{{background:#0f172a;color:#94a3b8}}
.badge{{padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}}
.known{{background:#14532d;color:#86efac}} .new{{background:#7c2d12;color:#fdba74}}
code{{color:#bae6fd}}
</style></head><body><div class="wrap">
<h1>Google One Play Billing SKU &amp; Offer Report</h1>
<p>Updated: {_html.escape(state.get('updatedAt') or 'never')}</p>
<div class="cards">
  <div class="card">Observed Offers<b>{len(skus)}</b></div>
  <div class="card">New (Not in plans.json)<b>{new_count}</b></div>
  <div class="card">Scanned Responses<b>{int(state.get('scannedResponses', 0))}</b></div>
  <div class="card">Matched Responses<b>{int(state.get('matchedResponses', 0))}</b></div>
</div>
<div class="bar">
  <input id="q" placeholder="Filter by SKU, offerId, price, or NEW/KNOWN..." oninput="filterRows()">
  <button onclick="exportJson()">Export JSON</button>
</div>
<h2>Observed SKUs &amp; Offers</h2>
<table id="tbl"><thead><tr><th>SKU</th><th>Offer ID</th><th>Price</th><th>Catalog</th><th>Hits</th><th>Last Seen</th></tr></thead>
<tbody>{rows_html or '<tr><td colspan="6">No SKUs recorded yet. Run --check-offers or select a plan first.</td></tr>'}</tbody></table>
<h2>Recent Scan Events (Latest 10)</h2>
<table><thead><tr><th>Time (UTC)</th><th>Source</th><th>Candidates</th><th>Added</th><th>Omitted</th></tr></thead>
<tbody>{events_html or '<tr><td colspan="5">No events recorded yet.</td></tr>'}</tbody></table>
<h2>Configured Fallback Rules</h2>
<table><thead><tr><th>Primary Plan</th><th>Fallback Chain</th></tr></thead>
<tbody>{fb_html or '<tr><td colspan="2">None</td></tr>'}</tbody></table>
</div>
<script id="raw" type="application/json">{json_blob}</script>
<script>
function filterRows(){{var q=document.getElementById('q').value.toLowerCase();var trs=document.querySelectorAll('#tbl tbody tr');trs.forEach(function(r){{r.style.display=r.textContent.toLowerCase().indexOf(q)>=0?'':'none';}});}}
function exportJson(){{var blob=new Blob([document.getElementById('raw').textContent],{{type:'application/json'}});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='g1-sku-report.json';a.click();}}
</script></body></html>"""
    out = Path(out_path)
    out.write_text(doc, encoding="utf-8")
    return out


class Session:
    def __init__(self, plans=None, scanner=None):
        self.plans = plans
        self.scanner = scanner
        self.device = None
        self.session = None
        self.script = None
        self.ready = threading.Event()
        self.launched_ok = threading.Event()
        self.launch_failed = threading.Event()
        self.detached_ev = threading.Event()
        self.purchase_done = threading.Event()
        self.purchase_code = None
        self.pd_code = None
        self.launch_code = None
        self.subs_checked = threading.Event()
        self.subs_has_g1 = False
        self.offers_checked = threading.Event()
        self.offers_audit = []
        self.last_ev = ""
        self.lock = threading.Lock()

    def _on_detached(self, reason, crash=None):
        warn(f"Frida session detached: {reason}" + (f" ({crash})" if crash else ""))
        self.detached_ev.set()
        self.launch_failed.set()

    def _on_message(self, message, _data):
        if message.get("type") == "error":
            fail(message.get("stack") or message.get("description") or str(message))
            self.launch_failed.set()
            return
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        if isinstance(payload, dict) and payload.get("type") == "frida:load-bridge":
            info(f"Loading Frida bridge: {payload.get('name')}")
            post_frida_bridge(self.script, payload.get("name") or "java")
            return
        if not isinstance(payload, dict):
            print(payload, flush=True)
            return
        ev = payload.get("ev") or ""
        with self.lock:
            self.last_ev = ev
        if ev == "offers_audit":
            try:
                self.offers_audit = json.loads(payload.get("items") or "[]")
            except Exception:
                self.offers_audit = []
            self.offers_checked.set()
            return
        if ev == "sku_seen":
            try:
                seen_items = json.loads(payload.get("items") or "[]")
                if self.scanner and seen_items:
                    self.scanner.record(seen_items, source=payload.get("source") or "frida")
            except Exception:
                pass
            return
        bits = [ev] if ev else []
        for k, v in payload.items():
            if k == "ev" or v in ("", None):
                continue
            bits.append(f"{k}={v}")
        print("[g1] " + "  ".join(str(x) for x in bits), flush=True)
        if ev == "ready":
            self.ready.set()
        elif ev == "pd":
            self.pd_code = str(payload.get("code", ""))
        elif ev == "launched":
            code = str(payload.get("code", ""))
            self.launch_code = code
            if code == "0":
                self.launched_ok.set()
                ok("Play checkout sheet opened; confirm manually on device (no auto-subscribe)")
            else:
                self.launch_failed.set()
                warn(f"launchBillingFlow returned code={code} {payload.get('msg', '')}")
        elif ev in ("no_offer", "no_purchase", "no_activity", "bad_target", "boot_err", "launch_err"):
            self.launch_failed.set()
        elif ev == "subs_check":
            self.subs_has_g1 = bool(payload.get("has_g1"))
            self.subs_checked.set()
        elif ev == "purchase_cb":
            code = str(payload.get("code", ""))
            with self.lock:
                self.purchase_code = code
            self.purchase_done.set()
            if code == "0":
                ok("Payment succeeded (purchase_cb code=0)")
            elif code == "1":
                warn("Payment canceled by user (purchase_cb code=1)")
            else:
                warn(f"Purchase callback returned code={code} {payload.get('msg', '')}")

    def attach(self, pid):
        self.ready.clear()
        self.detached_ev.clear()
        self.device = get_frida_device()
        info(f"attach pid={pid}")
        self.session = self.device.attach(pid)
        try:
            self.session.on("detached", self._on_detached)
        except Exception:
            pass
        js = _embed_java_bridge(JS_PATH.read_text(encoding="utf-8"))
        self.script = self.session.create_script(js)
        self.script.on("message", self._on_message)
        self.script.load()
        if not self.ready.wait(timeout=20):
            warn("BillingClient did not report ready within 20s; you can still try placing an order")
        else:
            ok("BillingClient ready")
        if self.plans:
            try:
                exp = self._exports()
                fn = getattr(exp, "set_plans", None) or getattr(exp, "setPlans", None)
                if callable(fn):
                    fn(json.dumps(self.plans, ensure_ascii=False))
            except Exception as e:
                warn(f"Failed to sync plans.json to Frida: {e}")

    def _exports(self):
        sc = self.script
        if hasattr(sc, "exports_sync"):
            return sc.exports_sync
        return sc.exports

    def buy(self, plan_id, stack=False, addon="", old_token=""):
        self.launched_ok.clear()
        self.launch_failed.clear()
        self.purchase_done.clear()
        with self.lock:
            self.purchase_code = None
            self.pd_code = None
            self.launch_code = None
        exp = self._exports()

        def rpc(names, arg=None, required=True):
            last = None
            for n in names:
                fn = getattr(exp, n, None)
                if not callable(fn):
                    continue
                try:
                    return fn() if arg is None else fn(arg)
                except Exception as e:
                    last = e
            if required:
                raise last or AttributeError(names[0])
            return None

        try:
            rpc(("set_addon", "setAddon"), addon or "", required=False)
            rpc(("set_old_token", "setOldToken"), old_token or "", required=False)
            ret = rpc(("stack",) if stack else ("buy",), plan_id)
            info(ret)
        except Exception as e:
            warn(f"RPC call failed, falling back to post: {e}")
            self.script.post({
                "type": "cfg",
                "payload": {"target": plan_id, "stack": stack, "addon": addon, "oldToken": old_token},
            })

    def check_subs(self, timeout=8.0):
        """Query live Play Billing subscriptions for active Google One plans."""
        self.subs_checked.clear()
        self.subs_has_g1 = False
        exp = self._exports()
        fn = getattr(exp, "check_subs", None) or getattr(exp, "checkSubs", None)
        if not callable(fn):
            return False
        try:
            fn()
        except Exception:
            return False
        self.subs_checked.wait(timeout=timeout)
        return self.subs_has_g1

    def check_offers(self, plans, timeout=15.0):
        """Query Play Billing for all configured SKU/offer availability without launching UI."""
        self.offers_checked.clear()
        self.offers_audit = []
        exp = self._exports()
        fn = getattr(exp, "check_offers", None) or getattr(exp, "checkOffers", None)
        if not callable(fn):
            warn("Current frida.js does not support checkOffers")
            return []
        try:
            fn()
        except Exception as e:
            warn(f"checkOffers call failed: {e}")
            return []
        if not self.offers_checked.wait(timeout=timeout):
            warn("Offer audit timed out")
            return []
        by_pair = {(it.get("sku"), it.get("offer")): it for it in self.offers_audit}
        print()
        print("-" * 76)
        print(f"  {'PLAN':<16} {'STATUS':<8} {'PRICE':<24} {'SKU / OFFER'}")
        print("-" * 76)
        for pid, spec in plans.items():
            hit = by_pair.get((spec["sku"], spec["offer"]))
            status = "OK" if hit else "MISS"
            price = hit.get("price", "-") if hit else "-"
            offer_label = spec["offer"] or "base"
            print(f"  {pid:<16} {status:<8} {price:<24} {spec['sku']} ({offer_label})")
        print("-" * 76)
        return self.offers_audit

    def wait_for_purchase(self, timeout=300.0, account=None, baseline=None):
        """Wait for Play checkout completion via callback or library.db update."""
        deadline = time.monotonic() + max(timeout, 1.0)
        next_db_poll = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.purchase_done.is_set():
                with self.lock:
                    return self.purchase_code
            if self.detached_ev.is_set():
                return "detached"
            if self.launch_failed.is_set():
                return None
            now = time.monotonic()
            if account and baseline is not None and self.launched_ok.is_set() and now >= next_db_poll:
                next_db_poll = now + 2.0
                cur = db_sub_snapshot(account)
                if cur != baseline and len(cur) > 0:
                    ok("Play Store subscription update detected; payment succeeded")
                    return "0"
            time.sleep(0.3)
        return "timeout"

    def close(self):
        try:
            if self.script:
                self.script.unload()
        except Exception:
            pass
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass
        self.script = None
        self.session = None


def pick_addon(plans):
    yt = [pid for pid in ("yt_1m", "yt_family", "yt_lite") if pid in plans]
    print("Add-on plan (submitted alongside the primary plan):")
    print("  0. None")
    for i, pid in enumerate(yt, 1):
        print(f"  {i}. {pid}  {plans[pid]['note']}")
    raw = input("Choice: ").strip() or "0"
    if raw in ("0", "n", "none", ""):
        return ""
    if raw in plans:
        return raw
    try:
        return yt[int(raw) - 1]
    except Exception:
        return ""


def parse_args():
    p = argparse.ArgumentParser(description="Google One Plan Injector")
    p.add_argument("-s", "--serial", default=os.environ.get("ANDROID_SERIAL") or "",
                   help="Target adb device serial")
    p.add_argument("--plan", default="", help="Plan ID or menu number to launch non-interactively")
    p.add_argument("--stack-plans", default="",
                   help="Comma-separated stack chain plan list (e.g. pro_12m,pro_6m_2,pro_6m)")
    p.add_argument("--stack", "--renew", action="store_true",
                   help="Renew/stack onto an existing Google One subscription (frida.js STACK=true)")
    p.add_argument("--addon", default="", help="Add-on plan ID, e.g. yt_1m")
    p.add_argument("--wait", type=float, default=8.0,
                   help="Seconds to wait after ordering when auto-remove is off (default 8.0)")
    p.add_argument("--wait-payment", type=float, default=300.0,
                   help="Timeout in seconds while waiting for payment completion (default 300)")
    p.add_argument("--auto-remove", action="store_true",
                   help="Automatically remove the Google account after payment succeeds (off by default)")
    p.add_argument("--loop", action="store_true",
                   help="Loop through all signed-in Google accounts (auto-remove & advance)")
    p.add_argument("--check-offers", action="store_true",
                   help="Check all plan offer availability and prices without launching checkout")
    p.add_argument("--report-html", nargs="?", const=str(REPORT_PATH), default="",
                   help="Export local HTML SKU/offer scan and fallback report (default g1-sku-report.html)")
    p.add_argument("--clear-scan", action="store_true",
                   help="Clear local SKU scan history (sku_scan.json) and exit")
    p.add_argument("--log-file", default="",
                   help="Write structured JSONL audit records to the given file")
    p.add_argument("--remove-accounts", action="store_true",
                   help="Immediately remove all Google accounts on the device and exit")
    return p.parse_args()


def resolve_plan(raw, plans, index=None):
    if not raw:
        return ""
    if raw in plans:
        return raw
    if index and raw in index:
        return index[raw]
    return ""


def resolve_plan_chain(raw_list, plans, index=None):
    """Parse a comma-separated list of plan IDs/menu numbers into validated plan IDs."""
    items = [part.strip() for part in (raw_list or "").split(",") if part.strip()]
    resolved = []
    for item in items:
        pid = resolve_plan(item, plans, index) or resolve_plan(item.lower(), plans, index)
        if not pid:
            return [], item
        resolved.append(pid)
    return resolved, ""


def main():
    _fix_stdio()
    args = parse_args()
    if args.serial:
        adb.serial = args.serial
        os.environ["ANDROID_SERIAL"] = args.serial

    if not JS_PATH.is_file():
        fail(f"Missing {JS_PATH}")
        sys.exit(1)
    plans, groups_cfg = load_catalog()
    if not plans:
        fail("Failed to load plan catalog (plans.json / frida.js)")
        sys.exit(1)

    scanner = SkuScanner(SCAN_PATH, plans=plans)
    if args.clear_scan:
        scanner.clear()
        ok(f"Cleared local SKU scan history: {SCAN_PATH}")
        return
    if args.report_html and not (args.check_offers or args.plan or args.stack_plans or args.loop):
        out = render_sku_report_html(scanner.load(), plans=plans, out_path=args.report_html)
        ok(f"Generated HTML report: {out}")
        return

    ensure_env()
    if args.remove_accounts:
        remove_google_accounts()
        return

    audit = AuditLogger(args.log_file)
    stack = bool(args.stack)
    addon = args.addon
    auto_remove = bool(args.auto_remove)
    groups = grouped_plans(plans, groups_cfg=groups_cfg)
    index = {str(i): pid for i, (_t, pid, _s) in enumerate(flatten_menu(groups), 1)}
    index.update({pid: pid for pid in plans})

    def remove_and_advance(dev, email):
        remove_google_accounts(dev, target_emails=[email])
        adb.shell("am", "force-stop", PKG)
        adb.shell("am", "force-stop", "com.android.vending")
        time.sleep(1.0)

    def handle_post_buy(sess, plan_id, spec, account="", target_emails=None, baseline=None):
        if not auto_remove:
            time.sleep(max(args.wait, 1.0))
            audit.write(
                account=account, plan=plan_id, sku=spec["sku"], offer=spec["offer"],
                stack=stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                purchase_code=None, action="launched_only",
            )
            return None
        info(f"Waiting for payment on device (timeout {int(args.wait_payment)}s, Ctrl+C to cancel)...")
        code = sess.wait_for_purchase(timeout=args.wait_payment, account=account, baseline=baseline)
        action = "kept"
        if code == "0":
            time.sleep(1.5)
            remove_google_accounts(sess.device, target_emails=target_emails)
            action = "paid_and_removed"
        elif code == "timeout":
            warn("Payment wait timed out; Google account was kept")
            action = "timeout"
        elif code == "detached":
            warn("Google One process detached; Google account was kept")
            action = "detached"
        elif code is not None:
            info(f"Payment not completed (code={code}); Google account was kept")
            action = f"unpaid_{code}"
        audit.write(
            account=account, plan=plan_id, sku=spec["sku"], offer=spec["offer"],
            stack=stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
            purchase_code=code, action=action,
        )
        return code

    def print_preflight(accts):
        print()
        print("-" * 64)
        print(f"  {'#':<3} {'ACCOUNT':<34} {'COUNTRY':<8} {'LOCAL_SUB'}")
        print("-" * 64)
        for i, email in enumerate(accts, 1):
            country = account_play_country(email)
            sub_flag = "YES" if db_has_g1_sub(email) else "NO"
            print(f"  {i:<3} {email:<34} {country:<8} {sub_flag}")
        print("-" * 64)

    def run_stack_chain(sess, plan_ids, account, initial_has_sub=None):
        """Execute a sequence of plans on one account: first as new (if unsubscribed), then stack (mode=5)."""
        has_sub = initial_has_sub
        if has_sub is None:
            has_sub = db_has_g1_sub(account) or sess.check_subs()
        paid_count = 0
        total_steps = len(plan_ids)
        for idx, pid_name in enumerate(plan_ids, 1):
            spec = plans[pid_name]
            use_stack = bool(stack or has_sub)
            print()
            info(f"Stack step [{idx}/{total_steps}] account={account}  plan={pid_name}  mode={'stack(mode=5)' if use_stack else 'new'}")
            info(spec["note"])
            baseline = db_sub_snapshot(account)
            old_tok = latest_db_token(baseline) if use_stack else ""
            sess.buy(pid_name, stack=use_stack, addon=addon, old_token=old_tok)
            info(f"Waiting for {account} to finish payment {idx}/{total_steps} (timeout {int(args.wait_payment)}s, Ctrl+C to abort)...")
            code = sess.wait_for_purchase(timeout=args.wait_payment, account=account, baseline=baseline)
            if code == "0":
                paid_count += 1
                has_sub = True
                audit.write(
                    account=account, plan=pid_name, sku=spec["sku"], offer=spec["offer"],
                    stack=use_stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                    purchase_code=code, step=f"{idx}/{total_steps}", action="stack_step_ok",
                )
                time.sleep(1.5)
                continue
            warn(f"Step {idx}/{total_steps} ({pid_name}) did not complete (code={code})")
            audit.write(
                account=account, plan=pid_name, sku=spec["sku"], offer=spec["offer"],
                stack=use_stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                purchase_code=code, step=f"{idx}/{total_steps}", action=f"stack_step_stopped_{code}",
            )
            if idx < total_steps:
                try:
                    ans = input("  [n] Skip this plan and continue chain / [q] Stop chain for this account: ").strip().lower()
                except EOFError:
                    ans = "q"
                if ans == "n":
                    continue
            return paid_count, False
        return paid_count, True

    def run_loop(plan_ids, initial_sess=None):
        is_chain = len(plan_ids) > 1
        single_pid = plan_ids[0]
        spec = plans[single_pid]
        initial = google_accounts()
        total = len(initial)
        if not total:
            warn("No Google accounts on device")
            return initial_sess
        print_preflight(initial)
        info(f"Starting loop across {total} Google account(s), plan(s): {', '.join(plan_ids)}")
        sess = initial_sess
        step = 0
        while True:
            accts = google_accounts()
            if not accts:
                ok("All Google accounts processed")
                break
            step += 1
            current = accts[0]
            print()
            print("=" * 64)
            info(f"Progress [{step}/{max(total, step)}] current={current}  (remaining={len(accts)})")
            if not is_chain and not stack and db_has_g1_sub(current):
                warn(f"{current} already has a Google One subscription in local DB -> removing and advancing")
                if sess:
                    sess.close()
                    sess = None
                audit.write(account=current, plan=single_pid, sku=spec["sku"], offer=spec["offer"], stack=stack, action="skipped_db_sub_removed")
                remove_and_advance(None, current)
                continue
            if sess is None:
                sess = Session(plans=plans, scanner=scanner)
                pid = start_google_one()
                sess.attach(pid)
            if not is_chain and not stack and sess.check_subs():
                warn(f"{current} has an active Google One subscription -> removing and advancing")
                dev = sess.device
                sess.close()
                sess = None
                audit.write(account=current, plan=single_pid, sku=spec["sku"], offer=spec["offer"], stack=stack, action="skipped_live_sub_removed")
                remove_and_advance(dev, current)
                continue
            if is_chain:
                paid_count, finished_chain = run_stack_chain(sess, plan_ids, current)
                if finished_chain and paid_count > 0:
                    dev = sess.device
                    sess.close()
                    sess = None
                    remove_and_advance(dev, current)
                    continue
                if paid_count == 0:
                    warn(f"{current} completed 0 payments in stack chain; account kept")
                else:
                    warn(f"{current} partially completed stack chain ({paid_count}/{len(plan_ids)}); account kept")
            else:
                info(f"{'Stack' if stack else 'New'} {single_pid}  sku={spec['sku']}  offer={spec['offer']}")
                info(spec["note"])
                baseline = db_sub_snapshot(current)
                old_tok = latest_db_token(baseline) if stack else ""
                sess.buy(single_pid, stack=stack, addon=addon, old_token=old_tok)
                info(f"Waiting for {current} to finish payment (timeout {int(args.wait_payment)}s, Ctrl+C to abort)...")
                code = sess.wait_for_purchase(timeout=args.wait_payment, account=current, baseline=baseline)
                if code == "0":
                    time.sleep(1.5)
                    dev = sess.device
                    audit.write(
                        account=current, plan=single_pid, sku=spec["sku"], offer=spec["offer"],
                        stack=stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                        purchase_code=code, action="paid_and_removed",
                    )
                    sess.close()
                    sess = None
                    remove_and_advance(dev, current)
                    continue
                warn(f"{current} did not complete payment (code={code}); account kept")
            try:
                ans = input("  [r] Remove this account and continue / [q] Stop loop: ").strip().lower()
            except EOFError:
                ans = "q"
            if ans == "r":
                dev = sess.device
                audit.write(
                    account=current, plan=",".join(plan_ids), sku=spec["sku"], offer=spec["offer"],
                    stack=stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                    purchase_code=sess.purchase_code, action="manual_removed_after_unpaid",
                )
                sess.close()
                sess = None
                remove_and_advance(dev, current)
                continue
            audit.write(
                account=current, plan=",".join(plan_ids), sku=spec["sku"], offer=spec["offer"],
                stack=stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                purchase_code=sess.purchase_code, action="loop_stopped",
            )
            break
        return sess

    if args.check_offers:
        sess = Session(plans=plans, scanner=scanner)
        pid = start_google_one()
        sess.attach(pid)
        items = sess.check_offers(plans)
        audit.write(account=",".join(google_accounts()), action="check_offers", offers_found=len(items))
        if args.report_html:
            out = render_sku_report_html(scanner.load(), plans=plans, out_path=args.report_html)
            ok(f"Generated HTML report: {out}")
        sess.close()
        return

    stack_chain = []
    if args.stack_plans:
        stack_chain, bad_item = resolve_plan_chain(args.stack_plans, plans, index)
        if not stack_chain:
            fail(f"--stack-plans contains invalid plan: {bad_item!r}")
            sys.exit(1)

    if args.loop:
        if stack_chain:
            loop_plans = stack_chain
        else:
            plan_id = resolve_plan(args.plan, plans, index)
            if not plan_id:
                fail("--loop requires a valid --plan or --stack-plans argument")
                sys.exit(1)
            loop_plans = [plan_id]
        sess = None
        try:
            sess = run_loop(loop_plans)
        except KeyboardInterrupt:
            print()
            warn("Loop interrupted")
        finally:
            if sess:
                sess.close()
        return

    sess = Session(plans=plans, scanner=scanner)
    pid = start_google_one()
    sess.attach(pid)

    if stack_chain:
        acct = (google_accounts() or [""])[0]
        try:
            paid_count, finished_chain = run_stack_chain(sess, stack_chain, acct)
            if auto_remove and finished_chain and paid_count > 0:
                remove_and_advance(sess.device, acct)
        except KeyboardInterrupt:
            print()
            warn("Stack chain canceled")
        sess.close()
        return

    if args.plan:
        plan_id = resolve_plan(args.plan, plans, index)
        if not plan_id:
            fail(f"Unknown plan {args.plan!r}")
            sess.close()
            sys.exit(1)
        spec = plans[plan_id]
        acct = (google_accounts() or [""])[0]
        baseline = db_sub_snapshot(acct)
        old_tok = latest_db_token(baseline) if stack else ""
        info(f"{'Stack' if stack else 'New'} {plan_id}  sku={spec['sku']}  offer={spec['offer']}")
        info(spec["note"])
        sess.buy(plan_id, stack=stack, addon=addon, old_token=old_tok)
        try:
            handle_post_buy(sess, plan_id, spec, account=acct, baseline=baseline)
        except KeyboardInterrupt:
            print()
            warn("Canceled waiting for payment")
        sess.close()
        return

    try:
        while True:
            index, _rows = print_menu(plans, stack, addon, auto_remove=auto_remove, groups_cfg=groups_cfg)
            try:
                raw = input("> ").strip()
            except EOFError:
                break
            if not raw:
                continue
            low = raw.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "l":
                continue
            if low == "s":
                stack = not stack
                ok("Renew/Stack mode: ON (stacks onto active subscription)" if stack else "Renew/Stack mode: OFF (new purchase)")
                continue
            if low == "a":
                addon = pick_addon(plans)
                ok(f"Add-on: {addon or 'None'}")
                continue
            if low == "r":
                auto_remove = not auto_remove
                ok("Auto-remove after payment: ON" if auto_remove else "Auto-remove after payment: OFF")
                continue
            if low == "o":
                if sess is None:
                    sess = Session(plans=plans, scanner=scanner)
                    pid = start_google_one()
                    sess.attach(pid)
                sess.check_offers(plans)
                continue
            if low == "h":
                out = render_sku_report_html(scanner.load(), plans=plans)
                ok(f"Generated HTML report: {out}")
                continue
            if low == "t":
                try:
                    raw_chain = input("Enter plans to stack (comma-separated, e.g. pro_12m,pro_6m_2,pro_6m): ").strip()
                except EOFError:
                    continue
                chain, bad_item = resolve_plan_chain(raw_chain, plans, index)
                if not chain:
                    warn(f"Unrecognized plan: {bad_item or raw_chain}")
                    continue
                if sess is None:
                    sess = Session(plans=plans, scanner=scanner)
                    pid = start_google_one()
                    sess.attach(pid)
                acct = (google_accounts() or [""])[0]
                try:
                    paid_count, finished_chain = run_stack_chain(sess, chain, acct)
                    if auto_remove and finished_chain and paid_count > 0:
                        remove_and_advance(sess.device, acct)
                        sess.close()
                        sess = None
                except KeyboardInterrupt:
                    print()
                    warn("Stack chain canceled; returning to menu")
                continue
            if low == "p":
                try:
                    raw_plan = input("Enter plan or stack chain for account loop (e.g. pro_12m or pro_12m,pro_6m_2): ").strip()
                except EOFError:
                    continue
                loop_plans, bad_item = resolve_plan_chain(raw_plan, plans, index)
                if not loop_plans:
                    warn(f"Unrecognized plan: {bad_item or raw_plan}")
                    continue
                sess = run_loop(loop_plans, initial_sess=sess)
                if sess is None and google_accounts():
                    sess = Session(plans=plans, scanner=scanner)
                    pid = start_google_one()
                    sess.attach(pid)
                continue
            if low == "x":
                remove_google_accounts(sess.device if sess else None)
                continue
            if low == "e":
                ensure_env()
                continue
            if low == "g":
                if sess:
                    sess.close()
                pid = start_google_one()
                sess = Session(plans=plans, scanner=scanner)
                sess.attach(pid)
                continue
            plan_id = index.get(raw) or index.get(low)
            if not plan_id:
                warn("Unrecognized input. Enter a plan number, plan ID, or q to quit.")
                continue
            if sess is None:
                sess = Session(plans=plans, scanner=scanner)
                pid = start_google_one()
                sess.attach(pid)
            spec = plans[plan_id]
            acct = (google_accounts() or [""])[0]
            baseline = db_sub_snapshot(acct)
            old_tok = latest_db_token(baseline) if stack else ""
            info(f"{'Stack' if stack else 'New'} {plan_id}  sku={spec['sku']}  offer={spec['offer']}")
            info(spec["note"])
            sess.buy(plan_id, stack=stack, addon=addon, old_token=old_tok)
            if auto_remove:
                try:
                    handle_post_buy(sess, plan_id, spec, account=acct, baseline=baseline)
                except KeyboardInterrupt:
                    print()
                    warn("Canceled waiting for payment; returning to menu")
            else:
                time.sleep(1.2)
    except KeyboardInterrupt:
        print()
        warn("Interrupted")
    finally:
        if sess:
            sess.close()
        info("Done")


if __name__ == "__main__":
    main()
