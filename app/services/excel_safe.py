"""Single shared Excel formula-injection guard.

Both export services used to copy-paste the identical ``_safe`` helper
(review finding m17); they now import this one function so the guard can
never drift apart. A string starting with ``=``, ``+``, ``-`` or ``@`` is
treated as a formula by spreadsheet apps, so it is prefixed with a single
quote to force plain-text interpretation.
"""

from __future__ import annotations


def safe_cell_value(value: object) -> object:
    """Neutralize Excel formula injection for one cell value."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value
