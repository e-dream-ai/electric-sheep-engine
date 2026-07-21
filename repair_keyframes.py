"""Repair dream start/end keyframes garbled by add_keyframes.py.

Phase 1 (repair): reassign every dream in the Meditations playlist to its
genealogy keyframes (gen=start, gen=end parsed from the dream name),
using the named keyframes that already exist on the playlist.
Phase 2 (verify): refetch all three playlists, confirm no kf_* references remain.
Phase 3 (cleanup, only with --delete): delete kf_* keyframes not referenced
by any dream in the three playlists.
"""
import os, sys, time
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.dream_types import UpdateDreamRequest

load_dotenv("/Users/spot/e-dream-ai/electric-sheep-engine/.env")
client = create_edream_client(backend_url=os.getenv("BACKEND_URL"), api_key=os.getenv("API_KEY"))

MEDITATIONS = "142f0b08-0288-45fe-a812-8a55b16f3f22"
WANDERLUST = "13489b20-cc0b-4923-8ea8-3f64015fe389"
SINGULARITIES = "912af337-ffad-45ef-8f3d-6aad08ee7f52"

DELETE = "--delete" in sys.argv

fixed = skipped = errors = 0
done_dreams = set()
for pl_uuid in (MEDITATIONS, WANDERLUST, SINGULARITIES):
    playlist = client.get_playlist(pl_uuid)
    kf_by_name = {pk["keyframe"]["name"]: pk["keyframe"]["uuid"]
                  for pk in playlist.get("playlistKeyframes", [])}
    print(f"\nplaylist {playlist.get('name', pl_uuid)}: named keyframes available: {len(kf_by_name)}", flush=True)

    for item in playlist.get("items", []):
        if item.get("type") != "dream" or not item.get("dreamItem"):
            continue
        d = item["dreamItem"]
        if d["uuid"] in done_dreams:
            continue
        done_dreams.add(d["uuid"])
        parts = d["name"].split("=")
        if len(parts) != 4:
            print(f"UNPARSEABLE: {d['name']}", flush=True)
            continue
        start_name = f"{parts[0]}={parts[2]}"
        end_name = f"{parts[0]}={parts[3]}"
        start_uuid = kf_by_name.get(start_name)
        end_uuid = kf_by_name.get(end_name)
        if not start_uuid or not end_uuid:
            print(f"MISSING KEYFRAME for {d['name']}: {start_name}={start_uuid} {end_name}={end_uuid}", flush=True)
            errors += 1
            continue
        cur_start = (d.get("startKeyframe") or {}).get("uuid")
        cur_end = (d.get("endKeyframe") or {}).get("uuid")
        if cur_start == start_uuid and cur_end == end_uuid:
            skipped += 1
            continue
        for attempt in range(3):
            try:
                client.update_dream(d["uuid"], UpdateDreamRequest(
                    startKeyframe=start_uuid, endKeyframe=end_uuid))
                fixed += 1
                break
            except Exception as e:
                print(f"retry {attempt+1} for {d['name']}: {e}", flush=True)
                time.sleep(2 * (attempt + 1))
        else:
            errors += 1
            print(f"FAILED: {d['name']}", flush=True)
        if (fixed + errors) % 50 == 0:
            print(f"progress: fixed={fixed} skipped={skipped} errors={errors}", flush=True)

print(f"\nrepair done: fixed={fixed} skipped={skipped} errors={errors}", flush=True)

# Phase 2: verify
print("\nverifying...", flush=True)
still_bad = 0
referenced = set()
playlists = {}
for uuid in (MEDITATIONS, WANDERLUST, SINGULARITIES):
    p = client.get_playlist(uuid)
    playlists[uuid] = p
    for item in p.get("items", []):
        if item.get("type") != "dream" or not item.get("dreamItem"):
            continue
        d = item["dreamItem"]
        for key in ("startKeyframe", "endKeyframe"):
            k = d.get(key)
            if k:
                referenced.add(k["uuid"])
                if (k.get("name") or "").startswith("kf_"):
                    still_bad += 1
                    print(f"STILL BAD: {d['name']} {key}={k.get('name')}", flush=True)
print(f"dream references to kf_* remaining: {still_bad}", flush=True)

# Phase 3: cleanup
garbage = []
for uuid, p in playlists.items():
    for pk in p.get("playlistKeyframes", []):
        k = pk["keyframe"]
        if (k.get("name") or "").startswith("kf_"):
            garbage.append(k)
print(f"kf_* keyframes on playlists: {len(garbage)}", flush=True)
if not DELETE:
    print("(dry run: pass --delete to remove them)", flush=True)
    sys.exit(0)
if still_bad:
    print("aborting deletion: kf_* keyframes still referenced", flush=True)
    sys.exit(1)
deleted = skipped_ref = 0
for k in garbage:
    if k["uuid"] in referenced:
        skipped_ref += 1
        continue
    for attempt in range(3):
        try:
            client.delete_keyframe(k["uuid"])
            deleted += 1
            break
        except Exception as e:
            print(f"delete retry {attempt+1} for {k['name']}: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    if deleted % 100 == 0 and deleted:
        print(f"deleted {deleted}...", flush=True)
print(f"cleanup done: deleted={deleted} skipped_referenced={skipped_ref}", flush=True)
