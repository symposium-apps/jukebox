#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

LABEL = "com.samoslabs.jukebox-import-companion"
PORT = 47321


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def tailscale_address() -> str:
    executable = shutil.which("tailscale")
    if not executable:
        raise RuntimeError("Tailscale is required")
    addresses = [item.strip() for item in run(executable, "ip", "-4").stdout.splitlines() if item.strip()]
    if len(addresses) != 1:
        raise RuntimeError("Exactly one Tailscale IPv4 address is required")
    return addresses[0]


def stop_existing(pid_file: Path, source_dir: Path, venv: Path) -> None:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        command = subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", "command="], text=True, timeout=5).strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pid_file.unlink(missing_ok=True)
        return
    if str(source_dir / "server.py") not in command or str(venv / "bin" / "python") not in command:
        raise RuntimeError("Refusing to stop an unrecognized process from the companion PID file")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        subprocess.run(["/bin/sleep", "0.1"], check=False)
    pid_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the Jukebox accountless import companion for macOS")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    source_dir = Path(__file__).resolve().parent
    support = Path.home() / "Library" / "Application Support" / "Samos Labs" / "Jukebox Import Companion"
    cache = Path.home() / "Library" / "Caches" / "Samos Labs" / "Jukebox Import Companion"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / f"{LABEL}.plist"
    venv = support / ".venv"
    token_file = support / "token"
    log_file = support / "companion.log"
    app_bundle = support / "Jukebox Import Companion.app"
    pid_file = support / "companion.pid"
    app_contents = app_bundle / "Contents"
    app_macos = app_contents / "MacOS"
    app_executable = app_macos / "JukeboxImportCompanion"

    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    launch_agents.mkdir(parents=True, exist_ok=True)
    support.chmod(0o700)
    cache.chmod(0o700)

    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    token_file.chmod(0o600)

    stop_existing(pid_file, source_dir, venv)
    if venv.exists():
        shutil.rmtree(venv)
    run(args.python, "-m", "venv", str(venv))
    run(str(venv / "bin" / "pip"), "install", "--disable-pip-version-check", "-q", "-U", "-r", str(source_dir / "requirements.txt"))

    address = tailscale_address()
    app_macos.mkdir(parents=True, exist_ok=True)
    app_info = {
        "CFBundleDisplayName": "Jukebox Import Companion",
        "CFBundleExecutable": "JukeboxImportCompanion",
        "CFBundleIdentifier": LABEL,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Jukebox Import Companion",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1.3.1",
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
    }
    with (app_contents / "Info.plist").open("wb") as stream:
        plistlib.dump(app_info, stream, sort_keys=True)
    executable_text = "\n".join([
        "#!/bin/zsh",
        f"export JUKEBOX_COMPANION_TOKEN_FILE={str(token_file)!r}",
        f"export JUKEBOX_COMPANION_PID_FILE={str(pid_file)!r}",
        "export JUKEBOX_COMPANION_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'",
        f"export PATH='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}'",
        f"exec {str(venv / 'bin' / 'python')!r} {str(source_dir / 'server.py')!r} --host 127.0.0.1 --port {PORT} --state-dir {str(cache)!r} >> {str(log_file)!r} 2>&1",
        "",
    ])
    app_executable.write_text(executable_text, encoding="utf-8")
    app_executable.chmod(0o700)
    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", "-g", str(app_bundle)],
        "RunAtLoad": True,
        "StartInterval": 60,
        "KeepAlive": False,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
    }
    temporary = plist_path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        plistlib.dump(plist, stream, sort_keys=True)
    os.replace(temporary, plist_path)
    plist_path.chmod(0o600)

    domain = f"gui/{os.getuid()}"
    run("launchctl", "bootout", domain, str(plist_path), check=False)
    run("launchctl", "bootstrap", domain, str(plist_path))
    run("launchctl", "enable", f"{domain}/{LABEL}")
    run(shutil.which("tailscale") or "tailscale", "serve", "--bg", f"--tcp={PORT}", f"tcp://127.0.0.1:{PORT}")
    print(f"installed {LABEL} at {address}:{PORT}")


if __name__ == "__main__":
    main()
