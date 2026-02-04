"""
WSGI wrapper for FastAPI ASGI application
Required for AppPaaS/uWSGI deployment
"""
from a2wsgi import ASGIMiddleware
from main import app

# Wrap the FastAPI (ASGI) app for WSGI compatibility
application = ASGIMiddleware(app)
