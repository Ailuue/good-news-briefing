"""The SQLite seen-table: remembers links across runs so we only surface new
items. A thin wrapper around a single connection."""

from __future__ import annotations
import sqlite3
import datetime

from . import config


class SeenStore:
    """Tracks which article links have been processed on previous runs."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    @classmethod
    def open(cls) -> "SeenStore":
        config.OUT_DIR.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(config.DB_PATH)
        con.execute("CREATE TABLE IF NOT EXISTS seen(link TEXT PRIMARY KEY, ts TEXT)")
        return cls(con)

    def is_seen(self, link: str) -> bool:
        return (
            self.con.execute(
                "SELECT 1 FROM seen WHERE link=?", (link,)
            ).fetchone()
            is not None
        )

    def mark_seen(self, link: str) -> None:
        self.con.execute(
            "INSERT OR IGNORE INTO seen(link, ts) VALUES(?, ?)",
            (link, datetime.datetime.now().isoformat()),
        )

    def commit(self) -> None:
        self.con.commit()
