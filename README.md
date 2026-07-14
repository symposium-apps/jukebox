# Player

Version: 0.1.0

Player is the first practical module for the suit: a Raspberry Pi hosted MP3 player with a WiFi control panel, local music storage, playlists, OLED/LCD status, and hardware-control headroom.

## What 0.1.0 Does

- Hosts a control panel on the Raspberry Pi.
- Uploads MP3 files into a local `library/` folder.
- Scans the local music library.
- Plays audio through the Pi using `mpv`.
- Supports play, pause, stop, next, previous, and volume.
- Saves simple playlists as JSON files.

## Run Locally

```powershell
cd D:\Apps\Player
python -m player.server --host 127.0.0.1 --port 8010
```

Open:

```text
http://127.0.0.1:8010
```

The device-style LCD controller lives at `/`. The full library manager for uploads,
playlists, and bench testing lives at `/manage`.

## Run On Windows As Player Desktop

Double-click:

```text
Player Desktop.cmd
```

This starts Player on `http://127.0.0.1:8020/manage` using:

```text
C:\Users\<you>\Downloads\Player
```

as the local music library.

Player stores playlists, generated cover art, and desktop state inside:

```text
C:\Users\<you>\Downloads\Player\.player
```

That keeps the Windows player library in one portable folder: albums plus Player metadata.

The desktop launcher starts the Python server and opens `/manage` in your default
browser. Audio plays in the browser itself (via the server's `/media/` route), so no
extra runtime is needed on Windows.

## Raspberry Pi Audio Backend

Install `mpv` on the Pi:

```bash
sudo apt update
sudo apt install -y mpv
```

Then run:

```bash
cd /home/samos/apps/Player
python3 -m player.server --host 0.0.0.0 --port 8010
```

## Systemd Service

Copy `scripts/player.service` to:

```bash
sudo cp scripts/player.service /etc/systemd/system/player.service
sudo systemctl daemon-reload
sudo systemctl enable --now player.service
```

Then open:

```text
http://<pi-ip>:8010
```

## Deploy From Windows

From this folder:

```powershell
$env:BUGGY_PASS='your-pi-password'
python .\scripts\deploy_to_pi.py --host 192.168.0.153 --install-service --install-mpv --install-display-deps
```

The deploy script uploads code only. It leaves the Pi's `library/` and `playlists/` folders alone.
