# AGENT.md — electric-sheep-engine

## Overview
Legacy Electric Sheep playlist synchronization engine. Manages automated playlist content and scheduling.

## Stack
- **Language:** Python
- **Dependencies:** edream_sdk, python-dotenv

## Project Structure
```
run.py             # Main entry point
keyframe.py        # Keyframe extraction logic
singularities.py   # Singularity processing
wanderlust.py      # Wanderlust playlist logic
thumbs.py          # Thumbnail handling
report.py          # Reporting utilities
```

## Commands
```bash
pip install -r requirements.txt
python run.py
```

## Key Patterns
- Uses edream_sdk for backend API communication
- Automates playlist content management
- Legacy system — may be superseded by newer engines
