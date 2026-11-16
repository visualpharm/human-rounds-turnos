#!/usr/bin/env python3
"""Run contact-sheet portrait edits through OpenAI Batch at the discounted rate.

The source sheets and their doctor ordering come from
``scripts/build_medico_photos.py --collect --sheet``.  One GPT Image 2 edit is
submitted per sheet (up to 12 people), so 30 portraits cost three image edits,
not 30.  The OpenAI Batch API then applies its asynchronous 50% discount.

The script is deliberately resumable:
  --submit  uploads sheets and creates exactly one batch, saving its ID locally
  --status  prints its current state without spending image tokens
  --ingest  downloads completed results, writes edited-sheet-*.png, and cuts
            the WebP tiles through the established pipeline

The Batch API supports /v1/images/edits.  Source sheets are uploaded as Files
first, then referenced by file ID in the JSONL request body; no image data is
duplicated in the batch JSONL.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "medico-photos-src"
STATE = SRC / "openai-batch.json"
MANIFEST = SRC / "sheet-manifest.json"

PROMPT = """Edit this exact 4-column by 3-row doctor portrait contact sheet into a polished medical-directory avatar sheet.

Hard invariants: preserve the exact grid, same number of people, same ordering, and empty tiles. Preserve every real person's identity. Do not invent, remove, stylize, beautify, age-change, or caricature anyone. Keep glasses, facial hair, hairstyle, expression, and approximate clothing. No text, labels, watermarks, borders, UI, rounded masks, or decoration.

Make every portrait readable in a tiny circular avatar: full head and shoulders visible, matching eye height, neutral professional lighting, mild sharpening, and a clean off-white/light-gray background. Keep surgical masks on. Remove non-clinical props, microphones, headphones, and overlays only when the underlying person can be reconstructed naturally."""


def _key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    return key


def _request(path: str, *, payload: dict | None = None, data: bytes | None = None,
             content_type: str = "application/json") -> dict:
    body = data if data is not None else (json.dumps(payload).encode() if payload is not None else None)
    request = urllib.request.Request(
        "https://api.openai.com" + path, data=body,
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": content_type},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"OpenAI {exc.code}: {detail}") from exc


def _multipart_file(path: Path, purpose: str, mime: str) -> bytes:
    boundary = "----sana-turnos-photo-batch"
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
         f"Content-Type: {mime}\r\n\r\n").encode(),
        path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _upload(path: Path, purpose: str, mime: str = "image/png") -> str:
    payload, content_type = _multipart_file(path, purpose, mime)
    response = _request("/v1/files", data=payload, content_type=content_type)
    return response["id"]


def submit() -> None:
    if STATE.exists():
        raise SystemExit(f"Batch state already exists: {STATE}; use --status or --ingest")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    requests = []
    sheet_files = {}
    for entry in manifest["sheets"]:
        sheet = SRC / entry["file"]
        file_id = _upload(sheet, "vision")
        sheet_files[entry["file"]] = file_id
        # Batch accepts this endpoint specifically.  Referencing the pre-uploaded
        # File avoids an enormous JSONL and keeps source pixels out of prompts.
        requests.append({
            "custom_id": sheet.stem,
            "method": "POST",
            "url": "/v1/images/edits",
            "body": {
                "model": "gpt-image-2",
                # The synchronous endpoint calls this multipart field `image`;
                # Batch is JSON-only and names the equivalent file-ID array
                # `images`.
                "images": [{"file_id": file_id}],
                "prompt": PROMPT,
                "size": "1536x1152",
                "quality": "high",
                "output_format": "png",
            },
        })
    jsonl = SRC / "openai-batch-input.jsonl"
    jsonl.write_text("".join(json.dumps(line) + "\n" for line in requests), encoding="utf-8")
    input_id = _upload(jsonl, "batch", "application/jsonl")
    batch = _request("/v1/batches", payload={
        "input_file_id": input_id,
        "endpoint": "/v1/images/edits",
        "completion_window": "24h",
        "metadata": {"project": "sana-turnos", "job": "doctor-portraits"},
    })
    STATE.write_text(json.dumps({
        "batch_id": batch["id"], "input_file_id": input_id, "source_files": sheet_files,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"submitted {batch['id']} ({len(requests)} contact sheets)")


def _state() -> dict:
    if not STATE.is_file():
        raise SystemExit("No batch state. Run --submit first.")
    return json.loads(STATE.read_text(encoding="utf-8"))


def _batch() -> dict:
    state = _state()
    return _request(f"/v1/batches/{state['batch_id']}")


def status() -> None:
    batch = _batch()
    counts = batch.get("request_counts", {})
    print(json.dumps({
        "id": batch["id"], "status": batch["status"], "counts": counts,
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
    }, indent=2))


def ingest() -> None:
    batch = _batch()
    if batch.get("status") != "completed":
        raise SystemExit(f"Batch is {batch.get('status')}; wait for completed before --ingest")
    output_id = batch.get("output_file_id")
    if not output_id:
        raise SystemExit("Completed batch has no output_file_id")
    request = urllib.request.Request(
        f"https://api.openai.com/v1/files/{output_id}/content",
        headers={"Authorization": f"Bearer {_key()}"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        lines = response.read().decode("utf-8").splitlines()
    failures = []
    for line in lines:
        item = json.loads(line)
        response = item.get("response", {})
        if response.get("status_code") != 200:
            failures.append(item)
            continue
        body = response["body"]
        raw = base64.b64decode(body["data"][0]["b64_json"])
        (SRC / f"edited-{item['custom_id']}.png").write_bytes(raw)
    if failures:
        (SRC / "openai-batch-errors.json").write_text(json.dumps(failures, indent=2) + "\n")
        raise SystemExit(f"{len(failures)} edit requests failed; saved openai-batch-errors.json")
    # Existing --edit is intentionally idempotent: it sees edited sheets and
    # only performs the deterministic tile cuts, never another paid edit.
    import subprocess
    subprocess.run([sys.executable, "scripts/build_medico_photos.py", "--edit"], cwd=ROOT, check=True)
    print("ingested edited sheets and cut portrait tiles")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--ingest", action="store_true")
    args = parser.parse_args()
    if args.submit:
        submit()
    if args.status:
        status()
    if args.ingest:
        ingest()
    if not any(vars(args).values()):
        parser.error("choose --submit, --status, or --ingest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
