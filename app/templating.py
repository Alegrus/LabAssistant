"""Shared Jinja2 templates instance (avoids circular imports with main.py)."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
