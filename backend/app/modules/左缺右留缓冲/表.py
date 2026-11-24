"""左缺右留缓冲：模块独立建表。基线对齐/悬账口径不被改写。

注意：使用逐条 execute 而非 executescript，避免在多步事务中被隐式提交。
"""
from __future__ import annotations

from app.db import 连接

语句 = (
    """CREATE TABLE IF NOT EXISTS 缓冲包 (
      包键 TEXT PRIMARY KEY,
      业务键 TEXT NOT NULL,
      右事件编号 TEXT NOT NULL,
      金额分 INTEGER NOT NULL,
      源键 TEXT NOT NULL DEFAULT '核乙',
      状态 TEXT NOT NULL DEFAULT '待左',
      物化时 TEXT NOT NULL,
      回放时 TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_缓冲包_业务键待左
      ON 缓冲包(业务键) WHERE 状态='待左'""",
    """CREATE TABLE IF NOT EXISTS 左缺处置 (
      编号 INTEGER PRIMARY KEY AUTOINCREMENT,
      业务键 TEXT NOT NULL,
      形态 TEXT NOT NULL,
      键 TEXT NOT NULL,
      状态 TEXT NOT NULL DEFAULT '待左',
      路径 TEXT NOT NULL DEFAULT '',
      右事件编号 TEXT NOT NULL DEFAULT '',
      金额分 INTEGER NOT NULL DEFAULT 0,
      处置时 TEXT NOT NULL,
      完成时 TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_左缺处置_业务键待左
      ON 左缺处置(业务键) WHERE 状态='待左'""",
    """CREATE TABLE IF NOT EXISTS 左缺对拍 (
      编号 INTEGER PRIMARY KEY AUTOINCREMENT,
      时刻 TEXT NOT NULL,
      待悬 INTEGER NOT NULL DEFAULT 0,
      待缓 INTEGER NOT NULL DEFAULT 0,
      回正 INTEGER NOT NULL DEFAULT 0,
      回放 INTEGER NOT NULL DEFAULT 0,
      右悬 INTEGER NOT NULL DEFAULT 0,
      右缓 INTEGER NOT NULL DEFAULT 0,
      是否通过 INTEGER NOT NULL DEFAULT 0,
      说明 TEXT NOT NULL DEFAULT '',
      问题 TEXT NOT NULL DEFAULT '[]'
    )""",
)


def 建表(conn=None) -> None:
    """建模块表。自带连接时提交；外部连接时由调用方掌握提交时机。"""
    own = conn is None
    conn = conn or 连接()
    try:
        for sql in 语句:
            conn.execute(sql)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def 有表(conn) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='左缺处置'"
        ).fetchone()
    )


def 确保(conn) -> None:
    """按需补建模块表；不提交，保持调用方事务原子性。"""
    if not 有表(conn):
        建表(conn)
