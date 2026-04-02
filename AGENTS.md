# AGENTS.md — electric-sheep-engine

## Overview

Python utility for managing playlists of AI-generated Electric Sheep content. Synchronizes content directories, handles video metadata, generates keyframes and thumbnails.

## Stack

- **Language:** Python 3
- **Dependencies:** python-dotenv, edream_sdk
- **Tools:** rsync, FFmpeg (for keyframes/thumbnails)

## Project Structure

```
sync.py                # Main sync/playlist management
keyframe.py            # Keyframe generation
keyframe_dash.py       # Keyframe dashboard
thumbs.py              # Thumbnail generation
graph.py               # Dream content analysis
wanderlust.py          # Content exploration
merge_playlists.py     # Playlist management
report.py              # Reporting utilities
copy_mp4_by_*.py       # Content utilities
extract_uuid_pairs.py  # UUID extraction
src/edream-sdk/        # SDK submodule
```

## Commands

```bash
pip install -r requirements.txt    # Install dependencies
python sync.py                     # Run playlist sync
python keyframe.py                 # Generate keyframes
python thumbs.py                   # Generate thumbnails
```

## Key Patterns

- Uses edream_sdk for backend API interactions
- Environment variables: BACKEND_URL, API_KEY, PLAYLIST_UUID
- Supports flock indexing and selective downloads
- Two-phase operations: download and upload

## Deployment

Runs as CLI tools, typically in batch/cron jobs.
