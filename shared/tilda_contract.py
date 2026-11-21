"""Tilda-native Beta wire contract. It grants no CMS write capability."""
import logging
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr

ReadMethod = Literal['getprojectslist', 'getprojectinfo', 'getpageslist', 'getpage']


def capabilities() -> dict[str, object]:
    return {'version': 'tilda_beta_v1', 'mode': 'read_only_manual', 'write_status': 'write_unsupported',
            'manual_required': True, 'read_methods': ['getprojectslist', 'getprojectinfo', 'getpageslist', 'getpage'],
            'requests_per_hour': 150, 'required_plan': 'Business', 'upstream_receipts': False}


def unsupported(page_id: str) -> dict[str, object]:
    return {'status': 'write_unsupported', 'page_id': page_id, 'manual_required': True,
            'upstream_mutation_calls': 0, 'receipt': None,
            'message': 'Apply and publish in the Tilda editor; verify the public page independently.'}


class TildaReadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    method: ReadMethod
    public_key: SecretStr
    secret_key: SecretStr
    external_project_id: str = Field(pattern=r'^[0-9]{1,20}$')
    page_id: str | None = Field(default=None, pattern=r'^[0-9]{1,20}$')


class PublishHint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    binding: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    token: str = Field(pattern=r'^[0-9a-f]{64}$', repr=False)
    projectid: str = Field(pattern=r'^[0-9]{1,20}$')
    pageid: str = Field(pattern=r'^[0-9]{1,20}$')
    published: str = Field(pattern=r'^[0-9]{1,12}$')
    publickey: str = Field(min_length=1, max_length=256, repr=False)


class ManualCheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    check_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    approval_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    recommendation_version_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def safe_callback_path(path: str) -> str:
    if path.startswith('/api/tilda/webhook/'):
        return '/api/tilda/webhook/[redacted]'
    if path.startswith('/webhook/'):
        return '/webhook/[redacted]'
    return path


class CallbackAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2].split('?', 1)[0]
            safe = safe_callback_path(path)
            if safe != path:
                record.args = (*args[:2], safe, *args[3:])
        return True


class TildaTransportFilter(logging.Filter):
    @staticmethod
    def scrub(value: object) -> object:
        original = str(value)
        redacted = re.sub(r'([?&](?:publickey|secretkey)=)[^&\s\"\'<>]+', r'\1[redacted]', original, flags=re.IGNORECASE)
        redacted = re.sub(r'(/(?:api/tilda/webhook|webhook)/)[^?\s\"\']+', r'\1[redacted]', redacted)
        return redacted if redacted != original else value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self.scrub(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self.scrub(value) for key, value in record.args.items()}
        return True


def install_transport_redaction() -> None:
    # Logger names verified against the pinned httpx/httpcore source.
    for name in ('httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy', 'httpcore.socks'):
        logger = logging.getLogger(name)
        if not any(isinstance(item, TildaTransportFilter) for item in logger.filters):
            logger.addFilter(TildaTransportFilter())
