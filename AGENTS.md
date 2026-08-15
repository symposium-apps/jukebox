# Jukebox agent contract

Jukebox exposes a profile-scoped music library through authenticated REST and MCP interfaces.

## Required start

1. Call `jukebox_get_context` through the installed app's local `/mcp`, or read `GET /api/agent/bootstrap`.
2. The password is owned by the installed instance at `UserData/Jukebox API/password.txt`. Never ask Jukebox to display or return its value.
3. If the file is absent or empty, remote REST and MCP are intentionally disabled and return `401 Unauthorized`.
4. Never put the password in a URL, query string, chat message, log, activity event, report, or committed file. Send it only in the `Authorization: Bearer …` header.

## Upload contract

- Upload audio and artwork as raw streaming file bodies with `PUT /api/v1/files/{relative-library-path}`.
- Preserve album folders. Place `cover.jpg`, `folder.png`, or another supported artwork file beside the album's tracks.
- Use `conflict=error|skip|replace|rename`; default is `error`.
- For a batch, upload files without rescanning each one, then call `POST /api/v1/library/rescan` once.
- Do not base64-encode large audio into MCP JSON. Use MCP for discovery/management and REST for bytes.

## Link import contract

- Inspect public YouTube or YouTube Music links with `POST /api/v1/imports/inspect`, then start selected MP3 items with `POST /api/v1/imports/jobs`.
- Treat the returned `inspection_id` and item IDs as the server-owned selection boundary; never invent extractor URLs or write directly into an installed release.
- MP4 is intentionally `coming_next` until Jukebox has a first-class video library and player. Do not bypass that gate by placing unindexed MP4 files in UserData.
- Imports run asynchronously in private app state and atomically place completed tagged MP3/artwork into UserData. Poll the job API instead of starting a second downloader process.
- Never provide browser cookies or account credentials unless a future explicit credential design is approved. Private, age-gated, region-restricted, or account-only items may remain unavailable.

## Managed app boundary

The App Store release is immutable. Write music, artwork, playlists, and the optional password only inside declared `UserData`. Never edit an installed managed release in place. Use the app-local API/MCP so the UI and generated artwork stay synchronized.
