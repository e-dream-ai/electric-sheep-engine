# AGENTS.md — electric-sheep-engine

## Overview

Python utility for managing playlists of AI-generated Electric Sheep content. Synchronizes content directories, handles video metadata, generates keyframes and thumbnails.

## Stack

- **Language:** Python 3
- **Dependencies:** python-dotenv, edream_sdk
- **Tools:** rsync, FFmpeg (for keyframes/thumbnails)

## Project Structure

```
engine.py              # Playlist management: sync, keyframe, wanderlust, singularities, report
repair_keyframes.py    # One-off repair of keyframes garbled by add_keyframes.py
keyframe_dash.py       # Keyframe dashboard
thumbs.py              # Thumbnail generation
graph.py               # Dream content analysis
merge_playlists.py     # Playlist management
copy_mp4_by_*.py       # Content utilities
extract_uuid_pairs.py  # UUID extraction
```

edream_sdk is a pip dependency (python-api `main`), not vendored. Don't commit
a checkout of it: `src/` is gitignored because `pip install -e git+...` clones
there, and that clone has been committed by accident four times.

## Commands

```bash
pip install -U -r requirements.txt                    # Install deps (-U pulls latest SDK main)
pip install -e ../python-api                          # Or: develop against a local SDK checkout
python engine.py sync --wanderlust --singularities    # Full pipeline
python engine.py keyframe --dry-run                   # Link genealogy keyframes
python engine.py wanderlust                           # Mirror playlist minus loops
python engine.py singularities                        # Mirror sheep touching singularities
python engine.py report --edges 5                     # Keyframe graph balance report
python thumbs.py                                      # Generate thumbnails
```

## Key Patterns

- Uses edream_sdk for backend API interactions
- Environment variables: BACKEND_URL, API_KEY, PLAYLIST_UUID, FLOCK_BEGIN_INDEX,
  LOOPLESS_PLAYLIST_UUID, SINGULARITIES_PLAYLIST_UUID
- Sheep names are GEN=ID=P1=P2; `Sheep.parse` in engine.py is the one parser
- `sync` phases: download, upload, wait for ingest, keyframe, then optional
  wanderlust/singularities; each phase prints a one-line summary and any
  phase error makes the exit status 1
- Every writing command supports `--dry-run`; `-v`/`-q` control verbosity
- Derived playlists go through `mirror()`: delete, batch add, reorder

## Deployment

Runs as CLI tools, typically in batch/cron jobs.
