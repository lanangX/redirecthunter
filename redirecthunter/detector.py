"""Redirect-detection pipeline orchestrator.

:class:`RedirectDetector` owns an ordered list of
:class:`~redirecthunter.plugins.base.RedirectDetectorPlugin` instances and
runs them in priority order against a single
:class:`~redirecthunter.plugins.base.DetectionContext`, returning the first
positive match. Pipeline order encodes real-world precedence: an HTTP-level
redirect (``http_location``) is what actual clients follow and always wins
over a page's meta-refresh or JS fallback aimed at browsers that ignore the
``Location`` header; meta-refresh in turn is simpler and more universally
supported than JS, so it's checked before ``javascript``.

Cloudflare classification runs separately via :meth:`RedirectDetector.classify_cloudflare`
since it annotates a result rather than participating in redirect-type
detection (see :mod:`redirecthunter.plugins.cloudflare`).

Plugins are constructed once and reused across every concurrent scan
worker — they are required to be stateless and side-effect free (enforced
by convention, documented on
:class:`~redirecthunter.plugins.base.RedirectDetectorPlugin`), so a single
shared ``RedirectDetector`` instance is safe under high async concurrency.
"""

from __future__ import annotations

from redirecthunter.models import CloudflareStatus, DetectionOutcome
from redirecthunter.plugins.base import DetectionContext, RedirectDetectorPlugin
from redirecthunter.plugins.cloudflare import CloudflarePlugin
from redirecthunter.plugins.http_location import HttpLocationPlugin
from redirecthunter.plugins.javascript import JavaScriptRedirectPlugin
from redirecthunter.plugins.meta_refresh import MetaRefreshPlugin

#: Default pipeline order. Do not reorder without updating the module
#: docstring's precedence rationale above.
_DEFAULT_PLUGINS: tuple[type[RedirectDetectorPlugin], ...] = (
    HttpLocationPlugin,
    MetaRefreshPlugin,
    JavaScriptRedirectPlugin,
)


class RedirectDetector:
    """Runs redirect-detection plugins in priority order and classifies Cloudflare.

    Constructed with dependency injection in mind: pass a custom
    ``plugins`` list to test a single plugin in isolation, reorder
    precedence, or add a third-party plugin without modifying this class.
    """

    def __init__(
        self,
        plugins: list[RedirectDetectorPlugin] | None = None,
        cloudflare_plugin: CloudflarePlugin | None = None,
    ) -> None:
        """Initialize the detector with an ordered plugin pipeline.

        Args:
            plugins: Ordered list of redirect-type plugins to run, highest
                priority first. Defaults to
                ``[HttpLocationPlugin(), MetaRefreshPlugin(), JavaScriptRedirectPlugin()]``.
            cloudflare_plugin: The Cloudflare classifier to use. Defaults
                to a new :class:`~redirecthunter.plugins.cloudflare.CloudflarePlugin`.
        """
        self._plugins: list[RedirectDetectorPlugin] = (
            plugins if plugins is not None else [cls() for cls in _DEFAULT_PLUGINS]
        )
        self._cloudflare_plugin = cloudflare_plugin or CloudflarePlugin()

    @property
    def plugin_names(self) -> list[str]:
        """Names of the active plugins, in pipeline order (for logging/debug)."""
        return [plugin.name for plugin in self._plugins]

    def detect(self, context: DetectionContext) -> DetectionOutcome | None:
        """Run the redirect-type pipeline and return the first match.

        Args:
            context: The response data to inspect.

        Returns:
            The first :class:`~redirecthunter.models.DetectionOutcome`
            produced by any plugin in pipeline order, or ``None`` if no
            plugin found a redirect.
        """
        for plugin in self._plugins:
            outcome = plugin.detect(context)
            if outcome is not None:
                return outcome
        return None

    def classify_cloudflare(self, context: DetectionContext) -> CloudflareStatus:
        """Classify Cloudflare protection for the given response context.

        Independent of :meth:`detect` — always runs regardless of whether
        a redirect was found, since Cloudflare presence and redirect
        behavior are orthogonal facts about a response.
        """
        return self._cloudflare_plugin.classify(context)

    def analyze(self, context: DetectionContext) -> tuple[DetectionOutcome | None, CloudflareStatus]:
        """Convenience method combining :meth:`detect` and :meth:`classify_cloudflare`.

        This is the single entry point :mod:`redirecthunter.analyzer` calls
        per response — it guarantees both the redirect-type pipeline and
        the Cloudflare classifier always run together against the same
        context, so callers can't accidentally skip one.
        """
        outcome = self.detect(context)
        cloudflare_status = self.classify_cloudflare(context)
        return outcome, cloudflare_status


def default_detector() -> RedirectDetector:
    """Build a ``RedirectDetector`` with the standard plugin pipeline.

    Provided as the normal construction path for application code (engine,
    CLI); tests and advanced use cases can still construct
    ``RedirectDetector`` directly with custom plugins.
    """
    return RedirectDetector()


__all__ = ["RedirectDetector", "default_detector"]
