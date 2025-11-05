"""wogger_pro package initialization."""

from ._build_info import APP_VERSION

__all__ = []

# Expose the application version; the build pipeline updates ``APP_VERSION``
# so packaged executables can report the correct release number.
__version__ = APP_VERSION
