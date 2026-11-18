"""Reoa's mark, in one place: install.py draws the shortcut icon from it and report.py puts it on every PDF.

On a 16 x 16 grid: a column of amounts in a ledger, closed by a double rule (the bookkeeper's sign for a final
total). Each bar is (x from, x to, row), one row high, white on a black square. logo.svg and the dashboard
(dashboard_template.html) draw the same bars.
"""

MARK_BARS = [(8, 13, 3), (10, 13, 5), (6, 13, 7), (3, 13, 10), (3, 13, 12)]


def mark_svg(css_class="mark", ink="#000000", paper="#ffffff"):
    """The mark as inline SVG; size it with CSS."""
    bars = "".join(f'<rect x="{a}" y="{r}" width="{b - a}" height="1"/>' for a, b, r in MARK_BARS)
    return (f'<svg class="{css_class}" viewBox="0 0 16 16" aria-hidden="true" shape-rendering="crispEdges">'
            f'<rect width="16" height="16" fill="{ink}"/><g fill="{paper}">{bars}</g></svg>')
