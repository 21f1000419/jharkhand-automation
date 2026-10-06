"""Page-by-page durable results for the finance transaction fetcher."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FINANCE_HEADERS = [
    "Sl. No.",
    "GRN NO",
    "REMITTER NAME",
    "ENTRY DATE",
    "TRANSACTION DATE",
    "CHEQUE/DD NO",
    "CIN NO",
    "BANK REFNO",
    "PAYMENT BY",
    "AMOUNT",
    "STATUS",
    "Extra Detail Link",
    "ACTION",
]
_FORMAT = "jharkhand-finance-transactions-v1"


@dataclass(frozen=True)
class FinancePageUpdate:
    account: str
    page: int
    rows: list[list[str]]
    total_rows: int


@dataclass(frozen=True)
class FinanceAccountResult:
    account: str
    status: str
    pages: int
    matching_rows: int
    added_rows: int
    error: str


@dataclass(frozen=True)
class FinanceCheckpointInfo:
    path: Path
    row_count: int
    accounts: list[FinanceAccountResult]
    filters: str

    @property
    def can_download(self) -> bool:
        return self.row_count > 0 or any(account.status == "completed" for account in self.accounts)


class FinanceCheckpoint:
    """Each page commits its rows and progress together, before the next click.

    Only this fetch's asyncio worker writes. Connections are short-lived so the
    UI can reopen the saved results after a browser, dialog, or app closes.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @classmethod
    def create(cls, path: Path, accounts: Sequence[str], filters: str) -> FinanceCheckpoint:
        checkpoint = cls(path)
        if checkpoint.path.exists():
            raise FileExistsError(f"A checkpoint already exists at {checkpoint.path}")
        checkpoint.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(checkpoint.path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE accounts (
                    username TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
                    pages INTEGER NOT NULL DEFAULT 0, matching_rows INTEGER NOT NULL DEFAULT 0,
                    added_rows INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE pages (username TEXT NOT NULL, number INTEGER NOT NULL,
                    PRIMARY KEY (username, number));
                CREATE TABLE transactions (
                    sequence INTEGER PRIMARY KEY, grn TEXT UNIQUE NOT NULL, row_json TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [("format", _FORMAT), ("created", datetime.now().isoformat()), ("filters", filters)],
            )
            connection.executemany(
                "INSERT INTO accounts (username) VALUES (?)", [(name,) for name in accounts]
            )
        return checkpoint

    def _connect(self) -> sqlite3.Connection:
        # rw refuses missing files but lets SQLite recover a hot rollback journal
        # if the app exited during a page commit. ro cannot perform that recovery.
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def set_account_status(self, account: str, status: str, error: str = "") -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE accounts SET status = ?, error = ? WHERE username = ?", (status, error, account)
            )

    def save_page(self, account: str, number: int, rows: Sequence[Sequence[str]]) -> FinancePageUpdate:
        added: list[list[str]] = []
        with closing(self._connect()) as connection, connection:
            page = connection.execute("INSERT OR IGNORE INTO pages VALUES (?, ?)", (account, number))
            if page.rowcount:
                for row in rows:
                    if len(row) != len(FINANCE_HEADERS) or not row[1].strip():
                        raise ValueError("Cannot checkpoint a transaction without all columns and a GRN.")
                    saved = [*row, account]
                    inserted = connection.execute(
                        "INSERT OR IGNORE INTO transactions (grn, row_json) VALUES (?, ?)",
                        (row[1].strip(), json.dumps(saved, ensure_ascii=False)),
                    )
                    if inserted.rowcount:
                        added.append(saved)
                connection.execute(
                    "UPDATE accounts SET pages = pages + 1, matching_rows = matching_rows + ?, "
                    "added_rows = added_rows + ? WHERE username = ?",
                    (len(rows), len(added), account),
                )
            total = int(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
        # The transaction has committed before the UI is notified or pagination continues.
        return FinancePageUpdate(account, number, added, total)

    def info(self) -> FinanceCheckpointInfo:
        with closing(self._connect()) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if metadata.get("format") != _FORMAT:
                raise ValueError("This is not a finance transaction checkpoint.")
            accounts = [
                FinanceAccountResult(*row)
                for row in connection.execute(
                    "SELECT username, status, pages, matching_rows, added_rows, error "
                    "FROM accounts ORDER BY rowid"
                )
            ]
            total = int(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
        return FinanceCheckpointInfo(self.path, total, accounts, metadata.get("filters", ""))

    def read_rows(self) -> list[list[str]]:
        self.info()
        rows: list[list[str]] = []
        with closing(self._connect()) as connection:
            for (value,) in connection.execute("SELECT row_json FROM transactions ORDER BY sequence"):
                row = json.loads(value)
                if (
                    not isinstance(row, list)
                    or len(row) != len(FINANCE_HEADERS) + 1
                    or not all(isinstance(cell, str) for cell in row)
                ):
                    raise ValueError("The checkpoint contains an invalid transaction row.")
                rows.append(row)
        return rows
