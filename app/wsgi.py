"""
WSGI config for FastAPI application.
This follows Django's wsgi.py structure for AppPaaS compatibility.
"""
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2wsgi import ASGIMiddleware
from main import app as fastapi_app

# WSGI application callable
application = ASGIMiddleware(fastapi_app)
