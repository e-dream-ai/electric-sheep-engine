"""Mirror a source playlist into a loopless playlist, dropping the loops.

Electric Sheep names encode a sheep's parents as GEN=ID=P1=P2, see
https://github.com/scottdraves/electricsheep/wiki/Protocol. A sheep whose
two parents are the same (P1 == P2) is a loop; Wanderlust is Meditations
with those removed, in the same relative order.

The sync is three passes: delete what no longer belongs, add what is
missing, then fix the order. Adds go through the batch endpoint, which
appends in array order, so a cold run lands already-ordered and the
reorder pass becomes a no-op.

Usage:
  python wanderlust.py [--source UUID] [--target UUID]
                       [--jobs N] [--dry-run] [-v | -q]
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.client.playlist_client import MAX_PLAYLIST_ITEMS_PER_BATCH
from edream_sdk.types.playlist_types import PlaylistItemType

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
LOOPLESS_PLAYLIST_UUID = os.getenv("LOOPLESS_PLAYLIST_UUID")

# 0 = errors only, 1 = summary, 2 = per-item detail
VERBOSITY = 1


def say(message: str, level: int = 1) -> None:
    if VERBOSITY >= level:
        print(message, flush=True)


def detail(message: str) -> None:
    say(message, level=2)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def parse_sheep_name(name: str) -> Optional[Tuple[str, str]]:
    """Return (start_keyframe_id, end_keyframe_id), or None if unparseable."""
    parts = (name or "").split("=")
    if len(parts) != 4:
        return None
    gen, _sheep_id, start, end = parts
    return f"{gen}={start}", f"{gen}={end}"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wanderlust", description=__doc__)
    parser.add_argument("--source", default=PLAYLIST_UUID, help="playlist to mirror")
    parser.add_argument(
        "--target", default=LOOPLESS_PLAYLIST_UUID, help="loopless playlist to update"
    )
    parser.add_argument("--jobs", type=positive_int, default=10, help="delete workers")
    parser.add_argument(
        "--dry-run", action="store_true", help="report changes without making them"
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="print every item"
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="print nothing but errors"
    )
    return parser.parse_args(argv)


def read_dreams(playlist: dict) -> List[dict]:
    return [
        item
        for item in (playlist.get("items") or [])
        if item.get("type") == "dream" and item.get("dreamItem")
    ]


def main() -> int:
    global VERBOSITY
    args = parse_args()
    VERBOSITY = 2 if args.verbose else 0 if args.quiet else 1

    if not args.source or not args.target:
        warn("source and target playlist uuids are required")
        return 2

    client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)
    started = time.time()

    source = client.get_playlist(args.source)
    target = client.get_playlist(args.target)

    # What the target should contain: every non-loop sheep, in source order.
    wanted_uuids: List[str] = []
    loops = 0
    unparseable = 0
    for item in read_dreams(source):
        dream = item["dreamItem"]
        parsed = parse_sheep_name(dream.get("name"))
        if not parsed:
            unparseable += 1
            detail(f"  unparseable: {dream.get('name')}")
            continue
        start_id, end_id = parsed
        if start_id == end_id:
            loops += 1
            detail(f"  loop, skipping: {dream['name']}")
            continue
        wanted_uuids.append(dream["uuid"])
        detail(f"  want: {dream['name']} ({dream['uuid']})")

    wanted = set(wanted_uuids)
    current_items = [
        (item["id"], item["dreamItem"]["uuid"], item["dreamItem"].get("name"))
        for item in read_dreams(target)
    ]
    current = {uuid for _, uuid, _ in current_items}

    to_delete = [entry for entry in current_items if entry[1] not in wanted]
    to_add = [uuid for uuid in wanted_uuids if uuid not in current]

    say(
        f"{source.get('name')} -> {target.get('name')}: "
        f"{len(current)} present, {len(wanted)} wanted "
        f"({loops} loops skipped)"
    )
    if unparseable:
        warn(f"{unparseable} dream names could not be parsed")

    failures = 0

    # Pass 1: delete. There is no batch remove endpoint, so this stays a pool.
    if to_delete:
        say(f"deleting {len(to_delete)}{' (dry run)' if args.dry_run else ''}")
        if not args.dry_run:
            def delete_one(entry):
                item_id, uuid, name = entry
                try:
                    client.delete_item_from_playlist(
                        uuid=args.target, playlist_item_id=item_id
                    )
                    return None, uuid, name
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    return error, uuid, name

            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for future in as_completed(
                    pool.submit(delete_one, entry) for entry in to_delete
                ):
                    error, uuid, name = future.result()
                    if error:
                        failures += 1
                        warn(f"failed to delete {name} ({uuid}): {error}")
                    else:
                        detail(f"  deleted: {name} ({uuid})")

    # Pass 2: add. One atomic request per chunk, sent in order so the items
    # land in order; each chunk is all-or-nothing on its own.
    if to_add:
        chunks = [
            to_add[i : i + MAX_PLAYLIST_ITEMS_PER_BATCH]
            for i in range(0, len(to_add), MAX_PLAYLIST_ITEMS_PER_BATCH)
        ]
        say(
            f"adding {len(to_add)} in {len(chunks)} batch(es)"
            f"{' (dry run)' if args.dry_run else ''}"
        )
        if not args.dry_run:
            for number, chunk in enumerate(chunks, start=1):
                try:
                    added = client.add_items_to_playlist(
                        args.target,
                        [
                            {"type": PlaylistItemType.DREAM, "uuid": uuid}
                            for uuid in chunk
                        ],
                    )
                    detail(f"  batch {number}/{len(chunks)}: added {added}")
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    failures += 1
                    warn(f"batch {number}/{len(chunks)} failed ({len(chunk)}): {error}")

    # Pass 3: order. Only refetch if we changed something; in the steady state
    # the copy we already have is current.
    changed = bool(to_delete or to_add) and not args.dry_run
    if changed:
        target = client.get_playlist(args.target)

    if args.dry_run:
        # Project what the target would look like: survivors in place, then
        # the additions appended in order, which is how the backend adds them.
        deleted_uuids = {uuid for _, uuid, _ in to_delete}
        actual_order = [
            uuid for _, uuid, _ in current_items if uuid not in deleted_uuids
        ] + to_add
        item_ids: Dict[str, int] = {}
    else:
        actual_order = []
        item_ids = {}
        for item in read_dreams(target):
            uuid = item["dreamItem"]["uuid"]
            item_ids[uuid] = item["id"]
            actual_order.append(uuid)

    if actual_order == wanted_uuids:
        say("order already correct")
    elif args.dry_run:
        say(f"would reorder {len(wanted_uuids)} items")
    else:
        reorder = [
            {"id": item_ids[uuid], "order": position}
            for position, uuid in enumerate(wanted_uuids)
            if uuid in item_ids
        ]
        if reorder:
            try:
                client.reorder_playlist(uuid=args.target, order=reorder)
                say(f"reordered {len(reorder)} items")
            except Exception as error:  # noqa: BLE001 - reported, not fatal
                failures += 1
                warn(f"reorder failed: {error}")

    verb = "would change" if args.dry_run else "changed"
    if to_delete or to_add:
        say(
            f"{verb}: -{len(to_delete)} +{len(to_add)} "
            f"in {time.time() - started:.1f}s"
        )
    else:
        say(f"in sync ({time.time() - started:.1f}s)")

    if failures:
        warn(f"{failures} operation(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
