"""同业务键对齐：双边金额比对，落对齐差 + 悬账单。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.services import 目录
from app.modules.左缺右留缓冲 import 服务 as 左缺

def 跑对齐() -> dict:
    规 = 目录.轨规()
    容差 = int(规.get("金额容差分", "0"))
    conn = 连接()
    try:
        # 左缺右留缓冲处置过的业务键归模块所有：基线重算不覆盖其形态与路径。
        模块键 = 左缺.待键(conn)
        模块键 |= {
            r["业务键"]
            for r in conn.execute("SELECT DISTINCT 业务键 FROM 左缺处置").fetchall()
        } if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='左缺处置'"
        ).fetchone() else set()

        # 清空上次对齐结果（基线默认：每次重算；模块键留痕保留）
        for t in ("对齐差", "悬账单"):
            if 模块键:
                conn.execute(
                    f"DELETE FROM {t} WHERE 业务键 NOT IN ({','.join('?' * len(模块键))})",
                    tuple(模块键),
                )
            else:
                conn.execute(f"DELETE FROM {t}")

        左键 = {
            r["业务键"]: r
            for r in conn.execute(
                "SELECT * FROM 左轨流水 WHERE 命运='已入左'"
            ).fetchall()
        }
        # 同键多笔：按业务键汇总金额（基线口径）
        左汇总: dict[str, int] = {}
        左代表: dict[str, str] = {}
        for r in conn.execute("SELECT * FROM 左轨流水 WHERE 命运='已入左'").fetchall():
            k = r["业务键"]
            左汇总[k] = 左汇总.get(k, 0) + int(r["金额分"])
            左代表[k] = r["事件编号"]

        右汇总: dict[str, int] = {}
        右代表: dict[str, str] = {}
        for r in conn.execute(
            "SELECT * FROM 右轨流水 WHERE 命运 IN ('已入右','右悬','右留缓冲')"
        ).fetchall():
            k = r["业务键"]
            右汇总[k] = 右汇总.get(k, 0) + int(r["金额分"])
            右代表[k] = r["事件编号"]

        所有键 = set(左汇总) | set(右汇总)
        对齐 = 0
        差 = 0
        悬 = 0
        时刻 = 此刻()

        for k in sorted(所有键 - 模块键):
            有左 = k in 左汇总
            有右 = k in 右汇总
            if 有左 and 有右:
                la, ra = 左汇总[k], 右汇总[k]
                if abs(la - ra) <= 容差:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                        (k, la, ra, "对齐", "双边金额在容差内", 时刻),
                    )
                    对齐 += 1
                else:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                        (k, la, ra, "金额不符", f"左{la}-右{ra}", 时刻),
                    )
                    差 += 1
            elif 有左:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                    (k, 左汇总[k], None, "右缺", "仅左轨有键", 时刻),
                )
                conn.execute(
                    "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                    (k, "左", 左汇总[k], 左代表.get(k, ""), "右轨缺键", 时刻),
                )
                悬 += 1
                差 += 1
            else:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                    (k, None, 右汇总[k], "左缺", "仅右轨有键", 时刻),
                )
                conn.execute(
                    "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                    (k, "右", 右汇总[k], 右代表.get(k, ""), "左轨缺键", 时刻),
                )
                悬 += 1
                差 += 1

        conn.commit()
        # 模块处置过的键由本模块维护，基线报告按现存留痕一并计数，便于对照。
        模块悬 = 0
        模块差 = 0
        if 模块键:
            占 = ",".join("?" * len(模块键))
            模块差 = conn.execute(
                f"SELECT COUNT(*) AS c FROM 对齐差 WHERE 业务键 IN ({占}) AND 差型<>'对齐'",
                tuple(模块键),
            ).fetchone()["c"]
            模块悬 = conn.execute(
                f"SELECT COUNT(*) AS c FROM 悬账单 WHERE 业务键 IN ({占})",
                tuple(模块键),
            ).fetchone()["c"]
        return {
            "键总数": len(所有键),
            "对齐": 对齐,
            "差与悬": 差 + 模块差,
            "悬账": 悬 + 模块悬,
            "容差分": 容差,
            "时刻": 时刻,
            "模块键数": len(模块键),
        }
    finally:
        conn.close()
