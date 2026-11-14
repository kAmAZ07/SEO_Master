"""AR-15: bounded canonical ZIP and detached Ed25519 release manifest.

Trust is supplied out of band. No key from the archive is ever trusted.
"""
from dataclasses import dataclass
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping, cast
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SLUG = "seo-master-connector"
MANIFEST = "release-manifest.json"
SIGNATURE = "release-signature.json"
CONTEXT = b"SEO-MASTER-PLUGIN-RELEASE-V1\n"
MAX_ARCHIVE = 3 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_FILE = 256 * 1024
PAYLOAD = (
    "admin/css/admin-styles.css", "admin/js/admin-scripts.js", "admin/settings.php",
    "includes/api-handler.php", "includes/compatibility-checker.php", "includes/hmac-validator.php",
    "includes/meta-filter.php", "includes/signature-verifier.php", "includes/v2-protocol.php",
    "includes/v2-service.php", "includes/v2-sql.php", "includes/v2-store.php",
    "readme.txt", "seo-master-plugin.php", "uninstall.php",
)
HASH = re.compile(r"[0-9a-f]{64}")


class PackageError(ValueError):
    """Stable codes only: never include a private key, path or raw provider error."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_package_file(path: Path) -> bytes:
    # O_NONBLOCK keeps a configured FIFO/device from hanging the worker before
    # fstat. Links may resolve only to a regular file; trust is in the byte hash.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_ARCHIVE:
            raise PackageError("package_file_invalid")
        return source.read(MAX_ARCHIVE + 1)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _json(data: bytes) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PackageError("package_duplicate_json_key")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(PackageError("package_json_invalid")))
        if not isinstance(value, dict) or canonical(value) != data:
            raise PackageError("package_json_not_canonical")
        return cast(dict[str, object], value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise PackageError("package_json_invalid") from exc


def public_key_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def trust_keys(raw: str) -> dict[str, bytes]:
    if len(raw) > 8192:
        raise PackageError("package_trust_invalid")
    try:
        # Config need not be canonical; duplicate keys remain forbidden.
        pairs = json.loads(raw, object_pairs_hook=lambda values: values)
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 4:
            raise ValueError()
        result: dict[str, bytes] = {}
        for pair in pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError()
            key_id, value = pair
            if not isinstance(key_id, str) or not isinstance(value, str) or key_id in result:
                raise ValueError()
            key = base64.b64decode(value, validate=True)
            if len(key) != 32 or sha256(key) != key_id or base64.b64encode(key).decode() != value:
                raise ValueError()
            result[key_id] = key
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise PackageError("package_trust_invalid") from exc


def version(payload: Mapping[str, bytes]) -> str:
    try:
        header = re.search(rb"^ \* Version: ([0-9]+[.][0-9]+[.][0-9]+(?:-[A-Za-z0-9.-]+)?)$", payload["seo-master-plugin.php"], re.MULTILINE)
        readme = re.search(rb"^Stable tag: ([^\r\n]+)$", payload["readme.txt"], re.MULTILINE)
        constant = re.search(rb"define\('SEO_MASTER_CONNECTOR_VERSION', '([^']+)'\);", payload["seo-master-plugin.php"])
        if header is None or readme is None or constant is None or not header[1] == readme[1] == constant[1] or len(header[1]) > 32:
            raise PackageError("package_version_invalid")
        return header[1].decode("ascii")
    except (KeyError, UnicodeError) as exc:
        raise PackageError("package_version_invalid") from exc


def zip_bytes(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED, allowZip64=False) as archive:
        for name in sorted(files):
            info = ZipInfo(SLUG + "/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = ZIP_STORED
            archive.writestr(info, files[name])
    return output.getvalue()


def build_package(payload: Mapping[str, bytes], source_commit: str, key: Ed25519PrivateKey) -> bytes:
    if set(payload) != set(PAYLOAD) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise PackageError("package_source_invalid")
    if sum(map(len, payload.values())) > MAX_TOTAL or any(not 1 <= len(value) <= MAX_FILE for value in payload.values()):
        raise PackageError("package_budget")
    public = public_key_bytes(key)
    key_id = sha256(public)
    manifest = canonical({"schema_version": 1, "plugin_id": SLUG, "plugin_version": version(payload),
        "source_commit": source_commit, "key_id": key_id,
        "files": [{"path": path, "size": len(payload[path]), "sha256": sha256(payload[path])} for path in PAYLOAD]})
    signature = canonical({"algorithm": "ed25519", "key_id": key_id,
        "signature": base64.b64encode(key.sign(CONTEXT + manifest)).decode("ascii")})
    return zip_bytes({**payload, MANIFEST: manifest, SIGNATURE: signature})


@dataclass(frozen=True)
class VerifiedPackage:
    archive_sha256: str
    plugin_version: str
    source_commit: str
    key_id: str
    files: Mapping[str, bytes]


def verify_package(data: bytes, keys: Mapping[str, bytes], *, expected_sha256: str | None = None) -> VerifiedPackage:
    if not 1 <= len(data) <= MAX_ARCHIVE:
        raise PackageError("package_budget")
    digest = sha256(data)
    if expected_sha256 is not None and (HASH.fullmatch(expected_sha256) is None or digest != expected_sha256):
        raise PackageError("package_archive_digest_invalid")
    allowed = {*PAYLOAD, MANIFEST, SIGNATURE}
    files: dict[str, bytes] = {}
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) != len(allowed) or archive.comment or sum(item.file_size for item in infos) > MAX_TOTAL + 17000:
                raise PackageError("package_entries_invalid")
            for info in infos:
                prefix = SLUG + "/"
                name = info.filename[len(prefix):] if info.filename.startswith(prefix) else ""
                budget = 16384 if name == MANIFEST else 512 if name == SIGNATURE else MAX_FILE
                if (name not in allowed or name in files or info.is_dir() or info.flag_bits != 0 or info.extra or info.comment
                        or info.compress_type != ZIP_STORED or not 1 <= info.file_size <= budget
                        or info.compress_size != info.file_size or info.create_system != 3
                        or (info.external_attr >> 16) != (stat.S_IFREG | 0o644)):
                    raise PackageError("package_entries_invalid")
                files[name] = archive.read(info)
        # Re-encoding enforces the exact local/central layout, names, metadata,
        # no ZIP64/multidisk/Unicode extras/preambles/comments/trailing bytes.
        if set(files) != allowed or zip_bytes(files) != data:
            raise PackageError("package_zip_not_canonical")
    except (BadZipFile, OSError, RuntimeError, NotImplementedError, OverflowError) as exc:
        raise PackageError("package_zip_invalid") from exc
    manifest = _json(files[MANIFEST])
    envelope = _json(files[SIGNATURE])
    if (set(manifest) != {"schema_version", "plugin_id", "plugin_version", "source_commit", "key_id", "files"}
            or not isinstance(manifest["schema_version"], int) or isinstance(manifest["schema_version"], bool)
            or manifest["schema_version"] != 1 or manifest["plugin_id"] != SLUG
            or set(envelope) != {"algorithm", "key_id", "signature"} or envelope["algorithm"] != "ed25519"
            or envelope["key_id"] != manifest["key_id"]):
        raise PackageError("package_manifest_invalid")
    key_id = manifest["key_id"]
    if not isinstance(key_id, str) or key_id not in keys or len(keys[key_id]) != 32 or sha256(keys[key_id]) != key_id:
        raise PackageError("package_untrusted_key")
    try:
        encoded = envelope["signature"]
        if not isinstance(encoded, str):
            raise ValueError()
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode() != encoded:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(keys[key_id]).verify(signature, CONTEXT + files[MANIFEST])
    except (InvalidSignature, ValueError) as exc:
        raise PackageError("package_signature_invalid") from exc
    payload = {path: files[path] for path in PAYLOAD}
    entries = [{"path": path, "size": len(payload[path]), "sha256": sha256(payload[path])} for path in PAYLOAD]
    if canonical(manifest["files"]) != canonical(entries) or manifest["plugin_version"] != version(payload):
        raise PackageError("package_files_invalid")
    commit = manifest["source_commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise PackageError("package_source_invalid")
    return VerifiedPackage(digest, version(payload), commit, key_id, files)
