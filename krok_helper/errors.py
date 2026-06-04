class ProcessingError(RuntimeError):
    """Raised when the ffmpeg pipeline cannot continue."""


class ExportCancelled(ProcessingError):
    """Raised when the user stops an export in progress."""
