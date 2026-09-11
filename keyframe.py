"""Link every dream in a playlist to its genealogy keyframes.

Electric Sheep names encode a sheep's parents as GEN=ID=P1=P2, see
https://github.com/scottdraves/electricsheep/wiki/Protocol. So the dream
GEN=ID=P1=P2 should start on the keyframe named GEN=P1 and end on GEN=P2.
Consecutive sheep then share a keyframe and playback is seamless.

Keyframe names are resolved cheapest-first:

  1. the playlist's own keyframes, indexed by name up front (no network)
  2. a backend name query, which recovers keyframes that exist but were
     never linked -- runs that died between creating and linking leave
     these behind
  3. create a new one

Step 2 is what stops every interrupted run from stranding another
duplicate in the sheep namespace.

Usage:
  python keyframe.py [--playlist_uuid UUID] [--flock-begin N]
                     [--jobs N] [--dry-run]
"""
import argparse
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple, TypeVar

from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.dream_types import Dream, UpdateDreamRequest
from edream_sdk.types.keyframe_types import Keyframe
from edream_sdk.types.playlist_types import Playlist, PlaylistKeyframe
from edream_sdk.types.user_types import User
from requests.exceptions import ConnectionError, HTTPError, Timeout

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
FLOCK_BEGIN_INDEX = int(os.getenv("FLOCK_BEGIN_INDEX", "0"))

RETRIES = 3
DRY_RUN_UUID = "<dry-run>"

ResultT = TypeVar("ResultT")
ItemT = TypeVar("ItemT")
DreamWork = Tuple[Dream, str, str]


class KeyframeScriptClient(Protocol):
    def get_logged_user(self) -> Optional[User]: ...

    def get_playlist(self, uuid: str) -> Playlist: ...

    def find_keyframe_by_name(
        self, name: str, user_uuid: Optional[str] = None
    ) -> Optional[Keyframe]: ...

    def link_keyframe_to_playlist(
        self, playlist_uuid: str, keyframe_uuid: str
    ) -> PlaylistKeyframe: ...

    def add_keyframe_to_playlist(
        self, playlist: Playlist, keyframe_name: str
    ) -> Keyframe: ...

    def update_dream(self, uuid: str, data: UpdateDreamRequest) -> Dream: ...


ClientFactory = Callable[[], KeyframeScriptClient]

stats: Counter[str] = Counter()
stats_lock = threading.Lock()


def bump(key: str, n: int = 1) -> None:
    with stats_lock:
        stats[key] += n


def with_retries(label: str, fn: Callable[[], ResultT]) -> ResultT:
    """Call fn, retrying transient failures with a widening backoff."""
    for attempt in range(RETRIES):
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e) or attempt == RETRIES - 1:
                raise
            print(f"  retry {attempt + 1} for {label}: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("retry loop exhausted without returning or raising")


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        status = error.response.status_code if error.response is not None else None
        return status == 429 or (status is not None and status >= 500)
    return isinstance(error, (ConnectionError, Timeout))


def run_pool(
    label: str,
    fn: Callable[[ItemT], None],
    items: Sequence[ItemT],
    jobs: int,
) -> None:
    """Map fn over items, reporting failures instead of aborting the run."""
    if not items:
        return
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for item, error in zip(items, pool.map(_guard(fn), items)):
            if error is not None:
                bump("errors")
                print(f"FAILED {label} {_describe(item)}: {error}", flush=True)


def _guard(
    fn: Callable[[ItemT], None],
) -> Callable[[ItemT], Optional[Exception]]:
    def wrapped(item: ItemT) -> Optional[Exception]:
        try:
            fn(item)
            return None
        except Exception as e:
            return e

    return wrapped


def _describe(item: object) -> str:
    if isinstance(item, tuple) and item:
        first = item[0]
        if isinstance(first, dict):
            return str(first.get("name", first))
        return str(first)
    return str(item)


class _ClientLocal(threading.local):
    client: Optional[KeyframeScriptClient] = None


def create_thread_local_client_factory() -> ClientFactory:
    local_state = _ClientLocal()

    def get_client() -> KeyframeScriptClient:
        client = local_state.client
        if client is None:
            client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)
            local_state.client = client
        return client

    return get_client


def resolve_missing_keyframes(
    client_factory: ClientFactory,
    playlist: Playlist,
    user_uuid: str,
    names: Sequence[str],
    jobs: int,
    dry_run: bool,
) -> Dict[str, str]:
    found_by_name: Dict[str, Optional[Keyframe]] = {}
    found_lock = threading.Lock()

    def find(name: str) -> None:
        existing = with_retries(
            f"find {name}",
            lambda: client_factory().find_keyframe_by_name(
                name, user_uuid=user_uuid
            ),
        )
        with found_lock:
            found_by_name[name] = existing

    run_pool("finding", find, names, jobs)

    resolved: Dict[str, str] = {}
    for name in names:
        if name not in found_by_name:
            continue
        existing = found_by_name[name]
        if dry_run:
            bump("would_link" if existing else "would_create")
            resolved[name] = existing["uuid"] if existing else DRY_RUN_UUID
            continue
        try:
            client = client_factory()
            if existing:
                client.link_keyframe_to_playlist(
                    playlist["uuid"], existing["uuid"]
                )
                resolved[name] = existing["uuid"]
                bump("linked")
            else:
                created = client.add_keyframe_to_playlist(playlist, name)
                resolved[name] = created["uuid"]
                bump("created")
        except Exception as error:
            bump("errors")
            print(f"FAILED resolving {name}: {error}", flush=True)

    return resolved


def parse_sheep_name(name: str) -> Optional[Tuple[int, str, str]]:
    """GEN=ID=P1=P2 -> (int(ID), "GEN=P1", "GEN=P2"), or None if not a sheep."""
    parts = name.split("=")
    if len(parts) != 4:
        return None
    gen, sheep_id, first_parent, second_parent = parts
    try:
        index = int(sheep_id)
    except ValueError:
        return None
    return index, f"{gen}={first_parent}", f"{gen}={second_parent}"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="keyframe", description=__doc__)
    parser.add_argument("--playlist_uuid", default=PLAYLIST_UUID)
    parser.add_argument(
        "--flock-begin",
        type=int,
        default=FLOCK_BEGIN_INDEX,
        help="ignore sheep with an id below this (default from FLOCK_BEGIN_INDEX)",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_int,
        default=4,
        help="concurrent API calls (default 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = parser.parse_args(argv)
    if not args.playlist_uuid:
        parser.error("no playlist: pass --playlist_uuid or set PLAYLIST_UUID")
    return args


def main() -> int:
    with stats_lock:
        stats.clear()
    args = parse_args()
    client_factory = create_thread_local_client_factory()
    client = client_factory()
    me = client.get_logged_user()
    if me is None:
        raise RuntimeError("backend did not return a logged-in user")
    playlist = client.get_playlist(args.playlist_uuid)

    # Index the playlist's keyframes by name once. The old linear scan per
    # lookup made this O(dreams x keyframes).
    keyframes_by_name: Dict[str, str] = {}
    for link in playlist.get("playlistKeyframes") or []:
        keyframe = link.get("keyframe") or {}
        if keyframe.get("name"):
            keyframes_by_name.setdefault(keyframe["name"], keyframe["uuid"])

    items = playlist.get("items") or []
    print(
        f"playlist {playlist.get('name', args.playlist_uuid)}: "
        f"{len(items)} items, {len(keyframes_by_name)} named keyframes",
        flush=True,
    )

    # Pass 1: work out what every dream needs, without calling the backend.
    work: List[DreamWork] = []
    for item in items:
        if item.get("type") != "dream" or not item.get("dreamItem"):
            continue
        dream = item["dreamItem"]
        parsed = parse_sheep_name(dream.get("name") or "")
        if not parsed:
            bump("unparseable")
            print(f"  unparseable: {dream.get('name')}", flush=True)
            continue
        index, start_name, end_name = parsed
        if index < args.flock_begin:
            bump("below_flock_begin")
            continue
        work.append((dream, start_name, end_name))

    missing = sorted(
        {name for _, start, end in work for name in (start, end)}
        - set(keyframes_by_name)
    )
    print(
        f"{len(work)} sheep in range, {len(missing)} keyframes to resolve",
        flush=True,
    )

    # Pass 2: search concurrently, then serialize the order-sensitive writes.
    keyframes_by_name.update(
        resolve_missing_keyframes(
            client_factory=client_factory,
            playlist=playlist,
            user_uuid=me["uuid"],
            names=missing,
            jobs=args.jobs,
            dry_run=args.dry_run,
        )
    )

    # Pass 3: point each dream at its two keyframes.
    def link_dream(entry: DreamWork) -> None:
        dream, start_name, end_name = entry
        start_uuid = keyframes_by_name.get(start_name)
        end_uuid = keyframes_by_name.get(end_name)
        if not start_uuid or not end_uuid:
            bump("unresolved")
            print(
                f"  no keyframe for {dream['name']}: "
                f"{start_name}={start_uuid} {end_name}={end_uuid}",
                flush=True,
            )
            return
        current_start = (dream.get("startKeyframe") or {}).get("uuid")
        current_end = (dream.get("endKeyframe") or {}).get("uuid")
        if current_start == start_uuid and current_end == end_uuid:
            bump("already_linked")
            return
        if args.dry_run:
            bump("would_update")
            return
        with_retries(
            f"update {dream['name']}",
            lambda: client_factory().update_dream(
                dream["uuid"],
                UpdateDreamRequest(startKeyframe=start_uuid, endKeyframe=end_uuid),
            ),
        )
        bump("updated")

    run_pool("updating", link_dream, work, args.jobs)

    print("\n" + ("would change:" if args.dry_run else "done:"), flush=True)
    for key in (
        "created",
        "linked",
        "updated",
        "would_create",
        "would_link",
        "would_update",
        "already_linked",
        "below_flock_begin",
        "unparseable",
        "unresolved",
        "errors",
    ):
        if stats[key]:
            print(f"  {key}: {stats[key]}", flush=True)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
