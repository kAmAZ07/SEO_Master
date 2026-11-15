"""Isolated Compose browser acceptance for HITL; upstream audit is a persisted fixture."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tests.wordpress.native import NativeWordPress


CLIENT_ENTRY = """
import asyncio
from pathlib import Path
from shared.netpolicy import NetPolicy, SafeHttpClient
from shared.cms_http import CMSHttpClient
import services.client_api_gateway.hitl_executor as executor
from services.client_api_gateway import public_verifier
original=executor.wordpress_target
def target(project_id,binding=None):
    return original(project_id,binding,http=CMSHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))))
executor.wordpress_target=target
BaseReader=public_verifier.PublicPageReader
class HeldReader(BaseReader):
    async def read(self,url):
        while Path('/tmp/ar9-hold-public').exists():
            await asyncio.sleep(.1)
        return await super().read(url)
def reader():
    return HeldReader(http=SafeHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))))
public_verifier.PublicPageReader=reader
def baseline_reader():
    return BaseReader(http=SafeHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))))
executor.PublicPageReader=baseline_reader
import uvicorn
uvicorn.run('services.client_api_gateway.main:app',host='0.0.0.0',port=8005)
"""


def run(root: Path, directory: Path, wp: 'NativeWordPress') -> None:
    from datetime import datetime, timedelta, timezone
    from uuid import UUID, uuid4
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from database.models import DomainAuthorization
    from services.audit_service.db.models import CrawlResult
    from services.management_service.approval_service import ApprovalService
    from services.management_service.migrations.runner import migrate
    from services.management_service.models import Task, TaskStatus, TaskType
    from services.semantic_service.proposals.model import ExternalEntity
    from services.semantic_service.proposals.repository import row_from_version
    from shared.page_evidence import seal_evidence
    from tests.e2e.compose_stand import ComposeContainers
    from tests.fixtures.management_proposal import management_proposal_fixture
    from tests.wordpress.app_http import AppStand, request, roles
    containers = ComposeContainers(directory, wp.owner, wp.network, wp.run)
    wp.container_launcher = containers.launch
    print('Compose fixture owner: ' + wp.owner)
    browser_network = wp.owner + '-browser-net'
    wp.run('network', 'create', '--internal', '--label', 'seo.test.owner=' + wp.owner, browser_network)
    wp.resources.append(('network', browser_network))
    with wp:
        os.environ['TEST_PG_ADMIN_URL'] = wp.start_postgres()
        from tests.integration.test_migrations_postgres import TempDatabase
        stand = AppStand(wp)
        stand.build()
        stand.infrastructure()
        for service, dockerfile in (('frontend', 'docker/frontend.Dockerfile'), ('browser', 'tests/e2e/Dockerfile.browser')):
            image = wp.owner + '-' + service + ':test'
            print('Building fixture image: ' + service)
            wp.resources.append(('image', image))
            wp.run('build', '--file', str(root / dockerfile), '--tag', image,
                '--label', 'seo.test.owner=' + wp.owner, str(root), timeout=900)
            stand.images[service] = image
        with TempDatabase() as database:
            database.alembic('upgrade', 'head')
            with database.engine.connect() as connection:
                migrate(connection, expected_role=database.engine.url.username or '')
            credentials = roles(database, wp)
            try:
                api = stand.start('api', credentials['api'][2])
                stand.start('management', credentials['management'][2])
                password = secrets.token_urlsafe(24) + 'aA9!'
                wp.private_values.append(password)
                email = 'browser@example.com'
                user = request(api, 'POST', '/api/auth/register', body={'email': email, 'password': password})
                token = user['token']
                wp.private_values.extend([token, user['refreshToken']])
                project = request(api, 'POST', '/api/projects', token=token,
                    body={'name': 'Browser fixture', 'url': 'http://wordpress.test/'}, expected=201)
                project_id, task_id, now = project['id'], str(uuid4()), datetime.now(timezone.utc)
                workspace = database.scalar('SELECT workspace_id FROM audit_schema.projects WHERE id=:id', id=project_id)
                wp.php('update_option("seo_master_project_id",' + json.dumps(project_id) + ');')
                entity = int(wp.php("$id=wp_insert_post(['post_type'=>'post','post_status'=>'publish','post_name'=>'browser-approval','post_title'=>'Мастерская обуви']); update_post_meta($id,'_seo_master_meta_title','Мастерская обуви'); echo $id;"))
                url = wp.php('echo get_permalink(' + str(entity) + ');')
                facts, _, _ = management_proposal_fixture(target_url=url, project_id=project_id, task_id=task_id)
                evidence = seal_evidence({'schema_version': 'page_evidence_v1', 'audit_id': 'browser-audit',
                    'project_id': project_id, 'url': url, 'final_url': url, 'status_code': 200,
                    'facts': facts['facts'], 'jsonld_blocks': 0})
                _, _, version = management_proposal_fixture(target_url=url, project_id=project_id, task_id=task_id,
                    entity=ExternalEntity(kind='post', id=str(entity)), evidence_override=[{
                        'evidence_id': evidence['evidence_id'], 'sha256': evidence['content_hash']}])
                with Session(database.engine) as session:
                    session.add(Task(id=UUID(task_id), project_id=UUID(project_id), task_type=TaskType.UPDATE_META,
                        status=TaskStatus.PENDING, url=url))
                    session.add(row_from_version(version))
                    session.add(CrawlResult(audit_id='browser-audit', project_id=project_id, root_url=url,
                        mode='full', status='completed', created_at=now, updated_at=now, crawled_at=now,
                        pages=[{'url': url, 'page_evidence': evidence}]))
                    session.add(DomainAuthorization(id=str(uuid4()), project_id=project_id, workspace_id=workspace,
                        status='verified', scope_origin='http://wordpress.test', scope_path_prefix='/',
                        challenge_hash='0' * 64, subject_user_id=user['user']['id'], issued_at=now-timedelta(days=1),
                        expires_at=now-timedelta(hours=23), verified_at=now))
                    session.commit()
                engine = create_engine(credentials['management'][1], isolation_level='READ COMMITTED', hide_parameters=True)
                with Session(engine) as session:
                    approval_id = str(ApprovalService(session).prepare(version, metadata={}).id)
                engine.dispose()
                stand.start('client', credentials['client'][2], project_id=project_id, client_entry=CLIENT_ENTRY)
                client_name = wp.owner + '-client'
                wp.run('exec', client_name, 'python', '-c', "from pathlib import Path; Path('/tmp/ar9-hold-public').touch()")
                frontend_name = wp.owner + '-frontend'
                wp.run('run', '-d', '--name', frontend_name, '--network', wp.network, '--network-alias', 'frontend',
                    '--label', 'seo.test.owner=' + wp.owner, '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev',
                    '--tmpfs', '/var/cache/nginx:rw,nosuid,nodev,uid=101,gid=101', stand.images['frontend'])
                wp.resources.append(('container', frontend_name))
                frontend_info = json.loads(wp.run('inspect', frontend_name))[0]
                assert frontend_info['HostConfig']['ReadonlyRootfs'] is True
                assert set(frontend_info['NetworkSettings']['Networks']) == {browser_network}
                assert wp.run('exec', frontend_name, 'id', '-u').strip() == '101'
                frontend_deadline = time.monotonic() + 30
                while time.monotonic() < frontend_deadline:
                    try:
                        healthy = json.loads(wp.run('exec', frontend_name, 'wget', '-qO-', 'http://127.0.0.1:8080/health', timeout=2))
                        assert healthy['status'] == 'ok'
                        break
                    except (RuntimeError, AssertionError, json.JSONDecodeError):
                        time.sleep(.2)
                else:
                    raise AssertionError('built frontend readiness timed out')
                browser_name = wp.owner + '-browser'
                fixture = {'email': email, 'password': password, 'task_id': task_id, 'approval_id': approval_id,
                    'version_id': version.version_id, 'content_hash': version.content_hash,
                    'before_title': version.changes[0].before, 'after_title': version.changes[0].after}
                wp.run('run', '-d', '--name', browser_name, '--network', wp.network,
                    '--label', 'seo.test.owner=' + wp.owner, '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev',
                    '-e', 'HOME=/tmp/browser-home', '-e', 'BROWSER_FIXTURE_JSON=' + json.dumps(fixture), stand.images['browser'])
                wp.resources.append(('container', browser_name))
                browser_info = json.loads(wp.run('inspect', browser_name))[0]
                assert browser_info['HostConfig']['ReadonlyRootfs'] is True
                assert browser_info['HostConfig']['NetworkMode'] == 'container:' + frontend_info['Id']
                assert wp.run('exec', browser_name, 'id', '-u').strip() == '10001'

                def phase(expected: str) -> dict[str, Any]:
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        try:
                            marker = json.loads(wp.run('exec', browser_name, 'cat', '/tmp/phase.json'))
                            if marker['phase'] == expected:
                                print('Browser checkpoint: ' + expected)
                                return marker
                        except (RuntimeError, json.JSONDecodeError):
                            pass
                        if wp.run('inspect', '--format', '{{.State.Running}}', browser_name).strip() != 'true':
                            logs = wp.run('logs', browser_name)
                            for secret in wp.private_values:
                                logs = logs.replace(secret, '[fixture-secret]')
                            raise AssertionError('browser exited before ' + expected + ': ' + logs[-1500:])
                        time.sleep(.2)
                    raise AssertionError('browser checkpoint timed out: ' + expected)

                def resume(name: str) -> None:
                    wp.run('exec', browser_name, 'python', '-c', "from pathlib import Path; Path('/tmp/continue').write_text(" + repr(name) + ')')

                def title() -> str:
                    return wp.php('echo get_post_meta(' + str(entity) + ", '_seo_master_meta_title', true);")

                def ledger_count() -> int:
                    return int(wp.php("global $wpdb; echo $wpdb->get_var($wpdb->prepare(\"SELECT COUNT(*) FROM %i WHERE JSON_UNQUOTE(JSON_EXTRACT(receipt, '$.entity_id'))=%d AND JSON_UNQUOTE(JSON_EXTRACT(receipt, '$.kind'))=%s\", $wpdb->prefix.'seo_master_operations_v2', " + str(entity) + ", 'receipt'));"))

                phase('viewed')
                assert title() == 'Мастерская обуви' and ledger_count() == 0
                resume('viewed')
                phase('approved')
                assert title() == 'Мастерская обуви' and ledger_count() == 0
                resume('approved')
                lost = phase('response_lost')
                operation_id = lost['writes'][1]['body']['operation_id']
                assert title() == 'Мастерская зимней обуви' and ledger_count() == 1
                resume('response_lost')
                phase('receipt_checked')
                assert database.scalar("SELECT metadata->>'state' FROM public.management_changelog WHERE id=:id", id=operation_id) in {'applied_unverified', 'verifying'}
                wp.run('exec', client_name, 'python', '-c', "from pathlib import Path; Path('/tmp/ar9-hold-public').unlink()")
                resume('receipt_checked')
                phase('verified')
                assert database.scalar("SELECT metadata->>'state' FROM public.management_changelog WHERE id=:id", id=operation_id) == 'verified'
                assert ledger_count() == 1
                original = request(api, 'GET', f'/api/hitl/tasks/{task_id}/executions/{operation_id}', token=token)
                record = original['record']
                retry_source = """
import asyncio,json,os
from copy import deepcopy
from shared.cms_http import CMSHttpClient
from shared.netpolicy import NetPolicy
from shared.hitl_execution import WordPressBinding,ExecutionError,bind_proposal_hash
from services.client_api_gateway.wordpress_v2 import WordPressV2
record=json.loads(RECORD_LITERAL)
receipt=json.loads(RECEIPT_LITERAL)
binding=WordPressBinding(base_url='http://wordpress.test',site_id=SITE_LITERAL,key_id='fixture')
target=WordPressV2(PROJECT_LITERAL,binding,os.environ['SEO_MASTER_HMAC_SECRET'],os.environ['SEO_MASTER_RECEIPT_SECRET'],http=CMSHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))))
async def check():
    for _ in range(10):
        assert await target.apply(record)==receipt
    results=await asyncio.gather(*(target.apply(record) for _ in range(10)),return_exceptions=True)
    reconciled=0
    for value in results:
        if isinstance(value,ExecutionError) and str(value)=='cms_request_rejected:503:storage_failure':
            # A transient SQL deadlock is an unknown result, never a success or
            # permission to resend apply. All writes have finished before reads.
            reconciled+=1
            value=await target.status(record)
        elif isinstance(value,BaseException):
            raise value
        assert value==receipt
    assert await target.preflight(record['version'])==record['after']
    changed=deepcopy(record)
    changed['command']['changes'][0]['value']='Different request under the same operation ID'
    bind_proposal_hash(changed['command'])
    try:
        await target.apply(changed)
    except ExecutionError as error:
        assert 'cms_request_rejected:409:idempotency_conflict' in str(error)
    else:
        raise AssertionError('same-id different request was accepted')
    print('PASS CMS retries: sequential apply receipts=10; concurrent apply receipts='+str(10-reconciled)+'; signed status receipts='+str(reconciled)+'; full managed snapshot unchanged')
asyncio.run(check())
print('PASS CMS: one logical mutation; exact receipt; same ID different hash409')
"""
                for key, value in {'RECORD_LITERAL': repr(json.dumps(record)), 'RECEIPT_LITERAL': repr(json.dumps(original['receipt'])),
                    'SITE_LITERAL': repr(wp.site_id), 'PROJECT_LITERAL': repr(project_id)}.items():
                    retry_source = retry_source.replace(key, value)
                print(wp.run('exec', '-i', client_name, 'python', '-B', stdin=retry_source), end='')
                assert ledger_count() == 1
                wp.php('update_post_meta(' + str(entity) + ", '_seo_master_meta_title', 'Manual drift');")
                resume('verified')
                phase('drift_denied')
                assert title() == 'Manual drift' and ledger_count() == 1
                resume('drift_denied')
                phase('rollback_cancelled')
                wp.php('update_post_meta(' + str(entity) + ", '_seo_master_meta_title', 'Мастерская зимней обуви');")
                resume('rollback_cancelled')
                done = phase('done')
                assert title() == 'Мастерская обуви' and ledger_count() == 2
                assert sum(write['path'].endswith('/apply') for write in done['writes']) == 1
                assert database.scalar("SELECT metadata->>'state' FROM public.management_changelog WHERE id=:id", id=operation_id) == 'rolled_back'
                rollback_id = [write['body']['operation_id'] for write in done['writes'] if write['path'].endswith('/rollback')][-1]
                restored = request(api, 'GET', f'/api/hitl/tasks/{task_id}/executions/{rollback_id}', token=token)
                assert restored['state'] == 'rollback_verified'
                assert restored['record']['original_operation_id'] == operation_id
                assert restored['receipt']['original_receipt_id'] == original['receipt']['receipt_id']
                assert restored['receipt']['after_digest'] == original['receipt']['before_digest']
                assert restored['verification']['last_result']['observation']['status'] == 200
                network = json.loads(wp.run('network', 'inspect', browser_network))[0]
                assert network['Internal'] is True
                resume('done')
                assert wp.run('wait', browser_name).strip() == '0'
                print('PASS AR-9 browser slice: real Compose/frontend/apps/signed WordPress; no mutation on view/approval; exact references; lost response without second apply; independent verified; manual drift denial; explicit rollback_verified.')
            finally:
                stand.stop_apps()
                for role, _local, _inside in credentials.values():
                    database.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-context', default='desktop-linux')
    parser.add_argument('--plugin-revision', default='HEAD')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from scripts.backend import build_test_environment
    from tests.wordpress.native import NativeWordPress
    from uuid import uuid4
    previous, previous_directory = dict(os.environ), Path.cwd()
    with tempfile.TemporaryDirectory(prefix='seo-browser-') as directory:
        workspace = Path(directory)
        # Tool OS/Docker context is captured before switching application imports
        # to the private HOME/profile. No application configuration is inherited.
        wp = NativeWordPress(root, project_id=str(uuid4()), site_id=str(uuid4()),
            context=args.docker_context, revision=args.plugin_revision)
        clean = build_test_environment(root, workspace)
        os.environ.clear()
        os.environ.update(clean)
        os.environ['NETPOLICY_PROFILE'] = 'test'
        os.chdir(workspace)
        try:
            run(root, workspace, wp)
        finally:
            os.chdir(previous_directory)
            os.environ.clear()
            os.environ.update(previous)


if __name__ == '__main__':
    main()
