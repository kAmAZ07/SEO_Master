"""Real built UI and backend; only the lost HTTP response is fault-injected."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import Request, Response, Route, expect, sync_playwright

PHASE = 'startup'
RESPONSES: list[str] = []


def checkpoint(phase: str, **data: object) -> None:
    global PHASE
    PHASE = phase
    Path('/tmp/phase.json').write_text(json.dumps({'phase': phase, **data}), encoding='utf-8')
    deadline = time.monotonic() + 180
    gate = Path('/tmp/continue')
    while time.monotonic() < deadline:
        if gate.exists() and gate.read_text() == phase:
            return
        time.sleep(.1)
    raise AssertionError('host_checkpoint_timeout')


def main() -> None:
    global PHASE
    fixture = json.loads(os.environ['BROWSER_FIXTURE_JSON'])
    Path('/tmp/browser-home').mkdir(mode=0o700, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(service_workers='block')
        context.set_default_timeout(15000)
        for path in ('/internal/executions', '/api/internal/executions', '/api/client/keys'):
            response = context.request.get('http://127.0.0.1:8080' + path)
            assert response.status == 404 and response.headers.get('content-type', '').startswith('text/html'), 'internal_ingress_boundary'
        denied = []
        writes = []
        priority_writes = []

        def policy(route: Route) -> None:
            target = urlsplit(route.request.url)
            if target.scheme != 'http' or target.hostname != '127.0.0.1' or target.port != 8080:
                denied.append('outside_fixture_origin:' + (target.hostname if target.hostname in {'localhost', '127.0.0.1', 'wordpress.test'} else 'other'))
                route.abort('blockedbyclient')
            else:
                route.continue_()

        context.route('**/*', policy)
        page = context.new_page()
        def response_seen(response: Response) -> None:
            path = urlsplit(response.url).path
            if path.startswith('/api/'):
                RESPONSES.append(path[:120] + ':' + str(response.status))
                del RESPONSES[:-12]
        page.on('response', response_seen)

        def observe(request: Request) -> None:
            path = urlsplit(request.url).path
            if request.method == 'POST' and '/api/hitl/' in path:
                target = priority_writes if path.endswith('/priority') else writes
                target.append({'path': path, 'body': request.post_data_json})

        page.on('request', observe)
        PHASE = 'login'
        page.goto('http://127.0.0.1:8080/login?redirect=/dashboard/hitl')
        assert page.evaluate('window.isSecureContext && typeof crypto.randomUUID === "function"')
        page.get_by_label('Email', exact=True).fill(fixture['email'])
        page.get_by_label('Пароль', exact=True).fill(fixture['password'])
        page.get_by_role('button', name='Войти', exact=True).click()
        PHASE = 'hitl_preview'
        expect(page.get_by_role('heading', name='HITL-согласования', exact=True)).to_be_visible()
        version = page.locator('[aria-label="Просматриваемая версия предложения"]')
        expect(version).to_contain_text(fixture['version_id'])
        expect(version).to_contain_text(fixture['content_hash'])
        diff = page.locator('table').filter(has_text=fixture['after_title'])
        diff.scroll_into_view_if_needed()
        expect(diff).to_be_visible()
        expect(diff).to_contain_text(fixture['before_title'])
        expect(diff).to_contain_text(fixture['after_title'])
        expect(diff.get_by_text(fixture['before_title'], exact=False)).to_be_visible()
        expect(diff.get_by_text(fixture['after_title'], exact=False)).to_be_visible()
        expect(page.get_by_role('button', name='Применить одобренные изменения', exact=True)).to_have_count(0)
        if writes or priority_writes or denied:
            print('BROWSER_BOUNDARY_DENIED count=' + str(len(denied)) + ' writes=' + str(len(writes)), file=sys.stderr)
            raise AssertionError('unexpected_boundary_traffic')
        checkpoint('viewed')

        PHASE = 'priority'
        priority_summary = page.locator('summary').filter(has_text='Приоритет:')
        priority = page.locator('details').filter(has=priority_summary)

        def save_priority(revision: int) -> dict:
            with page.expect_response(lambda response: urlsplit(response.url).path.endswith('/priority') and
                                      response.request.method == 'POST') as result:
                page.get_by_role('button', name='Сохранить приоритет', exact=True).click()
            assert result.value.status == 200
            snapshot = result.value.json()
            assert snapshot['revision'] == revision
            score = str(snapshot['effective_score']).removesuffix('.0')
            expect(priority_summary).to_have_text('Приоритет: ' + score)
            return snapshot

        priority_summary.click()
        page.get_by_label('Impact (0…1)', exact=True).fill('0.8')
        page.get_by_label('Confidence (0…1)', exact=True).fill('0.75')
        page.get_by_label('Effort (1…5)', exact=True).fill('2')
        page.get_by_label('Срочность', exact=True).select_option('high')
        page.get_by_label('Причина срочности', exact=True).fill('Release triage')
        page.get_by_label('Причина изменения', exact=True).fill('Explicit manual estimate')
        assert abs(save_priority(1)['computed_score'] - .3) < 1e-12
        priority_summary.click()
        page.get_by_label('Изменение приоритета', exact=True).select_option('override')
        page.get_by_label('Ручной балл (0…1)', exact=True).fill('0')
        page.get_by_label('Причина изменения', exact=True).fill('Defer after review')
        assert save_priority(2)['effective_score'] == 0
        priority_summary.click()
        expect(priority.get_by_text('Ручной приоритет 0:', exact=False)).to_be_visible()
        page.get_by_role('button', name='Журнал приоритетов', exact=True).click()
        expect(priority.locator('li').filter(has_text='Defer after review')).to_contain_text('→ 0')
        page.get_by_label('Изменение приоритета', exact=True).select_option('clear_override')
        page.get_by_label('Причина изменения', exact=True).fill('Use formula again')
        assert abs(save_priority(3)['effective_score'] - .3) < 1e-12
        assert [entry['body']['expected_revision'] for entry in priority_writes] == [0, 1, 2]
        assert not writes
        expect(version).to_contain_text(fixture['version_id'])
        expect(version).to_contain_text(fixture['content_hash'])
        checkpoint('priority_changed', priority_writes=priority_writes)

        page.get_by_role('button', name='Одобрить', exact=True).click()
        apply = page.get_by_role('button', name='Применить одобренные изменения', exact=True)
        expect(apply).to_be_enabled()
        assert len(writes) == 1
        assert writes[0]['body'] == {'approval_id': fixture['approval_id'],
            'recommendation_version_id': fixture['version_id'], 'content_hash': fixture['content_hash']}
        checkpoint('approved', writes=writes)

        def lose_apply_response(route: Route) -> None:
            result = route.fetch(timeout=45000)
            print('BROWSER_APPLY_RESPONSE status=' + str(result.status), file=sys.stderr)
            assert result.status == 200
            route.abort('failed')  # The real server and CMS committed before this fault.

        page.route('**/api/hitl/tasks/' + fixture['task_id'] + '/apply', lose_apply_response)
        PHASE = 'apply'
        apply.click()
        expect(apply).to_be_enabled()
        if len(writes) != 2 or not writes[1]['path'].endswith('/apply'):
            print('BROWSER_WRITES count=' + str(len(writes)), file=sys.stderr)
            raise AssertionError('unexpected_apply_requests')
        checkpoint('response_lost', writes=writes)
        page.unroute('**/api/hitl/tasks/' + fixture['task_id'] + '/apply', lose_apply_response)
        page.get_by_role('button', name='Обновить', exact=True).click()
        page.get_by_role('button', name='Квитанция и снимки публикации', exact=True).click()
        receipt = page.locator('[aria-label="Квитанция CMS"]')
        expect(receipt).to_contain_text('applied_unverified')
        expect(page.get_by_text('Изменения подтверждены независимым чтением публичной страницы.', exact=False)).to_have_count(0)
        assert len(writes) == 2
        checkpoint('receipt_checked')

        def refresh_until(fragment: str) -> None:
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                page.get_by_role('button', name='Обновить', exact=True).click()
                try:
                    expect(page.get_by_role('status').filter(has_text=fragment)).to_be_visible(timeout=1000)
                    return
                except AssertionError:
                    pass
            raise AssertionError('public_state_timeout')

        refresh_until('Изменения подтверждены независимым чтением публичной страницы.')
        assert len(writes) == 2
        checkpoint('verified', writes=writes)

        reason = page.get_by_label('Причина отката', exact=True)
        reason.fill('Restore the reviewed before snapshot')
        page.get_by_role('button', name='Подтвердить откат к прежним значениям', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Состояние страницы изменилось до публикации')).to_be_visible()
        checkpoint('drift_denied', writes=writes)
        page.get_by_role('button', name='Отменить откат до записи', exact=True).click()
        confirm = page.get_by_role('button', name='Подтвердить откат к прежним значениям', exact=True)
        expect(confirm).to_be_enabled()
        checkpoint('rollback_cancelled', writes=writes)
        confirm.click()
        refresh_until('Откат подтверждён независимым чтением публичной страницы.')
        page.get_by_role('button', name='Квитанция и снимки отката', exact=True).click()
        expect(page.locator('[aria-label="Квитанция CMS"]').filter(has_text='rollback_applied_unverified')).to_be_visible()
        assert denied == []
        checkpoint('done', writes=writes)
        context.close()
        browser.close()
    print('PASS browser: exact version approval, lost-response reconciliation, public verified, drift refusal and rollback_verified')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Playwright call logs can contain filled credentials. Never print them.
        print('BROWSER_FAILURE phase=' + PHASE + ' type=' + type(error).__name__, file=sys.stderr)
        print('BROWSER_HTTP ' + json.dumps(RESPONSES), file=sys.stderr)
        raise SystemExit(1) from None
