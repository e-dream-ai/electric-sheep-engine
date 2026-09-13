# Electric Sheep Engine

Manage a playlist of Electric Sheep: download the avi files produced by
the distributed renderer, upload them to the e-dream backend, link each
sheep to its genealogy keyframes, and maintain the derived Wanderlust
(no loops) and Singularities playlists.

#### install edream sdk

pip install -r requirements.txt

#### usage

Configure `.env` with `BACKEND_URL`, `API_KEY`, `PLAYLIST_UUID`,
`FLOCK_BEGIN_INDEX`, `LOOPLESS_PLAYLIST_UUID` and
`SINGULARITIES_PLAYLIST_UUID`, then:

    python engine.py sync --wanderlust --singularities   # the whole pipeline
    python engine.py keyframe                             # just link keyframes
    python engine.py wanderlust                           # just mirror loopless
    python engine.py singularities                        # just mirror singularities
    python engine.py report --edges 5                     # graph balance report

`sync` downloads, uploads, waits for the new dreams to finish ingesting,
links keyframes, then updates whichever derived playlists were asked for.
Every command that writes takes `--dry-run`; see `python engine.py <command> --help`.
