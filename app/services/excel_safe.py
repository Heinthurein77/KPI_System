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


def safe_file_part(value: str, fallback: str = "employee") -> str:
    """ASCII slug for a filename part — one shared copy (B-m7).

    Attendance and reports used to define their own ``_safe_file_part``
    with divergent rules: attendance kept only ASCII alphanumerics
    (``c.isascii()``), reports kept any ``isalnum()`` character, so a
    Burmese name slugged differently per endpoint. The shared rule is
    the stricter one (attendance's): non-ASCII letters become ``-``,
    so a name with no ASCII letters at all falls back to ``fallback``
    ("department" for a department file, "employee" for a per-person
    file) instead of producing an empty slug. Strips quotes, CR/LF and
    anything outside [a-z0-9-_], so a crafted name cannot inject
    headers via Content-Disposition.
    """
    slug = "".join(
        c if c.isascii() and (c.isalnum() or c in "-_") else "-"
        for c in value.lower().replace(" ", "-")
    )
    return slug.strip("-") or fallback
