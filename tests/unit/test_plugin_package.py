"""Real Ed25519/ZIP parser tests; no WordPress runtime claim."""
import base64
from copy import deepcopy
import io
import json
import os
import stat
import subprocess
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from shared.plugin_package import (
    MANIFEST,
    PAYLOAD,
    SIGNATURE,
    SLUG,
    PackageError,
    build_package,
    canonical,
    public_key_bytes,
    read_package_file,
    sha256,
    trust_keys,
    verify_package,
    zip_bytes,
)


@pytest.fixture()
def signed():
    # Parser/crypto fixtures only. The native packaging gate uses actual Git blobs.
    payload = {path: b"fixture\n" for path in PAYLOAD}
    payload[
        "seo-master-plugin.php"
    ] = b"<?php\n/**\n * Version: 0.3.0-dev\n */\ndefine('SEO_MASTER_CONNECTOR_VERSION', '0.3.0-dev');\n"
    payload["readme.txt"] = b"Stable tag: 0.3.0-dev\n"
    key = Ed25519PrivateKey.generate()
    public = public_key_bytes(key)
    keys = {sha256(public): public}
    data = build_package(payload, "a" * 40, key)
    return payload, key, keys, data


def files_in(data):
    with ZipFile(io.BytesIO(data)) as archive:
        return {
            info.filename[len(SLUG) + 1 :]: archive.read(info)
            for info in archive.infolist()
        }


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_trust",
        "missing_digest",
        "wrong_digest",
        "tampered",
        "untrusted",
        "missing_file",
    ],
)
def test_download_only_explicit_verified_snapshot(signed, tmp_path, monkeypatch, fault):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.api_gateway.routes import plugin_routes

    _, _, keys, data = signed
    artifact = tmp_path / "selected.zip"
    artifact.write_bytes(data if fault != "tampered" else data + b"tampered")
    trust = json.dumps(
        {
            identity: base64.b64encode(public).decode()
            for identity, public in keys.items()
        }
    )
    monkeypatch.setattr(
        plugin_routes.settings, "WORDPRESS_PLUGIN_PACKAGE_PATH", str(artifact)
    )
    monkeypatch.setattr(
        plugin_routes.settings,
        "WORDPRESS_PLUGIN_PACKAGE_SHA256",
        ""
        if fault == "missing_digest"
        else "0" * 64
        if fault == "wrong_digest"
        else sha256(data),
    )
    monkeypatch.setattr(
        plugin_routes.settings,
        "WORDPRESS_PLUGIN_RELEASE_KEYS_JSON",
        "" if fault == "missing_trust" else "{}" if fault == "untrusted" else trust,
    )
    if fault == "missing_file":
        artifact.unlink()
    original = plugin_routes.verify_package

    def verify_then_replace(*args, **kwargs):
        result = original(*args, **kwargs)
        artifact.write_bytes(b"replacement after verification")
        return result

    monkeypatch.setattr(plugin_routes, "verify_package", verify_then_replace)
    app = FastAPI()
    app.include_router(plugin_routes.router)
    with TestClient(app) as client:
        response = client.get("/api/wordpress-plugin")
    if fault is None:
        assert response.status_code == 200 and response.content == data
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    else:
        assert response.status_code == 503
        assert response.json() == {
            "detail": "WordPress plugin package is not available"
        }


def test_deterministic_package_and_out_of_band_trust(signed):
    payload, key, keys, data = signed
    assert build_package(deepcopy(payload), "a" * 40, key) == data
    result = verify_package(data, keys, expected_sha256=sha256(data))
    assert result.source_commit == "a" * 40 and result.plugin_version == "0.3.0-dev"
    assert set(result.files) == {*PAYLOAD, MANIFEST, SIGNATURE}
    encoded = json.dumps(
        {
            identity: base64.b64encode(public).decode()
            for identity, public in keys.items()
        }
    )
    assert trust_keys(encoded) == keys
    with pytest.raises(PackageError, match="untrusted"):
        verify_package(data, {})
    with pytest.raises(PackageError, match="archive_digest"):
        verify_package(data, keys, expected_sha256="0" * 64)


@pytest.mark.parametrize("path", PAYLOAD)
def test_modification_of_every_payload_is_detected(signed, path):
    _, _, keys, data = signed
    files = files_in(data)
    files[path] += b"\n/* tampered */"
    with pytest.raises(PackageError):
        verify_package(zip_bytes(files), keys)


@pytest.mark.parametrize(
    "fault",
    [
        "manifest",
        "signature",
        "key",
        "extra_key",
        "duplicate_json",
        "missing_file",
        "extra_file",
    ],
)
def test_manifest_signature_and_inventory_fail_closed(signed, fault):
    _, _, keys, data = signed
    files = files_in(data)
    if fault == "manifest":
        manifest = json.loads(files[MANIFEST])
        manifest["source_commit"] = "b" * 40
        files[MANIFEST] = canonical(manifest)
    elif fault == "duplicate_json":
        files[MANIFEST] = files[MANIFEST].replace(
            b'"schema_version":1', b'"schema_version":1,"schema_version":1'
        )
    elif fault in {"signature", "key", "extra_key"}:
        envelope = json.loads(files[SIGNATURE])
        if fault == "signature":
            envelope["signature"] = base64.b64encode(bytes(64)).decode()
        if fault == "key":
            envelope["key_id"] = "0" * 64
        if fault == "extra_key":
            envelope["public_key"] = base64.b64encode(
                public_key_bytes(Ed25519PrivateKey.generate())
            ).decode()
        files[SIGNATURE] = canonical(envelope)
    elif fault == "missing_file":
        del files[PAYLOAD[0]]
    else:
        files["includes/extra.php"] = b'<?php echo "bad";'
    with pytest.raises(PackageError):
        verify_package(zip_bytes(files), keys)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.php",
        "/absolute.php",
        "C:/evil.php",
        "includes/../evil.php",
        ".user.ini",
        ".htaccess",
        "includes/V2-store.php",
        "includes/v2-store.php.",
        "includes/v2-store.php ",
    ],
)
def test_zip_paths_and_unlisted_files_are_rejected(signed, name):
    _, _, keys, data = signed
    files = files_in(data)
    files[name] = b"bad"
    with pytest.raises(PackageError):
        verify_package(zip_bytes(files), keys)


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "symlink",
        "unicode_extra",
        "comment",
        "preamble",
        "trailing",
        "local_header",
    ],
)
def test_ambiguous_zip_layout_cannot_reach_extraction(signed, fault):
    _, _, keys, data = signed
    if fault == "preamble":
        changed = b"prefix" + data
    elif fault == "trailing":
        changed = data + b"<?php bad ?>"
    elif fault == "local_header":
        changed = data[:30] + b"x" + data[31:]
    else:
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_STORED) as archive:
            for name, contents in files_in(data).items():
                info = ZipInfo(SLUG + "/" + name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                if name == PAYLOAD[0]:
                    if fault == "symlink":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    if fault == "unicode_extra":
                        info.extra = b"\x75\x70\x00\x00"
                    if fault == "comment":
                        info.comment = b"ambiguous"
                archive.writestr(info, contents)
            if fault == "duplicate":
                with pytest.warns(UserWarning):
                    archive.writestr(SLUG + "/" + PAYLOAD[0], b"bad")
        changed = output.getvalue()
    with pytest.raises(PackageError):
        verify_package(changed, keys)


def test_zip_entry_budgets_and_untrusted_self_signed_root(signed):
    payload, _, keys, data = signed
    key = Ed25519PrivateKey.generate()
    with pytest.raises(PackageError, match="untrusted"):
        verify_package(build_package(payload, "a" * 40, key), keys)
    with pytest.raises(PackageError, match="budget"):
        verify_package(data + bytes(3 * 1024 * 1024), keys)


@pytest.fixture()
def source_repository(signed, tmp_path):
    from scripts.backend import build_tool_environment
    from scripts.plugin_package import PREFIX

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            env=build_tool_environment(),
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout

    git("init", "--quiet")
    for name, value in signed[0].items():
        target = tmp_path / PREFIX / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    git("add", "--", PREFIX)

    def commit():
        git(
            "-c",
            "user.name=Package Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "Synthetic package fixture",
        )

    commit()
    return tmp_path, git, commit


def test_build_reads_exact_commit_not_dirty_checkout(signed, source_repository):
    from scripts.plugin_package import PREFIX, committed_payload

    root, git, _ = source_repository
    revision, before = committed_payload(root, "HEAD")
    (root / PREFIX / PAYLOAD[0]).write_bytes(b"uncommitted dirty bytes")
    (root / PREFIX / "untracked.php").write_bytes(b"untracked ignored by release")
    after_revision, after = committed_payload(root, revision)
    assert before == after == signed[0] and after_revision == revision
    assert build_package(before, revision, signed[1]) == build_package(
        after, revision, signed[1]
    )
    assert revision == git("rev-parse", "HEAD").decode().strip()


def test_package_read_accepts_regular_bounded_file_only(signed, tmp_path):
    path = tmp_path / "artifact"
    path.write_bytes(signed[3])
    assert read_package_file(path) == signed[3]
    path.write_bytes(bytes(3 * 1024 * 1024 + 1))
    with pytest.raises(PackageError):
        read_package_file(path)
    path.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(path)
        with pytest.raises(PackageError):
            read_package_file(path)


@pytest.mark.parametrize("fault", ["executable", "symlink", "unlisted", "missing"])
def test_unapproved_git_inventory_rejected(source_repository, fault):
    from scripts.plugin_package import PREFIX, committed_payload

    root, git, commit = source_repository
    path = PREFIX + PAYLOAD[0]
    if fault in {"executable", "symlink"}:
        blob = git("rev-parse", "HEAD:" + path).decode().strip()
        git(
            "update-index",
            "--cacheinfo",
            "100755" if fault == "executable" else "120000",
            blob,
            path,
        )
    elif fault == "unlisted":
        (root / PREFIX / "injected.php").write_bytes(b"<?php injected")
        git("add", "--", PREFIX + "injected.php")
    else:
        git("rm", "--cached", "--", path)
    commit()
    with pytest.raises(PackageError):
        committed_payload(root, "HEAD")
