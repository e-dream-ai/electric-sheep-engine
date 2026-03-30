# AGENT.md — electric-sheep-engine

## Overview
Legacy Electric Sheep playlist synchronization engine. Manages automated playlist content and scheduling.

## Stack
- **Language:** Python
- **Dependencies:** edream_sdk, python-dotenv

## Project Structure
```
run.py             # Uploads a local directory of video files to the server
keyframe.py        # Uses filenames to determine which dreams are connected and creates keyframes so clients can connect them
singularities.py   # Updates the Singularities version of the Sheep playlist
wanderlust.py      # Updates the Wanderlust version of the Sheep playlist
thumbs.py          # Thumbnail handling
report.py          # Reports basic stats on all dreams and the graph they form, and recommends edges to balance the graph
```

## Commands
```bash
pip install -r requirements.txt
python run.py
```

## Key Patterns
- Uses edream_sdk for backend API communication
- Automates playlist content management
- connects the Legacy Electric Sheep to Infinidream
