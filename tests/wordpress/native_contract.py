"""Real WP 6.8.3/MariaDB transport contract, deliberately separate from staging E2E.

Run from the pinned management-test environment:
python -B -m tests.wordpress.native_contract --docker-context desktop-linux
"""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpcore

from services.client_api_gateway.wordpress_v2 import WordPressV2
from services.semantic_service.proposals.model import ExternalEntity
from shared.cms_http import CMSHttpClient
from shared.hitl_execution import ExecutionError, ExecutionRequest, WordPressBinding, bind_proposal_hash, make_command
from shared.netpolicy import NetPolicy
from shared.netpolicy import SafeHttpClient
from services.client_api_gateway.public_verifier import PublicPageReader
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture
from tests.wordpress.native import NativeWordPress


class LoopbackBackend(httpcore.AnyIOBackend):
    """Only the fixture's single mapped port; production transport has no such mapping."""
    def __init__(self, port: int):
        super().__init__()
        self.port = port

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != "127.0.0.1" or port != 80:
            raise RuntimeError("fixture destination escaped")
        return await super().connect_tcp(host, self.port, timeout=timeout, local_address=local_address, socket_options=socket_options)


async def resolver(host: str, port: int) -> list[str]:
    if host != "wordpress.test" or port != 80:
        raise RuntimeError("fixture DNS scope escaped")
    return ["127.0.0.1"]


async def expired_while_locked(wp: NativeWordPress, target: WordPressV2, record: dict) -> None:
    # A real second mysqli connection holds the row beyond the signed deadline.
    proc = await asyncio.create_subprocess_exec(*wp.docker, "exec", "-i", wp.wp, "php",
        env=wp.tool_environment, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    source = ("<?php $_SERVER['HTTP_HOST']='wordpress.test'; $_SERVER['REQUEST_URI']='/'; require '/var/www/html/wp-load.php'; "
              "global $wpdb; $wpdb->query('START TRANSACTION'); $wpdb->get_results('SELECT ID FROM '.$wpdb->posts.' WHERE ID="
              + str(wp.entity_id) + " FOR UPDATE'); echo \"locked\\n\"; fflush(STDOUT); sleep(3); $wpdb->query('COMMIT');")
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(source.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    try:
        assert await asyncio.wait_for(proc.stdout.readline(), 5) == b"locked\n"
        invalid = deepcopy(record)
        invalid["command"]["authorization_deadline"] = int(time.time()) + 1
        bind_proposal_hash(invalid["command"])
        try:
            await target.apply(invalid)
        except ExecutionError as error:
            assert "authorization_expired" in str(error), str(error)
        else:
            raise AssertionError("CMS lock wait bypassed deadline")
        assert await asyncio.wait_for(proc.wait(), 5) == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def run_contract(wp: NativeWordPress) -> None:
    os.environ["NETPOLICY_PROFILE"] = "test"
    client = CMSHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({"wordpress.test"})),
                           resolver=resolver, network_backend=LoopbackBackend(wp.port))
    binding = WordPressBinding(base_url="http://wordpress.test", site_id=wp.site_id, key_id="fixture")
    target = WordPressV2(PROJECT_ID, binding, wp.secret, wp.receipt_secret, http=client)
    _, _, version = management_proposal_fixture(entity=ExternalEntity(kind="post", id=str(wp.entity_id)), target_url="http://wordpress.test/catalog")
    before = await target.preflight(version.model_dump(mode="json"))
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=TASK_ID, approval_id=str(uuid4()),
                            recommendation_version_id=version.version_id, content_hash=version.content_hash)
    command, after = make_command(version.model_dump(mode="json"), refs, binding, before)
    command.update(target_url=version.target.url, authorization_deadline=int(time.time()) + 60)
    bind_proposal_hash(command)
    record = {**refs.model_dump(), "command": command, "before": before, "after": after}
    await target.dry_run(record)
    await expired_while_locked(wp, target, record)
    # Signed CAS/target rejection has zero SEO writes and zero committed operations.
    for field, value in [("target_url", "http://wordpress.test/moved"), ("authorization_deadline", int(time.time()) - 1),
                         ("before_digest", "0" * 64)]:
        invalid = deepcopy(record)
        invalid["command"][field] = value
        bind_proposal_hash(invalid["command"])
        try:
            await target.apply(invalid)
        except ExecutionError:
            pass
        else:
            raise AssertionError("invalid target/deadline/precondition wrote data")
        assert json.loads(wp.php("global $wpdb; echo json_encode(['title'=>get_post_meta(" + str(wp.entity_id) + ", '_seo_master_meta_title', true), 'operations'=>(int)$wpdb->get_var('SELECT COUNT(*) FROM '.$wpdb->prefix.'seo_master_operations_v2')]);")) == {"title": "Мастерская обуви", "operations": 0}
    # Unknown Options API callbacks must fail closed even if raw SQL and SEO fields match.
    wp.php("file_put_contents(WPMU_PLUGIN_DIR.'/fixture-url-filter.php', '<?php add_filter(\"option_home\", static fn() => \"http://wordpress.test/moved\");');")
    try:
        try:
            await target.apply(record)
        except ExecutionError as error:
            assert "unsupported_target_binding" in str(error), str(error)
        else:
            raise AssertionError("Options API filter escaped target binding")
    finally:
        wp.php("unlink(WPMU_PLUGIN_DIR.'/fixture-url-filter.php');")
    # Two actual HTTP requests use distinct transport nonces but one operation/body.
    receipts = await asyncio.gather(target.apply(record), target.apply(record))
    assert receipts[0] == receipts[1]
    assert receipts[0]["outcome"] == "applied_unverified"
    assert await target.status(record) == receipts[0]
    state = json.loads(wp.php("global $wpdb; echo json_encode(['title'=>get_post_meta(" + str(wp.entity_id) + ", '_seo_master_meta_title', true), 'operations'=>(int)$wpdb->get_var('SELECT COUNT(*) FROM '.$wpdb->prefix.'seo_master_operations_v2')]);"))
    assert state == {"title": "Мастерская зимней обуви", "operations": 1}
    print("PASS real WordPress/MariaDB: signed dry-run; mutation-free URL/deadline/CAS/filter denials; expiry during real SQL lock wait; concurrent idempotent apply; signed status/receipt; exact persisted title; 1 operation. Public HTML verification/staging E2E not claimed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-context", default="desktop-linux")
    parser.add_argument("--with-management", action="store_true")
    parser.add_argument("--with-apps", action="store_true")
    args = parser.parse_args()
    with NativeWordPress(Path(__file__).resolve().parents[2], project_id=PROJECT_ID, site_id=str(uuid4()), context=args.docker_context) as wp:
        asyncio.run(run_contract(wp))
        if args.with_management or args.with_apps:
            # Native tests never load the checkout's working .env configuration.
            import tempfile
            from scripts.backend import build_test_environment
            with tempfile.TemporaryDirectory(prefix="seo-native-authority-") as directory:
                clean = build_test_environment(wp.root, Path(directory))
                previous = dict(os.environ)
                previous_directory = Path.cwd()
                os.environ.clear()
                os.environ.update(clean)
                os.environ["NETPOLICY_PROFILE"] = "test"
                os.chdir(directory)
                try:
                    from tests.wordpress.native_authority import run_authority
                    binding = WordPressBinding(base_url="http://wordpress.test", site_id=wp.site_id, key_id="fixture")
                    def factory(project_id, saved=None):
                        assert project_id == PROJECT_ID and saved in (None, binding)
                        return WordPressV2(PROJECT_ID, binding, wp.secret, wp.receipt_secret,
                            http=CMSHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({"wordpress.test"})),
                                resolver=resolver, network_backend=LoopbackBackend(wp.port)))
                    if args.with_management:
                        public_reader = PublicPageReader(http=SafeHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({"wordpress.test"})),
                            resolver=resolver, network_backend=LoopbackBackend(wp.port)))
                        asyncio.run(run_authority(wp, factory, public_reader))
                    if args.with_apps:
                        from tests.wordpress.app_http import run_apps
                        run_apps(wp)
                finally:
                    os.chdir(previous_directory)
                    os.environ.clear()
                    os.environ.update(previous)


if __name__ == "__main__":
    main()
