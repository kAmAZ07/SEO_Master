from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.database_config import SessionLocal
from database.models import (
    Backlink,
    Crawl,
    EEATScore,
    FFScore,
    MetricsHistory,
    Page,
    Project as AuditProject,
    PublicAuditResult,
    SemanticEvent,
    User,
)
from services.client_api_gateway.db.models import ClientKey, DeploymentLog
from services.management_service.db.models import (
    Changelog,
    HITLApproval,
    HITLStatus,
    Project as ManagementProject,
    Task,
    TaskStatus,
    TaskType,
)
from services.project_integrations.models import ProjectIntegration


DEMO_SEED_VERSION = "2026-05-defense"
DEMO_NAMESPACE = uuid.UUID("0f16c9ac-9db8-49de-a1f4-59f7dcbb9fd0")
DEMO_USER_EMAIL = "demo-defense@seo-master.local"
DEMO_USER_PASSWORD = "DemoPass2026!"
DEMO_USER_PASSWORD_HASH = "$pbkdf2-sha256$29000$ei8lpFQKAaDUWiultBYCIA$8KmwWsik.pUAvCJoBaMxEHVBQqwRj87FVTUq58XNnYQ"
DEMO_USER_NAME = "Defense Demo User"
DEMO_PROJECT_ID = str(uuid.uuid5(DEMO_NAMESPACE, f"{DEMO_SEED_VERSION}:project"))
DEMO_PROJECT_NAME = "WordPress Demo Project"
DEMO_PROJECT_URL = "https://demo-wordpress.local"
DEMO_WORDPRESS_SECRET = "demo-wordpress-hmac-secret-2026"


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(DEMO_NAMESPACE, f"{DEMO_SEED_VERSION}:{name}"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


def demo_meta(**extra: Any) -> dict[str, Any]:
    return {
        "demo_seed": True,
        "demo_seed_version": DEMO_SEED_VERSION,
        **extra,
    }


def cleanup_demo_data(db) -> None:
    project_uuid = uuid.UUID(DEMO_PROJECT_ID)

    db.query(DeploymentLog).filter(DeploymentLog.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(ClientKey).filter(ClientKey.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(ProjectIntegration).filter(ProjectIntegration.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)

    db.query(Changelog).filter(Changelog.project_id == project_uuid).delete(synchronize_session=False)
    db.query(HITLApproval).filter(HITLApproval.project_id == project_uuid).delete(synchronize_session=False)
    db.query(Task).filter(Task.project_id == project_uuid).delete(synchronize_session=False)
    db.query(ManagementProject).filter(ManagementProject.id == project_uuid).delete(synchronize_session=False)

    db.query(PublicAuditResult).filter(PublicAuditResult.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(FFScore).filter(FFScore.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(EEATScore).filter(EEATScore.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(MetricsHistory).filter(MetricsHistory.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(SemanticEvent).filter(SemanticEvent.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)

    page_ids = [
        row[0]
        for row in (
            db.query(Page.id)
            .join(Crawl, Page.crawl_id == Crawl.id)
            .filter(Crawl.project_id == DEMO_PROJECT_ID)
            .all()
        )
    ]
    if page_ids:
        db.query(Backlink).filter(Backlink.page_id.in_(page_ids)).delete(synchronize_session=False)
        db.query(Page).filter(Page.id.in_(page_ids)).delete(synchronize_session=False)

    db.query(Crawl).filter(Crawl.project_id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.query(AuditProject).filter(AuditProject.id == DEMO_PROJECT_ID).delete(synchronize_session=False)
    db.commit()


def encrypted_credentials(payload: dict[str, Any]) -> str:
    if os.getenv("MASTER_ENCRYPTION_KEY"):
        try:
            from services.project_integrations.credentials_vault import CredentialsVault

            return CredentialsVault().encrypt(payload)
        except Exception:
            pass
    return json.dumps({"demo_only": True, "credentials": payload}, ensure_ascii=False, separators=(",", ":"))


def build_password_hash(password: str) -> str:
    if password == DEMO_USER_PASSWORD:
        return DEMO_USER_PASSWORD_HASH

    try:
        from passlib.context import CryptContext
    except ImportError as exc:
        raise RuntimeError("Custom demo password requires passlib. Use the default password or run from api-gateway image.") from exc

    return CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto").hash(password)


def upsert_demo_user(db, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            id=stable_uuid("user"),
            email=email,
            hashed_password=build_password_hash(password),
            full_name=DEMO_USER_NAME,
            is_active=True,
            is_superuser=False,
        )
    else:
        user.full_name = DEMO_USER_NAME
        user.is_active = True
        user.hashed_password = build_password_hash(password)
    db.add(user)
    db.flush()
    return user


def create_projects(db, user: User, now: datetime) -> None:
    audit_project = AuditProject(
        id=DEMO_PROJECT_ID,
        name=DEMO_PROJECT_NAME,
        url=DEMO_PROJECT_URL,
        status="active",
        owner_id=str(user.id),
        created_at=now - timedelta(days=32),
        updated_at=now,
    )
    management_project = ManagementProject(
        id=uuid.UUID(DEMO_PROJECT_ID),
        name=DEMO_PROJECT_NAME,
        domain="demo-wordpress.local",
        platform="wordpress",
        is_active=True,
        settings={
            "demo_mode": True,
            "cms": "wordpress",
            "approval_policy": "human_review_required",
        },
        meta=demo_meta(
            owner_id=str(user.id),
            url=DEMO_PROJECT_URL,
            scenario="WordPress HITL approval workflow",
        ),
        created_at=now - timedelta(days=32),
        updated_at=now,
    )
    db.add(audit_project)
    db.add(management_project)


def create_integrations(db, now: datetime) -> None:
    fingerprint = hashlib.sha256(DEMO_WORDPRESS_SECRET.encode("utf-8")).hexdigest()
    wp_details = {
        "base_url": DEMO_PROJECT_URL,
        "status": "connected",
        "plugin_health": {
            "health_url": f"{DEMO_PROJECT_URL}/wp-json/seo-master/v1/health",
            "status": "ok",
            "plugin": "seo-master-connector",
            "version": "0.2.0-demo",
            "checked_at": iso(now - timedelta(minutes=18)),
        },
        "hmac_key": {
            "key_id": "demo_wp_current",
            "fingerprint": fingerprint[:16],
            "generated_at": iso(now - timedelta(days=21)),
            "expires_at": iso(now + timedelta(days=69)),
            "grace_until": iso(now + timedelta(days=76)),
            "rotation_days": 90,
            "grace_days": 7,
        },
        "secret_delivery": "shown_once",
        **demo_meta(),
    }
    db.add(
        ProjectIntegration(
            id=uuid.UUID(stable_uuid("integration-wordpress")),
            project_id=DEMO_PROJECT_ID,
            platform="wordpress",
            encrypted_creds=encrypted_credentials(
                {
                    "base_url": DEMO_PROJECT_URL,
                    "hmac_secret": DEMO_WORDPRESS_SECRET,
                }
            ),
            creds_hint=f"{fingerprint[:6]}...",
            details=wp_details,
            connected_at=now - timedelta(days=21),
            updated_at=now - timedelta(minutes=18),
        )
    )
    db.add(
        ProjectIntegration(
            id=uuid.UUID(stable_uuid("integration-gsc")),
            project_id=DEMO_PROJECT_ID,
            platform="gsc",
            encrypted_creds=encrypted_credentials({"property_url": f"sc-domain:{DEMO_PROJECT_URL.removeprefix('https://')}"}),
            creds_hint="sc-dom...",
            details={
                "property_url": "sc-domain:demo-wordpress.local",
                "auth_mode": "service_account",
                "account_identifier": "demo-gsc-service-account",
                "status": "connected",
                **demo_meta(),
            },
            connected_at=now - timedelta(days=20),
            updated_at=now - timedelta(hours=2),
        )
    )
    db.add(
        ProjectIntegration(
            id=uuid.UUID(stable_uuid("integration-ga4")),
            project_id=DEMO_PROJECT_ID,
            platform="ga4",
            encrypted_creds=encrypted_credentials({"property_id": "demo-ga4-property"}),
            creds_hint="demo-g...",
            details={
                "property_id": "demo-ga4-property",
                "auth_mode": "service_account",
                "account_identifier": "demo-ga4-service-account",
                "status": "connected",
                **demo_meta(),
            },
            connected_at=now - timedelta(days=20),
            updated_at=now - timedelta(hours=2),
        )
    )
    db.add(
        ClientKey(
            id=uuid.UUID(stable_uuid("client-key-wordpress")),
            project_id=DEMO_PROJECT_ID,
            key_id="demo_wp_current",
            secret_ref="demo-seed:wordpress:hmac",
            is_active=True,
            created_at=now - timedelta(days=21),
            expires_at=now + timedelta(days=69),
            last_used_at=now - timedelta(minutes=16),
            meta=demo_meta(secret_fingerprint=fingerprint[:16], rotation_managed_by="demo_seed"),
        )
    )


def create_audit_history(db, now: datetime) -> None:
    audits = [
        {
            "key": "audit-1",
            "days": 18,
            "score": 61,
            "status": "completed",
            "findings": [
                ("missing_meta_description", "7 pages had weak meta descriptions", "high"),
                ("duplicate_h1", "3 pages used duplicate H1 headings", "medium"),
                ("schema_missing", "Product schema was not found", "medium"),
            ],
        },
        {
            "key": "audit-2",
            "days": 9,
            "score": 74,
            "status": "completed",
            "findings": [
                ("slow_lcp", "LCP exceeded 2.5s on landing pages", "medium"),
                ("thin_content", "Service pages need richer commercial intent blocks", "medium"),
            ],
        },
        {
            "key": "audit-3",
            "days": 1,
            "score": 83,
            "status": "completed",
            "findings": [
                ("internal_links", "Add links from blog pages to service pages", "low"),
                ("schema_enhancement", "FAQ schema can improve SERP presentation", "low"),
            ],
        },
    ]
    for item in audits:
        created_at = now - timedelta(days=item["days"], hours=2)
        findings = [
            {
                "code": code,
                "title": title,
                "severity": severity,
                "url": f"{DEMO_PROJECT_URL}/services/{index + 1}",
                **demo_meta(),
            }
            for index, (code, title, severity) in enumerate(item["findings"])
        ]
        db.add(
            PublicAuditResult(
                audit_id=stable_uuid(item["key"]),
                project_id=DEMO_PROJECT_ID,
                root_url=DEMO_PROJECT_URL,
                mode="public",
                site_type_hint="wordpress",
                platform="wordpress",
                seeds=[DEMO_PROJECT_URL],
                status=item["status"],
                summary={
                    "score": item["score"],
                    "pages_checked": 24 + item["days"],
                    "critical_issues": sum(1 for finding in findings if finding["severity"] == "high"),
                    "warnings": len(findings),
                    **demo_meta(),
                },
                findings=findings,
                pages=[
                    {
                        "url": DEMO_PROJECT_URL,
                        "status_code": 200,
                        "title": "Demo WordPress homepage",
                        "score": item["score"],
                    },
                    {
                        "url": f"{DEMO_PROJECT_URL}/services/implantation",
                        "status_code": 200,
                        "title": "Dental implantation",
                        "score": max(0, item["score"] - 8),
                    },
                ],
                options={"max_pages": 50, "demo_seed": True},
                created_at=created_at,
                updated_at=created_at + timedelta(minutes=7),
            )
        )


def create_project_stats(db, now: datetime) -> None:
    crawl = Crawl(
        id=stable_uuid("crawl-main"),
        project_id=DEMO_PROJECT_ID,
        status="completed",
        pages_crawled=6,
        total_pages=6,
        started_at=now - timedelta(days=1, hours=1),
        completed_at=now - timedelta(days=1, minutes=51),
        created_at=now - timedelta(days=1, hours=1),
        updated_at=now - timedelta(days=1, minutes=51),
    )
    db.add(crawl)
    page_specs = [
        ("home", DEMO_PROJECT_URL, "Demo WordPress homepage"),
        ("implant", f"{DEMO_PROJECT_URL}/services/implantation", "Dental implantation"),
        ("braces", f"{DEMO_PROJECT_URL}/services/braces", "Orthodontics and braces"),
        ("blog", f"{DEMO_PROJECT_URL}/blog/how-to-choose-dentist", "How to choose a dentist"),
    ]
    for index, (key, url, title) in enumerate(page_specs):
        page_id = stable_uuid(f"page-{key}")
        db.add(
            Page(
                id=page_id,
                crawl_id=crawl.id,
                url=url,
                status_code=200,
                title=title,
                description=f"Demo SEO description for {title.lower()}",
                h1=title,
                content_length=5200 + index * 850,
                load_time=0.82 + index * 0.13,
                meta_data=demo_meta(template="wordpress_page"),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        db.add(
            Backlink(
                id=stable_uuid(f"backlink-{key}"),
                page_id=page_id,
                source_url=f"https://partner-demo.example/reviews/{key}",
                anchor_text=f"{title} clinic review",
                link_type="dofollow" if index % 2 == 0 else "nofollow",
                discovered_at=now - timedelta(days=3 + index),
                created_at=now - timedelta(days=3 + index),
                updated_at=now - timedelta(days=3 + index),
            )
        )

    keywords = [
        ("dental implants moscow", 2400, 8.7, -2),
        ("orthodontist consultation", 1300, 11.4, 4),
        ("teeth whitening price", 1900, 6.1, -1),
        ("emergency dentist", 900, 14.2, 2),
        ("children dentistry", 700, 9.9, 0),
    ]
    for index, (keyword, volume, position, change) in enumerate(keywords):
        db.add(
            SemanticEvent(
                id=stable_uuid(f"keyword-{index}"),
                event_type="keyword_tracked",
                project_id=DEMO_PROJECT_ID,
                event_data={
                    "id": stable_uuid(f"keyword-id-{index}"),
                    "keyword": keyword,
                    "volume": volume,
                    "position": position,
                    "change": change,
                    "source": "demo_gsc_snapshot",
                    **demo_meta(),
                },
                created_at=now - timedelta(days=5, minutes=index),
                updated_at=now - timedelta(days=5, minutes=index),
            )
        )

    db.add(
        FFScore(
            score_id=stable_uuid("ff-score-current"),
            project_id=DEMO_PROJECT_ID,
            root_url=DEMO_PROJECT_URL,
            ff_score=82.4,
            components={"freshness": 78.0, "familiarity": 84.0, "quality": 85.0},
            inputs={
                "content_pages": 24,
                "gsc_queries": 412,
                "ga4_sessions_30d": 18420,
                **demo_meta(),
            },
            thresholds={"rescue_lt": 40, "growth_gt": 60},
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=3),
        )
    )
    db.add(
        EEATScore(
            score_id=stable_uuid("eeat-score-current"),
            project_id=DEMO_PROJECT_ID,
            root_url=DEMO_PROJECT_URL,
            score=76.0,
            breakdown={"expertise": 79, "experience": 72, "authority": 74, "trust": 81},
            signals=demo_meta(author_pages=3, medical_review_badges=5),
            created_at=now - timedelta(hours=4),
            updated_at=now - timedelta(hours=4),
        )
    )
    db.add(
        MetricsHistory(
            metric_id=stable_uuid("metrics-history-current"),
            project_id=DEMO_PROJECT_ID,
            root_url=DEMO_PROJECT_URL,
            metrics={
                "organic_clicks_30d": 6420,
                "organic_impressions_30d": 128500,
                "avg_position": 9.8,
                "hitl_actions": 18,
                "automated_actions": 43,
                **demo_meta(),
            },
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=2),
        )
    )


def task_payloads(now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "key": "task-pending-meta",
            "task_type": TaskType.UPDATE_META,
            "status": TaskStatus.PENDING,
            "hitl_status": HITLStatus.PENDING,
            "url": f"{DEMO_PROJECT_URL}/services/implantation",
            "title": "Improve meta title and description",
            "description": "AI suggested a more specific commercial snippet for the implantation service page.",
            "impact": 0.91,
            "effort": 0.18,
            "before": {
                "title": "Dental implantation",
                "meta_description": "Implants and dentistry services.",
                "h1": "Dental implantation",
            },
            "after": {
                "title": "Dental implants in Moscow - consultation and treatment plan",
                "meta_description": "Book a dental implant consultation, get diagnostics, pricing guidance and a personal treatment plan.",
                "h1": "Dental implants with treatment planning",
            },
            "recommendation": "Approve after checking that the medical wording matches the clinic policy.",
            "created_delta": timedelta(hours=5),
        },
        {
            "key": "task-pending-schema",
            "task_type": TaskType.UPDATE_SCHEMA,
            "status": TaskStatus.PENDING,
            "hitl_status": HITLStatus.PENDING,
            "url": f"{DEMO_PROJECT_URL}/services/braces",
            "title": "Add FAQ schema for orthodontics page",
            "description": "FAQ markup can improve eligibility for rich results.",
            "impact": 0.73,
            "effort": 0.27,
            "before": {"schema": None},
            "after": {
                "schema": {
                    "@type": "FAQPage",
                    "questions": [
                        "How long does orthodontic treatment take?",
                        "Can adults wear braces?",
                    ],
                }
            },
            "recommendation": "Approve if FAQ answers are already present on the visible page.",
            "created_delta": timedelta(hours=8),
        },
        {
            "key": "task-approved-links",
            "task_type": TaskType.ADD_INTERNAL_LINKS,
            "status": TaskStatus.APPROVED,
            "hitl_status": HITLStatus.APPROVED,
            "url": f"{DEMO_PROJECT_URL}/blog/how-to-choose-dentist",
            "title": "Add internal links from blog to service pages",
            "description": "Approved internal links increase crawl depth and topical relevance.",
            "impact": 0.66,
            "effort": 0.22,
            "before": {"internal_links": []},
            "after": {
                "internal_links": [
                    {"anchor": "dental implantation consultation", "url": f"{DEMO_PROJECT_URL}/services/implantation"},
                    {"anchor": "orthodontist consultation", "url": f"{DEMO_PROJECT_URL}/services/braces"},
                ]
            },
            "recommendation": "Already approved by demo reviewer.",
            "created_delta": timedelta(days=1, hours=3),
            "approved_delta": timedelta(days=1),
        },
        {
            "key": "task-rejected-content",
            "task_type": TaskType.UPDATE_CONTENT,
            "status": TaskStatus.REJECTED,
            "hitl_status": HITLStatus.REJECTED,
            "url": f"{DEMO_PROJECT_URL}/services/whitening",
            "title": "Rewrite whitening page intro",
            "description": "Rejected because claims need legal review before publication.",
            "impact": 0.58,
            "effort": 0.61,
            "before": {"intro": "Professional teeth whitening in one visit."},
            "after": {"intro": "Guaranteed whitening result in one visit with no sensitivity."},
            "recommendation": "Rejected: marketing claim is too strong for medical content.",
            "created_delta": timedelta(days=2),
            "rejected_delta": timedelta(days=1, hours=18),
        },
        {
            "key": "task-deployed-meta",
            "task_type": TaskType.UPDATE_META,
            "status": TaskStatus.DEPLOYED,
            "hitl_status": HITLStatus.APPROVED,
            "url": DEMO_PROJECT_URL,
            "title": "Deploy homepage SEO snippet",
            "description": "Approved and deployed through WordPress connector.",
            "impact": 0.81,
            "effort": 0.16,
            "before": {"title": "Dental clinic", "meta_description": "Dentistry services."},
            "after": {
                "title": "Family dental clinic - diagnostics, treatment and prevention",
                "meta_description": "Modern dental care with diagnostics, treatment planning and transparent pricing.",
            },
            "recommendation": "Successfully deployed to WordPress demo connector.",
            "created_delta": timedelta(days=4),
            "approved_delta": timedelta(days=3, hours=20),
            "deployed_delta": timedelta(days=3, hours=19, minutes=54),
        },
        {
            "key": "task-failed-schema",
            "task_type": TaskType.UPDATE_SCHEMA,
            "status": TaskStatus.FAILED,
            "hitl_status": HITLStatus.APPROVED,
            "url": f"{DEMO_PROJECT_URL}/prices",
            "title": "Deploy price list schema",
            "description": "Deployment failed in demo history because target page was locked.",
            "impact": 0.49,
            "effort": 0.34,
            "before": {"schema": None},
            "after": {"schema": {"@type": "OfferCatalog", "name": "Dental services price list"}},
            "recommendation": "Retry after editor lock is released.",
            "created_delta": timedelta(days=5),
            "approved_delta": timedelta(days=4, hours=18),
        },
        {
            "key": "task-completed-image",
            "task_type": TaskType.OPTIMIZE_IMAGES,
            "status": TaskStatus.COMPLETED,
            "url": f"{DEMO_PROJECT_URL}/blog/how-to-choose-dentist",
            "title": "Compress blog images",
            "description": "Images were optimized after audit recommendations.",
            "impact": 0.42,
            "effort": 0.28,
            "before": {"total_image_weight_kb": 1840},
            "after": {"total_image_weight_kb": 720},
            "recommendation": "Completed automatically by media optimizer.",
            "created_delta": timedelta(days=6),
            "completed_delta": timedelta(days=5, hours=22),
        },
    ]


def create_tasks_and_history(db, now: datetime) -> None:
    project_uuid = uuid.UUID(DEMO_PROJECT_ID)
    for spec in task_payloads(now):
        task_id = uuid.UUID(stable_uuid(spec["key"]))
        created_at = now - spec["created_delta"]
        priority = float(spec["impact"]) * (2 - float(spec["effort"]))
        meta = demo_meta(
            correlation_id=stable_uuid(f"{spec['key']}-correlation"),
            source="demo_orchestrator",
            url=spec["url"],
            rollback_available=spec["status"] in {TaskStatus.DEPLOYED, TaskStatus.FAILED},
            before=spec["before"],
            after=spec["after"],
        )
        task = Task(
            id=task_id,
            project_id=project_uuid,
            task_type=spec["task_type"],
            status=spec["status"],
            url=spec["url"],
            title=spec["title"],
            description=spec["description"],
            impact_score=spec["impact"],
            effort_score=spec["effort"],
            priority_score=priority,
            meta=meta,
            assigned_to="seo-demo-reviewer",
            created_at=created_at,
            updated_at=now - timedelta(minutes=30),
        )
        if spec.get("completed_delta"):
            task.completed_at = now - spec["completed_delta"]
        if spec.get("deployed_delta"):
            task.deployed_at = now - spec["deployed_delta"]
            task.completed_at = task.deployed_at
        db.add(task)

        hitl_status = spec.get("hitl_status")
        if hitl_status:
            approval = HITLApproval(
                id=uuid.UUID(stable_uuid(f"{spec['key']}-hitl")),
                task_id=task_id,
                project_id=project_uuid,
                status=hitl_status,
                diff_data={"before": spec["before"], "after": spec["after"]},
                impact_score=spec["impact"],
                recommendation=spec["recommendation"],
                meta=demo_meta(
                    correlation_id=meta["correlation_id"],
                    reviewer_queue="content-owner",
                    url=spec["url"],
                ),
                created_at=created_at + timedelta(minutes=3),
                updated_at=now - timedelta(minutes=25),
            )
            if hitl_status == HITLStatus.APPROVED:
                approval.approved_by = "demo-reviewer"
                approval.approved_at = now - spec.get("approved_delta", timedelta(hours=1))
            if hitl_status == HITLStatus.REJECTED:
                approval.rejected_by = "demo-reviewer"
                approval.rejected_at = now - spec.get("rejected_delta", timedelta(hours=1))
                approval.rejection_reason = spec["recommendation"]
            db.add(approval)

        if spec["status"] in {TaskStatus.DEPLOYED, TaskStatus.FAILED, TaskStatus.COMPLETED}:
            applied = spec["status"] in {TaskStatus.DEPLOYED, TaskStatus.COMPLETED}
            applied_at = task.deployed_at or task.completed_at
            db.add(
                Changelog(
                    id=uuid.UUID(stable_uuid(f"{spec['key']}-changelog")),
                    project_id=project_uuid,
                    task_id=task_id,
                    entity_id=spec["url"],
                    entity_type="wordpress_post",
                    change_type=spec["task_type"].value,
                    before_value=spec["before"],
                    after_value=spec["after"],
                    applied=applied,
                    applied_at=applied_at,
                    source="HITL" if spec.get("hitl_status") == HITLStatus.APPROVED else "auto",
                    meta=demo_meta(correlation_id=meta["correlation_id"]),
                    created_at=applied_at or created_at,
                )
            )
            db.add(
                DeploymentLog(
                    id=uuid.UUID(stable_uuid(f"{spec['key']}-deployment")),
                    project_id=DEMO_PROJECT_ID,
                    task_id=str(task_id),
                    change_type=spec["task_type"].value.lower(),
                    entity_id=spec["url"],
                    entity_type="wordpress_post",
                    status="applied" if applied else "failed",
                    error_message=None if applied else "Demo target page was locked by editor",
                    changes=[
                        {"op": "replace", "path": key, "value": value}
                        for key, value in spec["after"].items()
                    ],
                    meta=demo_meta(
                        dispatch={
                            "platform": "wordpress",
                            "target_path": "/wp-json/seo-master/v1/meta",
                            "rollback_available": True,
                        },
                        rollback_changes=[
                            {"op": "replace", "path": key, "value": value}
                            for key, value in spec["before"].items()
                        ],
                    ),
                    source_ip="127.0.0.1",
                    user_agent="demo-seed",
                    correlation_id=meta["correlation_id"],
                    created_at=applied_at or created_at,
                    applied_at=applied_at if applied else None,
                )
            )


def seed_demo_data(email: str, password: str, clean_first: bool) -> dict[str, Any]:
    db = SessionLocal()
    try:
        if clean_first:
            cleanup_demo_data(db)

        now = utc_now()
        user = upsert_demo_user(db, email, password)
        create_projects(db, user, now)
        create_integrations(db, now)
        create_audit_history(db, now)
        create_project_stats(db, now)
        create_tasks_and_history(db, now)
        db.commit()

        return {
            "user_email": email,
            "user_password": password,
            "project_id": DEMO_PROJECT_ID,
            "project_url": DEMO_PROJECT_URL,
            "seed_version": DEMO_SEED_VERSION,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed controlled demo data for defense presentation.")
    parser.add_argument("--email", default=os.getenv("DEMO_USER_EMAIL", DEMO_USER_EMAIL))
    parser.add_argument("--password", default=os.getenv("DEMO_USER_PASSWORD", DEMO_USER_PASSWORD))
    parser.add_argument("--no-clean", action="store_true", help="Do not remove previous demo seed data first.")
    args = parser.parse_args()

    result = seed_demo_data(args.email, args.password, clean_first=not args.no_clean)
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
