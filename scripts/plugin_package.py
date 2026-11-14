"""Build from committed Git blobs, or verify a package with out-of-band trust."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.backend import build_tool_environment
from shared.plugin_package import PAYLOAD, PackageError, build_package, read_package_file, sha256, trust_keys, verify_package

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "adapters/wordpress-plugin/"


def git_bytes(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=root, env=build_tool_environment(), capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise PackageError("package_git_failed")
    return result.stdout


def committed_payload(root: Path, revision: str) -> tuple[str, dict[str, bytes]]:
    # Resolve to a single exact commit before reading any tree/blob. No worktree
    # mtime, CRLF checkout, ignored files or dirty edits influence a release.
    commit = git_bytes(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode("ascii").strip()
    listing = git_bytes(root, "ls-tree", "-r", "-z", commit, "--", PREFIX)
    payload: dict[str, bytes] = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = header.split(b" ")
        path = raw_path.decode("utf-8")[len(PREFIX):]
        if path in PAYLOAD:
            if mode != b"100644" or kind != b"blob":
                raise PackageError("package_source_mode_invalid")
            value = git_bytes(root, "cat-file", "blob", blob.decode("ascii")).replace(b"\r\n", b"\n")
            value.decode("utf-8")
            if value.startswith(b"\xef\xbb\xbf"):
                raise PackageError("package_source_bom")
            payload[path] = value
        elif not path.startswith("dist/") and path.endswith((".php", ".js", ".css")):
            raise PackageError("package_unlisted_source")
    if set(payload) != set(PAYLOAD):
        raise PackageError("package_source_missing")
    return commit, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build")
    build.add_argument("--revision", default="HEAD")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--signing-key-env", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--trust-file", type=Path, required=True)
    verify.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            if not args.signing_key_env.isidentifier():
                raise PackageError("package_signing_key_invalid")
            raw = base64.b64decode(os.environ.get(args.signing_key_env, ""), validate=True)
            if len(raw) != 32:
                raise PackageError("package_signing_key_invalid")
            commit, payload = committed_payload(ROOT, args.revision)
            data = build_package(payload, commit, Ed25519PrivateKey.from_private_bytes(raw))
            # Exclusive create; never overwrite an artifact or follow a link.
            with args.output.open("xb") as output:
                output.write(data)
            print(json.dumps({"source_commit": commit, "archive_sha256": sha256(data), "bytes": len(data)}))
        else:
            data = read_package_file(args.package)
            with args.trust_file.open("r", encoding="utf-8") as source:
                keys = trust_keys(source.read(8193))
            info = verify_package(data, keys, expected_sha256=args.sha256)
            print(json.dumps({"source_commit": info.source_commit, "archive_sha256": info.archive_sha256,
                              "plugin_version": info.plugin_version, "key_id": info.key_id}))
        return 0
    except (PackageError, ValueError, OSError, UnicodeError, subprocess.SubprocessError):
        print("PLUGIN_PACKAGE_REJECTED: verify source, external trust, configuration and artifact; secrets redacted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
