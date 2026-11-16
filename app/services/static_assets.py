"""Native static-asset resolution — the terminal route's own domain logic.

Extracted from ``services/legacy_static.py`` (2026-08-26 native-terminal
migration, wave 2 family W2e) so the last catch-all ASGI route
(``routers/native_terminal.py``) can serve public/private static assets and
render the shared bounded error bodies without its call graph reaching a
module the architecture census treats as an adapter boundary
(``services.legacy_static``). Every function here is pure domain logic: it
takes parsed arguments and returns data or raises/represents a typed error —
no ``handler``/``wfile``/``send_response`` transport shape anywhere.

``services/legacy_static.py`` and its handler-shaped ``LegacyStaticMixin``
were retired with the pre-refactor topology, so this module is the single
real implementation of static-asset serving — there is no second copy to
keep in sync.
"""

import os
import html
import datetime
import email.utils
import mimetypes
import stat
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler

from app.core import data_packs
from app.core import paths
from app.core import sitedata


APP_ROOT = paths.PROJECT_ROOT
WEBDATA_DIR = sitedata.webdata_dir()

# The public asset trees. Outside them the fallback serves only the root web assets
# (core/paths.py) and the module assets module manifests declare — see is_public_static().
PUBLIC_STATIC_PREFIXES = ("/webdata/", "/static/", "/vendor/")
# Kept in the asset trees but not assets: documents and sources are not served there either
NOT_ASSETS = frozenset({"md", "py", "ts", "toml"})


class StaticResolution:
    """Closed base class for the terminal static-policy decision."""


@dataclass(frozen=True)
class StaticDenied(StaticResolution):
    status: int = 403


@dataclass(frozen=True)
class StaticNotFound(StaticResolution):
    status: int = 404


@dataclass
class StaticFile(StaticResolution):
    """One verified regular file whose descriptors stay owned until close."""

    fd: int
    parent_fd: int
    root_fd: int
    leaf: str
    size: int
    mtime: float
    signature: tuple
    content_type: str
    cache_control: str
    private_root: bool = False
    _closed: bool = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        for descriptor in {self.fd, self.parent_fd, self.root_fd}:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def chunks(self, chunk_size=1024 * 1024):
        """Yield stable chunks from the held descriptor and close on exit."""
        try:
            while True:
                if self.private_root and not data_packs.private_root_is_verified():
                    raise OSError("private static root verification changed")
                before = _stat_signature(os.fstat(self.fd))
                if before != self.signature:
                    raise OSError("static asset changed during read")
                chunk = os.read(self.fd, chunk_size)
                after = _stat_signature(os.fstat(self.fd))
                if after != self.signature:
                    raise OSError("static asset changed during read")
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()


def _stat_signature(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _same_identity(left, right):
    return _stat_signature(left) == _stat_signature(right)


def _open_regular_beneath(root, relative):
    """Open a regular file beneath ``root`` without following symlinks."""
    if not safe_data_relative_path(relative):
        raise OSError("unsafe static asset path")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    root_before = os.lstat(root)
    if not stat.S_ISDIR(root_before.st_mode) or stat.S_ISLNK(root_before.st_mode):
        raise OSError("unsafe static asset root")
    root_fd = os.open(root, os.O_RDONLY | directory | nofollow)
    current_fd = -1
    final_fd = -1
    try:
        root_open = os.fstat(root_fd)
        root_after = os.lstat(root)
        if not _same_identity(root_before, root_open) or not _same_identity(root_open, root_after):
            raise OSError("static asset root changed")
        current_fd = os.dup(root_fd)
        parts = relative.split("/")
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | directory | nofollow,
                dir_fd=current_fd,
            )
            opened = os.fstat(next_fd)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(next_fd)
                raise OSError("unsafe static asset parent")
            os.close(current_fd)
            current_fd = next_fd
        leaf = parts[-1]
        final_fd = os.open(
            leaf,
            os.O_RDONLY | nofollow | nonblock,
            dir_fd=current_fd,
        )
        opened = os.fstat(final_fd)
        named = os.stat(leaf, dir_fd=current_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_identity(opened, named)
        ):
            raise OSError("unsafe static asset")
        return root_fd, current_fd, final_fd, leaf, opened
    except BaseException:
        for descriptor in {final_fd, current_fd, root_fd}:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise


def _content_type(path):
    if path.lower().endswith(".woff2"):
        return "font/woff2"
    # Minimal deployment images may not have a WebP system MIME entry.
    # An octet-stream response downloads instead of opening the native photo.
    if path.lower().endswith(".webp"):
        return "image/webp"
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _cache_control(path, query):
    lower = path.lower()
    immutable = (
        ".js", ".css", ".woff2", ".woff", ".ttf", ".otf",
        ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".ico",
    )
    if lower.endswith(immutable):
        if "v=" in query:
            return "public, max-age=31536000, immutable"
        if lower.endswith((".js", ".css")):
            return "public, max-age=300"
        return "public, max-age=86400"
    return "no-cache"


def http_error_body(status, message=None):
    """Return the stdlib handler's stable HTML error document."""
    short, explain = BaseHTTPRequestHandler.responses[status]
    display = short if message is None else message
    return (BaseHTTPRequestHandler.error_message_format % {
        "code": status,
        "message": html.escape(display, quote=False),
        "explain": html.escape(explain, quote=False),
    }).encode("utf-8", "replace")


def disk_root(relative):
    """The directory a public path's file lives beneath, for a path relative to the URL root.

    The URL space keeps its historic shape whatever the directory layout: `modules/<id>/...`
    names a module's own file under the application code root, a root web asset
    (`admin-app.js`) lives in core/paths.py's ROOT_WEB_ASSETS_DIR, everything else is relative
    to the project root (APP_ROOT)."""
    if relative.split("/", 1)[0] == "modules":
        return os.path.normpath(os.path.join(APP_ROOT, paths.CODE_DIR))
    if relative in paths.ROOT_WEB_ASSETS:
        return os.path.normpath(os.path.join(APP_ROOT, paths.ROOT_WEB_ASSETS_DIR))
    return APP_ROOT


def _file_result(root, relative, request_path, query, private_root=False):
    root_fd, parent_fd, fd, leaf, opened = _open_regular_beneath(root, relative)
    return StaticFile(
        fd=fd,
        parent_fd=parent_fd,
        root_fd=root_fd,
        leaf=leaf,
        size=opened.st_size,
        mtime=opened.st_mtime,
        signature=_stat_signature(opened),
        content_type=_content_type(request_path),
        cache_control=_cache_control(request_path, query),
        private_root=private_root,
    )


def request_is_not_modified(result, if_modified_since, if_none_match=None):
    """Mirror SimpleHTTPRequestHandler's narrow If-Modified-Since rule."""
    if not if_modified_since or if_none_match:
        return False
    try:
        candidate = email.utils.parsedate_to_datetime(if_modified_since)
    except (TypeError, IndexError, OverflowError, ValueError):
        return False
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=datetime.timezone.utc)
    if candidate.tzinfo is not datetime.timezone.utc:
        return False
    modified = datetime.datetime.fromtimestamp(
        result.mtime,
        datetime.timezone.utc,
    ).replace(microsecond=0)
    return modified <= candidate


def resolve_static_request(path, query=""):
    """Resolve one terminal GET/HEAD request without reopening its pathname."""
    if not path or has_nul(path):
        return StaticDenied()
    decoded = urllib.parse.unquote(path)
    if decoded.startswith(("/webdata/", "/data/")):
        prefix = "/webdata/" if decoded.startswith("/webdata/") else "/data/"
        relative = decoded[len(prefix):].lstrip("/")
        if not safe_data_relative_path(relative):
            return StaticDenied()
        target = os.path.abspath(sitedata.path(relative))
        configured_private_root = data_packs.private_packs_dir()
        private_root = (
            os.path.abspath(configured_private_root)
            if configured_private_root
            else None
        )
        candidates = [os.path.abspath(WEBDATA_DIR)]
        if data_packs.packs_root_exists():
            candidates.append(os.path.abspath(data_packs.packs_dir()))
        if private_root and data_packs.private_root_is_verified():
            candidates.append(private_root)
        for configured_root in candidates:
            try:
                contained = os.path.commonpath((target, configured_root)) == configured_root
            except ValueError:
                contained = False
            if contained:
                is_private = bool(private_root and configured_root == private_root)
                if is_private and not data_packs.private_root_is_verified():
                    return StaticDenied()
                rel = os.path.relpath(target, configured_root).replace(os.sep, "/")
                try:
                    result = _file_result(
                        os.path.realpath(configured_root),
                        rel,
                        path,
                        query,
                        private_root=is_private,
                    )
                except OSError:
                    return StaticDenied()
                if result.private_root and not data_packs.private_root_is_verified():
                    result.close()
                    return StaticDenied()
                return result
        return StaticDenied()
    if not is_public_static(decoded):
        # 404 whether or not such a file exists: the answer does not map the checkout
        return StaticDenied() if _hidden(decoded) else StaticNotFound()
    relative = decoded.lstrip("/")
    try:
        return _file_result(disk_root(relative), relative, path, query)
    except OSError:
        return StaticDenied() if denied_static_is_known(decoded) else StaticNotFound()


def data_static_roots():
    """Return resolved roots that may back public data-asset URLs."""
    roots = [os.path.realpath(WEBDATA_DIR)]
    if data_packs.packs_root_exists():
        roots.append(os.path.realpath(data_packs.packs_dir()))
    if data_packs.private_root_is_verified():
        roots.append(os.path.realpath(data_packs.private_packs_dir()))
    return roots


def inside_static_root(target, roots):
    return any(target == root or target.startswith(root + os.sep) for root in roots)


def safe_data_relative_path(rel):
    """Accept only a plain relative asset name for a data-pack resolver."""
    if not rel or "\\" in rel:
        return False
    return all(part not in ("", ".", "..") for part in rel.split("/"))


def open_verified_private_file(root, target):
    """Open ``target`` beneath a verified private root without symlink races."""
    rel = os.path.relpath(target, root)
    if not safe_data_relative_path(rel):
        raise OSError("unsafe private asset path")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    fd = os.open(root, os.O_RDONLY | directory | nofollow)
    try:
        parts = rel.split("/")
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            next_fd = os.open(
                part,
                os.O_RDONLY | nofollow | (0 if last else directory),
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def has_nul(path):
    """Return whether a URL path contains a raw or percent-encoded NUL."""
    if not path:
        return False
    if "\x00" in path:
        return True
    try:
        return "\x00" in urllib.parse.unquote(path)
    except Exception:
        return True


def _hidden(path):
    """A dot segment (`/.env`, `/x/.git/config`): always refused, 403."""
    return any(segment.startswith(".") for segment in path.split("/"))


def _declared_module_assets():
    # Lazy: the module registry imports the fingerprint registry, which imports this module.
    from app.core import static_fingerprint
    return static_fingerprint._module_assets()


def is_public_static(path):
    """The explicit list the fallback serves (tests/test_static_allowlist.py): the public asset
    trees, the root web assets of core/paths.py, and the assets module manifests declare. Any
    other path — source, tests, documents, whether or not the file exists — is not served."""
    if not path or has_nul(path):
        return False
    if _hidden(path) or path.startswith("/data/"):
        return False
    if paths.ROOT_WEB_ASSETS_DIR != "." and path.startswith("/%s/" % paths.ROOT_WEB_ASSETS_DIR):
        return False  # a root web asset answers at its root URL only, not a second time under /static/
    if path.startswith(PUBLIC_STATIC_PREFIXES):
        # inside the asset trees too: a document or a source is not an asset (`static/platform/AGENTS.md`,
        # the Python and TypeScript sources a vendored kit ships)
        name = path.rsplit("/", 1)[-1]
        extension = name.rsplit(".", 1)[1].lower() if "." in name else ""
        return extension not in NOT_ASSETS
    relative = path[1:] if path.startswith("/") else path
    if relative in paths.ROOT_WEB_ASSETS:
        return True
    return relative.startswith("modules/") and relative in _declared_module_assets()


def denied_static_is_known(path):
    """Distinguish a denied real checkout resource (403) from unknown (404)."""
    if not path:
        return False
    segments = path.split("/")
    if any(segment.startswith(".") for segment in segments):
        return True
    if path.endswith("/"):
        return True
    try:
        relative = urllib.parse.unquote(path).lstrip("/")
        target = os.path.realpath(os.path.join(disk_root(relative), relative))
    except (ValueError, OSError):
        return True
    if not (target == APP_ROOT or target.startswith(APP_ROOT + os.sep)):
        return True
    return os.path.exists(target)
