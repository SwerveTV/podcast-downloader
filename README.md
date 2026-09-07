# Collaborative YouTube Podcast Downloader

`podcast-download` downloads recent full podcast episodes from YouTube playlist URLs listed in a shared spreadsheet. It is designed for multiple operators working from the same assignment sheet while keeping each show exclusive through per-show lock files and per-show download archives.

The tool reads `.csv`, `.xlsx`, or public Google Sheets URLs, filters jobs by `Operator`, selects the newest eligible episodes by upload/release metadata, downloads media with an external `yt-dlp` executable, and writes standardized per-show and master CSV manifests for ingest.

## Architecture

```text
podcast-downloader/
├── src/podcast_downloader/
│   ├── cli.py             # argparse CLI
│   ├── spreadsheet.py     # CSV/XLSX/Google Sheets parsing
│   ├── validation.py      # sheet and playlist validation
│   ├── episodes.py        # eligibility filters and newest-first selection
│   ├── ytdlp.py           # safe subprocess integration
│   ├── locks.py           # exclusive show locks
│   ├── runner.py          # download workflow
│   └── manifest.py        # per-show and master CSV output
├── tests/
├── examples/
└── config.example.yaml
```

Runtime output is organized as:

```text
Podcast_Ingest/
├── 01_Downloading/
│   ├── Trey/
│   ├── Operator_2/
│   └── Operator_3/
├── 02_Ready_for_QC/
├── 03_Ready_for_Ingest/
├── 04_Ingested/
├── archives/
├── logs/
└── manifests/
```

Each show downloads under the current operator in `01_Downloading`. After all selected episodes finish successfully, the show folder is moved into `02_Ready_for_QC`. Partial failures stay in the operator working folder.

## macOS Setup

Use Python 3.11 or newer:

```bash
cd /Users/treyconnet/Dev/VS-Code-Projects/podcast-downloader
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Install external media tools with Homebrew:

```bash
brew install yt-dlp ffmpeg
```

To locate Homebrew `yt-dlp` without changing global PATH order:

```bash
brew --prefix yt-dlp
$(brew --prefix yt-dlp)/bin/yt-dlp --version
```

Then pass it explicitly:

```bash
podcast-download run \
  --input "/path/to/shows.xlsx" \
  --operator "Trey" \
  --output "/path/to/Podcast_Ingest" \
  --yt-dlp-path "$(brew --prefix yt-dlp)/bin/yt-dlp"
```

`ffmpeg` still needs to be discoverable by name. If Homebrew is not first in PATH, launch the command with the Homebrew bin directory added for that process:

```bash
PATH="$(brew --prefix ffmpeg)/bin:$PATH" podcast-download run \
  --input "/path/to/shows.xlsx" \
  --operator "Trey" \
  --output "/path/to/Podcast_Ingest" \
  --yt-dlp-path "$(brew --prefix yt-dlp)/bin/yt-dlp"
```

## Spreadsheet Columns

Required for a valid job:

```text
Show Name
Full Episode Playlist
```

Current supported input columns:

```text
Network / Distributor
Show Name
Host(s)
Number of Episodes
Subscriber Count
Full Episode Playlist
Operator
Status
Videos Found
Videos Downloaded
Download Folder
CSV/Manifest
Ingest Status
Last Updated
Notes
```

The parser tolerates blank rows, network section headers, extra unnamed columns, inconsistent spacing, and missing optional values. Rows with a show name but no playlist are validation warnings and are not processed.

Public Google Sheets are read without Google API credentials by using the CSV export URL. The sheet is read-only; operators update assignment/status columns manually.

## Collaborative Workflow

1. Add or update show rows in the shared sheet.
2. Assign exactly one operator per show in the `Operator` column.
3. Each operator validates and lists only their jobs.
4. Each operator runs downloads into the shared `Podcast_Ingest` root.
5. Completed show folders move to `02_Ready_for_QC`.
6. Operators use the downloader CSV metadata to fill out the online master tracker.
7. Operators export the official ingest manifest from the tracker or its separate manifest tool.
8. `Ingest-Sync` runs normally from that official ingest manifest.
9. After QC/ingest stages move folders forward manually, run downloader consolidation when needed.

Add a new operator by entering their name in `Operator` for one or more rows. Matching is case-insensitive and whitespace-tolerant.

Add a new show by entering `Show Name` and `Full Episode Playlist`; optional metadata columns can be filled later.

## Commands

Validate a local spreadsheet:

```bash
podcast-download validate --input "/path/to/shows.xlsx"
```

Validate a public Google Sheet:

```bash
podcast-download validate --input "https://docs.google.com/spreadsheets/d/SHEET_ID/edit?gid=0"
```

List Trey’s assigned jobs:

```bash
podcast-download list --input "/path/to/shows.xlsx" --operator "Trey"
```

Dry-run planned work without downloading:

```bash
podcast-download run \
  --input "/path/to/shows.xlsx" \
  --operator "Trey" \
  --output "/path/to/Podcast_Ingest" \
  --dry-run
```

Download:

```bash
podcast-download run \
  --input "/path/to/shows.xlsx" \
  --operator "Trey" \
  --output "/path/to/Podcast_Ingest" \
  --yt-dlp-path "$(brew --prefix yt-dlp)/bin/yt-dlp" \
  --progress auto
```

Progress modes:

```text
auto   Rich progress bars in an interactive terminal, plain progress elsewhere
rich   Force Rich progress bars
plain  Print readable progress lines to stderr
none   Disable live progress
```

Metadata discovery is cached per show after the first successful discovery pass. Reruns still refresh the flat playlist listing, but if the scanned candidate IDs are unchanged, detailed per-video metadata is loaded from:

```text
Network/Show Name/metadata-cache/playlist-candidates.json
```

Force a fresh metadata pass:

```bash
podcast-download --config config.yaml run --refresh-metadata
```

Disable metadata caching:

```bash
podcast-download --config config.yaml run --no-metadata-cache
```

Retry failed or incomplete episodes by running the same command again. Successful video IDs are skipped through the per-show `download-archive.txt`; `.part` files are preserved for resumption.

Create a deterministic master manifest:

```bash
podcast-download consolidate --output "/path/to/Podcast_Ingest"
```

## Handoff To Ingest-Sync

`podcast-downloader` and `Ingest-Sync` intentionally have different responsibilities.

`podcast-downloader` is the acquisition tool. It downloads YouTube podcast assets, saves full `.info.json` metadata, writes per-show CSVs, and creates a downloader `master_manifest.csv`. That manifest is for review, QC, metadata copy/paste, audit, and filling out the online master tracker.

`Ingest-Sync` remains the delivery tool. It should consume the official ingest manifest exported from the online tracker or the separate manifest-generation tool, not a converted downloader manifest.

Recommended handoff:

```bash
podcast-download --config default.yaml run
podcast-download --config default.yaml consolidate
```

Then:

1. Open the per-show CSV or `Podcast_Ingest/manifests/master_manifest.csv`.
2. Fill the online master tracker using the downloaded metadata.
3. Export or download the official manifest expected by `Ingest-Sync`.
4. In `Ingest-Sync/config.yaml`, set `manifest_path` to that official manifest.
5. Add the downloader QC folder to `local_source_roots` so `Ingest-Sync` can find the downloaded files by basename.

Example `Ingest-Sync/config.yaml` values:

```yaml
manifest_path: "/path/to/downloaded/official-ingest-manifest.xlsx"
local_source_roots:
  - "/path/to/Podcast_Ingest/02_Ready_for_QC"
local_root: "/path/to/final/local/root"
```

Do not use the downloader CSV as `Ingest-Sync`'s `manifest_path` unless `Ingest-Sync` is explicitly changed to support that format. The downloader CSV contains acquisition metadata; the ingest manifest contains delivery filenames, GUIDs, target directories, and platform-specific ingest decisions.

Use a config file:

```bash
podcast-download --config config.yaml run
```

CLI flags override YAML values.

You can also put the spreadsheet, operator, output root, yt-dlp path, and progress default in YAML:

```yaml
input: "/path/to/shows.xlsx"
operator: "Trey"
output_root: "/path/to/Podcast_Ingest"
yt_dlp_path: "/opt/homebrew/bin/yt-dlp"
progress: "auto"
metadata_cache_enabled: true
metadata_cache_ttl_seconds: 86400
refresh_metadata: false
```

Then run:

```bash
podcast-download --config config.yaml run
```

Override any default on the command line:

```bash
podcast-download --config config.yaml run --operator "Operator 2" --progress plain
```

## Episode Selection

The default is the 10 newest eligible videos per playlist. Discovery is intentionally two-phase:

1. `yt-dlp --flat-playlist` scans only the configured recent depth.
2. Detailed metadata is fetched only for those recent entries.

This avoids scanning thousands of full metadata records. The tradeoff is that absolute chronological certainty depends on `playlist_scan_depth`; if a playlist is heavily reordered or contains many non-episodes near the top, increase the depth.

Eligibility defaults:

```text
episode_count: 10
playlist_scan_depth: 50
minimum_duration_seconds: 1200
exclude_shorts: true
exclude_livestreams: true
```

Deleted, private, unavailable, upcoming, and active livestream items are rejected. Shorts, clips, trailers, and unusually short videos are filtered conservatively. Rejections are logged with reasons.

Disable the duration filter:

```bash
podcast-download run ... --no-minimum-duration
```

Include Shorts or livestreams:

```bash
podcast-download run ... --include-shorts --include-livestreams
```

## Output Files

Show directory example:

```text
Network/
└── Show Name/
    ├── media/
    ├── thumbnails/
    ├── metadata-json/
    ├── descriptions/
    ├── Show Name.csv
    └── download-archive.txt
```

Episode filenames resemble:

```text
YYYY-MM-DD - Episode Title [VIDEO_ID].mp4
```

The YouTube video ID is always included for uniqueness. Unicode is preserved while unsafe filesystem characters are replaced and filename length is capped.

The `.info.json` file is the canonical full-fidelity metadata record. CSV manifests flatten stable fields for ingest, including playlist, channel, upload/release dates, timestamps, duration, counts, categories, tags, format information, relative paths, status, errors, timestamps, and tool version. Lists are serialized as JSON strings inside properly quoted UTF-8 CSV fields.

## Configuration Reference

See `config.example.yaml`.

```yaml
input: "/path/to/shows.xlsx"
operator: "Trey"
episode_count: 10
playlist_scan_depth: 50
minimum_duration_seconds: 1200
exclude_shorts: true
exclude_livestreams: true
yt_dlp_path: "/opt/homebrew/bin/yt-dlp"
format_selector: "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
output_root: "/path/to/Podcast_Ingest"
lock_timeout_seconds: 21600
retry_count: 3
filename_max_length: 180
convert_thumbnail_to_jpg: false
cookie_browser: null
cookie_file: null
sleep_interval_seconds: 0.0
clear_stale_locks: false
progress: "auto"
```

No real cookies or credentials should be committed. Cookie support is disabled by default.

## Locks

Before a show is processed, the tool creates `.download.lock` in that show’s active folder. The lock contains:

```json
{
  "operator": "Trey",
  "hostname": "machine-name",
  "pid": 12345,
  "timestamp": "2026-09-07T19:00:00+00:00"
}
```

If another active lock exists, processing is refused. Stale locks are detected by `lock_timeout_seconds`, but they are not replaced unless explicitly requested:

```bash
podcast-download run ... --clear-stale-locks
```

Locks are removed only by the process that owns them. Ctrl+C preserves partial downloads and does not mark a show complete.

## Download Archive Behavior

Each show has its own `download-archive.txt`. A global archive is intentionally avoided because multiple operators could write to it concurrently. Reruns are idempotent: archived video IDs are skipped, and `yt-dlp` is invoked with no-overwrite and resume-friendly options.

## Metadata Cache

Detailed YouTube metadata lookup is the slowest part of most reruns. The downloader caches the scanned candidate metadata inside each active show folder. The cache is reused only when all of these match:

```text
playlist ID
playlist scan depth
ordered candidate video IDs from the flat playlist scan
metadata_cache_ttl_seconds
```

This keeps retries fast without blindly trusting stale playlist state. Set `metadata_cache_ttl_seconds: 0` to keep cache entries valid until the playlist candidate list changes. Use `--refresh-metadata` after changing filters or when you need fresh title/date/count metadata.

## Testing

Run local checks:

```bash
ruff format .
ruff check .
mypy src
pytest
```

The automated tests mock `yt-dlp` and do not download YouTube media.

Opt-in metadata smoke test against a user-supplied playlist:

```bash
"$(brew --prefix yt-dlp)/bin/yt-dlp" \
  --flat-playlist \
  --dump-single-json \
  --playlist-end 3 \
  "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID" \
  > /tmp/podcast-downloader-smoke.json
```

This inspects playlist metadata only and does not download video.

## Troubleshooting

`Missing yt-dlp`: install it or pass `--yt-dlp-path`.

`Missing ffmpeg`: install `ffmpeg` and make it discoverable for the process.

`Private or unavailable playlist`: confirm the playlist is public or provide an approved cookie file/browser source locally.

`Private video` during candidate metadata lookup: the video is skipped and recorded in `rejected-episodes.json`. Private playlist failures remain fatal.

`Network failure` or `rate limiting`: rerun later, increase `sleep_interval_seconds`, or reduce concurrent operator activity.

`QC destination already exists`: another completed copy of the show is already present. Move, archive, or inspect it before retrying.

`Stale lock exists`: verify no operator is still running that show, then rerun with `--clear-stale-locks`.

`Insufficient disk space`: free space in the output root and rerun. Partial `.part` files remain resumable.

## Privacy

Do not commit cookies, browser profiles, authorization headers, exported private sheets, or downloaded media. Logs redact common cookie/auth markers, but operators should still treat logs as operational records and avoid sharing them broadly.

## Known Limitations

This version does not include a database, web UI, Google Sheets writeback, Google API credentials, Docker, cloud services, or automatic assignment. Operators update the shared spreadsheet manually. Playlist chronology is based on the configured scan depth plus YouTube metadata returned by `yt-dlp`.
