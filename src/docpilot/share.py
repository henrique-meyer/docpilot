"""Link-preview tags. WhatsApp reads the Open Graph image."""

from __future__ import annotations

from markupsafe import Markup

_ORIGIN = "https://docpilot.hmeyer.dev"
_IMAGE = f"{_ORIGIN}/assets/logo.png"
_PAGES = {
    "/": (
        "DocPilot",
        (
            "Ask questions about the documents that have been uploaded. "
            "Answers cite the page they came from, and you can open that page."
        ),
    ),
    "/documents": (
        "Documents · DocPilot",
        "The documents currently uploaded to DocPilot. Open one, or ask a question about them.",
    ),
}


def share_head(path: str) -> Markup:
    title, description = _PAGES[path]
    url = f"{_ORIGIN}/" if path == "/" else f"{_ORIGIN}{path}"
    return Markup(
        f"<title>{title}</title>\n"
        f'<meta name="description" content="{description}">\n'
        f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{description}">\n'
        f'<meta property="og:url" content="{url}">\n'
        '<meta property="og:type" content="website">\n'
        f'<meta property="og:image" content="{_IMAGE}">\n'
        '<meta property="og:image:type" content="image/png">\n'
        '<meta property="og:image:width" content="1024">\n'
        '<meta property="og:image:height" content="1024">\n'
        '<meta property="og:image:alt" content="DocPilot">\n'
        '<meta name="twitter:card" content="summary">\n'
        f'<meta name="twitter:title" content="{title}">\n'
        f'<meta name="twitter:description" content="{description}">\n'
        f'<meta name="twitter:image" content="{_IMAGE}">'
    )
