"""Compose launcher for the existing disposable native fixtures, not the shared stack."""
from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from scripts.project import restrict_permissions


def literals(value: Any) -> Any:
    """Compose interpolates dollars even inside JSON; fixture values are literals."""
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, dict):
        return {key: literals(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [literals(entry) for entry in value]
    return value


class ComposeContainers:
    def __init__(self, directory: Path, owner: str, network: str, execute: Callable[..., str]):
        if re.fullmatch(r'seo-wp-contract-[0-9a-f]{32}', owner) is None or network != owner + '-net':
            raise ValueError('compose_fixture_owner_invalid')
        self.directory = directory.resolve(strict=True)
        restrict_permissions(self.directory)
        self.path = self.directory / 'containers.json'
        self.owner, self.network, self.execute = owner, network, execute
        # Heterogeneous Compose JSON, populated only by the closed flag parser.
        self.services: dict[str, dict[str, Any]] = {}

    def launch(self, arguments: tuple[str, ...]) -> str:
        if not arguments or arguments[0] != 'run':
            raise ValueError('compose_fixture_command_invalid')
        service: dict[str, Any] = {'environment': {}, 'labels': {}, 'networks': {'fixture': {'aliases': []}}, 'tmpfs': [], 'ports': []}
        name = network = ''
        position = 1
        while position < len(arguments) and arguments[position].startswith('-'):
            flag = arguments[position]
            position += 1
            if flag in {'-d', '--rm'}:
                continue  # Native ownership cleanup also handles non-AutoRemove containers.
            if flag == '--read-only':
                service['read_only'] = True
                continue
            if flag not in {'--name', '--network', '--network-alias', '--label', '--tmpfs', '-e', '--publish', '--cap-drop', '--security-opt', '--entrypoint', '--hostname'} or position == len(arguments):
                raise ValueError('compose_fixture_flag_invalid')
            value = arguments[position]
            position += 1
            if flag == '--name':
                name = value
            elif flag == '--network':
                network = value
            elif flag == '--network-alias':
                service['networks']['fixture']['aliases'].append(value)
            elif flag in {'--label', '-e'}:
                if '=' not in value:
                    raise ValueError('compose_fixture_inherited_value_forbidden')
                key, content = value.split('=', 1)
                service['labels' if flag == '--label' else 'environment'][key] = content
            elif flag == '--publish':
                if re.fullmatch(r'127[.]0[.]0[.]1::[1-9][0-9]{0,4}', value) is None or int(value.rsplit(':', 1)[1]) > 65535:
                    raise ValueError('compose_fixture_port_invalid')
                service['ports'].append(value)
            elif flag == '--tmpfs':
                if not value.startswith('/') or ':' not in value or '\x00' in value:
                    raise ValueError('compose_fixture_tmpfs_invalid')
                service['tmpfs'].append(value)
            elif flag == '--entrypoint':
                service['entrypoint'] = [value]
            elif flag == '--hostname':
                service['hostname'] = value
            else:
                service.setdefault('cap_drop' if flag == '--cap-drop' else 'security_opt', []).append(value)
        slug = name.removeprefix(self.owner + '-')
        if (not name.startswith(self.owner + '-') or re.fullmatch('[a-z][a-z0-9-]*', slug) is None
                or network != self.network or service['labels'].get('seo.test.owner') != self.owner
                or position == len(arguments)):
            raise ValueError('compose_fixture_scope_invalid')
        service.update(container_name=name, image=arguments[position])
        if position + 1 < len(arguments):
            service['command'] = list(arguments[position + 1:])
        if slug == 'api':
            service['networks']['fixture']['aliases'].append('api-gateway')
            service['networks']['browser'] = {'aliases': ['api-gateway']}
        if slug == 'frontend':
            service['networks'] = {'browser': {'aliases': ['frontend']}}
        if slug == 'browser':
            if 'frontend' not in self.services:
                raise ValueError('compose_fixture_frontend_required')
            service.pop('networks')
            service['network_mode'] = 'service:frontend'
        self.services[slug] = service
        config = literals({'services': self.services, 'networks': {
            'fixture': {'external': True, 'name': self.network},
            'browser': {'external': True, 'name': self.owner + '-browser-net'},
        }})
        # The private temporary directory contains synthetic fixture credentials;
        # config and browser traces are never exported as CI artifacts.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory, delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output, ensure_ascii=True)
        try:
            os.replace(temporary, self.path)
            restrict_permissions(self.path)
            try:
                return self.execute('compose', '--project-name', self.owner, '--project-directory', str(self.directory),
                                    '--file', str(self.path), 'up', '--detach', '--no-deps', '--no-build', slug)
            except (RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
                # up can create a container before returning failure. It has not
                # reached the native fixture's normal resource registration yet.
                self.cleanup_failed_container(name)
                raise
        finally:
            temporary.unlink(missing_ok=True)

    def cleanup_failed_container(self, name: str) -> None:
        try:
            labels = json.loads(self.execute('inspect', '--format', '{{json .Config.Labels}}', name))
        except (RuntimeError, subprocess.SubprocessError):
            # Unknown/not-created: no authority to stop an unverified resource.
            return
        if not isinstance(labels, dict) or labels.get('seo.test.owner') != self.owner:
            raise ValueError('compose_fixture_cleanup_owner_mismatch')
        self.execute('stop', name)
        self.execute('container', 'rm', name)
