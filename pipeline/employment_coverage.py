"""Shared territorial coverage contract for employment datasets."""

from __future__ import annotations


# Metropolitan France: the 95 numeric codes exclude obsolete ``20`` and Corsica
# is represented by its two actual identifiers, hence 96 collection identifiers.
METROPOLITAN_DEPARTMENTS = tuple(
    [str(department).zfill(2) for department in range(1, 96) if department != 20]
    + ["2A", "2B"]
)

assert len(METROPOLITAN_DEPARTMENTS) == 96


def department_from_codgeo(codgeo: object) -> str:
    """Return the department identifier for a commune INSEE code."""
    code = str(codgeo).strip().upper()
    if code.startswith("2A") or code.startswith("2B"):
        return code[:2]
    return code[:2]
