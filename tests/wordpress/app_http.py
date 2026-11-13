"""AR-4 over actual running API/Management/Client apps, PG, Redis, RabbitMQ and WP.

Only CMS transport uses an explicit test-private host policy. Auth, transactions,
lifespans, routes, broker and business commands use the production implementations.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import time
from typing import Any, TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from database.models import DomainAuthorization
from services.audit_service.db.models import CrawlResult
from services.management_service.approval_service import ApprovalService
from services.management_service.migrations.runner import migrate
from services.management_service.models import Task, TaskStatus, TaskType
from services.semantic_service.proposals.model import ExternalEntity
from services.semantic_service.proposals.repository import row_from_version
from shared.page_evidence import seal_evidence
from tests.fixtures.management_proposal import management_proposal_fixture
from tests.wordpress.native import NativeWordPress

if TYPE_CHECKING:
    from tests.integration.test_migrations_postgres import TempDatabase

REDIS = "redis:7-alpine@sha256:7aec734b2bb298a1d769fd8729f13b8514a41bf90fcdd1f38ec52267fbaa8ee6"
RABBIT = "rabbitmq:3.12-management-alpine@sha256:0b44fbcc3a4bf22d00090f1353127577dbe1fcb109c41669733a9d7ecf6c3a78"
CLIENT_ENTRY = """
import os
import asyncio
from pathlib import Path
assert os.environ['NETPOLICY_PROFILE']=='test'
import services.client_api_gateway.hitl_executor as executor
from shared.cms_http import CMSHttpClient
from shared.netpolicy import NetPolicy
original=executor.wordpress_target
def target(project_id,binding=None):
    client=original(project_id,binding,http=CMSHttpClient(policy=NetPolicy(allow_private_hosts=frozenset({'wordpress.test'}))))
    apply=client.apply
    async def interrupted(record):
        receipt=await apply(record)
        if Path('/tmp/ar6-hold-receipt').exists():
            Path('/tmp/ar6-receipt-received').write_text(record['operation_id'])
            await asyncio.Event().wait()
        return receipt
    client.apply=interrupted
    return client
executor.wordpress_target=target
import uvicorn
uvicorn.run('services.client_api_gateway.main:app',host='0.0.0.0',port=8005)
"""


def request(base: str, method: str, path: str, *, token: str | None = None,
            body: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
            expected: int = 200) -> dict[str, Any]:
    with httpx.Client(timeout=50, trust_env=False, follow_redirects=False) as client:
        response = client.request(method, base + path, json=body,
            headers={**({"Authorization": "Bearer " + token} if token else {}), **(headers or {})})
    if response.status_code != expected:
        raise AssertionError(f"HTTP contract failed: {method} {path} expected {expected}, got {response.status_code}")
    # Heterogeneous JSON is checked by the scenario's field/value assertions.
    data = response.json()
    if not isinstance(data, dict):
        raise AssertionError("HTTP fixture expected a JSON object")
    return data


class AppStand:
    def __init__(self, wp: NativeWordPress) -> None:
        self.wp = wp
        self.apps: list[str] = []
        self.images: dict[str, str] = {}
        self.internal, self.executor, self.jwt = (secrets.token_hex(32) for _ in range(3))
        self.redis_password, self.rabbit_password = secrets.token_hex(32), secrets.token_hex(32)
        self.master = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        wp.private_values.extend([self.internal, self.executor, self.jwt, self.redis_password, self.rabbit_password, self.master])

    def build(self) -> None:
        for service, directory in (("api", "api_gateway"), ("management", "management_service"), ("client", "client_api_gateway")):
            image = self.wp.owner + "-" + service + ":test"
            self.wp.run("build", "--file", str(self.wp.root / "services" / directory / "Dockerfile"), "--tag", image, str(self.wp.root), timeout=600)
            self.images[service] = image

    def infrastructure(self) -> None:
        redis = self.wp.owner + "-redis"
        self.wp.run("run", "-d", "--rm", "--name", redis, "--network", self.wp.network, "--network-alias", "redis",
            "--label", "seo.test.owner=" + self.wp.owner, "--tmpfs", "/data:rw,nosuid", REDIS,
            "redis-server", "--save", "", "--appendonly", "no", "--requirepass", self.redis_password)
        self.wp.resources.append(("container", redis))
        rabbit = self.wp.owner + "-rabbit"
        self.wp.run("run", "-d", "--rm", "--name", rabbit, "--hostname", rabbit, "--network", self.wp.network,
            "--network-alias", "rabbitmq", "--label", "seo.test.owner=" + self.wp.owner, "--tmpfs", "/var/lib/rabbitmq:rw,nosuid",
            "-e", "RABBITMQ_DEFAULT_USER=ar4", "-e", "RABBITMQ_DEFAULT_PASS=" + self.rabbit_password, RABBIT)
        self.wp.resources.append(("container", rabbit))
        for _ in range(60):
            try:
                self.wp.run("exec", "--user", "rabbitmq", rabbit, "rabbitmq-diagnostics", "-q", "check_running", timeout=8)
                return
            except RuntimeError:
                time.sleep(1)
        raise RuntimeError("own RabbitMQ startup timed out")

    def start(self, service: str, role_url: str, *, project_id: str | None = None) -> str:
        port = {"api": 8000, "management": 8004, "client": 8005}[service]
        name = self.wp.owner + "-" + service
        environment = {"ENVIRONMENT": "production", "DATABASE_URL": role_url, "LOG_LEVEL": "WARNING",
            "REDIS_URL": "redis://:" + self.redis_password + "@redis:6379/0", "RABBITMQ_HOST": "rabbitmq",
            "RABBITMQ_USER": "ar4", "RABBITMQ_PASSWORD": self.rabbit_password, "INTERNAL_API_KEY": self.internal,
            "EXECUTOR_API_KEY": self.executor, "MANAGEMENT_EXECUTOR_API_KEY": self.executor,
            "JWT_SECRET_KEY": self.jwt, "MASTER_ENCRYPTION_KEY": self.master, "CORS_ORIGINS": "http://localhost",
            "CORS_ALLOW_CREDENTIALS": "false", "MANAGEMENT_SERVICE_URL": "http://management:8004",
            "API_GATEWAY_URL": "http://api:8000", "CLIENT_GATEWAY_URL": "http://client:8005", "NETPOLICY_PROFILE": "test"}
        if service == "client":
            assert project_id is not None
            environment.update(SEO_MASTER_HMAC_SECRET=self.wp.secret, SEO_MASTER_RECEIPT_SECRET=self.wp.receipt_secret,
                WORDPRESS_V2_TARGETS_JSON=json.dumps({project_id: {"base_url": "http://wordpress.test", "site_id": self.wp.site_id,
                    "key_id": "fixture", "secret_ref": "env:SEO_MASTER_HMAC_SECRET", "receipt_secret_ref": "env:SEO_MASTER_RECEIPT_SECRET"}}))
        # Keep an exited app until labelled cleanup, so startup diagnostics survive.
        args = ["run", "-d", "--name", name, "--network", self.wp.network, "--network-alias", service,
                "--label", "seo.test.owner=" + self.wp.owner, "--publish", f"127.0.0.1::{port}",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,nodev", "--tmpfs", "/app/logs:rw,nosuid,nodev,uid=10001,gid=10001"]
        for key, value in environment.items():
            args.extend(["-e", key + "=" + value])
        if service == "client":
            args.extend(["--entrypoint", "python", self.images[service], "-B", "-c", CLIENT_ENTRY])
        else:
            args.append(self.images[service])
        self.wp.run(*args)
        self.wp.resources.append(("container", name))
        self.apps.append(name)
        info = json.loads(self.wp.run("inspect", name))[0]
        assert info["Config"]["User"] == "appuser"
        assert info["HostConfig"]["ReadonlyRootfs"] is True
        host_port = info["NetworkSettings"]["Ports"][str(port) + "/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + str(host_port)
        path = "/health" if service == "api" else "/health/ready"
        for _ in range(60):
            try:
                request(base, "GET", path)
                assert self.wp.run("exec", name, "id", "-u").strip() == "10001"
                return base
            except (httpx.RequestError, AssertionError):
                time.sleep(.5)
        logs = self.wp.run("logs", name)[-5000:]
        for secret in self.wp.private_values:
            logs = logs.replace(secret, "[fixture-secret]")
        raise RuntimeError("own " + service + " readiness failed: " + logs)

    def stop_apps(self) -> None:
        for name in reversed(self.apps):
            info = json.loads(self.wp.run("inspect", name))[0]
            assert info["Config"]["Labels"]["seo.test.owner"] == self.wp.owner and name.startswith(self.wp.owner + "-")
            self.wp.run("stop", name)
            self.wp.run("container", "rm", name)
            self.wp.resources.remove(("container", name))
        self.apps.clear()


def roles(database: TempDatabase, wp: NativeWordPress) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}
    database.engine.hide_parameters = True
    for service in ("api", "management", "client"):
        role, password = "ar4_" + service + "_" + uuid4().hex[:10], secrets.token_hex(32)
        wp.private_values.append(password)
        with database.engine.begin() as conn:
            conn.execute(text(f'CREATE ROLE "{role}" LOGIN PASSWORD :password NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'), {"password": password})
            conn.execute(text(f'GRANT USAGE ON SCHEMA public,audit_schema,semantic_schema,reporting_schema TO "{role}"'))
            conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public,audit_schema,semantic_schema,reporting_schema TO "{role}"'))
            tables = ("public.users,audit_schema.workspaces,audit_schema.workspace_members,audit_schema.projects,audit_schema.domain_authorizations,audit_schema.project_integrations"
                if service == "api" else "public.tasks,public.hitl_approvals,public.management_changelog,public.management_outbox" if service == "management" else "public.client_keys,public.deployment_log")
            conn.execute(text(f'GRANT INSERT,UPDATE ON TABLE {tables} TO "{role}"'))
            if service == "management":
                conn.execute(text(f'GRANT INSERT ON public.hitl_history,public.processed_events TO "{role}"'))
        local = make_url(database.url).set(username=role, password=password)
        inside = local.set(host="postgres", port=5432)
        result[service] = (role, local.render_as_string(hide_password=False), inside.render_as_string(hide_password=False))
    return result


def run_apps(wp: NativeWordPress) -> None:
    os.environ["TEST_PG_ADMIN_URL"] = wp.start_postgres()
    from tests.integration.test_migrations_postgres import TempDatabase
    stand = AppStand(wp)
    stand.build()
    stand.infrastructure()
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        with database.engine.connect() as conn:
            migrate(conn, expected_role=database.engine.url.username or "")
        credentials = roles(database, wp)
        try:
            api = stand.start("api", credentials["api"][2])
            management = stand.start("management", credentials["management"][2])
            password = secrets.token_urlsafe(24) + "aA9!"
            wp.private_values.append(password)
            user = request(api, "POST", "/api/auth/register", body={"email": "ar4@example.com", "password": password})
            token = user["token"]
            wp.private_values.extend([token, user["refreshToken"]])
            assert request(api, "GET", "/api/auth/me", token=token)["id"] == user["user"]["id"]
            project = request(api, "POST", "/api/projects", token=token, body={"name": "AR4 HTTP", "url": "http://wordpress.test/"}, expected=201)
            project_id, task_id, actor = project["id"], str(uuid4()), user["user"]["id"]
            request(management, "GET", "/api/v1/hitl/tasks/" + task_id,
                    headers={"X-Internal-API-Key": stand.internal}, expected=401)
            workspace = database.scalar("SELECT workspace_id FROM audit_schema.projects WHERE id=:id", id=project_id)
            wp.php("update_option('seo_master_project_id'," + json.dumps(project_id) + ");")
            entity = int(wp.php("$id=wp_insert_post(['post_type'=>'post','post_status'=>'publish','post_name'=>'http-approval','post_title'=>'Мастерская обуви']); update_post_meta($id,'_seo_master_meta_title','Мастерская обуви'); echo $id;"))
            url, now = "http://wordpress.test/http-approval", datetime.now(timezone.utc)
            facts, _, _ = management_proposal_fixture(target_url=url, project_id=project_id, task_id=task_id)
            evidence = seal_evidence({"schema_version": "page_evidence_v1", "audit_id": "http-audit", "project_id": project_id,
                "url": url, "final_url": url, "status_code": 200, "facts": facts["facts"], "jsonld_blocks": 0})
            _, _, version = management_proposal_fixture(target_url=url, project_id=project_id, task_id=task_id,
                entity=ExternalEntity(kind="post", id=str(entity)), evidence_override=[{"evidence_id": evidence["evidence_id"], "sha256": evidence["content_hash"]}])
            with Session(database.engine) as session:
                session.add(Task(id=UUID(task_id), project_id=UUID(project_id), task_type=TaskType.UPDATE_META, status=TaskStatus.PENDING, url=url))
                session.add(row_from_version(version))
                session.add(CrawlResult(audit_id="http-audit", project_id=project_id, root_url=url, mode="full", status="completed",
                    created_at=now, updated_at=now, crawled_at=now, pages=[{"url": url, "page_evidence": evidence}]))
                session.commit()
            runtime = create_engine(credentials["management"][1], isolation_level="READ COMMITTED", hide_parameters=True)
            with Session(runtime) as session:
                approval = ApprovalService(session).prepare(version, metadata={})
                approval_id = str(approval.id)
            runtime.dispose()
            client = stand.start("client", credentials["client"][2], project_id=project_id)
            references = {"operation_id": str(uuid4()), "task_id": task_id, "approval_id": approval_id,
                          "recommendation_version_id": version.version_id, "content_hash": version.content_hash}
            def title() -> str:
                return wp.php("echo get_post_meta(" + str(entity) + ", '_seo_master_meta_title', true);")
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references, expected=409)
            decision = {key: references[key] for key in ("approval_id", "recommendation_version_id", "content_hash")}
            with database.engine.begin() as producer:
                producer.execute(text("SELECT pg_advisory_xact_lock_shared(74219031109004)"))
                request(api, "GET", f"/api/hitl/tasks/{task_id}", token=token, expected=409)
            viewed = request(api, "GET", f"/api/hitl/tasks/{task_id}", token=token)
            assert viewed["diff_data"]["recommendation_version"] == version.model_dump(mode="json")
            request(api, "POST", f"/api/hitl/tasks/{task_id}/approve", token=token, body=decision)
            assert title() == "Мастерская обуви"
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references, expected=409)  # ownership not verified yet
            with Session(database.engine) as session:
                session.add(DomainAuthorization(id=str(uuid4()), project_id=project_id, workspace_id=workspace, status="verified",
                    scope_origin="http://wordpress.test", scope_path_prefix="/", challenge_hash="0" * 64, subject_user_id=actor,
                    issued_at=now-timedelta(days=1), expires_at=now-timedelta(hours=23), verified_at=now))
                session.commit()
            request(client, "POST", "/internal/executions", token=token, body=references, headers={"X-Internal-API-Key": "forged"}, expected=401)
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", body=references, expected=403)  # Existing HTTPBearer missing-header contract.
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=user["refreshToken"], body=references, expected=401)
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body={**references, "changes": {"title": "unreviewed"}}, expected=422)
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body={**references, "content_hash": "0" * 64}, expected=409)
            stranger = request(api, "POST", "/api/auth/register", body={"email": "foreign@example.com", "password": password})
            wp.private_values.extend([stranger["token"], stranger["refreshToken"]])
            request(api, "GET", f"/api/hitl/tasks/{task_id}", token=stranger["token"], expected=404)
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=stranger["token"], body=references, expected=404)
            database.run("UPDATE audit_schema.workspace_members SET role='reviewer' WHERE user_id=:id", id=actor)
            request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references, expected=403)
            database.run("UPDATE audit_schema.workspace_members SET role='owner' WHERE user_id=:id", id=actor)
            assert title() == "Мастерская обуви"
            # Actual process loss AFTER WordPress commit/checked receipt and BEFORE
            # Management completion. A restart must use status, never a second apply.
            from concurrent.futures import ThreadPoolExecutor
            client_name = wp.owner + "-client"
            wp.run("exec", client_name, "python", "-c", "from pathlib import Path; Path('/tmp/ar6-hold-receipt').touch()")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references)
                for _ in range(80):
                    try:
                        marker = wp.run("exec", client_name, "cat", "/tmp/ar6-receipt-received", timeout=5).strip()
                        assert marker == references["operation_id"]
                        break
                    except RuntimeError:
                        time.sleep(.25)
                else:
                    raise AssertionError("fault injection did not reach post-commit boundary")
                assert title() == "Мастерская зимней обуви"
                assert database.scalar("SELECT metadata->>'state' FROM public.management_changelog WHERE id=:id", id=references["operation_id"]) == "applying"
                frozen = database.scalar("SELECT metadata->'record' FROM public.management_changelog WHERE id=:id", id=references["operation_id"])
                wp.run("kill", client_name)
                try:
                    pending.result(timeout=55)
                except (AssertionError, httpx.RequestError):
                    pass  # The original HTTP response is lost; CMS commit already proved.
                else:
                    raise AssertionError("interrupted Gateway unexpectedly delivered a receipt")
            database.run("UPDATE public.users SET is_active=false WHERE id=:id", id=actor)
            wp.run("start", client_name)
            for _ in range(110):
                if database.scalar("SELECT metadata->>'state' FROM public.management_changelog WHERE id=:id", id=references["operation_id"]) == "applied_unverified":
                    break
                time.sleep(1)
            else:
                raise AssertionError("restarted status worker did not reconcile committed WordPress operation")
            assert database.scalar("SELECT metadata->'record' FROM public.management_changelog WHERE id=:id", id=references["operation_id"]) == frozen
            assert database.scalar("SELECT jsonb_array_length(metadata->'recovery'->'attempts') FROM public.management_changelog WHERE id=:id", id=references["operation_id"]) == 1
            assert database.scalar("SELECT metadata->'recovery'->'attempts'->0->>'status' FROM public.management_changelog WHERE id=:id", id=references["operation_id"]) == "succeeded"
            database.run("UPDATE public.users SET is_active=true WHERE id=:id", id=actor)
            result = request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references)
            assert result["state"] == "applied_unverified" and title() == "Мастерская зимней обуви"
            count = wp.php("global $wpdb; echo $wpdb->get_var(\"SELECT count(*) FROM {$wpdb->prefix}seo_master_operations_v2 WHERE operation_id='" + references["operation_id"] + "'\");")
            assert count.strip() == "1"
            assert request(api, "POST", f"/api/hitl/tasks/{task_id}/apply", token=token, body=references) == result
            assert database.scalar("SELECT count(*) FROM public.management_changelog WHERE source='hitl_executor_v1'") == 1
            # The actual publisher and live consumer run with the restricted Management role.
            wp.run("exec", wp.owner + "-management", "python", "-B", "-c", "from services.management_service.tasks.periodic_tasks import dispatch_hitl_outbox; result=dispatch_hitl_outbox(); assert result['published']>=1")
            for _ in range(50):
                if database.scalar("SELECT count(*) FROM public.processed_events WHERE consumer_name='management.hitl_approved'") == 1:
                    break
                time.sleep(.1)
            else:
                raise AssertionError("live HITL inbox did not commit")
            for service, (_name, local, _inside) in credentials.items():
                engine = create_engine(local, hide_parameters=True)
                with engine.connect() as conn:
                    assert conn.scalar(text("SELECT NOT has_database_privilege(current_user,current_database(),'CREATE') AND NOT has_schema_privilege(current_user,'public','CREATE')")) is True
                    if service == "management":
                        assert conn.scalar(text("SELECT has_table_privilege(current_user,'audit_schema.workspace_members','UPDATE')")) is False
                engine.dispose()
            print("PASS actual HTTP apps: UID10001/read-only containers; migrated PG with distinct no-DDL roles; real Redis JWT sessions; approve without apply; Gateway SIGKILL after actual WordPress commit; restarted service-only status recovery with inactive original user; same frozen operation/hash and one WordPress ledger entry; real RabbitMQ outbox/inbox. CMS transport test-private host only; public verify/rollback/staging not claimed.")
        finally:
            stand.stop_apps()
            for role, _local, _inside in credentials.values():
                database.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')
