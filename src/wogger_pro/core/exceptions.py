"""Domain-specific exception types."""


class WoggerError(Exception):
    """Base application error."""


class PersistenceError(WoggerError):
    """Raised when persistence operations fail."""


class BackupError(PersistenceError):
    """Raised when backup operations fail."""


class SettingsError(WoggerError):
    """Raised when settings cannot be validated or saved."""


class SegmentConflictError(WoggerError):
    """Raised when a proposed segment overlaps an existing entry."""
