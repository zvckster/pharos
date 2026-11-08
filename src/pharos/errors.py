"""Errors with controlled, credential-free messages safe to include in reports."""


class CheckError(ValueError):
    """An expected upstream or installed-version failure with a safe message."""
