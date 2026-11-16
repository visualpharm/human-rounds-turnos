# Orphan photos of deleted doctors and centers

Until 2026-11-06 a doctor or center deleted for good left its photos in the
persistent volume: `data/medico-fotos/<id>.<ext>`, the doctor's private
`data/medico-fotos/.originals/<id>/` and `data/centro-fotos/<slug>.<ext>`.
Deletion now removes them; `scripts/fixes/purge_orphan_photos.py` removes what
was left before. It only touches a photo whose id is in no catalog record
(data pack or panel overlay, disabled and incomplete records included), never
a name that is not a valid id, and reads the catalog again right before each
removal. A catalog that lists no doctor or no center is refused (exit 2).

Run it from the app directory (the container's `/app`) of the installation,
by whoever operates it — dry run first, read the list, then apply:

    python3 scripts/fixes/purge_orphan_photos.py            # prints the orphans, removes nothing
    python3 scripts/fixes/purge_orphan_photos.py --apply    # removes them and prints each removal

The last line is a JSON report (`huerfanas`, `quitadas`, `ignoradas`). Safe to
run twice. Older backups still hold the removed files until they rotate out.
