# backend/routes/__init__.py
# Expose all Blueprint modules.

from .general import general_bp
from .signals import signals_bp
from .interactions import interactions_bp
from .boxed_warnings import boxed_warnings_bp
from .ml import ml_bp
