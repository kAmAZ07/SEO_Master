"""Closed fixture launcher grammar; no Docker, browser or application imports."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.e2e.compose_stand import ComposeContainers

OWNER = 'seo-wp-contract-' + 'a' * 32


class ComposeFixtureTests(unittest.TestCase):
    def test_partial_creation_cleanup_checks_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch('tests.e2e.compose_stand.restrict_permissions'):
            for label, failure in ((OWNER, RuntimeError), ('somebody-else', RuntimeError),
                                   (OWNER, KeyboardInterrupt), ('somebody-else', KeyboardInterrupt)):
                calls: list[tuple[str, ...]] = []

                def execute(*arguments: str) -> str:
                    calls.append(arguments)
                    if arguments[0] == 'compose':
                        raise failure('injected partial up failure')
                    if arguments[0] == 'inspect':
                        return json.dumps({'seo.test.owner': label})
                    return ''

                stand = ComposeContainers(Path(directory), OWNER, OWNER + '-net', execute)
                with self.subTest(label=label, failure=failure), self.assertRaises(failure if label == OWNER else ValueError):
                    stand.launch(('run', '--name', OWNER + '-api', '--network', OWNER + '-net',
                        '--label', 'seo.test.owner=' + OWNER, 'fixture-image'))
                destructive = [call[0] for call in calls if call[0] in {'stop', 'container'}]
                self.assertEqual(destructive, ['stop', 'container'] if label == OWNER else [])

    def test_literal_credentials_and_private_network_scope(self) -> None:
        calls: list[tuple[str, ...]] = []

        def execute(*arguments: str) -> str:
            calls.append(arguments)
            return 'started'

        with tempfile.TemporaryDirectory() as directory, patch('tests.e2e.compose_stand.restrict_permissions'):
            stand = ComposeContainers(Path(directory), OWNER, OWNER + '-net', execute)
            stand.launch(('run', '--name', OWNER + '-frontend', '--network', OWNER + '-net',
                '--label', 'seo.test.owner=' + OWNER, 'fixture-frontend'))
            self.assertEqual(stand.launch(('run', '-d', '--rm', '--name', OWNER + '-browser', '--network', OWNER + '-net',
                '--label', 'seo.test.owner=' + OWNER, '-e', 'FIXTURE_VALUE=${DO_NOT_EXPAND}', '--read-only',
                '--entrypoint', 'python', 'fixture-image', '-B', '-m', 'fixture')), 'started')
            config = json.loads(stand.path.read_text(encoding='utf-8'))
            browser = config['services']['browser']
            self.assertEqual(browser['environment']['FIXTURE_VALUE'], '$${DO_NOT_EXPAND}')
            self.assertNotIn('networks', browser)
            self.assertEqual(browser['network_mode'], 'service:frontend')
            self.assertEqual(config['services']['frontend']['networks'], {'browser': {'aliases': ['frontend']}})
            self.assertTrue(browser['read_only'])
            self.assertEqual(browser['command'], ['-B', '-m', 'fixture'])
            self.assertEqual(calls[-1][-5:], ('up', '--detach', '--no-deps', '--no-build', 'browser'))

    def test_unsafe_flags_or_shared_scope_never_reach_docker(self) -> None:
        base = ('run', '--name', OWNER + '-api', '--network', OWNER + '-net', '--label', 'seo.test.owner=' + OWNER)
        faults = [('--volume', '/:/host'), ('--privileged',), ('--publish', '0.0.0.0:8000:8000'),
                  ('-e', 'INHERITED_SECRET'), ('--network', 'shared'), ('--name', 'shared-api'),
                  ('--label', 'seo.test.owner=somebody-else')]
        with tempfile.TemporaryDirectory() as directory, patch('tests.e2e.compose_stand.restrict_permissions'):
            for fault in faults:
                calls: list[tuple[str, ...]] = []

                def execute(*arguments: str) -> str:
                    calls.append(arguments)
                    return ''

                stand = ComposeContainers(Path(directory), OWNER, OWNER + '-net', execute)
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    stand.launch((*base, *fault, 'fixture-image'))
                self.assertFalse(calls)
                self.assertFalse(stand.path.exists())
