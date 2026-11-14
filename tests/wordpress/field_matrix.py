"""Actual installed ZIP: CMS field/rollback/public HTML, not browser approval E2E."""
from copy import deepcopy
import json
import time
from uuid import uuid4

from services.client_api_gateway.public_verifier import PublicPageReader
from services.client_api_gateway.wordpress_v2 import WordPressV2
from services.semantic_service.proposals.model import ExternalEntity
from shared.hitl_execution import ExecutionRequest, WordPressBinding, bind_proposal_hash, make_command
from shared.hitl_rollback import bind_rollback_hash, make_rollback_command
from shared.hitl_verification import json_value
from shared.netpolicy import NetPolicy, SafeHttpClient
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture
from tests.wordpress.native import NativeWordPress

META = {'title': '_seo_master_meta_title', 'description': '_seo_master_meta_description', 'jsonld': '_seo_master_schema_jsonld'}


async def run_field_matrix(wp: NativeWordPress, target: WordPressV2, binding: WordPressBinding, backend, resolver) -> None:
    reader = PublicPageReader(http=SafeHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'})),
                                                 resolver=resolver, network_backend=backend))
    cases = 0
    for kind in ('post', 'page'):
        for state in ('missing', 'empty', 'unicode'):
            slug = 'matrix-' + kind + '-' + state
            entity = int(wp.php("echo wp_insert_post(" + php_value({'post_type': kind, 'post_status': 'publish', 'post_name': slug,
                'post_title': 'Original heading', 'post_content': 'Original body'}) + ");"))
            if state != 'missing':
                values = {field: '' if state == 'empty' else ('До & " \\ Юникод' if field != 'jsonld' else '{"@context":"https://schema.org","@type":"WebPage","name":"До"}') for field in META}
                wp.php('$values=' + php_value(values) + '; foreach ($values as $field=>$value) { update_post_meta(' + str(entity) + ', ' + php_value(META) + '[$field], wp_slash($value)); }')
            # Ask WP for the authoritative permalink; no URL guess becomes proof.
            url = wp.php('echo get_permalink(' + str(entity) + ');')
            _, _, model = management_proposal_fixture(entity=ExternalEntity(kind=kind, id=str(entity)), target_url=url)
            version = model.model_dump(mode='json')
            before = await target.preflight(version)
            values = {'title': 'После & " Юникод', 'description': 'Описание & строка " Юникод',
                      'jsonld': '{"@context":"https://schema.org","@type":"WebPage","name":"После & строка"}'}
            initial_public = await reader.read(url)
            for mode in ('set', 'remove'):
                # Direct signed CMS contract: these fixture changes do not claim
                # Semantic generation or a browser/user approval workflow.
                changed = deepcopy(version)
                changed['changes'] = [{'cms_field': field, 'before_state': cell['state'],
                    'before': cell.get('value'), 'mode': mode, 'after': values[field]} for field, cell in before['fields'].items()]
                refs = ExecutionRequest(operation_id=str(uuid4()), task_id=TASK_ID, approval_id=str(uuid4()),
                    recommendation_version_id=model.version_id, content_hash=model.content_hash)
                command, after = make_command(changed, refs, binding, before)
                command.update(target_url=url, authorization_deadline=int(time.time()) + 60)
                bind_proposal_hash(command)
                record = {**refs.model_dump(), 'command': command, 'before': before, 'after': after}
                await target.dry_run(record)
                receipt = await target.apply(record)
                assert await target.status(record) == receipt
                assert await target.preflight(version) == after
                persisted = json.loads(wp.php('$out=[]; foreach (' + php_value(META) + ' as $field=>$key) { $out[$field]=get_post_meta(' + str(entity) + ', $key, false); } echo json_encode($out);'))
                assert persisted == {field: [values[field]] if mode == 'set' else [] for field in META}
                observed = await reader.read(url)
                assert observed.facts is not None
                if mode == 'set':
                    assert observed.facts.titles == (values['title'],)
                    assert observed.facts.descriptions == (values['description'],)
                    assert observed.facts.jsonld == (json_value(values['jsonld']),)
                else:
                    assert observed.facts.descriptions == () and observed.facts.jsonld == ()
                    assert values['title'] not in observed.facts.titles
                intent = {'project_id': PROJECT_ID, 'operation_id': str(uuid4()), 'task_id': TASK_ID,
                    'recommendation_version_id': model.version_id, 'approval_id': refs.approval_id,
                    'reason': 'Disposable native field restoration', 'expected_current': after, 'restore_before': before,
                    'original_binding': binding.model_dump(), 'original_operation_id': refs.operation_id,
                    'original_receipt_id': receipt['receipt_id'], 'original_request_hash': receipt['request_hash'],
                    'original_proposal_hash': receipt['proposal_hash']}
                rollback, restored = make_rollback_command(intent, binding, after)
                rollback.update(target_url=url, authorization_deadline=int(time.time()) + 60)
                bind_rollback_hash(rollback)
                reverse = {**intent, 'operation_type': 'rollback', 'command': rollback, 'before': after, 'after': restored}
                await target.dry_run(reverse)
                restored_receipt = await target.apply(reverse)
                assert await target.status(reverse) == restored_receipt
                assert await target.preflight(version) == before
                actual_public = await reader.read(url)
                assert actual_public.facts == initial_public.facts
                cases += 1
            untouched = json.loads(wp.php('$p=get_post(' + str(entity) + '); echo json_encode([$p->post_title,$p->post_content]);'))
            assert untouched == ['Original heading', 'Original body']
    print(f'PASS installed ZIP field matrix: {cases} post/page x missing/empty/Unicode x set/remove cases; title/description/JSON-LD exact SQL values, signed receipt/status, anonymous HTML, exact rollback; post_title/content unchanged. Core theme only, not Yoast/RankMath or browser E2E.')


def php_value(value) -> str:
    # A literal base64 string prevents PHP/shell interpolation of test text.
    import base64
    encoded = base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode('ascii')
    return "json_decode(base64_decode('" + encoded + "'), true)"
