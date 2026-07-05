from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session


VIEWS_DIR = Path(__file__).resolve().parents[2] / "sql" / "views"


def refresh_views(session: Session) -> None:
    for path in sorted(VIEWS_DIR.glob("mart_*.sql")):
        session.execute(text(path.read_text(encoding="utf-8")))
