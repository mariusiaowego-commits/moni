"""V2 placeholder: direction-specific rubrics (design §10).

V1 ships only the general ten questions in :mod:`moni.scoring.rubric`.
Direction scoring needs three tables (``bv_direction``,
``bv_direction_research``, ``article_bv_direction``) plus a research prompt;
none of that is wired up yet.  ``score_general_bv`` already accepts a
``Rubric`` instance so a direction rubric can be plugged in without touching
the pipeline.
"""
from __future__ import annotations

__all__: list[str] = []
