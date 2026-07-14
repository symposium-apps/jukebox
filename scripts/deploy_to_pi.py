from __future__ import annotations

import argparse
import os
import posixpath
import shlex
from pathlib import Path

import paramiko


EXCLUDE_DIRS = {"__pycache__", ".git", ".venv"}
EXCLUDE_FILES = {".gitkeep", "state.json"}
DEFAULT_SKIP_CONTENT_DIRS = {"library", "playlists"}


def should_skip_file(path: Path, root: Path, skip_content_dirs: set[str]) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if path.name in EXCLUDE_FILES:
        return True
    if rel.parts and rel.parts[0] in skip_content_dirs:
        return True
    return False


def remote_mkdir(sftp: paramiko.SFTPClient, path: str) -> None:
    parts = [part for part in path.split("/") if part]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def upload_tree(
    sftp: paramiko.SFTPClient,
    local_root: Path,
    remote_root: str,
    skip_content_dirs: set[str],
) -> int:
    count = 0
    remote_mkdir(sftp, remote_root)
    remote_mkdir(sftp, posixpath.join(remote_root, "library"))
    remote_mkdir(sftp, posixpath.join(remote_root, "playlists"))

    for path in local_root.rglob("*"):
        if path.is_dir():
            if path.name in EXCLUDE_DIRS:
                continue
            rel = path.relative_to(local_root).as_posix()
            if rel.split("/")[0] in skip_content_dirs:
                continue
            remote_mkdir(sftp, posixpath.join(remote_root, rel))
            continue
        if should_skip_file(path, local_root, skip_content_dirs):
            continue
        rel = path.relative_to(local_root).as_posix()
        remote_path = posixpath.join(remote_root, rel)
        remote_mkdir(sftp, posixpath.dirname(remote_path))
        sftp.put(str(path), remote_path)
        count += 1
    return count


def run(client: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Player to a Raspberry Pi")
    parser.add_argument("--host", default="192.168.0.153")
    parser.add_argument("--user", default="samos")
    parser.add_argument("--password-env", default="BUGGY_PASS")
    parser.add_argument("--remote-root", default="/home/samos/apps/Player")
    parser.add_argument("--install-service", action="store_true")
    parser.add_argument("--install-mpv", action="store_true")
    parser.add_argument("--install-display-deps", action="store_true")
    parser.add_argument("--install-metadata-deps", action="store_true")
    parser.add_argument("--include-library", action="store_true", help="Upload local library/ music files to the Pi")
    parser.add_argument("--include-playlists", action="store_true", help="Upload local playlists/ files to the Pi")
    args = parser.parse_args()

    password = os.environ.get(args.password_env) or os.environ.get("PLAYER_PASS")
    if not password:
        raise SystemExit(f"Set ${args.password_env} or $PLAYER_PASS first")

    local_root = Path(__file__).resolve().parents[1]
    skip_content_dirs = set(DEFAULT_SKIP_CONTENT_DIRS)
    if args.include_library:
        skip_content_dirs.discard("library")
    if args.include_playlists:
        skip_content_dirs.discard("playlists")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, password=password, timeout=12, look_for_keys=False, allow_agent=False)

    try:
        with client.open_sftp() as sftp:
            count = upload_tree(sftp, local_root, args.remote_root, skip_content_dirs)
        print(f"Uploaded {count} files to {args.host}:{args.remote_root}")

        sudo_prefix = f"printf '%s\\n' {shlex.quote(password)} | sudo -S -p ''"
        commands = [
            f"cd {shlex.quote(args.remote_root)} && python3 -m py_compile player/*.py",
        ]
        if args.install_mpv:
            commands.append(f"{sudo_prefix} apt update && {sudo_prefix} apt install -y mpv")
        if args.install_display_deps:
            commands.append(f"{sudo_prefix} apt update && {sudo_prefix} apt install -y python3-pil python3-smbus i2c-tools python3-spidev python3-gpiozero python3-lgpio")
        if args.install_metadata_deps:
            commands.append(f"{sudo_prefix} apt update && {sudo_prefix} apt install -y python3-mutagen")
        if args.install_service:
            commands.extend(
                [
                    f"{sudo_prefix} cp {shlex.quote(args.remote_root)}/scripts/player.service /etc/systemd/system/player.service",
                    f"{sudo_prefix} systemctl daemon-reload",
                    f"{sudo_prefix} systemctl enable player.service",
                ]
            )
        commands.extend(
            [
                f"{sudo_prefix} systemctl restart player.service || true",
                "systemctl is-active player.service || true",
            ]
        )
        code, out, err = run(client, " && ".join(commands), timeout=240)
        print(out.strip())
        if err.strip():
            print(err.strip())
        if code != 0:
            raise SystemExit(code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
