"""Wrong-page recovery detection for hash-routed and path-routed SPAs."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


def wrong_page_recovered(
    url_history: list[str],
    target_url: str,
) -> bool:
    """Check whether the browser recovered to the target from a wrong page.

    Recovery means the browser moved *toward* the target:
    - From elsewhere → reached the target or a descendant route
    - From the target itself → drilled deeper into a descendant route
    - From a descendant of the target → drilled even deeper

    Lateral moves (e.g. ``#/products`` → ``#/cart``) are not recovery.
    Hash-routed shell pages (``store.html#/products``) are detected
    automatically when any visited URL has a ``#/``-style fragment.
    """
    if not url_history:
        return False

    base = _base_url(target_url)
    bases = {u: _base_url(u) for u in url_history}
    is_shell = any(
        bases[u] == base
        and urlsplit(_strip_trailing_hash(u)).fragment.startswith("/")
        for u in url_history
    )

    target = _parse(target_url, shell=is_shell)
    start = _parse(url_history[0], shell=is_shell and bases[url_history[0]] == base)
    rest = [
        _parse(u, shell=is_shell and bases[u] == base)
        for u in url_history[1:]
    ]

    if start == target:
        return any(loc.context == target.context and _is_descendant(loc.route, target.route) for loc in rest)

    if start.context == target.context and _is_descendant(start.route, target.route):
        return any(loc.context == target.context and _is_descendant(loc.route, start.route) for loc in rest)

    return any(
        loc.context == target.context
        and (loc == target or _is_descendant(loc.route, target.route))
        for loc in rest
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _Location:
    context: str
    route: tuple[str, ...]


def _strip_trailing_hash(url: str) -> str:
    return url[:-1] if url.endswith("#") else url


def _base_url(url: str) -> str:
    p = urlsplit(_strip_trailing_hash(url))
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme}://{p.netloc}{p.path or '/'}{q}"


def _parse(url: str, *, shell: bool = False) -> _Location:
    p = urlsplit(_strip_trailing_hash(url))
    path = p.path or "/"
    q = f"?{p.query}" if p.query else ""

    if p.fragment.startswith("/") or path.endswith(".html") or shell:
        context = f"{p.scheme}://{p.netloc}{path}{q}"
        route_src = p.fragment if p.fragment.startswith("/") else ""
    else:
        context = f"{p.scheme}://{p.netloc}"
        route_src = path

    return _Location(context=context, route=_split_route(route_src))


def _split_route(src: str) -> tuple[str, ...]:
    s = src.strip().strip("/")
    return tuple(seg for seg in s.split("/") if seg) if s else ()


def _is_descendant(route: tuple[str, ...], ancestor: tuple[str, ...]) -> bool:
    return len(route) > len(ancestor) and route[: len(ancestor)] == ancestor
