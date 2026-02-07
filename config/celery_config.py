# celery_config.py

import os
import hashlib
import redis
from functools import wraps
from celery import Celery
from kombu import Queue, Exchange
from celery.schedules import crontab

BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "amqp://user:password@rabbitmq:5672//"
)

RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://redis:6379/1"
)

redis_client = redis.from_url(RESULT_BACKEND)

app = Celery('seo_platform')

def cache_llm_result(ttl=604800):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_data = f"{args}{kwargs}"
            cache_key = f"llm:{hashlib.sha256(cache_data.encode()).hexdigest()}"
            
            cached = redis_client.get(cache_key)
            if cached:
                return cached.decode('utf-8')
            
            result = func(*args, **kwargs)
            
            redis_client.setex(cache_key, ttl, str(result))
            return result
        return wrapper
    return decorator

default_exchange = Exchange('default', type='direct')
priority_exchange = Exchange('priority', type='direct')

CELERY_QUEUES = (
    Queue(
        'high_priority',
        exchange=priority_exchange,
        routing_key='high',
        queue_arguments={'x-max-priority': 10}
    ),
    
    Queue(
        'audit_crawl',
        exchange=default_exchange,
        routing_key='audit.crawl',
        queue_arguments={'x-max-priority': 5}
    ),
    
    Queue(
        'public_audit_queue',
        exchange=default_exchange,
        routing_key='audit.public',
        queue_arguments={
            'x-max-priority': 3,
            'x-max-length': 10
        }
    ),
    
    Queue(
        'semantic_analysis',
        exchange=default_exchange,
        routing_key='semantic.analysis'
    ),
    
    Queue(
        'llm_generation',
        exchange=default_exchange,
        routing_key='semantic.llm',
        queue_arguments={'x-max-priority': 7}
    ),
    
    Queue(
        'reporting_export',
        exchange=default_exchange,
        routing_key='reporting.export'
    ),
    
    Queue(
        'periodic',
        exchange=default_exchange,
        routing_key='periodic'
    ),
    
    Queue(
        'maintenance',
        exchange=default_exchange,
        routing_key='maintenance',
        queue_arguments={'x-max-priority': 1}
    ),
)

CELERY_ROUTES = {
    'audit_service.tasks.crawl_website': {
        'queue': 'audit_crawl',
        'routing_key': 'audit.crawl'
    },
    'audit_service.tasks.public_audit': {
        'queue': 'public_audit_queue',
        'routing_key': 'audit.public'
    },
    'audit_service.tasks.check_cwv': {
        'queue': 'audit_crawl',
        'routing_key': 'audit.crawl'
    },
    'audit_service.tasks.validate_schema': {
        'queue': 'audit_crawl',
        'routing_key': 'audit.crawl'
    },
    'audit_service.tasks.analyze_backlinks_gsc': {
        'queue': 'audit_crawl',
        'routing_key': 'audit.crawl'
    },
    
    'semantic_service.tasks.calculate_ff_score': {
        'queue': 'semantic_analysis',
        'routing_key': 'semantic.analysis'
    },
    'semantic_service.tasks.calculate_eeat_score': {
        'queue': 'semantic_analysis',
        'routing_key': 'semantic.analysis'
    },
    'semantic_service.tasks.content_gap_analysis': {
        'queue': 'semantic_analysis',
        'routing_key': 'semantic.analysis'
    },
    
    'semantic_service.tasks.generate_title_description': {
        'queue': 'llm_generation',
        'routing_key': 'semantic.llm'
    },
    'semantic_service.tasks.generate_h1': {
        'queue': 'llm_generation',
        'routing_key': 'semantic.llm'
    },
    'semantic_service.tasks.generate_schema_org': {
        'queue': 'llm_generation',
        'routing_key': 'semantic.llm'
    },
    
    'semantic_service.tasks.generate_content_analysis': {
        'queue': 'llm_generation',
        'routing_key': 'semantic.llm'
    },
    'semantic_service.tasks.generate_eeat_analysis': {
        'queue': 'llm_generation',
        'routing_key': 'semantic.llm'
    },
    
    'reporting_service.tasks.collect_gsc_data': {
        'queue': 'reporting_export',
        'routing_key': 'reporting.export'
    },
    'reporting_service.tasks.collect_ga4_data': {
        'queue': 'reporting_export',
        'routing_key': 'reporting.export'
    },
    'reporting_service.tasks.collect_yandex_webmaster_data': {
        'queue': 'reporting_export',
        'routing_key': 'reporting.export'
    },
    'reporting_service.tasks.export_csv_report': {
        'queue': 'reporting_export',
        'routing_key': 'reporting.export'
    },
    'reporting_service.tasks.calculate_cost_efficiency': {
        'queue': 'reporting_export',
        'routing_key': 'reporting.export'
    },
    
    'shared.tasks.cleanup_old_crawl_data': {
        'queue': 'maintenance',
        'routing_key': 'maintenance'
    },
    'shared.tasks.cleanup_public_audit_results': {
        'queue': 'maintenance',
        'routing_key': 'maintenance'
    },
}

CELERY_TASK_ANNOTATIONS = {
    'audit_service.tasks.crawl_website': {
        'rate_limit': '10/m',
        'time_limit': 86400,
        'soft_time_limit': 82800,
        'max_retries': 3,
        'default_retry_delay': 300,
        'autoretry_for': (ConnectionError, TimeoutError),
        'retry_backoff': True,
    },
    
    'audit_service.tasks.public_audit': {
        'rate_limit': '5/h',
        'time_limit': 60,
        'soft_time_limit': 55,
        'max_retries': 1,
        'ignore_result': False,
    },
    
    'audit_service.tasks.check_cwv': {
        'time_limit': 30,
        'soft_time_limit': 25,
        'max_retries': 2,
        'retry_backoff': True,
    },
    
    'semantic_service.tasks.calculate_ff_score': {
        'time_limit': 3600,
        'soft_time_limit': 3500,
        'max_retries': 2,
        'autoretry_for': (Exception,),
        'retry_backoff': True,
    },
    
    'semantic_service.tasks.calculate_eeat_score': {
        'time_limit': 1800,
        'soft_time_limit': 1700,
        'max_retries': 2,
    },
    
    'semantic_service.tasks.generate_title_description': {
        'time_limit': 5,
        'soft_time_limit': 4,
        'max_retries': 2,
        'autoretry_for': (TimeoutError, ConnectionError),
        'retry_backoff': True,
        'retry_backoff_max': 30,
    },
    'semantic_service.tasks.generate_h1': {
        'time_limit': 5,
        'soft_time_limit': 4,
        'max_retries': 2,
        'autoretry_for': (TimeoutError, ConnectionError),
        'retry_backoff': True,
    },
    'semantic_service.tasks.generate_schema_org': {
        'time_limit': 5,
        'soft_time_limit': 4,
        'max_retries': 2,
        'autoretry_for': (TimeoutError, ConnectionError),
        'retry_backoff': True,
    },
    
    'semantic_service.tasks.generate_content_analysis': {
        'time_limit': 15,
        'soft_time_limit': 12,
        'max_retries': 2,
        'autoretry_for': (TimeoutError, ConnectionError),
        'retry_backoff': True,
        'retry_backoff_max': 60,
    },
    'semantic_service.tasks.generate_eeat_analysis': {
        'time_limit': 15,
        'soft_time_limit': 12,
        'max_retries': 2,
        'autoretry_for': (TimeoutError, ConnectionError),
        'retry_backoff': True,
    },
    
    'reporting_service.tasks.collect_gsc_data': {
        'rate_limit': '1200/m',
        'time_limit': 600,
        'soft_time_limit': 550,
        'max_retries': 5,
        'autoretry_for': (ConnectionError, TimeoutError),
        'retry_backoff': True,
        'retry_backoff_max': 300,
    },
    
    'reporting_service.tasks.collect_ga4_data': {
        'rate_limit': '100/m',
        'time_limit': 600,
        'max_retries': 3,
        'autoretry_for': (ConnectionError, TimeoutError),
        'retry_backoff': True,
    },
    
    'reporting_service.tasks.collect_yandex_webmaster_data': {
        'rate_limit': '60/m',
        'time_limit': 600,
        'max_retries': 3,
        'autoretry_for': (ConnectionError, TimeoutError),
        'retry_backoff': True,
    },
    
    'reporting_service.tasks.export_csv_report': {
        'time_limit': 300,
        'soft_time_limit': 280,
        'max_retries': 2,
    },
}

CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_TASK_TIME_LIMIT = 3600
CELERY_TASK_SOFT_TIME_LIMIT = 3500

CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_DISABLE_RATE_LIMITS = False

CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000

CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'Europe/Moscow'
CELERY_ENABLE_UTC = True

CELERY_RESULT_EXPIRES = 86400
CELERY_RESULT_PERSISTENT = True
CELERY_RESULT_EXTENDED = True

CELERY_TASK_IGNORE_RESULT = False
CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED = True

CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True

CELERY_FLOWER_PORT = 5555

CELERY_WORKER_LOG_FORMAT = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'
CELERY_WORKER_TASK_LOG_FORMAT = '[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s'

CELERY_WORKER_REDIRECT_STDOUTS = True
CELERY_WORKER_REDIRECT_STDOUTS_LEVEL = 'INFO'

BROKER_POOL_LIMIT = 10
BROKER_CONNECTION_TIMEOUT = 10
BROKER_CONNECTION_RETRY = True
BROKER_CONNECTION_MAX_RETRIES = 10

BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 43200,
    'max_retries': 5,
    'interval_start': 0,
    'interval_step': 0.2,
    'interval_max': 0.5,
}

CELERY_BEAT_SCHEDULE = {
    'cleanup-old-crawl-data': {
        'task': 'shared.tasks.cleanup_old_crawl_data',
        'schedule': crontab(hour=3, minute=0),
        'options': {'queue': 'maintenance'}
    },
    
    'cleanup-public-audits': {
        'task': 'shared.tasks.cleanup_public_audit_results',
        'schedule': crontab(hour=2, minute=0),
        'kwargs': {'retention_days': 7},
        'options': {'queue': 'maintenance'}
    },
    
    'update-gsc-data': {
        'task': 'reporting_service.tasks.scheduled_gsc_update',
        'schedule': crontab(hour=1, minute=0),
        'options': {'queue': 'reporting_export'}
    },
    
    'update-ga4-data': {
        'task': 'reporting_service.tasks.scheduled_ga4_update',
        'schedule': crontab(hour=1, minute=30),
        'options': {'queue': 'reporting_export'}
    },
    
    'update-yandex-data': {
        'task': 'reporting_service.tasks.scheduled_yandex_update',
        'schedule': crontab(hour=1, minute=45),
        'options': {'queue': 'reporting_export'}
    },
    
    'recalculate-ff-scores': {
        'task': 'semantic_service.tasks.batch_recalculate_ff_scores',
        'schedule': crontab(hour=4, minute=0),
        'options': {'queue': 'semantic_analysis'}
    },
    
    'cleanup-llm-cache': {
        'task': 'shared.tasks.cleanup_expired_llm_cache',
        'schedule': crontab(hour=5, minute=0),
        'options': {'queue': 'maintenance'}
    },
    
    'health-check': {
        'task': 'shared.tasks.worker_health_check',
        'schedule': crontab(minute='*/15'),
        'options': {'queue': 'high_priority'}
    },
}

app.config_from_object(__name__)

app.autodiscover_tasks([
    'audit_service',
    'semantic_service',
    'reporting_service',
    'shared'
])

if os.getenv('ENVIRONMENT') == 'production':
    app.conf.broker_transport_options = {
        'master_name': 'mymaster',
        'sentinel_kwargs': {
            'password': os.getenv('REDIS_PASSWORD'),
            'socket_timeout': 0.1,
        },
    }
    
    if os.getenv('BROKER_USE_SSL', 'false').lower() == 'true':
        app.conf.broker_use_ssl = {
            'ssl_cert_reqs': 'required',
            'ssl_ca_certs': '/certs/ca.pem',
            'ssl_certfile': '/certs/client.pem',
            'ssl_keyfile': '/certs/key.pem',
        }

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

if __name__ == '__main__':
    app.start()
