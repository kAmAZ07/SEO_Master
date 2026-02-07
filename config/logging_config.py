# logging_config.py

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from pythonjsonlogger import jsonlogger
from typing import Dict, Any, Optional
import time

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

class SensitiveDataFilter(logging.Filter):
    
    SENSITIVE_KEYS = [
        'password', 'token', 'api_key', 'secret', 'authorization',
        'access_token', 'refresh_token', 'credentials', 'bearer',
        'OPENAI_API_KEY', 'GSC_CREDENTIALS', 'GA4_CREDENTIALS',
        'YANDEX_WEBMASTER_TOKEN', 'WORDPRESS_APP_PASSWORD', 
        'TILDA_SECRET_KEY', 'NEWS_API_KEY', 'REDIS_PASSWORD',
        'POSTGRES_PASSWORD', 'RABBITMQ_PASSWORD', 'JWT_SECRET_KEY'
    ]
    
    def filter(self, record):
        if hasattr(record, 'msg'):
            msg = str(record.msg)
            for key in self.SENSITIVE_KEYS:
                if key in msg.lower():
                    record.msg = self._mask_sensitive_data(msg)
        
        if hasattr(record, 'args') and record.args:
            record.args = tuple(
                self._mask_if_sensitive(arg) for arg in record.args
            )
        
        return True
    
    def _mask_sensitive_data(self, text):
        import re
        patterns = [
            (r'(api[_-]?key\s*[=:]\s*)[^\s&]+', r'\1***MASKED***'),
            (r'(token\s*[=:]\s*)[^\s&]+', r'\1***MASKED***'),
            (r'(password\s*[=:]\s*)[^\s&]+', r'\1***MASKED***'),
            (r'(sk-[a-zA-Z0-9]{20,})', r'sk-***MASKED***'),
            (r'(Bearer\s+)[^\s]+', r'\1***MASKED***'),
            (r'(gho_[a-zA-Z0-9]{36})', r'gho_***MASKED***'),
            (r'(ghp_[a-zA-Z0-9]{36})', r'ghp_***MASKED***'),
        ]
        
        for pattern, replacement in patterns:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        return text
    
    def _mask_if_sensitive(self, value):
        if isinstance(value, str):
            return self._mask_sensitive_data(value)
        return value


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        
        log_record['timestamp'] = self.formatTime(record, self.datefmt)
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['module'] = record.module
        log_record['function'] = record.funcName
        log_record['line'] = record.lineno
        
        if hasattr(record, 'service_name'):
            log_record['service'] = record.service_name
        
        if hasattr(record, 'request_id'):
            log_record['request_id'] = record.request_id
        
        if hasattr(record, 'user_id'):
            log_record['user_id'] = record.user_id
        
        if hasattr(record, 'project_id'):
            log_record['project_id'] = record.project_id
        
        if hasattr(record, 'crawl_id'):
            log_record['crawl_id'] = record.crawl_id
        
        if hasattr(record, 'task_id'):
            log_record['task_id'] = record.task_id
        
        if record.exc_info:
            log_record['exception'] = self.formatException(record.exc_info)


class MetricsLogger:
    
    _metrics = {
        'tasks_started': 0,
        'tasks_completed': 0,
        'tasks_failed': 0,
        'api_calls_success': 0,
        'api_calls_failed': 0,
        'cache_hits': 0,
        'cache_misses': 0,
    }
    
    @classmethod
    def increment(cls, metric_name: str, value: int = 1):
        if metric_name in cls._metrics:
            cls._metrics[metric_name] += value
    
    @classmethod
    def get_metrics(cls) -> Dict[str, int]:
        return cls._metrics.copy()
    
    @classmethod
    def reset_metrics(cls):
        for key in cls._metrics:
            cls._metrics[key] = 0


def setup_logging(service_name="seo_platform"):
    
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    sensitive_filter = SensitiveDataFilter()
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)
    
    if ENVIRONMENT == "production":
        console_formatter = CustomJsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s'
        )
    else:
        console_formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(sensitive_filter)
    logger.addHandler(console_handler)
    
    app_log_file = LOG_DIR / f"{service_name}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        app_log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setLevel(LOG_LEVEL)
    
    if ENVIRONMENT == "production":
        file_formatter = CustomJsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s'
        )
    else:
        file_formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(sensitive_filter)
    logger.addHandler(file_handler)
    
    error_log_file = LOG_DIR / f"{service_name}_error.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    error_handler.addFilter(sensitive_filter)
    logger.addHandler(error_handler)
    
    access_log_file = LOG_DIR / f"{service_name}_access.log"
    access_handler = logging.handlers.RotatingFileHandler(
        access_log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding='utf-8'
    )
    access_handler.setLevel(logging.INFO)
    access_handler.setFormatter(file_formatter)
    access_handler.addFilter(sensitive_filter)
    
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addHandler(access_handler)
    
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("kombu").setLevel(logging.WARNING)
    logging.getLogger("amqp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("scrapy").setLevel(logging.INFO)
    
    return logger


def get_logger(name, service_name=None):
    logger = logging.getLogger(name)
    
    if service_name:
        logger = logging.LoggerAdapter(logger, {'service_name': service_name})
    
    return logger


class RequestContextFilter(logging.Filter):
    
    def filter(self, record):
        from contextvars import ContextVar
        
        request_id_var: ContextVar[str] = ContextVar('request_id', default=None)
        user_id_var: ContextVar[str] = ContextVar('user_id', default=None)
        
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        
        return True


def setup_request_logging():
    request_filter = RequestContextFilter()
    
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(request_filter)


def setup_celery_logging():
    from celery.signals import after_setup_logger, after_setup_task_logger
    
    @after_setup_logger.connect
    def setup_loggers(logger, *args, **kwargs):
        for handler in logger.handlers:
            handler.addFilter(SensitiveDataFilter())
    
    @after_setup_task_logger.connect
    def setup_task_loggers(logger, *args, **kwargs):
        for handler in logger.handlers:
            handler.addFilter(SensitiveDataFilter())


def log_external_api_call(logger, service_name, endpoint, duration, status_code, error=None):
    extra = {
        'api_service': service_name,
        'endpoint': endpoint,
        'duration_ms': round(duration * 1000, 2),
        'status_code': status_code,
    }
    
    if error:
        logger.error(
            f"External API call failed: {service_name} - {endpoint}",
            extra={**extra, 'error': str(error)},
            exc_info=True
        )
        MetricsLogger.increment('api_calls_failed')
    else:
        logger.info(
            f"External API call: {service_name} - {endpoint}",
            extra=extra
        )
        MetricsLogger.increment('api_calls_success')


def log_task_execution(logger, task_name, task_id, duration, status, error=None):
    extra = {
        'task_name': task_name,
        'task_id': task_id,
        'duration_seconds': round(duration, 2),
        'status': status,
    }
    
    if error:
        logger.error(
            f"Task failed: {task_name} ({task_id})",
            extra={**extra, 'error': str(error)},
            exc_info=True
        )
        MetricsLogger.increment('tasks_failed')
    else:
        logger.info(
            f"Task completed: {task_name} ({task_id})",
            extra=extra
        )
        MetricsLogger.increment('tasks_completed')


class AuditLogger:
    
    def __init__(self):
        self.logger = get_logger('audit_service', service_name='audit')
    
    def log_crawl_started(self, project_id, crawl_id, url):
        self.logger.info(
            f"Crawl started: {url}",
            extra={'project_id': project_id, 'crawl_id': crawl_id, 'url': url}
        )
        MetricsLogger.increment('tasks_started')
    
    def log_crawl_completed(self, project_id, crawl_id, pages_count, duration):
        self.logger.info(
            f"Crawl completed: {pages_count} pages in {duration:.2f}s",
            extra={
                'project_id': project_id,
                'crawl_id': crawl_id,
                'pages_count': pages_count,
                'duration_seconds': duration
            }
        )
    
    def log_crawl_failed(self, project_id, crawl_id, error):
        self.logger.error(
            f"Crawl failed",
            extra={'project_id': project_id, 'crawl_id': crawl_id},
            exc_info=True
        )
    
    def log_page_crawled(self, crawl_id, url, status_code, load_time):
        self.logger.debug(
            f"Page crawled: {url} ({status_code})",
            extra={
                'crawl_id': crawl_id,
                'url': url,
                'status_code': status_code,
                'load_time_ms': round(load_time * 1000, 2)
            }
        )
    
    def log_cwv_check(self, page_id, url, lcp, fid, cls, is_good):
        self.logger.info(
            f"CWV checked: {url} - {'GOOD' if is_good else 'POOR'}",
            extra={
                'page_id': page_id,
                'url': url,
                'lcp': lcp,
                'fid': fid,
                'cls': cls,
                'is_good': is_good
            }
        )
    
    def log_schema_validation(self, page_id, url, has_schema, is_valid, errors):
        self.logger.info(
            f"Schema validation: {url} - {'VALID' if is_valid else 'INVALID'}",
            extra={
                'page_id': page_id,
                'url': url,
                'has_schema': has_schema,
                'is_valid': is_valid,
                'errors_count': len(errors) if errors else 0
            }
        )
    
    def log_backlinks_analysis(self, page_id, backlinks_count, source):
        self.logger.info(
            f"Backlinks analyzed: {backlinks_count} links from {source}",
            extra={
                'page_id': page_id,
                'backlinks_count': backlinks_count,
                'source': source
            }
        )
    
    def log_public_audit_started(self, url, ip_address):
        self.logger.info(
            f"Public audit started: {url}",
            extra={'url': url, 'ip_address': ip_address}
        )
    
    def log_public_audit_rate_limit(self, ip_address, attempts):
        self.logger.warning(
            f"Public audit rate limit exceeded: {ip_address}",
            extra={'ip_address': ip_address, 'attempts': attempts}
        )
    
    def log_playwright_crash(self, crawl_id, error):
        self.logger.error(
            f"Playwright browser crashed",
            extra={'crawl_id': crawl_id},
            exc_info=True
        )


class SemanticLogger:
    
    def __init__(self):
        self.logger = get_logger('semantic_service', service_name='semantic')
    
    def log_ff_score_calculated(self, project_id, page_id, score, components):
        self.logger.info(
            f"FF-Score calculated: {score:.2f}",
            extra={
                'project_id': project_id,
                'page_id': page_id,
                'ff_score': score,
                'freshness': components.get('freshness'),
                'familiarity': components.get('familiarity'),
                'quality': components.get('quality')
            }
        )
    
    def log_eeat_score_calculated(self, page_id, score, components):
        self.logger.info(
            f"E-E-A-T Score calculated: {score:.2f}",
            extra={
                'page_id': page_id,
                'eeat_score': score,
                'experience': components.get('experience'),
                'expertise': components.get('expertise'),
                'authoritativeness': components.get('authoritativeness'),
                'trustworthiness': components.get('trustworthiness')
            }
        )
    
    def log_llm_generation(self, page_id, generation_type, model, tokens, cache_hit, duration):
        self.logger.info(
            f"LLM generation: {generation_type} - {'CACHE HIT' if cache_hit else 'API CALL'}",
            extra={
                'page_id': page_id,
                'generation_type': generation_type,
                'model': model,
                'tokens_used': tokens,
                'cache_hit': cache_hit,
                'duration_seconds': duration
            }
        )
        
        if cache_hit:
            MetricsLogger.increment('cache_hits')
        else:
            MetricsLogger.increment('cache_misses')
    
    def log_llm_timeout(self, page_id, generation_type, timeout):
        self.logger.warning(
            f"LLM generation timeout: {generation_type} - fallback to template",
            extra={
                'page_id': page_id,
                'generation_type': generation_type,
                'timeout_seconds': timeout
            }
        )
    
    def log_llm_fallback(self, page_id, generation_type, reason):
        self.logger.warning(
            f"LLM fallback activated: {generation_type}",
            extra={
                'page_id': page_id,
                'generation_type': generation_type,
                'reason': reason
            }
        )
    
    def log_content_gap_found(self, project_id, page_id, gap_type, missing_keywords_count):
        self.logger.info(
            f"Content gap detected: {gap_type}",
            extra={
                'project_id': project_id,
                'page_id': page_id,
                'gap_type': gap_type,
                'missing_keywords_count': missing_keywords_count
            }
        )
    
    def log_semantic_distance_calculated(self, page_id, distance, top_competitor):
        self.logger.info(
            f"Semantic distance calculated: {distance:.2f}",
            extra={
                'page_id': page_id,
                'semantic_distance': distance,
                'top_competitor': top_competitor
            }
        )


class ReportingLogger:
    
    def __init__(self):
        self.logger = get_logger('reporting_service', service_name='reporting')
    
    def log_data_collection_started(self, source, project_id, date_range):
        self.logger.info(
            f"Data collection started: {source}",
            extra={
                'source': source,
                'project_id': project_id,
                'date_range': date_range
            }
        )
    
    def log_data_collection_completed(self, source, project_id, date_range, records_count, duration):
        self.logger.info(
            f"Data collected from {source}: {records_count} records",
            extra={
                'source': source,
                'project_id': project_id,
                'date_range': date_range,
                'records_count': records_count,
                'duration_seconds': duration
            }
        )
    
    def log_csv_export_started(self, report_type, project_id):
        self.logger.info(
            f"CSV export started: {report_type}",
            extra={
                'report_type': report_type,
                'project_id': project_id
            }
        )
    
    def log_csv_export_completed(self, report_type, project_id, file_path, rows_count, duration):
        self.logger.info(
            f"CSV exported: {report_type} - {rows_count} rows",
            extra={
                'report_type': report_type,
                'project_id': project_id,
                'file_path': file_path,
                'rows_count': rows_count,
                'duration_seconds': duration
            }
        )
    
    def log_api_rate_limit(self, api_name, retry_after):
        self.logger.warning(
            f"API rate limit hit: {api_name} - retry after {retry_after}s",
            extra={
                'api_name': api_name,
                'retry_after_seconds': retry_after
            }
        )
    
    def log_cost_efficiency_calculated(self, project_id, cost, traffic, roi):
        self.logger.info(
            f"Cost-Efficiency calculated: ROI {roi:.2f}%",
            extra={
                'project_id': project_id,
                'total_cost': cost,
                'organic_traffic': traffic,
                'roi': roi
            }
        )


class EventLogger:
    
    def __init__(self):
        self.logger = get_logger('domain_events', service_name='events')
    
    def log_crawl_completed_event(self, crawl_id, project_id, pages_count, event_id):
        self.logger.info(
            f"Event: CrawlCompleted",
            extra={
                'event_type': 'CrawlCompleted',
                'event_id': event_id,
                'crawl_id': crawl_id,
                'project_id': project_id,
                'pages_count': pages_count
            }
        )
    
    def log_ff_score_recalculated_event(self, project_id, score, event_id):
        self.logger.info(
            f"Event: FFScoreRecalculated",
            extra={
                'event_type': 'FFScoreRecalculated',
                'event_id': event_id,
                'project_id': project_id,
                'ff_score': score
            }
        )
    
    def log_task_created_event(self, task_id, task_type, project_id, priority):
        self.logger.info(
            f"Event: TaskCreated - {task_type}",
            extra={
                'event_type': 'TaskCreated',
                'task_id': task_id,
                'task_type': task_type,
                'project_id': project_id,
                'priority': priority
            }
        )
    
    def log_hitl_approved_event(self, change_id, approved_by, impact_score):
        self.logger.info(
            f"Event: HITLApproved",
            extra={
                'event_type': 'HITLApproved',
                'change_id': change_id,
                'approved_by': approved_by,
                'impact_score': impact_score
            }
        )
    
    def log_event_published(self, event_type, aggregate_id, event_data):
        self.logger.debug(
            f"Event published: {event_type}",
            extra={
                'event_type': event_type,
                'aggregate_id': aggregate_id,
                'event_data_size': len(str(event_data))
            }
        )
    
    def log_event_processed(self, event_id, event_type, processor, duration):
        self.logger.info(
            f"Event processed: {event_type} by {processor}",
            extra={
                'event_id': event_id,
                'event_type': event_type,
                'processor': processor,
                'duration_ms': round(duration * 1000, 2)
            }
        )


class HITLLogger:
    
    def __init__(self):
        self.logger = get_logger('hitl', service_name='hitl')
    
    def log_change_created(self, change_id, entity_type, change_type, impact_score):
        self.logger.info(
            f"HITL change created: {change_type} on {entity_type}",
            extra={
                'change_id': change_id,
                'entity_type': entity_type,
                'change_type': change_type,
                'impact_score': impact_score
            }
        )
    
    def log_change_approved(self, change_id, approved_by, impact_score):
        self.logger.info(
            f"HITL change approved",
            extra={
                'change_id': change_id,
                'approved_by': approved_by,
                'impact_score': impact_score
            }
        )
    
    def log_change_rejected(self, change_id, rejected_by, reason):
        self.logger.warning(
            f"HITL change rejected: {reason}",
            extra={
                'change_id': change_id,
                'rejected_by': rejected_by,
                'reason': reason
            }
        )
    
    def log_change_applied(self, change_id, entity_id, success):
        self.logger.info(
            f"HITL change applied: {'SUCCESS' if success else 'FAILED'}",
            extra={
                'change_id': change_id,
                'entity_id': entity_id,
                'success': success
            }
        )
    
    def log_diff_generated(self, change_id, before_size, after_size):
        self.logger.debug(
            f"Diff generated for HITL",
            extra={
                'change_id': change_id,
                'before_size_bytes': before_size,
                'after_size_bytes': after_size
            }
        )


class APIRetryLogger:
    
    def __init__(self):
        self.logger = get_logger('api_retry', service_name='api')
    
    def log_retry_attempt(self, api_name, attempt, max_retries, backoff_seconds, error_code):
        self.logger.warning(
            f"API retry attempt {attempt}/{max_retries}: {api_name} (error {error_code})",
            extra={
                'api_name': api_name,
                'attempt': attempt,
                'max_retries': max_retries,
                'backoff_seconds': backoff_seconds,
                'error_code': error_code
            }
        )
    
    def log_exponential_backoff(self, api_name, attempt, wait_seconds):
        self.logger.info(
            f"Exponential backoff: {api_name} - waiting {wait_seconds}s",
            extra={
                'api_name': api_name,
                'attempt': attempt,
                'wait_seconds': wait_seconds,
                'backoff_type': 'exponential'
            }
        )
    
    def log_fallback_to_cache(self, api_name, cache_age_hours):
        self.logger.warning(
            f"API unavailable: {api_name} - using cached data ({cache_age_hours}h old)",
            extra={
                'api_name': api_name,
                'cache_age_hours': cache_age_hours,
                'fallback': True
            }
        )
        MetricsLogger.increment('cache_hits')
    
    def log_max_retries_exceeded(self, api_name, total_attempts):
        self.logger.error(
            f"Max retries exceeded: {api_name} after {total_attempts} attempts",
            extra={
                'api_name': api_name,
                'total_attempts': total_attempts,
                'fatal': True
            }
        )


class ClientAPILogger:
    
    def __init__(self):
        self.logger = get_logger('client_api', service_name='client_api')
    
    def log_wordpress_connection(self, site_url, success):
        self.logger.info(
            f"WordPress connection: {site_url} - {'SUCCESS' if success else 'FAILED'}",
            extra={
                'platform': 'wordpress',
                'site_url': site_url,
                'success': success
            }
        )
    
    def log_wordpress_update(self, site_url, update_type, page_id, success):
        self.logger.info(
            f"WordPress update: {update_type} - {'SUCCESS' if success else 'FAILED'}",
            extra={
                'platform': 'wordpress',
                'site_url': site_url,
                'update_type': update_type,
                'page_id': page_id,
                'success': success
            }
        )
    
    def log_tilda_connection(self, project_id, success):
        self.logger.info(
            f"Tilda connection: {project_id} - {'SUCCESS' if success else 'FAILED'}",
            extra={
                'platform': 'tilda',
                'project_id': project_id,
                'success': success
            }
        )
    
    def log_tilda_update(self, project_id, page_id, update_type, success):
        self.logger.info(
            f"Tilda update: {update_type} - {'SUCCESS' if success else 'FAILED'}",
            extra={
                'platform': 'tilda',
                'project_id': project_id,
                'page_id': page_id,
                'update_type': update_type,
                'success': success
            }
        )
    
    def log_client_api_error(self, platform, error_type, error_message):
        self.logger.error(
            f"Client API error: {platform} - {error_type}",
            extra={
                'platform': platform,
                'error_type': error_type,
                'error_message': error_message
            }
        )


class ManagementLogger:
    
    def __init__(self):
        self.logger = get_logger('management_service', service_name='management')
    
    def log_task_prioritization(self, project_id, tasks_count, ff_score):
        self.logger.info(
            f"Tasks prioritized: {tasks_count} tasks based on FF-Score {ff_score:.2f}",
            extra={
                'project_id': project_id,
                'tasks_count': tasks_count,
                'ff_score': ff_score
            }
        )
    
    def log_optimization_mode_switch(self, project_id, old_mode, new_mode, ff_score):
        self.logger.info(
            f"Optimization mode switched: {old_mode} → {new_mode}",
            extra={
                'project_id': project_id,
                'old_mode': old_mode,
                'new_mode': new_mode,
                'ff_score': ff_score
            }
        )
    
    def log_seo_robot_action(self, project_id, action_type, target, automated):
        self.logger.info(
            f"SEO Robot action: {action_type} on {target}",
            extra={
                'project_id': project_id,
                'action_type': action_type,
                'target': target,
                'automated': automated
            }
        )


class SharedLogger:
    
    def __init__(self):
        self.logger = get_logger('shared', service_name='shared')
    
    def log_changelog_entry(self, entity_id, entity_type, change_type, impact_score):
        self.logger.info(
            f"Changelog: {change_type} on {entity_type}",
            extra={
                'entity_id': entity_id,
                'entity_type': entity_type,
                'change_type': change_type,
                'impact_score': impact_score
            }
        )
    
    def log_db_migration(self, revision, direction, duration):
        self.logger.info(
            f"DB migration: {direction} to {revision}",
            extra={
                'revision': revision,
                'direction': direction,
                'duration_seconds': duration
            }
        )
    
    def log_cache_cleared(self, cache_type, keys_deleted):
        self.logger.info(
            f"Cache cleared: {cache_type} - {keys_deleted} keys",
            extra={
                'cache_type': cache_type,
                'keys_deleted': keys_deleted
            }
        )


LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] %(levelname)-8s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'json': {
            '()': 'logging_config.CustomJsonFormatter',
            'format': '%(timestamp)s %(level)s %(name)s %(message)s'
        },
    },
    'filters': {
        'sensitive_data': {
            '()': 'logging_config.SensitiveDataFilter',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': LOG_LEVEL,
            'formatter': 'json' if ENVIRONMENT == 'production' else 'default',
            'filters': ['sensitive_data'],
            'stream': 'ext://sys.stdout',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': LOG_LEVEL,
            'formatter': 'json' if ENVIRONMENT == 'production' else 'default',
            'filters': ['sensitive_data'],
            'filename': str(LOG_DIR / 'app.log'),
            'maxBytes': 52428800,
            'backupCount': 10,
            'encoding': 'utf-8',
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'ERROR',
            'formatter': 'json' if ENVIRONMENT == 'production' else 'default',
            'filters': ['sensitive_data'],
            'filename': str(LOG_DIR / 'error.log'),
            'maxBytes': 52428800,
            'backupCount': 10,
            'encoding': 'utf-8',
        },
        'events_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'json',
            'filters': ['sensitive_data'],
            'filename': str(LOG_DIR / 'domain_events.log'),
            'maxBytes': 52428800,
            'backupCount': 10,
            'encoding': 'utf-8',
        },
        'api_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'json' if ENVIRONMENT == 'production' else 'default',
            'filters': ['sensitive_data'],
            'filename': str(LOG_DIR / 'api_calls.log'),
            'maxBytes': 52428800,
            'backupCount': 10,
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        '': {
            'handlers': ['console', 'file', 'error_file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'uvicorn': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'uvicorn.access': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'sqlalchemy.engine': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'domain_events': {
            'handlers': ['console', 'events_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'api_retry': {
            'handlers': ['console', 'api_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'audit_service': {
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'semantic_service': {
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'reporting_service': {
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


if __name__ == '__main__':
    setup_logging("test_service")
    
    audit_logger = AuditLogger()
    audit_logger.log_crawl_started("proj-123", "crawl-456", "https://example.com")
    audit_logger.log_crawl_completed("proj-123", "crawl-456", 150, 45.3)
    
    semantic_logger = SemanticLogger()
    semantic_logger.log_ff_score_calculated(
        "proj-123", "page-789", 75.5,
        {'freshness': 80, 'familiarity': 70, 'quality': 78}
    )
    
    event_logger = EventLogger()
    event_logger.log_crawl_completed_event("crawl-456", "proj-123", 150, "event-001")
    event_logger.log_ff_score_recalculated_event("proj-123", 75.5, "event-002")
    
    hitl_logger = HITLLogger()
    hitl_logger.log_change_created("change-123", "title", "update", 0.85)
    hitl_logger.log_change_approved("change-123", "user-001", 0.85)
    
    retry_logger = APIRetryLogger()
    retry_logger.log_retry_attempt("GSC", 2, 5, 60, 429)
    retry_logger.log_fallback_to_cache("GSC", 12)
    
    client_logger = ClientAPILogger()
    client_logger.log_wordpress_connection("https://site.com", True)
    client_logger.log_tilda_update("123456", "page-789", "meta_update", True)
    
    print(f"\nMetrics: {MetricsLogger.get_metrics()}")
