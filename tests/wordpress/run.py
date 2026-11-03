"""Изолированные PHP unit/contract и общие Python/PHP vectors; не WP/InnoDB E2E."""
from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import TypeAlias, Union

JsonValue: TypeAlias = Union[None, bool, int, str, list["JsonValue"], dict[str, "JsonValue"]]
REQUIRED_PHP_SOURCES = (
    "adapters/wordpress-plugin/seo-master-plugin.php",
    "adapters/wordpress-plugin/uninstall.php",
    "adapters/wordpress-plugin/admin/settings.php",
    "adapters/wordpress-plugin/includes/api-handler.php",
    "adapters/wordpress-plugin/includes/compatibility-checker.php",
    "adapters/wordpress-plugin/includes/hmac-validator.php",
    "adapters/wordpress-plugin/includes/meta-filter.php",
    "adapters/wordpress-plugin/includes/signature-verifier.php",
    "adapters/wordpress-plugin/includes/v2-protocol.php",
    "adapters/wordpress-plugin/includes/v2-service.php",
    "adapters/wordpress-plugin/includes/v2-sql.php",
    "adapters/wordpress-plugin/includes/v2-store.php",
    "tests/wordpress/legacy-regression.php",
    "tests/wordpress/store-contract.php",
    "tests/wordpress/v2-contract.php",
    "tests/wordpress/vectors.php",
    "tests/wordpress/wp-adapter.php",
)
ARTIFACT_DIRECTORIES = frozenset({"dist", "generated", "vendor"})


def discover_php_sources(root: Path) -> list[Path]:
    missing = [name for name in REQUIRED_PHP_SOURCES if not (root / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing required PHP source(s): {', '.join(missing)}")
    plugin = root / "adapters/wordpress-plugin"
    sources = [source for source in plugin.glob("*.php") if source.is_file()]
    for directory in (plugin / "admin", plugin / "includes", root / "tests/wordpress"):
        sources.extend(
            source for source in directory.rglob("*.php")
            if source.is_file()
            and not ARTIFACT_DIRECTORIES.intersection(source.relative_to(directory).parts[:-1])
        )
    return sorted(sources)


def as_object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected JSON object")
    # json.loads is untyped; all nodes are recursively checked at this boundary.
    return {key: as_json(entry) for key, entry in value.items()}


def as_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, list):
        return [as_json(entry) for entry in value]
    return as_object(value)


def as_array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError("Expected JSON array")
    return value


def as_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected JSON string")
    return value


def canonical(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_php(php: Path, root: Path, environment: dict[str, str], arguments: list[str], stdin: str | None = None) -> str:
    result = subprocess.run(
        [str(php), "-n", "-d", "disable_functions=ctype_digit", *arguments],
        cwd=root, env=environment, input=stdin,
        encoding="utf-8", capture_output=True, timeout=45, check=False,
    )
    if result.returncode != 0:
        # PHP harnesses never include key values in their diagnostics.
        raise RuntimeError(f"PHP gate failed: {arguments}\n{result.stdout}\n{result.stderr}")
    if result.stderr:
        raise RuntimeError(f"Unexpected PHP diagnostics: {result.stderr}")
    return result.stdout


def check_vectors(php: Path, root: Path, environment: dict[str, str]) -> None:
    vector_path = root / "tests/vectors/wordpress/v2.json"
    vectors = as_object(json.loads(vector_path.read_text(encoding="utf-8")))
    secret = secrets.token_hex(32)
    receipt_secret = secrets.token_hex(32)
    child_environment = {**environment, "SM_TEST_HMAC_KEY": secret, "SM_TEST_RECEIPT_KEY": receipt_secret}
    actual = as_object(json.loads(run_php(
        php, root, child_environment, ["tests/wordpress/vectors.php"], canonical(vectors),
    )))
    headers = as_object(vectors["headers"])
    expected_signing: list[JsonValue] = []
    for entry in as_array(vectors["signing"]):
        vector = as_object(entry)
        body_hash = digest(as_string(vector["body"]))
        message = "\n".join([
            "SEO-MASTER-HMAC-V2", as_string(vector["method"]), as_string(vector["route"]), body_hash,
            *[as_string(headers[name]) for name in (
                "x-seo-timestamp", "x-seo-nonce", "x-seo-key-id", "x-seo-project-id", "x-seo-site-id", "x-seo-operation-id",
            )],
        ])
        expected_signing.append({
            "body_digest": body_hash, "message": message,
            "signature": hmac.new(secret.encode("ascii"), message.encode("utf-8"), hashlib.sha256).hexdigest(),
        })
    receipt_key = hmac.new(receipt_secret.encode("ascii"), b"SEO-MASTER-RECEIPT-KEY-V2", hashlib.sha256).digest()
    expected_snapshots: list[JsonValue] = []
    for snapshot in as_array(vectors["snapshots"]):
        encoded = canonical(snapshot)
        expected_snapshots.append({
            "canonical": encoded, "digest": digest(encoded),
            "receipt_signature": hmac.new(receipt_key, ("SEO-MASTER-RECEIPT-V2\n" + encoded).encode("utf-8"), hashlib.sha256).hexdigest(),
        })
    expected_changes = [as_object(entry)["valid"] for entry in as_array(vectors["changes"])]
    expected_documents = [as_object(entry)["valid"] for entry in as_array(vectors["documents"])]
    expected_rollbacks: list[JsonValue] = []
    guard_key = hmac.new(receipt_secret.encode("ascii"), b"SEO-MASTER-ROLLBACK-GUARD-KEY-V2", hashlib.sha256).digest()
    for entry in as_array(vectors["rollback"]):
        vector = as_object(entry)
        original = as_object(vector["original"])
        references = {
            "project_id": original["project_id"], "site_id": original["site_id"], "entity_id": original["entity_id"],
            "original_operation_id": original["operation_id"], "original_receipt_id": original["receipt_id"],
            "original_request_hash": original["request_hash"],
        }
        identity_digest = digest("SEO-MASTER-ROLLBACK-GUARD-IDENTITY-V2\n" + canonical(references))
        uuid_bytes = bytearray.fromhex(identity_digest[:32])
        uuid_bytes[6] = (uuid_bytes[6] & 15) | 128
        uuid_bytes[8] = (uuid_bytes[8] & 63) | 128
        uuid_hex = uuid_bytes.hex()
        guard_id = f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:]}"
        command = as_object(vector["command"])
        proposal = {name: value for name, value in command.items() if name not in {"operation_id", "approval_ref", "proposal_hash"}}
        proposal_hash = digest("SEO-MASTER-ROLLBACK-PROPOSAL-V2\n" + canonical(proposal))
        command["proposal_hash"] = proposal_hash
        request_hash = digest(canonical(command))
        guard: dict[str, JsonValue] = {
            "protocol": "seo-master/v2", "kind": "rollback_guard", "guard_id": guard_id,
            "identity_digest": identity_digest, "references": references, "rollback_operation_id": command["operation_id"],
            "rollback_request_hash": request_hash,
            "rollback_receipt_id": digest("SEO-MASTER-RECEIPT-ID-V2\n" + as_string(command["operation_id"]) + "\n" + request_hash),
        }
        guard_signature = hmac.new(guard_key, ("SEO-MASTER-ROLLBACK-GUARD-V2\n" + canonical(guard)).encode("utf-8"), hashlib.sha256).hexdigest()
        ordinary_signature = hmac.new(receipt_key, ("SEO-MASTER-RECEIPT-V2\n" + canonical(guard)).encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(guard_signature, ordinary_signature):
            raise RuntimeError("Guard and receipt purposes must be separated")
        expected_rollbacks.append({
            "identity": {"guard_id": guard_id, "identity_digest": identity_digest, "references": references},
            "proposal_hash": proposal_hash, "guard_signature": guard_signature, "receipt_signature": ordinary_signature,
        })
    expected: dict[str, JsonValue] = {
        "signing": expected_signing, "snapshots": expected_snapshots, "changes": expected_changes, "documents": expected_documents,
        "rollback": expected_rollbacks,
    }
    if actual != expected:
        raise RuntimeError("Python/PHP vector mismatch (secret material omitted)")
    if len({as_object(entry)["body_digest"] for entry in expected_signing}) != len(expected_signing):
        raise RuntimeError("Signing corpus must exercise distinct byte encodings")
    print(f"Python/PHP vectors: {len(expected_signing)} signing + {len(expected_snapshots)} canonical/receipt + {len(expected_changes)} schema + {len(expected_documents)} raw-document + {len(expected_rollbacks)} rollback identity/purpose passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--php", required=True, type=Path)
    parser.add_argument("--lint-only", action="store_true")
    arguments = parser.parse_args()
    php = arguments.php.resolve(strict=True)
    if not php.is_file():
        raise ValueError("--php must point to a PHP executable")
    root = Path(__file__).resolve().parents[2]
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "PATH", "PATHEXT", "COMSPEC"}
    }
    sources = discover_php_sources(root)
    for source in sources:
        if not source.read_bytes().startswith(b"<?php"):
            raise RuntimeError(f"PHP source emits bytes before opening tag: {source.relative_to(root)}")
        run_php(php, root, environment, ["-l", str(source)])
    ast.parse(Path(__file__).read_text(encoding="utf-8"))
    print(f"PHP syntax: {len(sources)} files passed; Python runner syntax passed")
    if not arguments.lint_only:
        for harness in ("legacy-regression.php", "v2-contract.php", "wp-adapter.php", "store-contract.php"):
            print(run_php(php, root, environment, [f"tests/wordpress/{harness}"]), end="")
        check_vectors(php, root, environment)
        print("PASS: isolated unit/contracts only; real WordPress/InnoDB/concurrency/crash gates are NOT satisfied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
