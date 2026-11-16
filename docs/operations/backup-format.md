# HRBACK01 encrypted archive format

Version 1. All integers are unsigned big-endian; all JSON is UTF-8, ASCII
escaped, sorted keys, no whitespace or duplicate keys. Readers reject
noncanonical JSON. No compression outside the encrypted payload. Cryptographic
primitives come from the pinned cryptography package.

## Byte layout

| Offset | Bytes | Meaning |
|---|---|---|
| 0 | 8 | ASCII HRBACK01 (magic and format version) |
| 8 | 4 | Immutable content-header byte length H, 1…65536 |
| 12 | H | Canonical content-header JSON |
| 12+H | 4 | Mutable envelope byte length E, 1…65536 |
| 16+H | E | Canonical array of 1…64 wrapped-key records |
| 16+H+E | 32 | HMAC-SHA256 under the random data key of ALL preceding bytes |
| 48+H+E | variable | Chunk frames, including exactly one terminal frame |

Content header fields: version=1; archive_id=canonical lowercase UUID;
cipher="AES-256-GCM"; kdf="none-random-256"; wrap="AES-256-GCM";
chunk_size (64…16777216 bytes, default 1048576); nonce_prefix (8 hex
characters encoding four cryptographically random bytes).

Each envelope record is {key_id, nonce, wrapped}. key_id is the first 8
lowercase hex characters of SHA-256(KEK). KEK and data key are independent
random 32-byte values. nonce is a fresh random 12-byte AES-GCM wrapping nonce,
hex encoded. wrapped is the 32-byte data key plus 16-byte GCM tag, hex encoded.
Wrapping AAD is ASCII "HRBACK-wrap-v1:" followed by the archive UUID.
There is no password KDF and no passphrase mode.

The immutable content header includes the algorithm/wrapping descriptors,
archive id and chunk size. Its SHA-256 binds all chunk frames. The separate
mutable envelope is authenticated by the header MAC, but is deliberately NOT
part of the chunk-header hash. Hashing mutable wrapped-key bytes into chunk
AAD would make rewrapping without re-encryption mathematically impossible.
This separation is the explicit resolution of the amendment's two requirements.

## Frames

Each frame is: 4-byte ciphertext length L; 1-byte final flag (0 or 1);
L ciphertext bytes including the 16-byte GCM tag. Nonfinal plaintext must
equal chunk_size. Final plaintext is 0…chunk_size. Empty archives still have
one authenticated empty final frame. Writers use lookahead, so an exact
multiple of chunk_size terminates on its last nonempty frame.

Chunk counter starts at 0, increases by exactly 1, and must be less than
2^64. The reader derives the index from position, never from attacker input.
The 96-bit nonce is nonce_prefix || uint64(counter). Every frame's AAD is
SHA-256(content-header JSON) || uint64(counter) || final_flag.
Per-archive random data keys prevent cross-archive nonce/key reuse. Counters
prevent within-archive reuse. Missing terminal flags, truncation, swapped,
duplicated or substituted chunks, altered flags, and bytes following the
terminal frame are errors. Nothing is published to the operator's restore
destination until the entire AEAD stream and file manifest validate.

## Plaintext and ownership

Plaintext is a regular-file-only POSIX tar: database.dump (pg_dump custom
format), data/**, optional legacy-data/**, legacy-webdata/** and private-packs/**,
config/installation.env, and manifest.json. No absolute paths, links,
special files, parent components, duplicate names or archive permissions
are accepted on restore. Every file except manifest.json appears exactly
once in manifest.files with its byte length and SHA-256. Empty directories are
recorded in manifest.directories and recreated only after safe-path validation.

manifest.signature maps each retained key_id to its HMAC-SHA256 hex digest. Its input is ASCII
"HRBACK-manifest-v1:" followed by canonical manifest JSON with signature
removed. Every retained installation KEK signs it, so either the old saved key
or the new saved key alone can recover a newly produced archive. This is an installation
ownership MAC, not a public-key signature. The AEAD envelope and file hashes
protect integrity; the producer id, UUID filename, immutable journal receipt,
and this MAC jointly admit a retention candidate.

The manifest records release commit/image, requirements hash, schema head,
pg_dump/Python/cryptography versions, snapshot/dump/file-copy timestamps,
key-table row counts, file-reference omissions, skips and changing files.
The source tree is not included.

## Operator key and rotation

Saved key text encodes 32 key bytes followed by the first four bytes of
SHA-256(ASCII "HRBACK-key-v1" || key). RFC4648 base32, no padding, grouped
every 5 characters with hyphens. Input accepts spaces and lowercase.
The checksum detects transcription mistakes; it is not a password verifier.

Settings rotation retains old KEKs in an installation-managed secret and
shows the NEW key only in the generating response. Existing archives remain
readable with the operator's old saved key. New archives carry wrapped copies
for retained keys as well as the new key. Keep both saved keys until historical
archives are retired. core.backup_crypto.rewrap validates the old stream,
changes only the envelope/MAC, and copies ciphertext frames unchanged.
Offline recovery accepts the new wrapped key using the authenticated header,
all AEAD frames and file hashes. The old manifest ownership key must remain
available for the EXTRA retention ownership validation of rewrapped old archives.
There is no automatic historical remote-object rewrite.
