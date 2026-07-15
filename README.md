# Player

Player is a SYM music player with a browser-based library manager, playlists, playback controls, and an optional compact `/mini-sym` status view.

## Install in SYM

Install **Player** from the SYM-OS App Store. SYM-Node owns installation, port allocation, start/restart, and the public app URL. Do not manually start a second server or configure a fixed port.

After installation:

1. Open Player in SYM-OS.
2. Open `/manage` for the full library manager.
3. Use **Upload**, **Folder**, or drag and drop to add music.
4. Use `/` for the device-style player controls.

Player accepts MP3, M4A, AAC, OGG, WAV, and FLAC audio. Album artwork can be uploaded as JPG, PNG, or WebP.

## App-owned storage

Player keeps uploaded music and runtime state inside its own app folder:

```text
$HOME/project_files/Apps/player-python/.sym-data/
```

The managed data layout is:

```text
.sym-data/
├── library/       # Uploaded music and album artwork
├── playlists/     # Saved playlists
├── assets/        # Generated/runtime artwork
└── state.json     # Player state
```

The app creates these directories automatically. Upload music through `/manage`; do not copy files into another profile, `/Users`, `/home/samos`, or a shared top-level library.

The manifest declares `.sym-data` as persistent app data so it remains app-scoped across managed restarts and updates.

## Managed runtime contract

Player:

- reads its assigned port from `PORT`;
- reads `HOST` when supplied by SYM-Node;
- defaults storage to `<app-root>/.sym-data`;
- exposes `GET /_sym/health` for managed health checks;
- exposes `GET /mini-sym` for the compact Sym Browser viewer;
- starts through the committed `package.json` and `package-lock.json` contract.

The npm start command launches the Python standard-library server. The core web app has no third-party Python package dependency.

## Optional hardware audio

Browser playback works without a host audio backend. On hardware that has `mpv` installed, Player can also use `mpv` for host audio output. OLED/LCD support is optional and depends on the target device.

These hardware integrations are not required for installation from the SYM App Store.

## Development checks

Run finite checks only; do not leave a manual server running beside the SYM-managed instance.

```bash
npm ci
python3 -m compileall -q player
```

For managed start or restart, use SYM-Node's profile-scoped app lifecycle action.
