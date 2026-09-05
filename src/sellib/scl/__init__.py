"""IEC 61850 SCL: reading an SCD/ICD, and the bit -> MMS item tables.

Kept as a self-contained subpackage that imports nothing from the rest of
`sellib`, so it can leave as its own library the day the SEL-specific half
(the ``db:`` ``sAddr`` grammar) gets a proper vendor seam.
"""
