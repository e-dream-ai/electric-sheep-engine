"""Manage a playlist of Electric Sheep on the e-dream backend.

Electric Sheep names encode a sheep's parents as GEN=ID=P1=P2, see
https://github.com/scottdraves/electricsheep/wiki/Protocol. The sheep
GEN=ID=P1=P2 starts on the keyframe GEN=P1 and ends on GEN=P2, so
consecutive sheep share a keyframe and playback is seamless. A loop is a
sheep whose parents are the same (GEN=K=K=K); a keyframe with no loop is
a singularity.

commands:
  sync           download new sheep from the sheepserver, upload them, wait
                 for them to ingest, link keyframes, then optionally
                 update the wanderlust and singularities playlists
  keyframe       link every sheep to its genealogy keyframes
  wanderlust     mirror the playlist into one without loops
  singularities  mirror the sheep that touch a singularity into a playlist
  report         rank keyframes by in/out balance and suggest new edges

environment (.env): BACKEND_URL, API_KEY, PLAYLIST_UUID, FLOCK_BEGIN_INDEX,
LOOPLESS_PLAYLIST_UUID, SINGULARITIES_PLAYLIST_UUID

examples:
  python engine.py sync --wanderlust --singularities
  python engine.py keyframe --flock-begin 0 --dry-run
  python engine.py report --edges 5 --weight
"""
import argparse
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
)

from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.client.edream_client import EDreamClient
from edream_sdk.client.playlist_client import MAX_PLAYLIST_ITEMS_PER_BATCH
from edream_sdk.types.dream_types import Dream, UpdateDreamRequest
from edream_sdk.types.keyframe_types import Keyframe
from edream_sdk.types.playlist_types import Playlist, PlaylistItemType
from requests.exceptions import ConnectionError, HTTPError, Timeout

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
LOOPLESS_PLAYLIST_UUID = os.getenv("LOOPLESS_PLAYLIST_UUID")
SINGULARITIES_PLAYLIST_UUID = os.getenv("SINGULARITIES_PLAYLIST_UUID")
FLOCK_BEGIN_INDEX = int(os.getenv("FLOCK_BEGIN_INDEX", "0"))

REMOTE = "sheep@v3d0.sheepserver.net:/sheep/hidef/"
REMOTE_PATTERN = "00248*.avi"
SET_URL = "http://v3d0.sheepserver.net/cgi/set"

RETRIES = 3
DRY_RUN_UUID = "<dry-run>"
# Dream statuses after which the backend is done with a dream.
INGESTED = {"processed", "failed"}

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


# ---------------------------------------------------------------------------
# Output and bookkeeping

# 0 = errors only, 1 = summary, 2 = per-item detail
VERBOSITY = 1


def say(message: str, level: int = 1) -> None:
    if VERBOSITY >= level:
        print(message, flush=True)


def detail(message: str) -> None:
    say(message, level=2)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class Stats:
    """Thread-safe counters for one phase; any "errors" fail the run."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.counts[key] += n

    def fail(self, message: str) -> None:
        self.bump("errors")
        warn(message)

    def finish(self, label: str) -> int:
        """Print a one-line summary and return the phase's exit status."""
        summary = ", ".join(
            f"{key} {n}" for key, n in sorted(self.counts.items()) if n
        )
        if summary:
            say(f"{label}: {summary}")
        return 1 if self.counts["errors"] else 0


# ---------------------------------------------------------------------------
# Backend access

class _ThreadClient(threading.local):
    client: Optional[EDreamClient] = None


_thread_client = _ThreadClient()


def api() -> EDreamClient:
    """This thread's backend client; worker threads each get their own."""
    if _thread_client.client is None:
        _thread_client.client = create_edream_client(
            backend_url=BACKEND_URL, api_key=API_KEY
        )
    return _thread_client.client


def with_retries(label: str, fn: Callable[[], ResultT]) -> ResultT:
    """Call fn, retrying transient failures with a widening backoff.

    Only use this for reads and idempotent writes: a retried create or
    delete whose first attempt actually landed would fail or duplicate.
    """
    for attempt in range(RETRIES):
        try:
            return fn()
        except Exception as error:
            if not _is_retryable(error) or attempt == RETRIES - 1:
                raise
            say(f"  retry {attempt + 1} for {label}: {error}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("retry loop exhausted without returning or raising")


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        status = error.response.status_code if error.response is not None else None
        return status == 429 or (status is not None and status >= 500)
    return isinstance(error, (ConnectionError, Timeout))


def run_pool(
    stats: Stats,
    label: str,
    fn: Callable[[ItemT], None],
    items: Sequence[ItemT],
    jobs: int,
    describe: Callable[[ItemT], object] = str,
) -> None:
    """Map fn over items concurrently, counting failures instead of aborting."""
    if not items:
        return

    def guarded(item: ItemT) -> Optional[Exception]:
        try:
            fn(item)
            return None
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for item, error in zip(items, pool.map(guarded, items)):
            if error is not None:
                stats.fail(f"FAILED {label} {describe(item)}: {error}")


def get_playlist(uuid: str) -> Playlist:
    return with_retries(f"get playlist {uuid}", lambda: api().get_playlist(uuid))


def dream_items(playlist: Playlist) -> List[dict]:
    return [
        item
        for item in (playlist.get("items") or [])
        if item.get("type") == "dream" and item.get("dreamItem")
    ]


# ---------------------------------------------------------------------------
# Sheep names and the keyframe graph

class Sheep(NamedTuple):
    """A parsed GEN=ID=P1=P2 name, with its dream when it came from a playlist."""

    gen: str
    id: str
    first: str
    last: str
    dream: Optional[Dream] = None

    @classmethod
    def parse(cls, name: Optional[str], dream: Optional[Dream] = None) -> Optional["Sheep"]:
        parts = (name or "").split("=")
        if len(parts) != 4 or not parts[1].isdigit():
            return None
        return cls(*parts, dream=dream)

    @property
    def name(self) -> str:
        return "=".join((self.gen, self.id, self.first, self.last))

    @property
    def number(self) -> int:
        return int(self.id)

    @property
    def start(self) -> str:
        return f"{self.gen}={self.first}"

    @property
    def end(self) -> str:
        return f"{self.gen}={self.last}"

    @property
    def is_loop(self) -> bool:
        return self.first == self.last

    def __str__(self) -> str:
        return self.name


def read_sheep(playlist: Playlist, stats: Stats) -> List[Sheep]:
    """The playlist's sheep in order, skipping (and counting) other names."""
    flock = []
    for item in dream_items(playlist):
        dream = item["dreamItem"]
        sheep = Sheep.parse(dream.get("name"), dream)
        if sheep is None:
            stats.bump("unparseable")
            detail(f"  unparseable: {dream.get('name')}")
        else:
            flock.append(sheep)
    return flock


def keyframe_graph(
    flock: Iterable[Sheep],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Successors and predecessors of each keyframe, one edge per sheep."""
    succs: Dict[str, List[str]] = {}
    preds: Dict[str, List[str]] = {}
    for sheep in flock:
        if sheep.end in succs.get(sheep.start, ()):
            say(f"duplicate edge {sheep.start} -> {sheep.end}")
        succs.setdefault(sheep.start, []).append(sheep.end)
        preds.setdefault(sheep.end, []).append(sheep.start)
    return succs, preds


def singularities(flock: Iterable[Sheep]) -> Set[str]:
    """Keyframes that no loop plays through."""
    keyframes: Set[str] = set()
    looped: Set[str] = set()
    for sheep in flock:
        keyframes.update((sheep.start, sheep.end))
        if sheep.is_loop:
            looped.add(sheep.start)
    return keyframes - looped


# ---------------------------------------------------------------------------
# Download and upload

def download(args: argparse.Namespace) -> int:
    """Rsync the sheep we don't already have from the sheepserver."""
    stats = Stats()
    os.makedirs(args.directory, exist_ok=True)
    say("listing remote files")
    try:
        listing = subprocess.run(
            ["rsync", "--list-only", REMOTE + REMOTE_PATTERN],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as error:
        stats.fail(f"rsync listing failed: {error.stderr.strip() or error}")
        return stats.finish("download")

    wanted = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        remote_size = int(parts[1].replace(",", ""))
        filename = parts[-1]
        sheep = Sheep.parse(os.path.splitext(filename)[0])
        if sheep and sheep.number < args.flock_begin:
            continue
        local_path = os.path.join(args.directory, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
            continue
        wanted.append(filename)
        detail(f"  download {filename}")

    if not wanted:
        say("no new files to download")
    elif args.dry_run:
        stats.bump("would_download", len(wanted))
    else:
        say(f"downloading {len(wanted)} files")
        progress = ["--progress"] if VERBOSITY >= 1 else []
        try:
            subprocess.run(
                ["rsync", "--partial", "--size-only", *progress,
                 "--files-from=-", REMOTE, args.directory],
                input="\n".join(wanted), text=True, check=True,
            )
            stats.bump("downloaded", len(wanted))
        except subprocess.CalledProcessError as error:
            stats.fail(f"rsync download failed: {error}")
    return stats.finish("download")


def upload(args: argparse.Namespace) -> int:
    """Upload every local sheep the playlist doesn't have yet, one at a time."""
    stats = Stats()
    uploaded = {
        item["dreamItem"].get("name") for item in dream_items(get_playlist(args.playlist))
    }
    files = []
    for filename in sorted(os.listdir(args.directory)):
        if filename.startswith("."):  # rsync partials, .DS_Store
            continue
        name = os.path.splitext(filename)[0]
        sheep = Sheep.parse(name)
        if sheep and sheep.number < args.flock_begin:
            continue
        if name not in uploaded:
            files.append(os.path.join(args.directory, filename))

    if args.dry_run:
        for path in files:
            detail(f"  upload {path}")
        stats.bump("would_upload", len(files))
        return stats.finish("upload")

    if files:
        say(f"uploading {len(files)} files")
    for path in files:
        say(f"upload {path}")
        try:
            api().add_file_to_playlist(
                uuid=args.playlist,
                file_path=path,
                progress_callback=_show_progress if VERBOSITY >= 1 else None,
            )
            stats.bump("uploaded")
        except Exception as error:
            stats.fail(f"FAILED upload {path}: {error}")
    return stats.finish("upload")


def _show_progress(bytes_uploaded: int, total_bytes: int, percentage: float) -> None:
    say(f"  upload progress: {percentage:.1f}%")


def wait_for_ingest(args: argparse.Namespace, playlist: Playlist) -> Tuple[int, Playlist]:
    """Poll until no dream on the playlist is still queued or processing.

    Waits on the whole playlist rather than just this run's uploads, so
    dreams left ingesting by an earlier, interrupted run are covered too.
    Returns the exit status and the playlist, refetched if anything changed.
    """
    stats = Stats()
    pending = {
        dream["uuid"]: dream.get("name")
        for dream in (item["dreamItem"] for item in dream_items(playlist))
        if dream.get("status") not in INGESTED
    }
    if not pending:
        return 0, playlist
    if args.dry_run:
        say(f"{len(pending)} dreams still ingesting (dry run, not waiting)")
        return 0, playlist

    say(f"waiting for {len(pending)} dreams to ingest")
    deadline = time.monotonic() + args.wait_timeout * 60
    while pending:
        if time.monotonic() >= deadline:
            stats.fail(
                f"gave up after {args.wait_timeout:g} min with {len(pending)} "
                "dreams still ingesting; keyframe will skip them until a later run"
            )
            break
        time.sleep(args.wait_interval)
        for uuid, name in list(pending.items()):
            try:
                dream = with_retries(f"poll {name}", lambda: api().get_dream(uuid))
            except Exception as error:
                warn(f"could not poll {name}: {error}")
                continue
            status = dream.get("status") if dream else None
            if dream is None:
                stats.fail(f"{name} disappeared while ingesting")
            elif status == "failed":
                stats.fail(f"{name} failed to ingest: {dream.get('error')}")
            elif status in INGESTED:
                stats.bump("ingested")
                detail(f"  ingested {name}")
            else:
                continue
            del pending[uuid]
        if pending:
            detail(f"  {len(pending)} still ingesting")
    return stats.finish("ingest"), get_playlist(args.playlist)


# ---------------------------------------------------------------------------
# Keyframes

def link_keyframes(args: argparse.Namespace, playlist: Playlist) -> int:
    """Point every sheep's dream at its GEN=P1 and GEN=P2 keyframes."""
    stats = Stats()

    # Index the playlist's own keyframes by name once, so most lookups
    # need no network at all.
    keyframes: Dict[str, str] = {}
    for link in playlist.get("playlistKeyframes") or []:
        keyframe = link.get("keyframe") or {}
        if keyframe.get("name"):
            keyframes.setdefault(keyframe["name"], keyframe["uuid"])

    work: List[Sheep] = []
    for sheep in read_sheep(playlist, stats):
        if sheep.number < args.flock_begin:
            stats.bump("below_flock_begin")
        elif sheep.dream.get("status") not in INGESTED:
            stats.bump("still_ingesting")
        else:
            work.append(sheep)

    missing = sorted({name for s in work for name in (s.start, s.end)} - set(keyframes))
    say(
        f"{playlist.get('name', args.playlist)}: {len(work)} sheep to check, "
        f"{len(keyframes)} named keyframes, {len(missing)} to resolve"
    )
    keyframes.update(resolve_keyframes(args, playlist, missing, stats))

    def link(sheep: Sheep) -> None:
        start_uuid = keyframes.get(sheep.start)
        end_uuid = keyframes.get(sheep.end)
        if not start_uuid or not end_uuid:
            stats.bump("unresolved")
            warn(f"no keyframe for {sheep}: {sheep.start}={start_uuid} {sheep.end}={end_uuid}")
            return
        dream = sheep.dream
        if (
            (dream.get("startKeyframe") or {}).get("uuid") == start_uuid
            and (dream.get("endKeyframe") or {}).get("uuid") == end_uuid
        ):
            stats.bump("already_linked")
            return
        if args.dry_run:
            stats.bump("would_update")
            return
        with_retries(
            f"update {sheep}",
            lambda: api().update_dream(
                dream["uuid"],
                UpdateDreamRequest(startKeyframe=start_uuid, endKeyframe=end_uuid),
            ),
        )
        stats.bump("updated")
        detail(f"  linked {sheep}")

    run_pool(stats, "updating", link, work, args.jobs)
    return stats.finish("keyframe")


def resolve_keyframes(
    args: argparse.Namespace, playlist: Playlist, names: Sequence[str], stats: Stats
) -> Dict[str, str]:
    """Find or create each named keyframe on the playlist; returns name -> uuid.

    Searching the backend by name before creating recovers keyframes that
    exist but were never linked -- a run that died between creating and
    linking leaves these behind -- so interrupted runs don't strand
    duplicates in the sheep namespace.
    """
    if not names:
        return {}
    me = with_retries("get logged user", lambda: api().get_logged_user())
    if me is None:
        raise RuntimeError("backend did not return a logged-in user")

    found: Dict[str, Optional[Keyframe]] = {}

    def find(name: str) -> None:
        found[name] = with_retries(
            f"find {name}",
            lambda: api().find_keyframe_by_name(name, user_uuid=me["uuid"]),
        )

    run_pool(stats, "finding", find, names, args.jobs)

    # Creating and linking change the playlist, so do them one at a time.
    resolved: Dict[str, str] = {}
    for name in names:
        if name not in found:
            continue
        existing = found[name]
        if args.dry_run:
            stats.bump("would_link" if existing else "would_create")
            resolved[name] = existing["uuid"] if existing else DRY_RUN_UUID
            continue
        try:
            if existing:
                api().link_keyframe_to_playlist(playlist["uuid"], existing["uuid"])
                resolved[name] = existing["uuid"]
                stats.bump("linked")
            else:
                resolved[name] = api().add_keyframe_to_playlist(playlist, name)["uuid"]
                stats.bump("created")
        except Exception as error:
            stats.fail(f"FAILED resolving {name}: {error}")
    return resolved


# ---------------------------------------------------------------------------
# Derived playlists

def mirror(
    args: argparse.Namespace,
    stats: Stats,
    source: Playlist,
    target_uuid: str,
    wanted_uuids: Sequence[str],
) -> None:
    """Make the target playlist hold exactly wanted_uuids, in that order.

    Three passes: delete what no longer belongs, add what is missing, then
    fix the order. Adds go through the batch endpoint, which appends in
    array order, so a cold run lands already ordered and the reorder pass
    is a no-op.
    """
    started = time.time()
    wanted_uuids = list(dict.fromkeys(wanted_uuids))
    wanted = set(wanted_uuids)
    target = get_playlist(target_uuid)
    current = [(item["id"], item["dreamItem"]) for item in dream_items(target)]
    present = {dream["uuid"] for _, dream in current}
    to_delete = [(item_id, dream) for item_id, dream in current if dream["uuid"] not in wanted]
    to_add = [uuid for uuid in wanted_uuids if uuid not in present]
    dry = " (dry run)" if args.dry_run else ""

    say(
        f"{source.get('name')} -> {target.get('name')}: "
        f"{len(present)} present, {len(wanted)} wanted"
    )

    # Pass 1: delete. There is no batch remove endpoint, so this is a pool.
    if to_delete:
        say(f"deleting {len(to_delete)}{dry}")
        if not args.dry_run:
            def delete(entry: Tuple[int, Dream]) -> None:
                item_id, dream = entry
                api().delete_item_from_playlist(uuid=target_uuid, playlist_item_id=item_id)
                detail(f"  deleted: {dream.get('name')} ({dream['uuid']})")

            run_pool(
                stats, "deleting", delete, to_delete, args.jobs,
                describe=lambda entry: entry[1].get("name"),
            )

    # Pass 2: add. One atomic request per chunk, sent in order so the items
    # land in order; each chunk is all-or-nothing on its own.
    if to_add:
        chunks = [
            to_add[i : i + MAX_PLAYLIST_ITEMS_PER_BATCH]
            for i in range(0, len(to_add), MAX_PLAYLIST_ITEMS_PER_BATCH)
        ]
        say(f"adding {len(to_add)} in {len(chunks)} batch(es){dry}")
        if not args.dry_run:
            for number, chunk in enumerate(chunks, start=1):
                try:
                    added = api().add_items_to_playlist(
                        target_uuid,
                        [{"type": PlaylistItemType.DREAM, "uuid": uuid} for uuid in chunk],
                    )
                    detail(f"  batch {number}/{len(chunks)}: added {added}")
                except Exception as error:
                    stats.fail(f"batch {number}/{len(chunks)} failed ({len(chunk)}): {error}")

    # Pass 3: order. Only refetch if we changed something; in the steady
    # state the copy we already have is current.
    if args.dry_run:
        # Survivors stay in place and additions are appended in order,
        # which is how the backend adds them.
        deleted = {dream["uuid"] for _, dream in to_delete}
        actual_order = [d["uuid"] for _, d in current if d["uuid"] not in deleted] + to_add
        item_ids: Dict[str, int] = {}
    else:
        if to_delete or to_add:
            target = get_playlist(target_uuid)
        items = dream_items(target)
        actual_order = [item["dreamItem"]["uuid"] for item in items]
        item_ids = {item["dreamItem"]["uuid"]: item["id"] for item in items}

    reordered = False
    if actual_order == wanted_uuids:
        detail("order already correct")
    elif args.dry_run:
        reordered = True
        say(f"would reorder {len(wanted_uuids)} items")
    else:
        order = [
            {"id": item_ids[uuid], "order": position}
            for position, uuid in enumerate(wanted_uuids)
            if uuid in item_ids
        ]
        if order:
            try:
                api().reorder_playlist(uuid=target_uuid, order=order)
                reordered = True
                say(f"reordered {len(order)} items")
            except Exception as error:
                stats.fail(f"reorder failed: {error}")

    elapsed = time.time() - started
    verb = "would change" if args.dry_run else "changed"
    if to_delete or to_add:
        say(f"{verb}: -{len(to_delete)} +{len(to_add)} in {elapsed:.1f}s")
    elif reordered:
        say(f"{verb}: order only in {elapsed:.1f}s")
    else:
        say(f"in sync ({elapsed:.1f}s)")


def update_wanderlust(args: argparse.Namespace, source: Playlist, target_uuid: str) -> int:
    """Mirror every sheep except the loops, in source order."""
    stats = Stats()
    wanted = []
    for sheep in read_sheep(source, stats):
        if sheep.is_loop:
            stats.bump("loops")
            detail(f"  loop, skipping: {sheep}")
        else:
            wanted.append(sheep.dream["uuid"])
    mirror(args, stats, source, target_uuid, wanted)
    return stats.finish("wanderlust")


def update_singularities(args: argparse.Namespace, source: Playlist, target_uuid: str) -> int:
    """Mirror every sheep that starts or ends on a singularity, in source order."""
    stats = Stats()
    flock = read_sheep(source, stats)
    singular = singularities(flock)
    stats.bump("singularities", len(singular))
    wanted = [
        sheep.dream["uuid"]
        for sheep in flock
        if sheep.start in singular or sheep.end in singular
    ]
    mirror(args, stats, source, target_uuid, wanted)
    return stats.finish("singularities")


# ---------------------------------------------------------------------------
# Report

def report(args: argparse.Namespace) -> int:
    playlist = get_playlist(args.playlist)
    flock = read_sheep(playlist, Stats())
    succs, preds = keyframe_graph(flock)

    def ins(key: str) -> int:
        return len(preds.get(key, ()))

    def outs(key: str) -> int:
        return len(succs.get(key, ()))

    def balance(key: str) -> Tuple[int, str]:
        return outs(key) - ins(key), key

    ranked = sorted(set(succs) | set(preds), key=balance)
    print("\nio balance ranking")
    for key in ranked:
        print(f"{key} {ins(key)} {outs(key)}")

    # Singularities come from the main playlist even when reporting on
    # another: a derived playlist like wanderlust has no loops at all.
    if args.playlist == PLAYLIST_UUID:
        main_flock, main_succs = flock, succs
    else:
        main_flock = read_sheep(get_playlist(PLAYLIST_UUID), Stats())
        main_succs = keyframe_graph(main_flock)[0]
    print("\nsingularity ranking")
    for key in sorted(singularities(main_flock), key=balance):
        print(f"{key} {ins(key)} {outs(key)}")

    if args.edges <= 0:
        return 0
    print(f"\nedges recommended for {'weight' if args.weight else 'balance'}\n")
    if args.weight:
        by_weight = sorted(ranked, key=lambda key: (outs(key) + ins(key), key))
        pairs = zip(by_weight[0::2], by_weight[1::2])
    else:
        # Pair the biggest net sinks with the biggest net sources.
        half = len(ranked) // 2
        pairs = zip(ranked[:half], reversed(ranked[len(ranked) - half :]))
    emitted = 0
    for begin, end in pairs:
        if emitted == args.edges:
            break
        if end in main_succs.get(begin, ()) or end in succs.get(begin, ()):
            print(f"# skipping existing edge {begin} -> {end}")
            continue
        print(f"{SET_URL}?name=beginid&value={begin.split('=')[1]}")
        print(f"{SET_URL}?name=endid&value={end.split('=')[1]}")
        print()
        emitted += 1
    return 0


# ---------------------------------------------------------------------------
# Commands

def need(value: Optional[str], what: str) -> str:
    if not value:
        raise SystemExit(f"no {what}")
    return value


def cmd_sync(args: argparse.Namespace) -> int:
    need(args.playlist, "playlist: pass --playlist or set PLAYLIST_UUID")
    if args.wanderlust is not None:
        need(args.wanderlust, "wanderlust playlist: pass one or set LOOPLESS_PLAYLIST_UUID")
    if args.singularities is not None:
        need(args.singularities, "singularities playlist: pass one or set SINGULARITIES_PLAYLIST_UUID")

    codes = []
    if not args.no_download:
        codes.append(download(args))
    if not args.no_upload:
        codes.append(upload(args))
    playlist = get_playlist(args.playlist)
    if not args.no_wait:
        code, playlist = wait_for_ingest(args, playlist)
        codes.append(code)
    if not args.no_keyframes:
        codes.append(link_keyframes(args, playlist))
    if args.wanderlust:
        codes.append(update_wanderlust(args, playlist, args.wanderlust))
    if args.singularities:
        codes.append(update_singularities(args, playlist, args.singularities))
    return max(codes, default=0)


def cmd_keyframe(args: argparse.Namespace) -> int:
    need(args.playlist, "playlist: pass --playlist or set PLAYLIST_UUID")
    return link_keyframes(args, get_playlist(args.playlist))


def cmd_wanderlust(args: argparse.Namespace) -> int:
    need(args.playlist, "source playlist: pass --playlist or set PLAYLIST_UUID")
    need(args.target, "target playlist: pass --target or set LOOPLESS_PLAYLIST_UUID")
    return update_wanderlust(args, get_playlist(args.playlist), args.target)


def cmd_singularities(args: argparse.Namespace) -> int:
    need(args.playlist, "source playlist: pass --playlist or set PLAYLIST_UUID")
    need(args.target, "target playlist: pass --target or set SINGULARITIES_PLAYLIST_UUID")
    return update_singularities(args, get_playlist(args.playlist), args.target)


def cmd_report(args: argparse.Namespace) -> int:
    need(args.playlist, "playlist: pass --playlist or set PLAYLIST_UUID")
    need(PLAYLIST_UUID, "main playlist for singularities: set PLAYLIST_UUID")
    return report(args)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return number


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--playlist", "--playlist_uuid", dest="playlist", default=PLAYLIST_UUID,
        help="main playlist (default PLAYLIST_UUID)",
    )
    verbosity = common.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="print every item")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="print nothing but errors")

    writes = argparse.ArgumentParser(add_help=False)
    writes.add_argument("--jobs", type=positive_int, default=8, help="concurrent API calls (default 8)")
    writes.add_argument("--dry-run", action="store_true", help="report changes without making them")

    flock = argparse.ArgumentParser(add_help=False)
    flock.add_argument(
        "--flock-begin", type=int, default=FLOCK_BEGIN_INDEX,
        help="ignore sheep with an id below this (default FLOCK_BEGIN_INDEX)",
    )

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    sync = commands.add_parser(
        "sync", parents=[common, writes, flock],
        help="download, upload, wait for ingest, keyframe, and derive playlists",
    )
    sync.add_argument("--directory", "-d", default="sheep", help="local directory (default sheep)")
    sync.add_argument("--no-download", action="store_true", help="skip the download phase")
    sync.add_argument("--no-upload", action="store_true", help="skip the upload phase")
    sync.add_argument("--no-wait", action="store_true", help="don't wait for dreams to ingest")
    sync.add_argument(
        "--wait-timeout", type=non_negative_float, default=120, metavar="MINUTES",
        help="stop waiting for ingest after this long (default 120)",
    )
    sync.add_argument(
        "--wait-interval", type=non_negative_float, default=30, metavar="SECONDS",
        help="seconds between ingest polls (default 30)",
    )
    sync.add_argument("--no-keyframes", action="store_true", help="skip linking keyframes")
    sync.add_argument(
        "--wanderlust", nargs="?", const=LOOPLESS_PLAYLIST_UUID or "", metavar="UUID",
        help="then update the wanderlust playlist (default LOOPLESS_PLAYLIST_UUID)",
    )
    sync.add_argument(
        "--singularities", nargs="?", const=SINGULARITIES_PLAYLIST_UUID or "", metavar="UUID",
        help="then update the singularities playlist (default SINGULARITIES_PLAYLIST_UUID)",
    )
    sync.set_defaults(func=cmd_sync)

    keyframe = commands.add_parser(
        "keyframe", parents=[common, writes, flock],
        help="link every sheep to its genealogy keyframes",
    )
    keyframe.set_defaults(func=cmd_keyframe)

    wanderlust = commands.add_parser(
        "wanderlust", parents=[common, writes], help="mirror the playlist without loops",
    )
    wanderlust.add_argument(
        "--target", default=LOOPLESS_PLAYLIST_UUID,
        help="playlist to update (default LOOPLESS_PLAYLIST_UUID)",
    )
    wanderlust.set_defaults(func=cmd_wanderlust)

    singular = commands.add_parser(
        "singularities", parents=[common, writes],
        help="mirror the sheep that touch a singularity",
    )
    singular.add_argument(
        "--target", default=SINGULARITIES_PLAYLIST_UUID,
        help="playlist to update (default SINGULARITIES_PLAYLIST_UUID)",
    )
    singular.set_defaults(func=cmd_singularities)

    report_parser = commands.add_parser(
        "report", parents=[common], help="rank keyframes and suggest new edges",
    )
    report_parser.add_argument("--edges", type=int, default=0, help="number of edges to recommend")
    report_parser.add_argument(
        "--weight", action="store_true", help="select edges by weight instead of balance",
    )
    report_parser.set_defaults(func=cmd_report)

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    global VERBOSITY
    args = parse_args(argv)
    VERBOSITY = 2 if args.verbose else 0 if args.quiet else 1
    need(BACKEND_URL and API_KEY, "backend: set BACKEND_URL and API_KEY (see .env)")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
