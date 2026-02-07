from typing import List, Dict, Any, Optional, Tuple, Set
from sqlalchemy.orm import Session
from datetime import datetime
import httpx
import re
import json
import hashlib
from collections import defaultdict
from urllib.parse import urlparse

from services.management_service.config import settings
from services.management_service.db.models import Project, Task, TaskType, TaskStatus
from services.management_service.db.session import get_db
from services.management_service.events.task_created import publish_task_created_event
from config.loggingconfig import get_logger

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from prometheus_client import Counter, Histogram
import redis.asyncio as redis

logger = get_logger(__name__)

interlink_generation_duration = Histogram(
    'interlink_generation_duration_seconds',
    'Duration of interlink generation',
    ['project_id']
)

interlinks_generated_total = Counter(
    'interlinks_generated_total',
    'Total number of interlinks generated',
    ['project_id']
)

interlink_errors_total = Counter(
    'interlink_errors_total',
    'Total errors during interlink generation',
    ['error_type']
)

semantic_api_calls_total = Counter(
    'semantic_api_calls_total',
    'Total calls to Semantic Service API',
    ['endpoint', 'cached']
)


class InternalLink:
    
    def __init__(
        self,
        source_url: str,
        target_url: str,
        anchor_text: str,
        context: str,
        relevance_score: float,
        position: Optional[str] = None,
        impact_score: Optional[float] = None
    ):
        self.source_url = source_url
        self.target_url = target_url
        self.anchor_text = anchor_text
        self.context = context
        self.relevance_score = relevance_score
        self.position = position or "body"
        self.impact_score = impact_score or 0.5


class InterlinkGenerator:
    
    def __init__(self, db: Session, redis_client: Optional[redis.Redis] = None):
        self.db = db
        self.redis_client = redis_client
        self.min_relevance_score = 0.6
        self.max_links_per_page = 10
        self.min_anchor_length = 15
        self.max_anchor_length = 60
        self.min_content_words = 100
        self.cache_ttl = 604800 
        self.link_graph: Dict[str, Set[str]] = defaultdict(set)
        self._validate_config()
    
    def _validate_config(self):
        if not settings.INTERNAL_API_KEY:
            raise ValueError("INTERNAL_API_KEY is not configured")
        
        if not settings.SEMANTIC_SERVICE_URL:
            raise ValueError("SEMANTIC_SERVICE_URL is not configured")
        
        if not settings.AUDIT_SERVICE_URL:
            raise ValueError("AUDIT_SERVICE_URL is not configured")
        
        if len(settings.INTERNAL_API_KEY) < 32:
            logger.warning("INTERNAL_API_KEY should be at least 32 characters")
    
    def _get_cache_key(self, prefix: str, data: Dict[str, Any]) -> str:
        data_str = json.dumps(data, sort_keys=True)
        hash_value = hashlib.sha256(data_str.encode()).hexdigest()
        return f"interlink:{prefix}:{hash_value}"
    
    async def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        if not self.redis_client:
            return None
        
        try:
            cached = await self.redis_client.get(cache_key)
            if cached:
                semantic_api_calls_total.labels(endpoint='cached', cached='hit').inc()
                return json.loads(cached)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        
        return None
    
    async def _set_cached(self, cache_key: str, data: Dict[str, Any], ttl: int):
        if not self.redis_client:
            return
        
        try:
            await self.redis_client.setex(
                cache_key,
                ttl,
                json.dumps(data)
            )
        except Exception as e:
            logger.error(f"Redis set error: {e}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
    )
    async def _call_semantic_service(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        if use_cache:
            cache_key = self._get_cache_key(f"semantic:{endpoint}", payload)
            cached_result = await self._get_cached(cache_key)
            if cached_result:
                return cached_result
        
        semantic_api_calls_total.labels(endpoint=endpoint, cached='miss').inc()
        
        timeout = httpx.Timeout(30.0, connect=5.0, read=25.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            headers = {
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "Content-Type": "application/json"
            }
            if correlation_id:
                headers["X-Correlation-ID"] = correlation_id
            
            try:
                response = await client.post(
                    f"{settings.SEMANTIC_SERVICE_URL}{endpoint}",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                result = response.json()
                
                if use_cache:
                    await self._set_cached(cache_key, result, self.cache_ttl)
                
                return result
                
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Semantic Service HTTP error: {e.response.status_code}",
                    extra={"endpoint": endpoint, "correlation_id": correlation_id}
                )
                interlink_errors_total.labels(error_type='semantic_api_http').inc()
                raise
            except httpx.TimeoutException as e:
                logger.error(
                    f"Semantic Service timeout: {e}",
                    extra={"endpoint": endpoint, "correlation_id": correlation_id}
                )
                interlink_errors_total.labels(error_type='semantic_api_timeout').inc()
                raise
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_project_pages(self, project_id: str) -> List[Dict[str, Any]]:
        timeout = httpx.Timeout(30.0, connect=5.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{settings.AUDIT_SERVICE_URL}/internal/project/{project_id}/pages",
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            )
            response.raise_for_status()
            return response.json()["pages"]
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_page_content(self, project_id: str, url: str) -> Dict[str, Any]:
        timeout = httpx.Timeout(30.0, connect=5.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{settings.AUDIT_SERVICE_URL}/internal/page/content",
                params={"project_id": project_id, "url": url},
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            )
            response.raise_for_status()
            return response.json()
    
    async def calculate_semantic_similarity(
        self,
        text1: str,
        text2: str,
        correlation_id: Optional[str] = None
    ) -> float:
        result = await self._call_semantic_service(
            "/internal/semantic/similarity",
            {
                "text1": text1[:1000],
                "text2": text2[:1000]
            },
            correlation_id,
            use_cache=True
        )
        return float(result.get("similarity_score", 0.0))
    
    async def extract_keywords(
        self,
        text: str,
        max_keywords: int = 10,
        correlation_id: Optional[str] = None
    ) -> List[str]:
        result = await self._call_semantic_service(
            "/internal/semantic/extract-keywords",
            {
                "text": text[:2000],
                "max_keywords": max_keywords
            },
            correlation_id,
            use_cache=True
        )
        return result.get("keywords", [])
    
    async def generate_anchor_text_llm(
        self,
        source_context: str,
        target_page_title: str,
        target_page_description: str,
        keywords: List[str],
        correlation_id: Optional[str] = None
    ) -> str:
        result = await self._call_semantic_service(
            "/internal/content/generate-anchor",
            {
                "source_context": source_context[:500],
                "target_title": target_page_title[:200],
                "target_description": target_page_description[:300],
                "keywords": keywords[:5]
            },
            correlation_id,
            use_cache=True
        )
        return result.get("anchor_text", "")
    
    def extract_sentences_with_keywords(
        self,
        content: str,
        keywords: List[str],
        max_sentences: int = 5
    ) -> List[str]:
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        
        scored_sentences = []
        for sentence in sentences:
            score = sum(1 for kw in keywords if kw.lower() in sentence.lower())
            if score > 0:
                scored_sentences.append((sentence, score))
        
        scored_sentences.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored_sentences[:max_sentences]]
    
    async def calculate_page_relevance(
        self,
        source_page: Dict[str, Any],
        target_page: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> float:
        source_text = f"{source_page.get('title', '')} {source_page.get('description', '')} {source_page.get('h1', '')}"
        target_text = f"{target_page.get('title', '')} {target_page.get('description', '')} {target_page.get('h1', '')}"
        
        if not source_text.strip() or not target_text.strip():
            return 0.0
        
        try:
            similarity = await self.calculate_semantic_similarity(
                source_text,
                target_text,
                correlation_id
            )
            return similarity
        except Exception as e:
            logger.error(
                f"Failed to calculate semantic similarity: {e}",
                extra={"correlation_id": correlation_id}
            )
            interlink_errors_total.labels(error_type='similarity_calculation').inc()
            return 0.0
    
    def _is_circular_link(self, source_url: str, target_url: str) -> bool:
        if source_url in self.link_graph.get(target_url, set()):
            return True
        return False
    
    def _add_to_graph(self, source_url: str, target_url: str):
        self.link_graph[source_url].add(target_url)
    
    async def find_relevant_pages(
        self,
        source_page: Dict[str, Any],
        candidate_pages: List[Dict[str, Any]],
        correlation_id: Optional[str] = None
    ) -> List[Tuple[Dict[str, Any], float]]:
        relevant_pages = []
        
        source_url = source_page["url"]
        
        for candidate in candidate_pages:
            if candidate["url"] == source_url:
                continue
            
            if not self._is_same_domain(source_url, candidate["url"]):
                continue
            
            if self._is_circular_link(source_url, candidate["url"]):
                logger.debug(f"Skipping circular link: {source_url} -> {candidate['url']}")
                continue
            
            relevance = await self.calculate_page_relevance(
                source_page,
                candidate,
                correlation_id
            )
            
            if relevance >= self.min_relevance_score:
                relevant_pages.append((candidate, relevance))
        
        relevant_pages.sort(key=lambda x: x[1], reverse=True)
        
        return relevant_pages[:self.max_links_per_page]
    
    def _is_same_domain(self, url1: str, url2: str) -> bool:
        domain1 = urlparse(url1).netloc
        domain2 = urlparse(url2).netloc
        
        return domain1 == domain2
    
    def _calculate_impact_score(
        self,
        relevance_score: float,
        page_importance: float,
        keyword_overlap: int
    ) -> float:
        keyword_score = min(keyword_overlap / 10.0, 1.0)
        
        impact = (
            relevance_score * 0.5 +
            page_importance * 0.3 +
            keyword_score * 0.2
        )
        
        return round(impact, 3)
    
    async def generate_interlinks_for_page(
        self,
        project_id: str,
        source_url: str,
        correlation_id: Optional[str] = None
    ) -> List[InternalLink]:
        logger.info(
            f"Generating interlinks for page",
            extra={"project_id": project_id, "url": source_url, "correlation_id": correlation_id}
        )
        
        try:
            source_content = await self.get_page_content(project_id, source_url)
            
            content_word_count = len(source_content.get("content", "").split())
            if content_word_count < self.min_content_words:
                logger.warning(
                    f"Page {source_url} has insufficient content ({content_word_count} words)",
                    extra={"correlation_id": correlation_id}
                )
                return []
            
            all_pages = await self.get_project_pages(project_id)
            
            relevant_pages = await self.find_relevant_pages(
                source_content,
                all_pages,
                correlation_id
            )
            
            if not relevant_pages:
                logger.info(f"No relevant pages found for {source_url}")
                return []
            
            source_keywords = await self.extract_keywords(
                source_content.get("content", ""),
                max_keywords=20,
                correlation_id=correlation_id
            )
            
            interlinks = []
            
            for target_page, relevance_score in relevant_pages:
                try:
                    target_keywords = await self.extract_keywords(
                        target_page.get("content", ""),
                        max_keywords=10,
                        correlation_id=correlation_id
                    )
                    
                    common_keywords = list(set(source_keywords) & set(target_keywords))
                    
                    if not common_keywords:
                        common_keywords = target_keywords[:3]
                    
                    context_sentences = self.extract_sentences_with_keywords(
                        source_content.get("content", ""),
                        common_keywords,
                        max_sentences=3
                    )
                    
                    if context_sentences:
                        context = " ".join(context_sentences)[:500]
                    else:
                        context = source_content.get("content", "")[:500]
                    
                    anchor_text = await self.generate_anchor_text_llm(
                        source_context=context,
                        target_page_title=target_page.get("title", ""),
                        target_page_description=target_page.get("description", ""),
                        keywords=common_keywords,
                        correlation_id=correlation_id
                    )
                    
                    anchor_text = self._sanitize_anchor_text(anchor_text)
                    
                    if len(anchor_text) < self.min_anchor_length:
                        anchor_text = target_page.get("title", "")[:self.max_anchor_length]
                    
                    page_importance = source_content.get("importance", 0.5)
                    impact_score = self._calculate_impact_score(
                        relevance_score,
                        page_importance,
                        len(common_keywords)
                    )
                    
                    interlink = InternalLink(
                        source_url=source_url,
                        target_url=target_page["url"],
                        anchor_text=anchor_text,
                        context=context[:200],
                        relevance_score=relevance_score,
                        position="body",
                        impact_score=impact_score
                    )
                    
                    interlinks.append(interlink)
                    
                    self._add_to_graph(source_url, target_page["url"])
                    
                    interlinks_generated_total.labels(project_id=project_id).inc()
                    
                except Exception as e:
                    logger.error(
                        f"Failed to generate interlink for {target_page['url']}: {e}",
                        extra={"correlation_id": correlation_id}
                    )
                    interlink_errors_total.labels(error_type='link_generation').inc()
                    continue
            
            logger.info(
                f"Generated {len(interlinks)} interlinks for {source_url}",
                extra={"correlation_id": correlation_id}
            )
            
            return interlinks
            
        except Exception as e:
            logger.error(
                f"Failed to generate interlinks for page {source_url}: {e}",
                extra={"correlation_id": correlation_id}
            )
            interlink_errors_total.labels(error_type='page_processing').inc()
            raise
    
    def _sanitize_anchor_text(self, text: str) -> str:
        text = text.strip()
        
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s\-]', '', text)
        
        if len(text) > self.max_anchor_length:
            text = text[:self.max_anchor_length].rsplit(' ', 1)[0]
        
        return text
    
    async def create_interlink_tasks(
        self,
        project_id: str,
        interlinks: List[InternalLink],
        correlation_id: Optional[str] = None
    ) -> List[str]:
        
        task_ids = []
        
        links_by_source = defaultdict(list)
        for link in interlinks:
            links_by_source[link.source_url].append(link)
        
        tasks_to_add = []
        
        for source_url, links in links_by_source.items():
            avg_impact = sum(link.impact_score for link in links) / len(links)
            
            task = Task(
                project_id=project_id,
                task_type=TaskType.ADD_INTERNAL_LINKS,
                status=TaskStatus.PENDING,
                url=source_url,
                metadata={
                    "interlinks": [
                        {
                            "target_url": link.target_url,
                            "anchor_text": link.anchor_text,
                            "context": link.context,
                            "relevance_score": link.relevance_score,
                            "position": link.position,
                            "impact_score": link.impact_score
                        }
                        for link in links
                    ],
                    "total_links": len(links),
                    "average_impact_score": round(avg_impact, 3),
                    "correlation_id": correlation_id,
                    "created_at": datetime.utcnow().isoformat()
                }
            )
            
            tasks_to_add.append(task)
        
        for task in tasks_to_add:
            self.db.add(task)
        
        self.db.flush()
        
        for task in tasks_to_add:
            task_ids.append(str(task.id))
            
            try:
                await publish_task_created_event(
                    db=self.db,
                    task_id=str(task.id),
                    project_id=project_id,
                    task_type=TaskType.ADD_INTERNAL_LINKS,
                    url=task.url,
                    metadata=task.metadata,
                    correlation_id=correlation_id
                )
            except Exception as e:
                logger.error(
                    f"Failed to publish TaskCreated event: {e}",
                    extra={"task_id": str(task.id), "correlation_id": correlation_id}
                )
        
        self.db.commit()
        
        logger.info(
            f"Created {len(task_ids)} interlink tasks for project {project_id}",
            extra={"correlation_id": correlation_id}
        )
        
        return task_ids
    
    @interlink_generation_duration.labels(project_id='').time()
    async def generate_interlinks_for_project(
        self,
        project_id: str,
        max_pages: Optional[int] = None,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate interlinks for entire project"""
        logger.info(
            f"Starting interlink generation for project {project_id}",
            extra={"correlation_id": correlation_id, "max_pages": max_pages}
        )
        
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        self.link_graph.clear()
        
        all_pages = await self.get_project_pages(project_id)
        
        if max_pages:
            all_pages = all_pages[:max_pages]
        
        logger.info(f"Processing {len(all_pages)} pages for project {project_id}")
        
        all_interlinks = []
        processed_pages = 0
        failed_pages = 0
        
        for page in all_pages:
            try:
                interlinks = await self.generate_interlinks_for_page(
                    project_id,
                    page["url"],
                    correlation_id
                )
                all_interlinks.extend(interlinks)
                processed_pages += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to generate interlinks for page {page['url']}: {e}",
                    extra={"correlation_id": correlation_id}
                )
                failed_pages += 1
                continue
        
        # Create tasks and publish events
        task_ids = []
        if all_interlinks:
            task_ids = await self.create_interlink_tasks(
                project_id,
                all_interlinks,
                correlation_id
            )
        
        result = {
            "project_id": project_id,
            "total_interlinks": len(all_interlinks),
            "pages_processed": processed_pages,
            "pages_failed": failed_pages,
            "tasks_created": len(task_ids),
            "task_ids": task_ids,
            "correlation_id": correlation_id,
            "completed_at": datetime.utcnow().isoformat()
        }
        
        logger.info(
            f"Completed interlink generation for project {project_id}",
            extra=result
        )
        
        return result


async def generate_interlinks(
    db: Session,
    project_id: str,
    max_pages: Optional[int] = None,
    correlation_id: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None
) -> Dict[str, Any]:
    
    generator = InterlinkGenerator(db, redis_client)
    return await generator.generate_interlinks_for_project(
        project_id,
        max_pages,
        correlation_id
    )


async def generate_interlinks_for_page(
    db: Session,
    project_id: str,
    url: str,
    correlation_id: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None
) -> List[InternalLink]:
    
    generator = InterlinkGenerator(db, redis_client)
    return await generator.generate_interlinks_for_page(
        project_id,
        url,
        correlation_id
    )
