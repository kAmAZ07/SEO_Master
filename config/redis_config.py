import os
import redis
import hashlib
import json
from typing import Optional, Any, Dict, List
from functools import wraps
from contextlib import contextmanager
import time

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_DB_CACHE = int(os.getenv("REDIS_DB_CACHE", "0"))
REDIS_DB_CELERY = int(os.getenv("REDIS_DB_CELERY", "1"))
REDIS_DB_SESSION = int(os.getenv("REDIS_DB_SESSION", "2"))
REDIS_DB_RATE_LIMIT = int(os.getenv("REDIS_DB_RATE_LIMIT", "3"))

REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
REDIS_SOCKET_CONNECT_TIMEOUT = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def get_redis_url(db: int = 0) -> str:
    if REDIS_PASSWORD:
        return f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{db}"
    return f"redis://{REDIS_HOST}:{REDIS_PORT}/{db}"


class RedisConnectionPool:
    
    _pools = {}
    
    @classmethod
    def get_pool(cls, db: int = 0) -> redis.ConnectionPool:
        if db not in cls._pools:
            cls._pools[db] = redis.ConnectionPool(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                db=db,
                max_connections=REDIS_MAX_CONNECTIONS,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                decode_responses=True,
                retry_on_timeout=True,
                health_check_interval=30
            )
        return cls._pools[db]
    
    @classmethod
    def get_client(cls, db: int = 0) -> redis.Redis:
        pool = cls.get_pool(db)
        return redis.Redis(connection_pool=pool)
    
    @classmethod
    def close_all(cls):
        for pool in cls._pools.values():
            pool.disconnect()
        cls._pools.clear()


class RedisCache:
    
    def __init__(self, db: int = REDIS_DB_CACHE):
        self.client = RedisConnectionPool.get_client(db)
    
    def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except redis.RedisError:
            return None
    
    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        try:
            if ttl:
                return self.client.setex(key, ttl, value)
            else:
                return self.client.set(key, value)
        except redis.RedisError:
            return False
    
    def delete(self, key: str) -> bool:
        try:
            return bool(self.client.delete(key))
        except redis.RedisError:
            return False
    
    def exists(self, key: str) -> bool:
        try:
            return bool(self.client.exists(key))
        except redis.RedisError:
            return False
    
    def expire(self, key: str, ttl: int) -> bool:
        try:
            return bool(self.client.expire(key, ttl))
        except redis.RedisError:
            return False
    
    def ttl(self, key: str) -> int:
        try:
            return self.client.ttl(key)
        except redis.RedisError:
            return -1
    
    def keys(self, pattern: str) -> List[str]:
        try:
            return self.client.keys(pattern)
        except redis.RedisError:
            return []
    
    def delete_pattern(self, pattern: str) -> int:
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except redis.RedisError:
            return 0
    
    def flush(self) -> bool:
        try:
            return self.client.flushdb()
        except redis.RedisError:
            return False
    
    def incr(self, key: str, amount: int = 1) -> Optional[int]:
        try:
            return self.client.incrby(key, amount)
        except redis.RedisError:
            return None
    
    def decr(self, key: str, amount: int = 1) -> Optional[int]:
        try:
            return self.client.decrby(key, amount)
        except redis.RedisError:
            return None


class LLMCache:
    
    TTL_SIMPLE = 604800
    TTL_COMPLEX = 604800
    
    def __init__(self):
        self.cache = RedisCache(REDIS_DB_CACHE)
    
    def _generate_key(self, prompt: str, content: str, model: str = "gpt-4") -> str:
        cache_data = f"{model}:{prompt}:{content}"
        hash_key = hashlib.sha256(cache_data.encode()).hexdigest()
        return f"llm:{hash_key}"
    
    def get(self, prompt: str, content: str, model: str = "gpt-4") -> Optional[str]:
        key = self._generate_key(prompt, content, model)
        return self.cache.get(key)
    
    def set(self, prompt: str, content: str, result: str, model: str = "gpt-4", ttl: int = TTL_SIMPLE):
        key = self._generate_key(prompt, content, model)
        return self.cache.set(key, result, ttl)
    
    def exists(self, prompt: str, content: str, model: str = "gpt-4") -> bool:
        key = self._generate_key(prompt, content, model)
        return self.cache.exists(key)
    
    def clear_expired(self):
        return self.cache.delete_pattern("llm:*")
    
    def get_cache_stats(self) -> Dict[str, int]:
        keys = self.cache.keys("llm:*")
        return {
            'total_entries': len(keys),
            'total_size_bytes': sum(len(self.cache.get(k) or '') for k in keys)
        }


class APICache:
    
    TTL_GSC = 600
    TTL_GA4 = 600
    TTL_YANDEX = 600
    TTL_PAGESPEED = 3600
    TTL_NEWS = 1800
    
    def __init__(self):
        self.cache = RedisCache(REDIS_DB_CACHE)
    
    def _generate_key(self, api_name: str, endpoint: str, params: Dict[str, Any]) -> str:
        params_str = json.dumps(params, sort_keys=True)
        hash_key = hashlib.md5(f"{endpoint}:{params_str}".encode()).hexdigest()
        return f"api:{api_name}:{hash_key}"
    
    def get_gsc_data(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._generate_key("gsc", endpoint, params)
        data = self.cache.get(key)
        return json.loads(data) if data else None
    
    def set_gsc_data(self, endpoint: str, params: Dict[str, Any], data: Dict[str, Any]):
        key = self._generate_key("gsc", endpoint, params)
        return self.cache.set(key, json.dumps(data), self.TTL_GSC)
    
    def get_ga4_data(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._generate_key("ga4", endpoint, params)
        data = self.cache.get(key)
        return json.loads(data) if data else None
    
    def set_ga4_data(self, endpoint: str, params: Dict[str, Any], data: Dict[str, Any]):
        key = self._generate_key("ga4", endpoint, params)
        return self.cache.set(key, json.dumps(data), self.TTL_GA4)
    
    def get_yandex_data(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._generate_key("yandex", endpoint, params)
        data = self.cache.get(key)
        return json.loads(data) if data else None
    
    def set_yandex_data(self, endpoint: str, params: Dict[str, Any], data: Dict[str, Any]):
        key = self._generate_key("yandex", endpoint, params)
        return self.cache.set(key, json.dumps(data), self.TTL_YANDEX)
    
    def get_pagespeed_data(self, url: str) -> Optional[Dict[str, Any]]:
        key = f"api:pagespeed:{hashlib.md5(url.encode()).hexdigest()}"
        data = self.cache.get(key)
        return json.loads(data) if data else None
    
    def set_pagespeed_data(self, url: str, data: Dict[str, Any]):
        key = f"api:pagespeed:{hashlib.md5(url.encode()).hexdigest()}"
        return self.cache.set(key, json.dumps(data), self.TTL_PAGESPEED)
    
    def get_cache_age(self, api_name: str, endpoint: str, params: Dict[str, Any]) -> int:
        key = self._generate_key(api_name, endpoint, params)
        ttl = self.cache.ttl(key)
        if ttl > 0:
            max_ttl = getattr(self, f"TTL_{api_name.upper()}", 600)
            return max_ttl - ttl
        return -1
    
    def invalidate_api(self, api_name: str):
        return self.cache.delete_pattern(f"api:{api_name}:*")


class RateLimiter:
    
    def __init__(self):
        self.client = RedisConnectionPool.get_client(REDIS_DB_RATE_LIMIT)
    
    def check_rate_limit(self, identifier: str, max_requests: int, window_seconds: int) -> bool:
        key = f"ratelimit:{identifier}"
        
        try:
            current = self.client.get(key)
            
            if current is None:
                self.client.setex(key, window_seconds, 1)
                return True
            
            current_count = int(current)
            if current_count >= max_requests:
                return False
            
            self.client.incr(key)
            return True
        
        except redis.RedisError:
            return True
    
    def get_remaining_requests(self, identifier: str, max_requests: int) -> int:
        key = f"ratelimit:{identifier}"
        try:
            current = self.client.get(key)
            if current is None:
                return max_requests
            return max(0, max_requests - int(current))
        except redis.RedisError:
            return max_requests
    
    def get_reset_time(self, identifier: str) -> int:
        key = f"ratelimit:{identifier}"
        try:
            return self.client.ttl(key)
        except redis.RedisError:
            return 0
    
    def reset_limit(self, identifier: str):
        key = f"ratelimit:{identifier}"
        try:
            self.client.delete(key)
        except redis.RedisError:
            pass


class PublicAuditRateLimiter:
    
    MAX_AUDITS_PER_HOUR = 5
    WINDOW_SECONDS = 3600
    
    def __init__(self):
        self.rate_limiter = RateLimiter()
    
    def can_audit(self, ip_address: str) -> bool:
        identifier = f"public_audit:{ip_address}"
        return self.rate_limiter.check_rate_limit(
            identifier,
            self.MAX_AUDITS_PER_HOUR,
            self.WINDOW_SECONDS
        )
    
    def get_remaining_audits(self, ip_address: str) -> int:
        identifier = f"public_audit:{ip_address}"
        return self.rate_limiter.get_remaining_requests(
            identifier,
            self.MAX_AUDITS_PER_HOUR
        )
    
    def get_reset_time(self, ip_address: str) -> int:
        identifier = f"public_audit:{ip_address}"
        return self.rate_limiter.get_reset_time(identifier)


class SessionStore:
    
    DEFAULT_TTL = 3600
    
    def __init__(self):
        self.client = RedisConnectionPool.get_client(REDIS_DB_SESSION)
    
    def create_session(self, session_id: str, data: Dict[str, Any], ttl: int = DEFAULT_TTL) -> bool:
        key = f"session:{session_id}"
        try:
            return self.client.setex(key, ttl, json.dumps(data))
        except redis.RedisError:
            return False
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        key = f"session:{session_id}"
        try:
            data = self.client.get(key)
            return json.loads(data) if data else None
        except (redis.RedisError, json.JSONDecodeError):
            return None
    
    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        key = f"session:{session_id}"
        try:
            ttl = self.client.ttl(key)
            if ttl > 0:
                return self.client.setex(key, ttl, json.dumps(data))
            return False
        except redis.RedisError:
            return False
    
    def delete_session(self, session_id: str) -> bool:
        key = f"session:{session_id}"
        try:
            return bool(self.client.delete(key))
        except redis.RedisError:
            return False
    
    def refresh_session(self, session_id: str, ttl: int = DEFAULT_TTL) -> bool:
        key = f"session:{session_id}"
        try:
            return bool(self.client.expire(key, ttl))
        except redis.RedisError:
            return False


class DistributedLock:
    
    def __init__(self, lock_name: str, timeout: int = 30, db: int = REDIS_DB_CACHE):
        self.lock_name = f"lock:{lock_name}"
        self.timeout = timeout
        self.client = RedisConnectionPool.get_client(db)
        self.lock_id = None
    
    def acquire(self, blocking: bool = True, blocking_timeout: Optional[int] = None) -> bool:
        import uuid
        self.lock_id = str(uuid.uuid4())
        
        start_time = time.time()
        
        while True:
            try:
                acquired = self.client.set(
                    self.lock_name,
                    self.lock_id,
                    nx=True,
                    ex=self.timeout
                )
                
                if acquired:
                    return True
                
                if not blocking:
                    return False
                
                if blocking_timeout and (time.time() - start_time) >= blocking_timeout:
                    return False
                
                time.sleep(0.1)
            
            except redis.RedisError:
                return False
    
    def release(self) -> bool:
        if not self.lock_id:
            return False
        
        try:
            stored_id = self.client.get(self.lock_name)
            if stored_id == self.lock_id:
                return bool(self.client.delete(self.lock_name))
            return False
        except redis.RedisError:
            return False
    
    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock: {self.lock_name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class CrawlStateManager:
    
    def __init__(self):
        self.cache = RedisCache(REDIS_DB_CACHE)
    
    def set_crawl_state(self, crawl_id: str, state: Dict[str, Any], ttl: int = 86400):
        key = f"crawl:state:{crawl_id}"
        return self.cache.set(key, json.dumps(state), ttl)
    
    def get_crawl_state(self, crawl_id: str) -> Optional[Dict[str, Any]]:
        key = f"crawl:state:{crawl_id}"
        data = self.cache.get(key)
        return json.loads(data) if data else None
    
    def update_crawl_progress(self, crawl_id: str, pages_crawled: int):
        key = f"crawl:progress:{crawl_id}"
        return self.cache.set(key, str(pages_crawled))
    
    def get_crawl_progress(self, crawl_id: str) -> int:
        key = f"crawl:progress:{crawl_id}"
        data = self.cache.get(key)
        return int(data) if data else 0
    
    def add_crawled_url(self, crawl_id: str, url: str):
        key = f"crawl:urls:{crawl_id}"
        try:
            client = RedisConnectionPool.get_client(REDIS_DB_CACHE)
            client.sadd(key, url)
            client.expire(key, 86400)
        except redis.RedisError:
            pass
    
    def is_url_crawled(self, crawl_id: str, url: str) -> bool:
        key = f"crawl:urls:{crawl_id}"
        try:
            client = RedisConnectionPool.get_client(REDIS_DB_CACHE)
            return bool(client.sismember(key, url))
        except redis.RedisError:
            return False


class TaskQueue:
    
    def __init__(self):
        self.client = RedisConnectionPool.get_client(REDIS_DB_CACHE)
    
    def push_task(self, queue_name: str, task_data: Dict[str, Any], priority: int = 0):
        key = f"queue:{queue_name}"
        try:
            self.client.zadd(key, {json.dumps(task_data): priority})
        except redis.RedisError:
            pass
    
    def pop_task(self, queue_name: str) -> Optional[Dict[str, Any]]:
        key = f"queue:{queue_name}"
        try:
            result = self.client.zpopmax(key)
            if result:
                task_json, _ = result[0]
                return json.loads(task_json)
            return None
        except (redis.RedisError, json.JSONDecodeError):
            return None
    
    def get_queue_size(self, queue_name: str) -> int:
        key = f"queue:{queue_name}"
        try:
            return self.client.zcard(key)
        except redis.RedisError:
            return 0


def cached(ttl: int = 300, key_prefix: str = "cache"):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = RedisCache(REDIS_DB_CACHE)
            
            cache_key_data = f"{func.__module__}.{func.__name__}:{args}:{kwargs}"
            cache_key = f"{key_prefix}:{hashlib.md5(cache_key_data.encode()).hexdigest()}"
            
            cached_result = cache.get(cache_key)
            if cached_result:
                return json.loads(cached_result)
            
            result = func(*args, **kwargs)
            
            cache.set(cache_key, json.dumps(result), ttl)
            
            return result
        return wrapper
    return decorator


@contextmanager
def distributed_lock(lock_name: str, timeout: int = 30):
    lock = DistributedLock(lock_name, timeout)
    try:
        if lock.acquire(blocking=True, blocking_timeout=timeout):
            yield lock
        else:
            raise TimeoutError(f"Could not acquire lock: {lock_name}")
    finally:
        lock.release()


def get_llm_cache() -> LLMCache:
    return LLMCache()


def get_api_cache() -> APICache:
    return APICache()


def get_public_audit_limiter() -> PublicAuditRateLimiter:
    return PublicAuditRateLimiter()


def get_session_store() -> SessionStore:
    return SessionStore()


def get_crawl_state_manager() -> CrawlStateManager:
    return CrawlStateManager()


def health_check() -> bool:
    try:
        client = RedisConnectionPool.get_client(REDIS_DB_CACHE)
        return client.ping()
    except redis.RedisError:
        return False


if __name__ == '__main__':
    print(f"Redis URL: {get_redis_url(REDIS_DB_CACHE)}")
    print(f"Health check: {health_check()}")
    
    llm_cache = get_llm_cache()
    llm_cache.set("test prompt", "test content", "test result")
    print(f"LLM cache test: {llm_cache.get('test prompt', 'test content')}")
    
    rate_limiter = get_public_audit_limiter()
    print(f"Can audit: {rate_limiter.can_audit('127.0.0.1')}")
    print(f"Remaining: {rate_limiter.get_remaining_audits('127.0.0.1')}")
    
    with distributed_lock("test_lock"):
        print("Lock acquired")
    
    print("\nRedis configuration loaded successfully")
