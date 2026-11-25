# -*- coding: utf-8 -*-
"""
Google One 套餐注入器

  python main.py

自动找 adb、连模拟器/手机，缺 frida-server / module_521 就补齐。
你只需要：设备已 root、已登录 Google 账号、已安装 Google One。
选套餐后弹出收银台，不会自动点订阅。

续费：菜单里按 s 打开「续费叠加」（对应 frida.js 的 STACK=true），
会把新套餐叠到当前 Google One 订阅上，而不是新开一单。
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
        "pro10_1m_90", "pro10_3m_85",
    ]),
    ("Ultra 5x", [
        "ultra20_3m_90", "ultra20_3m_80", "ultra20_3m_75",
    ]),
    ("Ultra 20x", [
        "ultra_1m_90", "ultra_3m_90", "ultra_3m_80", "ultra_1m_75", "ultra_3m_75",
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
        info("重启 adb server")
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
            info("没有在线设备，尝试连接本机模拟器端口（MuMu / 雷电 / 夜神）")
            self._connect_emulators()
            time.sleep(0.6)
            rows = self.devices()
        if not rows:
            info("等待设备上线 ...")
            try:
                self._cmd(["wait-for-device"], serial="", timeout=15)
            except subprocess.TimeoutExpired:
                pass
            time.sleep(1.0)
            self._connect_emulators()
            rows = self.devices()
        rows = self._dedupe(rows)
        if not rows:
            fail("没有可用的 adb 设备。请先启动模拟器或插上手机并打开 USB 调试。")
            sys.exit(1)
        return rows

    def pick(self):
        rows = self.discover()
        if self.serial and self.serial in rows:
            ok(f"设备 {self.serial}")
            return self.serial
        if self.serial and self.serial not in rows:
            warn(f"指定的 {self.serial} 不在线，改自动选择")
        if len(rows) == 1:
            self.serial = rows[0]
        else:
            scores = {s: self._score(s) for s in rows}
            ranked = sorted(rows, key=lambda s: scores[s], reverse=True)
            self.serial = ranked[0]
            info("检测到多台设备，自动选分最高的一台：")
            for s in ranked:
                info(f"  {s}  score={scores[s]}")
        os.environ["ANDROID_SERIAL"] = self.serial
        ok(f"设备 {self.serial}")
        return self.serial


adb = Adb()


def parse_plans(js_text):
    """Parse PLANS from frida.js so the menu stays in sync with the hook."""
    block = re.search(r"var PLANS = \{([\s\S]*?)\n\};", js_text)
    if not block:
        return {}
    plans = {}
    for m in re.finditer(
        r"(\w+)\s*:\s*\{\s*sku:\s*\"([^\"]+)\"\s*,\s*offer:\s*\"([^\"]+)\"\s*,\s*note:\s*\"([^\"]*)\"",
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
        groups.append(("其他", extra))
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
    mode = "续费叠加（STACK=true，叠到现有订阅上）" if stack else "新购（STACK=false）"
    print()
    print("=" * 64)
    print("  Google One 套餐注入")
    print(f"  模式: {mode}")
    print(f"  附加: {addon or '无'}")
    print(f"  支付后自动删号: {'开' if auto_remove else '关'}")
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
    print("  输入编号或套餐名下单")
    print("  s  切换续费叠加（需要设备上已有 Google One 订阅）")
    print("  t  连环叠加 / Tumpuk Payment（同一账号按顺序购买并叠加多个套餐）")
    print("  a  设置 YouTube 附加套餐")
    print("  r  切换支付成功后自动移除 Google 账号")
    print("  p  循环处理设备上所有 Google 账号（已订阅则删号跳下一个）")
    print("  o  检查所有套餐 Offer 可用性与价格")
    print("  x  立即移除设备上的 Google 账号")
    print("  e  检测并补齐环境")
    print("  g  重启 Google One 并重新注入")
    print("  l  重新列出套餐")
    print("  q  退出")
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
        ok(f"本机 frida {ver}")
        return ver
    warn("本机没有 frida，正在 pip install frida-tools ...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "frida-tools"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        fail((r.stderr or r.stdout or "pip 失败").strip()[:400])
        sys.exit(1)
    ver = host_frida_version()
    if not ver:
        fail("安装了 frida-tools 但仍找不到 frida。请检查 PATH。")
        sys.exit(1)
    ok(f"本机 frida {ver}")
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
        ok(f"module_521.apk 已在设备 {DEX_REMOTE}")
        return
    info("设备缺少 module_521.apk，正在从内置 dex 解开并 push ...")
    try:
        from module_b64 import MODULE_B64
    except Exception as e:
        fail(f"读不到 module_b64.py: {e}")
        sys.exit(1)
    import base64
    tmp = Path(tempfile.gettempdir()) / "module_521.apk"
    tmp.write_bytes(base64.b64decode(MODULE_B64))
    r = adb.run(["push", str(tmp), DEX_REMOTE])
    print((r.stdout or r.stderr or "").strip(), flush=True)
    adb.shell("chmod", "644", DEX_REMOTE)
    if not remote_exists(DEX_REMOTE):
        fail("push module_521.apk 失败")
        sys.exit(1)
    ok("module_521.apk 已推送")


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
        info(f"解压 {xz.name} ...")
        return _xz_to_file(xz, unpacked)
    return None


def download_frida_server(ver, abi_tag):
    cache = HERE / "assets"
    cache.mkdir(exist_ok=True)
    name = f"frida-server-{ver}-{abi_tag}.xz"
    url = f"https://github.com/frida/frida/releases/download/{ver}/{name}"
    xz = cache / name
    info(f"下载 {url}")
    try:
        urllib.request.urlretrieve(url, xz)
    except Exception as e:
        fail(f"下载失败: {e}")
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
        fail(f"无法识别设备 ABI: {abi!r}")
        sys.exit(1)
    info(f"设备 ABI {abi} -> {abi_tag}")

    remote_ver = remote_frida_version()
    running = (adb.shell("pidof", "frida-server") or "").strip()
    if running and remote_ver == ver:
        ok(f"frida-server {remote_ver} 已在跑 pid={running.split()[0]}")
        return

    if remote_ver and remote_ver != ver:
        warn(f"设备 frida-server {remote_ver} 和本机 {ver} 不一致，将替换")

    local = None
    cache = HERE / "assets" / f"frida-server-{ver}-{abi_tag}"
    if cache.is_file() and cache.stat().st_size > 1024 * 1024:
        local = cache
    if local is None:
        local = download_frida_server(ver, abi_tag)
    if local is None:
        local = local_frida_server(abi_tag)
    if local is None:
        fail("无法准备 frida-server。请把对应 ABI 的文件放到 Gemini/assets/ 或 ../bin/")
        sys.exit(1)

    if running:
        su_sh("killall -9 frida-server")
        time.sleep(0.5)

    info(f"push {local.name} ({ver}) -> {FRIDA_REMOTE}")
    r = adb.run(["push", str(local), FRIDA_REMOTE])
    print((r.stdout or r.stderr or "").strip(), flush=True)

    running = start_remote_frida()
    if not running:
        fail("frida-server 启动失败。设备需要 root。")
        sys.exit(1)
    ok(f"frida-server {ver} 已启动 pid={running.split()[0]}")


def ensure_google_one():
    path = adb.shell("pm", "path", PKG)
    if "package:" in path:
        ok("Google One 已安装")
        return
    fail("设备上没有 Google One（com.google.android.apps.subscriptions.red）")
    fail("请先从 Play Store 安装 Google One。")
    sys.exit(1)


def ensure_google_account():
    accts = google_accounts()
    if accts:
        ok("Google 账号: " + ", ".join(accts))
        return accts
    fail("设备上没有登录 Google 账号。请先在系统设置或 Play Store 登录。")
    sys.exit(1)


def ensure_root():
    if has_root():
        ok("设备已 root")
        return
    fail("设备没有 root（su 失败）。请先 root 后再运行。")
    sys.exit(1)


def print_env(ver):
    accts = google_accounts()
    print()
    print("-" * 64)
    print("  环境")
    print("-" * 64)
    print(f"  设备          {adb.serial}")
    print(f"  ABI           {device_abi() or '?'}")
    print(f"  root          {'是' if has_root() else '否'}")
    print(f"  Google 账号   {', '.join(accts) if accts else '无'}")
    print(f"  本机 frida    {ver or '无'}")
    print(f"  frida-server  {(adb.shell('pidof', 'frida-server') or '未运行').split()[0]}")
    print(f"  module_521    {'有' if remote_exists(DEX_REMOTE) else '无'}")
    g1 = "有" if "package:" in adb.shell("pm", "path", PKG) else "无"
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
        fail("Google One 没有启动起来")
        sys.exit(1)
    time.sleep(2.5)
    pid = (g1_pid() or [pid])[0]
    ok(f"Google One pid={pid}")
    return int(pid)


def post_frida_bridge(script, name):
    """Frida 17 把 Java 桥拆出去了，脚本一用 Java.perform 就会要这份源码。"""
    try:
        import frida_tools
        root = Path(frida_tools.__file__).resolve().parent / "bridges"
    except Exception as e:
        fail(f"找不到 frida_tools/bridges: {e}")
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
    fail(f"没有名为 {stem} 的 Frida bridge")
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
                info("已内嵌 frida-java-bridge")
            return java_js.read_text(encoding="utf-8") + "\n;var Java = bridge;\n" + js
    except Exception as e:
        if not quiet:
            warn(f"未能内嵌 Java bridge: {e}")
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
        info("设备上当前没有需要移除的 Google 账号")
        return True
    targets = set(target_emails) if target_emails else None
    removing = [a for a in before if targets is None or a in targets]
    if not removing:
        info("未找到要移除的目标 Google 账号")
        return True
    info("正在移除 Google 账号: " + ", ".join(removing))
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
        warn(f"删号回调异常: {err}")
    for name, removed in results:
        if removed:
            ok(f"已移除 Google 账号: {name}")
        else:
            warn(f"移除账号未确认成功: {name}")

    time.sleep(0.8)
    after = google_accounts()
    remaining_targets = [a for a in after if targets is None or a in targets]
    if not remaining_targets:
        if not after:
            ok("设备上已无 Google 账号")
        else:
            ok("目标账号已移除，剩余账号: " + ", ".join(after))
        return True
    warn("仍有目标 Google 账号残留: " + ", ".join(remaining_targets))
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


class Session:
    def __init__(self, plans=None):
        self.plans = plans
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
        warn(f"Frida 会话断开: {reason}" + (f" ({crash})" if crash else ""))
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
            info(f"加载 Frida bridge: {payload.get('name')}")
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
                ok("收银台已弹出，去设备上确认即可（不会自动点订阅）")
            else:
                self.launch_failed.set()
                warn(f"launchBillingFlow 返回 code={code} {payload.get('msg', '')}")
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
                ok("支付成功 (purchase_cb code=0)")
            elif code == "1":
                warn("用户取消了支付 (purchase_cb code=1)")
            else:
                warn(f"支付回调返回 code={code} {payload.get('msg', '')}")

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
            warn("BillingClient 未在 20 秒内就绪，仍可尝试下单")
        else:
            ok("BillingClient 就绪")
        if self.plans:
            try:
                exp = self._exports()
                fn = getattr(exp, "set_plans", None) or getattr(exp, "setPlans", None)
                if callable(fn):
                    fn(json.dumps(self.plans, ensure_ascii=False))
            except Exception as e:
                warn(f"同步 plans.json 到 Frida 失败: {e}")

    def _exports(self):
        sc = self.script
        if hasattr(sc, "exports_sync"):
            return sc.exports_sync
        return sc.exports

    def buy(self, plan_id, stack=False, addon=""):
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
            ret = rpc(("stack",) if stack else ("buy",), plan_id)
            info(ret)
        except Exception as e:
            warn(f"rpc 调用失败，改用 post: {e}")
            self.script.post({
                "type": "cfg",
                "payload": {"target": plan_id, "stack": stack, "addon": addon},
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
            warn("当前 frida.js 不支持 checkOffers")
            return []
        try:
            fn()
        except Exception as e:
            warn(f"checkOffers 调用失败: {e}")
            return []
        if not self.offers_checked.wait(timeout=timeout):
            warn("查询 Offer 超时")
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
            print(f"  {pid:<16} {status:<8} {price:<24} {spec['sku']} ({spec['offer']})")
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
                    ok("检测到 Play Store 订阅记录更新，支付成功")
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
    print("附加套餐（会和主套餐一起提交）：")
    print("  0. 无")
    for i, pid in enumerate(yt, 1):
        print(f"  {i}. {pid}  {plans[pid]['note']}")
    raw = input("选择: ").strip() or "0"
    if raw in ("0", "n", "none", ""):
        return ""
    if raw in plans:
        return raw
    try:
        return yt[int(raw) - 1]
    except Exception:
        return ""


def parse_args():
    p = argparse.ArgumentParser(description="Google One 套餐注入")
    p.add_argument("-s", "--serial", default=os.environ.get("ANDROID_SERIAL") or "")
    p.add_argument("--plan", default="", help="直接下单套餐名或菜单编号，不进交互")
    p.add_argument("--stack-plans", default="",
                   help="连环叠加/tumpuk 套餐列表（逗号分隔），如 pro_12m,pro_6m_2,pro_6m")
    p.add_argument("--stack", "--renew", action="store_true",
                   help="续费叠加：在现有 Google One 订阅上叠加（frida.js STACK=true）")
    p.add_argument("--addon", default="", help="附加套餐 id，例如 yt_1m")
    p.add_argument("--wait", type=float, default=8.0, help="关闭自动删号时下单后等待秒数")
    p.add_argument("--wait-payment", type=float, default=300.0,
                   help="等待用户完成支付的超时秒数（默认 300）")
    p.add_argument("--auto-remove", action="store_true",
                   help="支付成功后自动移除设备上的 Google 账号（默认关闭，需手动开启）")
    p.add_argument("--loop", action="store_true",
                   help="按顺序循环处理设备上所有已登录的 Google 账号（已订阅直接删号跳下一个）")
    p.add_argument("--check-offers", action="store_true",
                   help="仅检查所有套餐 Offer 的可用性与价格，不弹出收银台")
    p.add_argument("--log-file", default="",
                   help="将每次操作结果写入指定 JSONL 日志文件")
    p.add_argument("--remove-accounts", action="store_true",
                   help="直接移除设备上的所有 Google 账号并退出")
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
        fail(f"找不到 {JS_PATH}")
        sys.exit(1)
    plans, groups_cfg = load_catalog()
    if not plans:
        fail("未能加载套餐列表 (plans.json / frida.js)")
        sys.exit(1)

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
        info(f"等待设备端完成支付（超时 {int(args.wait_payment)}s，Ctrl+C 可取消等待）...")
        code = sess.wait_for_purchase(timeout=args.wait_payment, account=account, baseline=baseline)
        action = "kept"
        if code == "0":
            time.sleep(1.5)
            remove_google_accounts(sess.device, target_emails=target_emails)
            action = "paid_and_removed"
        elif code == "timeout":
            warn("等待支付超时，未移除 Google 账号")
            action = "timeout"
        elif code == "detached":
            warn("Google One 进程已断开，未移除 Google 账号")
            action = "detached"
        elif code is not None:
            info(f"未完成支付 (code={code})，保留 Google 账号")
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
            info(f"叠加步骤 [{idx}/{total_steps}] 账号={account}  套餐={pid_name}  模式={'叠加(mode=5)' if use_stack else '首单新购'}")
            info(spec["note"])
            baseline = db_sub_snapshot(account)
            sess.buy(pid_name, stack=use_stack, addon=addon)
            info(f"等待 {account} 完成第 {idx}/{total_steps} 笔支付（超时 {int(args.wait_payment)}s，Ctrl+C 可中止）...")
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
            warn(f"第 {idx}/{total_steps} 笔 ({pid_name}) 未完成支付 (code={code})")
            audit.write(
                account=account, plan=pid_name, sku=spec["sku"], offer=spec["offer"],
                stack=use_stack, pd_code=sess.pd_code, launch_code=sess.launch_code,
                purchase_code=code, step=f"{idx}/{total_steps}", action=f"stack_step_stopped_{code}",
            )
            if idx < total_steps:
                try:
                    ans = input("  [n] 跳过当前套餐继续叠加下一个 / [q] 结束当前账号叠加: ").strip().lower()
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
            warn("设备上没有 Google 账号")
            return initial_sess
        print_preflight(initial)
        info(f"开始循环处理 {total} 个 Google 账号，套餐: {', '.join(plan_ids)}")
        sess = initial_sess
        step = 0
        while True:
            accts = google_accounts()
            if not accts:
                ok("全部 Google 账号处理完毕")
                break
            step += 1
            current = accts[0]
            print()
            print("=" * 64)
            info(f"进度 [{step}/{max(total, step)}] 当前账号: {current}  (剩余 {len(accts)} 个)")
            if not is_chain and not stack and db_has_g1_sub(current):
                warn(f"{current} 在本地记录中已订阅 Google One -> 直接移除并切下一个")
                if sess:
                    sess.close()
                    sess = None
                audit.write(account=current, plan=single_pid, sku=spec["sku"], offer=spec["offer"], stack=stack, action="skipped_db_sub_removed")
                remove_and_advance(None, current)
                continue
            if sess is None:
                sess = Session(plans=plans)
                pid = start_google_one()
                sess.attach(pid)
            if not is_chain and not stack and sess.check_subs():
                warn(f"{current} 检测到已有 Google One 订阅 -> 直接移除并切下一个")
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
                    warn(f"{current} 连环叠加未完成任何支付，未自动删号")
                else:
                    warn(f"{current} 连环叠加部分完成 ({paid_count}/{len(plan_ids)})，未自动删号")
            else:
                info(f"{'续费叠加' if stack else '新购'} {single_pid}  sku={spec['sku']}  offer={spec['offer']}")
                info(spec["note"])
                baseline = db_sub_snapshot(current)
                sess.buy(single_pid, stack=stack, addon=addon)
                info(f"等待 {current} 完成支付（超时 {int(args.wait_payment)}s，Ctrl+C 可中止）...")
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
                warn(f"{current} 未完成支付 (code={code})，未自动删号")
            try:
                ans = input("  [r] 移除该账号并继续下一个 / [q] 停止循环: ").strip().lower()
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
        sess = Session(plans=plans)
        pid = start_google_one()
        sess.attach(pid)
        items = sess.check_offers(plans)
        audit.write(account=",".join(google_accounts()), action="check_offers", offers_found=len(items))
        sess.close()
        return

    stack_chain = []
    if args.stack_plans:
        stack_chain, bad_item = resolve_plan_chain(args.stack_plans, plans, index)
        if not stack_chain:
            fail(f"--stack-plans 包含无效套餐: {bad_item!r}")
            sys.exit(1)

    if args.loop:
        if stack_chain:
            loop_plans = stack_chain
        else:
            plan_id = resolve_plan(args.plan, plans, index)
            if not plan_id:
                fail("--loop 需要配合有效的 --plan 或 --stack-plans 参数使用")
                sys.exit(1)
            loop_plans = [plan_id]
        sess = None
        try:
            sess = run_loop(loop_plans)
        except KeyboardInterrupt:
            print()
            warn("循环已中断")
        finally:
            if sess:
                sess.close()
        return

    sess = Session(plans=plans)
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
            warn("已取消连环叠加")
        sess.close()
        return

    if args.plan:
        plan_id = resolve_plan(args.plan, plans, index)
        if not plan_id:
            fail(f"未知套餐 {args.plan!r}")
            sess.close()
            sys.exit(1)
        spec = plans[plan_id]
        acct = (google_accounts() or [""])[0]
        baseline = db_sub_snapshot(acct)
        info(f"{'续费叠加' if stack else '新购'} {plan_id}  sku={spec['sku']}  offer={spec['offer']}")
        info(spec["note"])
        sess.buy(plan_id, stack=stack, addon=addon)
        try:
            handle_post_buy(sess, plan_id, spec, account=acct, baseline=baseline)
        except KeyboardInterrupt:
            print()
            warn("已取消等待支付")
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
                ok("续费叠加: 开（会叠到现有订阅上）" if stack else "续费叠加: 关（新购）")
                continue
            if low == "a":
                addon = pick_addon(plans)
                ok(f"附加套餐: {addon or '无'}")
                continue
            if low == "r":
                auto_remove = not auto_remove
                ok("支付后自动删号: 开" if auto_remove else "支付后自动删号: 关")
                continue
            if low == "o":
                if sess is None:
                    sess = Session(plans=plans)
                    pid = start_google_one()
                    sess.attach(pid)
                sess.check_offers(plans)
                continue
            if low == "t":
                try:
                    raw_chain = input("输入要连环叠加的套餐（逗号分隔，例如 pro_12m,pro_6m_2,pro_6m）: ").strip()
                except EOFError:
                    continue
                chain, bad_item = resolve_plan_chain(raw_chain, plans, index)
                if not chain:
                    warn(f"无法识别套餐: {bad_item or raw_chain}")
                    continue
                if sess is None:
                    sess = Session(plans=plans)
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
                    warn("已取消连环叠加，返回菜单")
                continue
            if low == "p":
                try:
                    raw_plan = input("循环使用的套餐或连环叠加套餐 (例如 pro_12m 或 pro_12m,pro_6m_2): ").strip()
                except EOFError:
                    continue
                loop_plans, bad_item = resolve_plan_chain(raw_plan, plans, index)
                if not loop_plans:
                    warn(f"无法识别套餐: {bad_item or raw_plan}")
                    continue
                sess = run_loop(loop_plans, initial_sess=sess)
                if sess is None and google_accounts():
                    sess = Session(plans=plans)
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
                sess = Session(plans=plans)
                sess.attach(pid)
                continue
            plan_id = index.get(raw) or index.get(low)
            if not plan_id:
                warn("无法识别输入。输入编号、套餐名，或 q 退出。")
                continue
            if sess is None:
                sess = Session(plans=plans)
                pid = start_google_one()
                sess.attach(pid)
            spec = plans[plan_id]
            acct = (google_accounts() or [""])[0]
            baseline = db_sub_snapshot(acct)
            info(f"{'续费叠加' if stack else '新购'} {plan_id}  sku={spec['sku']}  offer={spec['offer']}")
            info(spec["note"])
            sess.buy(plan_id, stack=stack, addon=addon)
            if auto_remove:
                try:
                    handle_post_buy(sess, plan_id, spec, account=acct, baseline=baseline)
                except KeyboardInterrupt:
                    print()
                    warn("已取消等待支付，返回菜单")
            else:
                time.sleep(1.2)
    except KeyboardInterrupt:
        print()
        warn("已中断")
    finally:
        if sess:
            sess.close()
        info("结束")


if __name__ == "__main__":
    main()
