"""Exercise the installed signed connector with actual pinned SEO-plugin ZIPs."""

import argparse
import asyncio
from collections.abc import Awaitable
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

import httpcore

from services.client_api_gateway.public_verifier import PublicPageReader
from services.client_api_gateway.wordpress_v2 import WordPressV2
from services.semantic_service.proposals.model import ExternalEntity
from shared.cms_http import CMSHttpClient
from shared.hitl_execution import ExecutionError, ExecutionRequest, WordPressBinding, bind_proposal_hash, make_command
from shared.hitl_rollback import bind_rollback_hash, make_rollback_command
from shared.netpolicy import NetPolicy, SafeHttpClient
from shared.plugin_package import sha256
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture
from tests.wordpress.field_matrix import php_value
from tests.wordpress.native import NativeWordPress
from tests.wordpress.native_contract import resolver


@dataclass(frozen=True)
class Plugin:
    slug: str
    version: str
    entry: str
    checksum: str

    @property
    def filename(self) -> str:
        return f'{self.slug}.{self.version}.zip'


PLUGINS = (
    Plugin('wordpress-seo', '26.2', 'wp-seo.php', 'b59d2393653b3a27172de31cbeb5999e004fcf0281548b65c4e97edb0ca8dbc9'),
    Plugin('seo-by-rank-math', '1.0.282', 'rank-math.php', 'd4434c5726c62cdd819f0129cac3df5fea0c8efafbcd812856cf87f5e05691a3'),
)


class ContainerStream(httpcore.AsyncNetworkStream):
    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        assert self.process.stdout is not None
        try:
            return await asyncio.wait_for(self.process.stdout.read(max_bytes), timeout)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout() from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(buffer)
        try:
            await asyncio.wait_for(self.process.stdin.drain(), timeout)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout() from exc
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise httpcore.WriteError() from exc

    async def aclose(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


class ContainerBackend(httpcore.AnyIOBackend):
    """Raw HTTP through Docker exec to the fixture's loopback, with no host port."""
    def __init__(self, wp: NativeWordPress):
        super().__init__()
        self.wp = wp

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options: object = None) -> httpcore.AsyncNetworkStream:
        if (host, port) != ('127.0.0.1', 80):
            raise RuntimeError('SEO fixture destination escaped')
        # Bash sees only this fixed relay program. HTTP request bytes arrive via stdin.
        relay = 'exec 3<>/dev/tcp/127.0.0.1/80; cat <&3 & reader=$!; trap \'kill "$reader" 2>/dev/null || true\' EXIT; cat >&3'
        process = await asyncio.create_subprocess_exec(*self.wp.docker, 'exec', '-i', self.wp.wp,
            'bash', '-ec', relay, env=self.wp.tool_environment, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        return ContainerStream(process)


def install(wp: NativeWordPress, directory: Path, plugin: Plugin) -> None:
    archive = directory / plugin.filename
    info = json.loads(wp.run('network', 'inspect', wp.network))[0]
    container = json.loads(wp.run('inspect', wp.wp))[0]
    if (not info['Internal'] or info['Labels']['seo.test.owner'] != wp.owner
            or set(container['NetworkSettings']['Networks']) != {wp.network}
            or container['Config']['Labels']['seo.test.owner'] != wp.owner
            or container['HostConfig']['PortBindings']):
        raise ValueError('SEO fixture container is outside the isolated network')
    if not archive.is_file() or archive.is_symlink() or not archive.resolve(strict=True).is_relative_to(directory):
        raise ValueError('SEO fixture ZIP must be a file inside the supplied directory')
    if archive.stat().st_size > 16 * 1024 * 1024 or sha256(archive.read_bytes()) != plugin.checksum:
        raise ValueError('SEO fixture ZIP checksum mismatch')
    wp.run('cp', str(archive), wp.wp + ':/tmp/seo-matrix.zip')
    # The copied bytes are verified again inside the owned, isolated container.
    wp.php("""
if (hash_file('sha256', '/tmp/seo-matrix.zip') !== EXPECTED_HASH) { throw new RuntimeException('seo_zip_changed'); }
require_once ABSPATH . 'wp-admin/includes/file.php';
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
if (!defined('FS_METHOD')) { define('FS_METHOD', 'direct'); }
wp_set_current_user(get_user_by('login','fixture_admin')->ID);
ob_start();
$result = (new Plugin_Upgrader(new Automatic_Upgrader_Skin()))->install('/tmp/seo-matrix.zip');
ob_end_clean();
if ($result !== true) { throw new RuntimeException('seo_fixture_install_failed'); }
$activated = activate_plugin(PLUGIN_ENTRY);
if (is_wp_error($activated) || !is_plugin_active(PLUGIN_ENTRY) || get_plugin_data(WP_PLUGIN_DIR.'/'.PLUGIN_ENTRY)['Version'] !== PLUGIN_VERSION) {
    throw new RuntimeException('seo_fixture_activation_failed');
}
""".replace('EXPECTED_HASH', php_value(plugin.checksum)).replace('PLUGIN_ENTRY', php_value(plugin.slug + '/' + plugin.entry))
          .replace('PLUGIN_VERSION', php_value(plugin.version)))


def forward_record(document: dict[str, Any], binding: WordPressBinding, before: dict[str, Any],
                   fields: dict[str, str]) -> dict[str, Any]:
    changed = deepcopy(document)
    changed['changes'] = [{'cms_field': field, 'before_state': before['fields'][field]['state'],
        'before': before['fields'][field].get('value'), 'mode': 'set', 'after': value} for field, value in fields.items()]
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=TASK_ID, approval_id=str(uuid4()),
        recommendation_version_id=document['version_id'], content_hash=document['content_hash'])
    command, after = make_command(changed, refs, binding, before)
    command.update(target_url=document['target']['url'], authorization_deadline=int(time.time()) + 60)
    bind_proposal_hash(command)
    return {**refs.model_dump(), 'command': command, 'before': before, 'after': after}


async def assert_rejected(call: Awaitable[object], error: str) -> None:
    try:
        await call
    except ExecutionError as exc:
        assert str(exc) == error
    else:
        raise AssertionError(f'Expected CMS error {error}')


async def matrix(wp: NativeWordPress, plugin: Plugin) -> None:
    binding = WordPressBinding(base_url='http://wordpress.test', site_id=wp.site_id, key_id='fixture')
    policy = NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))
    backend = ContainerBackend(wp)
    target = WordPressV2(PROJECT_ID, binding, wp.secret, wp.receipt_secret,
                         http=CMSHttpClient(policy=policy, resolver=resolver, network_backend=backend))
    reader = PublicPageReader(http=SafeHttpClient(policy=policy, resolver=resolver, network_backend=backend))
    capabilities_url = 'http://wordpress.test/wp-json/seo-master/v2/capabilities'
    rank_math = plugin.slug == 'seo-by-rank-math'
    for kind in ('post', 'page'):
        if rank_math:
            wp.php("delete_option('rank_math_registration_skip');")
        entity = int(wp.php('echo wp_insert_post(' + php_value({'post_type': kind, 'post_status': 'publish',
            'post_name': 'seo-matrix-' + kind, 'post_title': 'Original heading', 'post_content': 'Original body'}) + ');'))
        if kind == 'page':
            wp.php(f"update_post_meta({entity}, '_seo_master_meta_title', 'Previous title');"
                   f"update_post_meta({entity}, '_seo_master_meta_description', 'Previous description');")
        url = wp.php(f'echo get_permalink({entity});')
        _, _, version = management_proposal_fixture(entity=ExternalEntity(kind=kind, id=str(entity)), target_url=url)
        document = version.model_dump(mode='json')
        before = await target.preflight(document)

        values = {'title': 'Новый заголовок & «кавычки»', 'description': 'Описание & «кавычки»'}
        record = forward_record(document, binding, before, values)
        after = record['after']
        if rank_math:
            result = await reader.http.fetch(capabilities_url)
            capabilities = json.loads(result.body)
            assert result.status == 200 and not capabilities['apply_enabled'] and capabilities['rollback_enabled']
            assert capabilities['rank_math'] == 'setup_required'
            await assert_rejected(target.dry_run(record), 'cms_request_rejected:422:rank_math_setup_required')
            await assert_rejected(target.apply(record), 'cms_request_rejected:422:rank_math_setup_required')
            await assert_rejected(target.status(record), 'cms_operation_not_found')
            assert await target.preflight(document) == before
            # Rank Math's offline "Skip Now" choice persists exactly this option.
            wp.php("add_option('rank_math_registration_skip', true);")
            result = await reader.http.fetch(capabilities_url)
            assert result.status == 200 and json.loads(result.body)['apply_enabled']
        initial_public = await reader.read(url)
        assert initial_public.facts is not None

        jsonld = forward_record(document, binding, before,
            {'jsonld': '{"@context":"https://schema.org","@type":"WebPage","name":"Manual"}'})
        await assert_rejected(target.dry_run(jsonld), 'cms_request_rejected:422:unsupported_field')
        await assert_rejected(target.apply(jsonld), 'cms_request_rejected:422:unsupported_field')
        await assert_rejected(target.status(jsonld), 'cms_operation_not_found')
        assert await target.preflight(document) == before
        assert (await reader.read(url)).facts == initial_public.facts

        await target.dry_run(record)
        assert await target.preflight(document) == before
        assert (await reader.read(url)).facts == initial_public.facts
        receipt = await target.apply(record)
        assert await target.status(record) == receipt
        assert await target.preflight(document) == after
        public = (await reader.read(url)).facts
        assert public is not None, f'no public HTML facts for {kind}'
        assert public.titles == (values['title'],), (kind, 'titles', public.titles)
        assert public.descriptions == (values['description'],), (kind, 'descriptions', public.descriptions)
        if rank_math:
            wp.php("delete_option('rank_math_registration_skip');")
            assert await target.status(record) == receipt
            assert await target.apply(record) == receipt

        intent = {'project_id': PROJECT_ID, 'operation_id': str(uuid4()), 'task_id': TASK_ID,
            'recommendation_version_id': version.version_id, 'approval_id': record['approval_id'],
            'reason': 'Restore original SEO output', 'expected_current': after, 'restore_before': before,
            'original_binding': binding.model_dump(), 'original_operation_id': record['operation_id'],
            'original_receipt_id': receipt['receipt_id'], 'original_request_hash': receipt['request_hash'],
            'original_proposal_hash': receipt['proposal_hash']}
        rollback, restored = make_rollback_command(intent, binding, after)
        rollback.update(target_url=url, authorization_deadline=int(time.time()) + 60)
        bind_rollback_hash(rollback)
        reverse = {**intent, 'operation_type': 'rollback', 'command': rollback, 'before': after, 'after': restored}
        await target.dry_run(reverse)
        assert await target.preflight(document) == after
        reversed_receipt = await target.apply(reverse)
        assert await target.status(reverse) == reversed_receipt
        assert await target.preflight(document) == before
        if rank_math:
            wp.php("add_option('rank_math_registration_skip', true);")
        assert (await reader.read(url)).facts == initial_public.facts
        untouched = json.loads(wp.php(f'$p=get_post({entity}); echo json_encode([$p->post_title,$p->post_content]);'))
        assert untouched == ['Original heading', 'Original body']


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-context', default='desktop-linux')
    parser.add_argument('--plugins-dir', type=Path, required=True)
    parser.add_argument('--plugin-revision', help='Git revision for connector payload; defaults to working source')
    parser.add_argument('--plugin', choices=tuple(plugin.slug for plugin in PLUGINS))
    args = parser.parse_args()
    directory = args.plugins_dir.resolve(strict=True)
    os.environ['NETPOLICY_PROFILE'] = 'test'
    for plugin in PLUGINS:
        if args.plugin and plugin.slug != args.plugin:
            continue
        with NativeWordPress(Path(__file__).resolve().parents[2], project_id=PROJECT_ID, site_id=str(uuid4()),
                             context=args.docker_context, revision=args.plugin_revision, internal_network=True) as wp:
            install(wp, directory, plugin)
            versions = json.loads(wp.php("global $wpdb; echo wp_json_encode([get_bloginfo('version'), PHP_VERSION, "
                                         "get_stylesheet(), wp_get_theme()->get('Version'), $wpdb->get_var('SELECT VERSION()')]);"))
            print('WordPress, PHP, theme, theme version, MariaDB:', *versions, flush=True)
            asyncio.run(matrix(wp, plugin))
            print(f'PASS {plugin.slug} {plugin.version}: post/page title, description, exact rollback and JSON-LD denial.')


if __name__ == '__main__':
    main()
