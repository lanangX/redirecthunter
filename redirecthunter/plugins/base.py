"""Shared contract for redirect-detection plugins.

Defined in its own module (rather than in ``detector.py``) so that plugin
modules can import :class:`RedirectDetectorPlugin` and
:class:`DetectionContext` without creating a circular import with
``detector.py``, which in turn imports each concrete plugin to build its
pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

from redirecthunter.models import DetectionOutcome

if TYPE_CHECKING:
    from selectolax.parser import HTMLParser


class DetectionContext:
    """Everything a redirect-detector plugin needs to inspect one response.

    Instances are constructed once per HTTP response by
    :mod:`redirecthunter.analyzer` and passed to every plugin in the
    pipeline in turn. HTML parsing is deferred and cached: plugins that
    only need headers (``http_location``) never pay the parsing cost, and
    plugins that do need the DOM (``meta_refresh``, ``javascript``) share a
    single parsed tree instead of each re-parsing the body.
    """

    __slots__ = ("url", "status_code", "headers", "body_text", "cookies", "_tree_cache")

    def __init__(
        self,
        url: str,
        status_code: int,
        headers: Mapping[str, str],
        body_text: str | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize a detection context for a single response.

        Args:
            url: The URL that produced this response (used by plugins that
                need to resolve relative redirect targets against it).
            status_code: The HTTP status code of the response.
            headers: Response headers. Accepts ``httpx.Headers`` (natively
                case-insensitive) or a plain ``dict`` (used in tests) —
                use :meth:`get_header` for lookups that work with either.
            body_text: Decoded response body, or ``None`` for HEAD requests
                / responses with no body.
            cookies: Parsed response cookies (name -> value).
        """
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.body_text = body_text
        self.cookies = dict(cookies) if cookies else {}
        self._tree_cache: HTMLParser | None | bool = False  # False = not yet computed

    def get_header(self, name: str) -> str | None:
        """Case-insensitive header lookup that works with any Mapping type."""
        value = self.headers.get(name)
        if value is not None:
            return value
        lowered = name.lower()
        for key, val in self.headers.items():
            if key.lower() == lowered:
                return val
        return None

    @property
    def html_tree(self) -> HTMLParser | None:
        """Lazily parsed, cached selectolax HTML tree of :attr:`body_text`.

        Returns ``None`` if there is no body to parse (e.g. HEAD requests).
        Parsing happens at most once per context, regardless of how many
        plugins access this property.
        """
        if self._tree_cache is False:
            if self.body_text:
                from selectolax.parser import HTMLParser as _HTMLParser

                self._tree_cache = _HTMLParser(self.body_text)
            else:
                self._tree_cache = None
        return self._tree_cache  # type: ignore[return-value]


class RedirectDetectorPlugin(ABC):
    """Base class for a single redirect-detection strategy.

    Concrete plugins must be stateless (safe to share a single instance
    across concurrent async workers) and side-effect free — ``detect()``
    only inspects the given context and returns a result; it must never
    perform I/O (no follow-up requests, no bypass attempts).
    """

    #: Short, stable identifier used in ``DetectionOutcome.source`` and in
    #: CLI/log output. Must be overridden by subclasses.
    name: str = "base"

    @abstractmethod
    def detect(self, context: DetectionContext) -> DetectionOutcome | None:
        """Inspect ``context`` and return a positive detection, if any.

        Args:
            context: The response data to inspect.

        Returns:
            A :class:`~redirecthunter.models.DetectionOutcome` if this
            plugin's redirect pattern was found, otherwise ``None``.
        """
        raise NotImplementedError


__all__ = ["DetectionContext", "RedirectDetectorPlugin"]
