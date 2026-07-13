# Gunicorn configuration file for cobien-backend
# Automatically loaded by Gunicorn on Render startup.

import os
import multiprocessing

# Use gthread worker class to handle slow client downloads (like images/logs)
# concurrently without blocking the main sync workers and triggering timeouts.
worker_class = 'gthread'

# Dynamically set worker count. Render free/basic tiers have 1-2 CPUs,
# so 2-4 workers is ideal.
workers = int(os.getenv("WEB_CONCURRENCY", "4"))

# Threads per worker. Allows handling up to (workers * threads) concurrent requests.
threads = 4

# Increase request timeout to 60s to prevent early worker restarts
timeout = 60

# Keep-alive connections for better performance
keepalive = 2
max_requests = 1000
max_requests_jitter = 50
