from fastapi import HTTPException, Request
from typing import Optional, Callable
from datetime import datetime
import redis
import hashlib
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.logging_config import get_logger
from services.api_gateway.config import settings, get_redis_config

logger = get_logger(__name__)


class RateLimiter:
    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        default_limit: int = 5,
        default_window: int = 3600
    ):
        if redis_client is None:
            redis_config = get_redis_config()
            self.redis = redis.Redis(
                host=redis_config["host"],
                port=redis_config["port"],
                password=redis_config["password"],
                db=redis_config["db"],
                decode_responses=True
            )
        else:
            self.redis = redis_client
        
        self.default_limit = default_limit
        self.default_window = default_window
    
    def _get_client_identifier(self, request: Request) -> str:
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")
        
        identifier = f"{client_ip}:{user_agent}"
        return hashlib.md5(identifier.encode()).hexdigest()
    
    def _get_redis_key(self, identifier: str, endpoint: str) -> str:
        return f"rate_limit:{endpoint}:{identifier}"
    
    def check_rate_limit(
        self,
        request: Request,
        limit: Optional[int] = None,
        window: Optional[int] = None,
        endpoint: Optional[str] = None
    ) -> dict:
        if limit is None:
            limit = self.default_limit
        
        if window is None:
            window = self.default_window
        
        if endpoint is None:
            endpoint = request.url.path
        
        identifier = self._get_client_identifier(request)
        key = self._get_redis_key(identifier, endpoint)
        
        try:
            current_count = self.redis.get(key)
            
            if current_count is None:
                current_count = 0
            else:
                current_count = int(current_count)
            
            remaining = max(0, limit - current_count)
            ttl = self.redis.ttl(key)
            
            if ttl < 0:
                ttl = window
            
            return {
                "allowed": current_count < limit,
                "limit": limit,
                "remaining": remaining,
                "reset_in": ttl,
                "current": current_count
            }
            
        except redis.RedisError as e:
            logger.error(f"Redis error in rate limiter: {e}")
            return {
                "allowed": True,
                "limit": limit,
                "remaining": limit,
                "reset_in": window,
                "current": 0
            }
    
    def increment(
        self,
        request: Request,
        endpoint: Optional[str] = None,
        window: Optional[int] = None
    ) -> bool:
        if window is None:
            window = self.default_window
        
        if endpoint is None:
            endpoint = request.url.path
        
        identifier = self._get_client_identifier(request)
        key = self._get_redis_key(identifier, endpoint)
        
        try:
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            pipe.execute()
            return True
        except redis.RedisError as e:
            logger.error(f"Redis error incrementing rate limit: {e}")
            return False
    
    def reset(
        self,
        request: Request,
        endpoint: Optional[str] = None
    ) -> bool:
        if endpoint is None:
            endpoint = request.url.path
        
        identifier = self._get_client_identifier(request)
        key = self._get_redis_key(identifier, endpoint)
        
        try:
            self.redis.delete(key)
            return True
        except redis.RedisError as e:
            logger.error(f"Redis error resetting rate limit: {e}")
            return False


rate_limiter = RateLimiter(
    default_limit=settings.PUBLIC_RATE_LIMIT,
    default_window=settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS
)


class RateLimitDependency:
    def __init__(
        self,
        limit: Optional[int] = None,
        window: Optional[int] = None,
        endpoint: Optional[str] = None
    ):
        self.limit = limit or settings.PUBLIC_RATE_LIMIT
        self.window = window or settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS
        self.endpoint = endpoint
    
    async def __call__(self, request: Request) -> dict:
        status = rate_limiter.check_rate_limit(
            request=request,
            limit=self.limit,
            window=self.window,
            endpoint=self.endpoint
        )
        
        if not status["allowed"]:
            logger.warning(
                f"Rate limit exceeded",
                extra={
                    "client": request.client.host if request.client else "unknown",
                    "endpoint": self.endpoint or request.url.path,
                    "limit": self.limit,
                    "current": status["current"]
                }
            )
            
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": status["limit"],
                    "remaining": status["remaining"],
                    "reset_in": status["reset_in"],
                    "message": f"Too many requests. Please try again in {status['reset_in']} seconds."
                },
                headers={
                    "X-RateLimit-Limit": str(status["limit"]),
                    "X-RateLimit-Remaining": str(status["remaining"]),
                    "X-RateLimit-Reset": str(status["reset_in"]),
                    "Retry-After": str(status["reset_in"])
                }
            )
        
        rate_limiter.increment(
            request=request,
            endpoint=self.endpoint,
            window=self.window
        )
        
        return status


def rate_limit(
    limit: Optional[int] = None,
    window: Optional[int] = None,
    endpoint: Optional[str] = None
) -> Callable:
    return RateLimitDependency(limit=limit, window=window, endpoint=endpoint)


def get_rate_limit_status(request: Request, endpoint: Optional[str] = None) -> dict:
    return rate_limiter.check_rate_limit(request=request, endpoint=endpoint)


def reset_rate_limit(request: Request, endpoint: Optional[str] = None) -> bool:
    return rate_limiter.reset(request=request, endpoint=endpoint)
