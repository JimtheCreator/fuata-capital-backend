from celery import Celery

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from config import get_settings

settings = get_settings()
redis_url = settings.redis_url

# Ensure the URL has the SSL parameter if it's not already in .env
if "ssl_cert_reqs" not in redis_url:
    delimiter = "&" if "?" in redis_url else "?"
    redis_url = f"{redis_url}{delimiter}ssl_cert_reqs=none"

celery_app = Celery(
    "fuata_capital_workers",
    broker=redis_url,
    backend=redis_url,
    include=['app.infrastructure.workers.tasks']
)

celery_app.conf.update(
    # Broker Settings
    broker_use_ssl={'ssl_cert_reqs': None},
    
    # Result Backend Settings (This is what's failing now)
    redis_backend_use_ssl={'ssl_cert_reqs': None},
    
    # Connection Security
    broker_connection_retry_on_startup=True,
    
    # This helps with Upstash's specific threading model
    redis_backend_health_check_interval=30,
)