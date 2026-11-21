"""Restore the synthetic HTTP fixture into its own networkless PostgreSQL."""

from hashlib import sha256
import json
import re
import secrets
import time

from services.project_integrations.credentials_vault import CredentialsVault
from shared.hitl_execution import validate_receipt
from tests.wordpress.native import POSTGRES, NativeWordPress


RESTORE_DB = 'restore_fixture'
CHECKED_TABLES = (
    'audit_schema.projects', 'audit_schema.workspace_members', 'audit_schema.crawl_results',
    'audit_schema.project_integrations', 'semantic_schema.recommendation_versions',
    'public.tasks', 'public.hitl_approvals', 'public.management_changelog',
    'public.management_outbox', 'public.processed_events', 'reporting_schema.reports',
)


def start_restore_target(wp: NativeWordPress, target: str) -> None:
    password = secrets.token_hex(32)
    wp.private_values.append(password)
    try:
        wp.run('run', '-d', '--rm', '--name', target, '--network', 'none',
               '--label', 'seo.test.owner=' + wp.owner, '--tmpfs', '/var/lib/postgresql/data:rw,nosuid',
               '-e', 'POSTGRES_PASSWORD=' + password, '-e', 'POSTGRES_DB=' + RESTORE_DB, POSTGRES)
    except (RuntimeError, KeyboardInterrupt):
        # Docker may create the container before the client reports a failure.
        # Only remove the exact resource carrying this invocation's owner label.
        created = wp.run('container', 'ls', '-a', '--filter', 'name=^/' + target + '$',
                         '--filter', 'label=seo.test.owner=' + wp.owner, '--format', '{{.Names}}').splitlines()
        if created == [target]:
            candidate = json.loads(wp.run('inspect', target))[0]
            if candidate['Name'] == '/' + target and candidate['Config']['Labels']['seo.test.owner'] == wp.owner:
                wp.run('container', 'rm', '--force', candidate['Id'])
        raise
    wp.resources.append(('container', target))


def check_restore(wp: NativeWordPress, source_db: str, *, master_key: str, expected_source_key: str,
                  expected_version_hash: str, operation_id: str, rollback_id: str) -> None:
    if re.fullmatch(r'al4_[0-9a-f]{12}', source_db) is None:
        raise ValueError('restore source is not a disposable fixture database')
    source_name = wp.owner + '-pg'
    source = json.loads(wp.run('inspect', source_name))[0]
    if ('container', source_name) not in wp.resources or source['Config']['Labels']['seo.test.owner'] != wp.owner:
        raise ValueError('restore source does not belong to this fixture')

    target = wp.owner + '-restore-pg'
    start_restore_target(wp, target)
    config = json.loads(wp.run('inspect', target))[0]
    assert config['Config']['Labels']['seo.test.owner'] == wp.owner
    assert config['HostConfig']['NetworkMode'] == 'none' and not config['HostConfig']['PortBindings']

    # -h 127.0.0.1 checks the final server, not the entrypoint's transient socket.
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            wp.run('exec', target, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', RESTORE_DB, timeout=5)
            break
        except RuntimeError:
            time.sleep(.25)
    else:
        raise AssertionError('networkless restore PostgreSQL did not start')

    def query(container: str, database: str, sql: str) -> str:
        return wp.run('exec', container, 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', database,
                      '-c', sql, timeout=20).strip()

    def snapshot(container: str, database: str) -> dict[str, tuple[int, str]]:
        result = {}
        for table in CHECKED_TABLES:
            rows = query(container, database,
                         f'SELECT row_to_json(t)::text FROM {table} AS t ORDER BY row_to_json(t)::text COLLATE "C"')
            result[table] = (len(rows.splitlines()), sha256(rows.encode()).hexdigest())
        return result

    assert query(target, RESTORE_DB, "SELECT to_regclass('public.management_changelog') IS NULL") == 't'
    before = snapshot(source_name, source_db)
    assert all(count > 0 for count, _ in before.values())
    dump = wp.run('exec', source_name, 'pg_dump', '-U', 'postgres', '-d', source_db,
                  '--no-owner', '--no-privileges', '--encoding=UTF8', timeout=40)
    assert expected_source_key not in dump and master_key not in dump
    wp.run('exec', '-i', target, 'psql', '-Xq', '--single-transaction', '-v', 'ON_ERROR_STOP=1',
           '-U', 'postgres', '-d', RESTORE_DB, stdin=dump, timeout=50)
    assert snapshot(target, RESTORE_DB) == before

    ciphertext = query(target, RESTORE_DB, 'SELECT encrypted_creds FROM audit_schema.project_integrations')
    assert CredentialsVault(master_key).decrypt(ciphertext) == {'api_key': expected_source_key}
    assert query(target, RESTORE_DB, "SELECT data->>'kind' FROM reporting_schema.reports") == 'backup_fixture'
    assert query(target, RESTORE_DB, "SELECT count(*) FROM audit_schema.workspace_members WHERE role='owner'") == '1'
    # Validate the two restored signed receipts against the original commands and
    # the separately retained receipt key. The fixture contains no extra operations.
    operations = json.loads(query(target, RESTORE_DB,
        "SELECT json_object_agg(id, metadata) FROM public.management_changelog WHERE source='hitl_executor_v1'"))
    for identity, status in ((operation_id, 'rolled_back'), (rollback_id, 'rollback_verified')):
        operation = operations[identity]
        record = operation['record']
        assert operation['state'] == status and record['version']['content_hash'] == expected_version_hash
        validate_receipt(operation['receipt'], record['command'], record['before'], record['after'],
                         secret=wp.receipt_secret, key_id='fixture')
    print('PASS AR-16 PostgreSQL restore: isolated target, snapshot hashes, encrypted source, report, workspace role and signed apply/rollback chain.')
