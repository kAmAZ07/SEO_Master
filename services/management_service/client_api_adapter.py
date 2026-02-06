from typing import Dict, Any, Optional, List
from datetime import datetime
import httpx
from sqlalchemy.orm import Session

from services.management_service.config import settings
from services.management_service.db.models import Task, TaskStatus, TaskType, Changelog
from config.loggingconfig import get_logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from prometheus_client import Counter, Histogram

logger = get_logger(__name__)

deployment_requests_total = Counter(
    'deployment_requests_total',
    'Total deployment requests to Client API Gateway',
    ['status']
)

deployment_duration = Histogram(
    'deployment_duration_seconds',
    'Duration of deployment requests'
)

deployment_errors_total = Counter(
    'deployment_errors_total',
    'Total deployment errors',
    ['error_type']
)


class ClientAPIAdapter:
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.CLIENT_API_GATEWAY_URL
        self.internal_api_key = settings.INTERNAL_API_KEY
        self.timeout = httpx.Timeout(60.0, connect=10.0, read=50.0)
        
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY not configured")
        
        if not self.base_url:
            raise ValueError("CLIENT_API_GATEWAY_URL not configured")
        
        if len(self.internal_api_key) < 32:
            logger.warning("INTERNAL_API_KEY should be at least 32 characters")
    
    def _validate_change_data(self, changes_data: Dict[str, Any]):
        required_fields = ['project_id', 'task_id', 'change_type', 'entity_id', 'entity_type', 'changes']
        
        for field in required_fields:
            if field not in changes_data:
                raise ValueError(f"Missing required field: {field}")
        
        if not isinstance(changes_data['changes'], dict):
            raise ValueError("changes must be a dictionary")
        
        if 'before' not in changes_data['changes'] or 'after' not in changes_data['changes']:
            raise ValueError("changes must contain 'before' and 'after' keys")
    
    def _build_deployment_payload(
        self,
        task_id: str,
        changes_data: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        self._validate_change_data(changes_data)
        
        payload = {
            "project_id": changes_data['project_id'],
            "task_id": task_id,
            "change_type": changes_data['change_type'],
            "entity_id": changes_data['entity_id'],
            "entity_type": changes_data['entity_type'],
            "changes": changes_data['changes'],
            "priority": changes_data.get('priority', 5),
            "metadata": {
                **(changes_data.get('metadata', {})),
                "correlation_id": correlation_id,
                "deployed_at": datetime.utcnow().isoformat(),
                "deployed_from": "management_service"
            }
        }
        
        return payload
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
    )
    async def _send_deployment_request(
        self,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json"
        }
        
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                with deployment_duration.time():
                    response = await client.post(
                        f"{self.base_url}/internal/deploy",
                        json=payload,
                        headers=headers
                    )
                    response.raise_for_status()
                
                result = response.json()
                deployment_requests_total.labels(status='success').inc()
                
                return result
                
            except httpx.HTTPStatusError as e:
                deployment_requests_total.labels(status='error').inc()
                deployment_errors_total.labels(error_type='http_error').inc()
                
                logger.error(
                    f"Client API Gateway HTTP error: {e.response.status_code}",
                    extra={
                        "task_id": payload.get('task_id'),
                        "project_id": payload.get('project_id'),
                        "status_code": e.response.status_code,
                        "response_body": e.response.text[:500],
                        "correlation_id": correlation_id
                    }
                )
                raise
                
            except httpx.TimeoutException as e:
                deployment_requests_total.labels(status='timeout').inc()
                deployment_errors_total.labels(error_type='timeout').inc()
                
                logger.error(
                    f"Client API Gateway timeout: {e}",
                    extra={
                        "task_id": payload.get('task_id'),
                        "project_id": payload.get('project_id'),
                        "correlation_id": correlation_id
                    }
                )
                raise
    
    async def deploy_changes(
        self,
        task_id: str,
        changes_data: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        logger.info(
            f"Deploying changes for task {task_id}",
            extra={
                "task_id": task_id,
                "project_id": changes_data.get('project_id'),
                "change_type": changes_data.get('change_type'),
                "entity_id": changes_data.get('entity_id'),
                "correlation_id": correlation_id
            }
        )
        
        try:
            payload = self._build_deployment_payload(task_id, changes_data, correlation_id)
            
            result = await self._send_deployment_request(payload, correlation_id)
            
            logger.info(
                f"Successfully deployed changes for task {task_id}",
                extra={
                    "task_id": task_id,
                    "project_id": changes_data.get('project_id'),
                    "change_id": result.get('change_id'),
                    "status": result.get('status'),
                    "correlation_id": correlation_id
                }
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Failed to deploy changes for task {task_id}: {e}",
                extra={
                    "task_id": task_id,
                    "project_id": changes_data.get('project_id'),
                    "correlation_id": correlation_id,
                    "error": str(e)
                }
            )
            raise
    
    async def deploy_multiple_changes(
        self,
        task_id: str,
        changes_list: List[Dict[str, Any]],
        correlation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        
        results = []
        failed_deployments = []
        
        logger.info(
            f"Deploying {len(changes_list)} changes for task {task_id}",
            extra={
                "task_id": task_id,
                "total_changes": len(changes_list),
                "correlation_id": correlation_id
            }
        )
        
        for idx, changes_data in enumerate(changes_list):
            try:
                result = await self.deploy_changes(task_id, changes_data, correlation_id)
                results.append({
                    "index": idx,
                    "success": True,
                    "result": result
                })
                
            except Exception as e:
                failed_deployments.append({
                    "index": idx,
                    "changes_data": changes_data,
                    "error": str(e)
                })
                
                results.append({
                    "index": idx,
                    "success": False,
                    "error": str(e)
                })
        
        if failed_deployments:
            logger.warning(
                f"Failed to deploy {len(failed_deployments)} out of {len(changes_list)} changes",
                extra={
                    "task_id": task_id,
                    "failed_count": len(failed_deployments),
                    "total_count": len(changes_list),
                    "correlation_id": correlation_id
                }
            )
        
        return results
    
    async def get_deployment_status(
        self,
        project_id: str,
        change_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json"
        }
        
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/changes/pending/{project_id}",
                    headers=headers
                )
                response.raise_for_status()
                
                result = response.json()
                
                for change in result:
                    if change.get('change_id') == change_id:
                        return change
                
                return {"status": "not_found"}
                
            except Exception as e:
                logger.error(
                    f"Failed to get deployment status: {e}",
                    extra={
                        "project_id": project_id,
                        "change_id": change_id,
                        "correlation_id": correlation_id
                    }
                )
                raise
    
    async def get_pending_changes(
        self,
        project_id: str,
        limit: int = 50,
        correlation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        
        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json"
        }
        
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/changes/pending/{project_id}",
                    params={"limit": limit},
                    headers=headers
                )
                response.raise_for_status()
                
                return response.json()
                
            except Exception as e:
                logger.error(
                    f"Failed to get pending changes: {e}",
                    extra={
                        "project_id": project_id,
                        "correlation_id": correlation_id
                    }
                )
                raise


def _get_entity_type(task_type: TaskType) -> str:
    
    entity_type_map = {
        TaskType.UPDATE_META: "wordpress_post",
        TaskType.UPDATE_CONTENT: "wordpress_content",
        TaskType.ADD_INTERNAL_LINKS: "wordpress_post",
        TaskType.UPDATE_SCHEMA: "wordpress_post",
        TaskType.FIX_404: "wordpress_redirect",
        TaskType.UPDATE_TILDA_PAGE: "tilda_page"
    }
    
    return entity_type_map.get(task_type, "wordpress_post")


def _extract_changes_from_task(task: Task) -> Dict[str, Any]:
    
    metadata = task.metadata or {}
    
    if task.task_type == TaskType.ADD_INTERNAL_LINKS:
        interlinks = metadata.get('interlinks', [])
        
        return {
            "before": {"internal_links": []},
            "after": {
                "internal_links": [
                    {
                        "target_url": link['target_url'],
                        "anchor_text": link['anchor_text'],
                        "position": link.get('position', 'body')
                    }
                    for link in interlinks
                ]
            }
        }
    
    elif task.task_type == TaskType.UPDATE_META:
        diff_data = metadata.get('diff_data', {})
        
        return {
            "before": {
                "title": diff_data.get('before', {}).get('title', ''),
                "description": diff_data.get('before', {}).get('description', ''),
                "h1": diff_data.get('before', {}).get('h1', '')
            },
            "after": {
                "title": diff_data.get('after', {}).get('title', ''),
                "description": diff_data.get('after', {}).get('description', ''),
                "h1": diff_data.get('after', {}).get('h1', '')
            }
        }
    
    elif task.task_type == TaskType.UPDATE_SCHEMA:
        diff_data = metadata.get('diff_data', {})
        
        return {
            "before": {"schema": diff_data.get('before', {}).get('schema', {})},
            "after": {"schema": diff_data.get('after', {}).get('schema', {})}
        }
    
    elif 'diff_data' in metadata:
        diff_data = metadata['diff_data']
        return {
            "before": diff_data.get('before', {}),
            "after": diff_data.get('after', {})
        }
    
    else:
        return {
            "before": {},
            "after": metadata.get('changes', {})
        }


def _calculate_priority(task: Task) -> int:
    
    metadata = task.metadata or {}
    
    impact_score = metadata.get('impact_score', 0.5)
    
    if 'average_impact_score' in metadata:
        impact_score = metadata['average_impact_score']
    
    priority = int(impact_score * 10)
    
    return max(1, min(priority, 10))


def _log_to_changelog(
    db: Session,
    task: Task,
    change_id: str,
    deployment_result: Dict[str, Any],
    correlation_id: Optional[str] = None
):
    
    changes = _extract_changes_from_task(task)
    
    changelog_entry = Changelog(
        project_id=task.project_id,
        task_id=str(task.id),
        entity_id=task.url,
        entity_type=_get_entity_type(task.task_type),
        change_type=task.task_type.value,
        before_value=changes.get('before'),
        after_value=changes.get('after'),
        applied=False,
        source="HITL" if task.status == TaskStatus.APPROVED else "auto",
        metadata={
            "change_id": change_id,
            "correlation_id": correlation_id,
            "deployment_status": deployment_result.get('status'),
            "created_at": datetime.utcnow().isoformat()
        }
    )
    
    db.add(changelog_entry)
    db.commit()
    
    logger.info(
        f"Logged deployment to changelog",
        extra={
            "task_id": str(task.id),
            "change_id": change_id,
            "project_id": task.project_id,
            "correlation_id": correlation_id
        }
    )


async def deploy_changes(
    task_id: str,
    changes_data: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    
    adapter = ClientAPIAdapter()
    return await adapter.deploy_changes(task_id, changes_data, correlation_id)


async def deploy_task_changes(
    db: Session,
    task: Task,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    
    if task.status != TaskStatus.APPROVED:
        raise ValueError(f"Task {task.id} is not approved for deployment")
    
    adapter = ClientAPIAdapter()
    
    changes_data = {
        "project_id": task.project_id,
        "task_id": str(task.id),
        "change_type": task.task_type.value,
        "entity_id": task.url,
        "entity_type": _get_entity_type(task.task_type),
        "changes": _extract_changes_from_task(task),
        "priority": _calculate_priority(task),
        "metadata": task.metadata
    }
    
    try:
        result = await adapter.deploy_changes(str(task.id), changes_data, correlation_id)
        
        change_id = result.get('change_id')
        
        task.status = TaskStatus.DEPLOYED
        task.metadata = {
            **(task.metadata or {}),
            "deployment": {
                "change_id": change_id,
                "deployed_at": datetime.utcnow().isoformat(),
                "status": result.get('status')
            }
        }
        
        db.add(task)
        db.flush()
        
        _log_to_changelog(db, task, change_id, result, correlation_id)
        
        db.commit()
        
        logger.info(
            f"Task {task.id} deployed successfully",
            extra={
                "task_id": str(task.id),
                "project_id": task.project_id,
                "change_id": change_id,
                "correlation_id": correlation_id
            }
        )
        
        return result
        
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.metadata = {
            **(task.metadata or {}),
            "error": {
                "message": str(e),
                "failed_at": datetime.utcnow().isoformat()
            }
        }
        
        db.add(task)
        db.commit()
        
        logger.error(
            f"Failed to deploy task {task.id}: {e}",
            extra={
                "task_id": str(task.id),
                "project_id": task.project_id,
                "correlation_id": correlation_id
            }
        )
        raise


async def deploy_interlink_task(
    db: Session,
    task: Task,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    
    if task.task_type != TaskType.ADD_INTERNAL_LINKS:
        raise ValueError(f"Task {task.id} is not an interlink task")
    
    if task.status != TaskStatus.APPROVED:
        raise ValueError(f"Task {task.id} is not approved")
    
    return await deploy_task_changes(db, task, correlation_id)


async def deploy_approved_tasks(
    db: Session,
    project_id: str,
    max_tasks: int = 10,
    correlation_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    
    approved_tasks = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status == TaskStatus.APPROVED
    ).order_by(
        Task.metadata['average_impact_score'].desc().nullslast()
    ).limit(max_tasks).all()
    
    results = []
    
    for task in approved_tasks:
        try:
            result = await deploy_task_changes(db, task, correlation_id)
            results.append({
                "task_id": str(task.id),
                "success": True,
                "result": result
            })
        except Exception as e:
            results.append({
                "task_id": str(task.id),
                "success": False,
                "error": str(e)
            })
    
    logger.info(
        f"Deployed {len(results)} tasks for project {project_id}",
        extra={
            "project_id": project_id,
            "total_tasks": len(results),
            "correlation_id": correlation_id
        }
    )
    
    return results
