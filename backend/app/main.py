"""ASGI entry point.

Importing this module is side-effect free. Persistent startup work is owned by
the application lifespan in ``app.bootstrap.lifecycle``.
"""

from .bootstrap import create_app


app = create_app()

__all__ = ["app", "create_app"]
