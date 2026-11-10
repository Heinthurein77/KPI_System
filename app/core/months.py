"""Single shared copy of the English month names.

Eight backend modules used to each define their own MONTH_NAMES list/tuple
(review finding m17); they all import from here now so the names can never
drift apart. A list (not a tuple) so every existing use — indexing,
``.index(period)`` and iteration — keeps working unchanged.
"""

from __future__ import annotations

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
