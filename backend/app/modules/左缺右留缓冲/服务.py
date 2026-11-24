"""左缺右留缓冲：处置 / 回放 / 编排 / 对拍。

关键不变式（硬推理约束）:
1. 仅右轨有键（左缺）时，立即择定且只择定一种形态：
   悬账（当场右悬，写悬行不写缓冲包）或 缓冲（物化包，写包不写悬行）。
2. 写悬/写包与右轨命运同事务、同成败（右命运=右悬 / 右留缓冲）。
3. 同一业务键待左期间不得悬与缓冲双落（唯一部分索引 + BEGIN IMMEDIATE 串行）。
4. 左到后回放对齐：悬形态走「悬账回正」，缓冲形态走「缓冲回放」；
   两边都能对齐，但路径与读引用不同。
"""
from __future__ import annotations

import json

from app.db import 连接
from app.seed import 此刻
from app.services import 目录
from app.modules.左缺右留缓冲 import 表
from app.modules.左缺右留缓冲.常量 import (
    形态悬账,
    形态缓冲,
    选项,
    策略键,
    默认形态,
    待左,
    已回正,
    已回放,
    路径悬账回正,
    路径缓冲回放,
    右悬命运,
    右缓命运,
    右入命运,
    右有效命运,
    悬原因前缀,
    差说明悬,
    差说明缓,
    左缺差型,
    悬键,
    包键,
)


# ------------------------------------------------------------------ 基础读取

def _连():
    conn = 连接()
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _占位(n: int) -> str:
    return ",".join("?" * n)


def _轨规值(conn, 键: str, 默认: str) -> str:
    row = conn.execute("SELECT 值 FROM 轨规 WHERE 键=?", (键,)).fetchone()
    return str(row["值"]) if row else 默认


def 取策略(conn=None) -> str:
    own = conn is None
    conn = conn or _连()
    try:
        v = _轨规值(conn, 策略键, 默认形态)
        return v if v in 选项 else 默认形态
    finally:
        if own:
            conn.close()


def _容差(conn) -> int:
    try:
        return int(_轨规值(conn, "金额容差分", "0"))
    except (TypeError, ValueError):
        return 0


def _左汇总(conn, 业务键: str) -> tuple[int, str, int]:
    rows = conn.execute(
        "SELECT 金额分, 事件编号 FROM 左轨流水 WHERE 业务键=? AND 命运='已入左' ORDER BY 事件时",
        (业务键,),
    ).fetchall()
    # 取最新一条为代表（同键多笔按基线汇总口径求和）
    return sum(int(r["金额分"]) for r in rows), (rows[-1]["事件编号"] if rows else ""), len(rows)


def _右汇总(conn, 业务键: str) -> tuple[int, str, int]:
    rows = conn.execute(
        f"""SELECT 金额分, 事件编号 FROM 右轨流水
            WHERE 业务键=? AND 命运 IN ({_占位(len(右有效命运))})
            ORDER BY 事件时""",
        (业务键, *右有效命运),
    ).fetchall()
    # 同键多笔按基线汇总口径求和；右轨道已完成/待左命运均计入
    return sum(int(r["金额分"]) for r in rows), (rows[-1]["事件编号"] if rows else ""), len(rows)


def _右命运集(conn, 业务键: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT 命运 FROM 右轨流水 WHERE 业务键=?", (业务键,)
    ).fetchall()
    return {r["命运"] for r in rows}


def _待行(conn, 业务键: str):
    return conn.execute(
        "SELECT * FROM 左缺处置 WHERE 业务键=? AND 状态=? ORDER BY 编号 DESC LIMIT 1",
        (业务键, 待左),
    ).fetchone()


def _最新行(conn, 业务键: str):
    return conn.execute(
        """SELECT * FROM 左缺处置 WHERE 业务键=?
           ORDER BY CASE 状态 WHEN '待左' THEN 0 ELSE 1 END, 编号 DESC LIMIT 1""",
        (业务键,),
    ).fetchone()


def _悬行(conn, 业务键: str):
    return conn.execute(
        "SELECT * FROM 悬账单 WHERE 业务键=? AND 悬边='右' ORDER BY 编号 DESC LIMIT 1",
        (业务键,),
    ).fetchone()


def _包行(conn, 业务键: str):
    return conn.execute("SELECT * FROM 缓冲包 WHERE 包键=?", (包键(业务键),)).fetchone()


def _差行(conn, 业务键: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM 对齐差 WHERE 业务键=? ORDER BY 编号 DESC", (业务键,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 形态落地

def _写悬(conn, 业务键: str, 金额: int, 右代表: str, 时刻: str) -> None:
    """当场右悬：悬账单悬边=右；不写缓冲表（连带清掉任何缓冲包）。"""
    conn.execute("DELETE FROM 悬账单 WHERE 业务键=? AND 悬边='右'", (业务键,))
    conn.execute("DELETE FROM 缓冲包 WHERE 业务键=?", (业务键,))
    conn.execute(
        "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
        (业务键, "右", 金额, 右代表, 悬原因前缀, 时刻),
    )


def _删悬(conn, 业务键: str) -> None:
    conn.execute("DELETE FROM 悬账单 WHERE 业务键=? AND 悬边='右'", (业务键,))


def _写包(conn, 业务键: str, 金额: int, 右代表: str, 源键: str, 时刻: str) -> None:
    """右留缓冲物化包：入缓冲待左；不写悬账（连带清掉任何右悬行）。"""
    conn.execute("DELETE FROM 悬账单 WHERE 业务键=? AND 悬边='右'", (业务键,))
    conn.execute(
        """INSERT INTO 缓冲包(包键,业务键,右事件编号,金额分,源键,状态,物化时,回放时)
           VALUES (?,?,?,?,?,?,?,'')
           ON CONFLICT(包键) DO UPDATE SET
             右事件编号=excluded.右事件编号,
             金额分=excluded.金额分,
             源键=excluded.源键,
             状态='待左',
             物化时=excluded.物化时,
             回放时=''""",
        (包键(业务键), 业务键, 右代表, 金额, 源键, 待左, 时刻),
    )


def _闭包(conn, 业务键: str, 时刻: str) -> None:
    conn.execute(
        "UPDATE 缓冲包 SET 状态=?, 回放时=? WHERE 包键=?",
        (已回放, 时刻, 包键(业务键)),
    )


def _写左缺差(conn, 业务键: str, 右金额: int, 说明: str, 时刻: str) -> None:
    conn.execute("DELETE FROM 对齐差 WHERE 业务键=?", (业务键,))
    conn.execute(
        """INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻)
           VALUES (?,?,?,?,?,?)""",
        (业务键, None, 右金额, 左缺差型, 说明, 时刻),
    )


def _写成对差(
    conn, 业务键: str, 左金额: int, 右金额: int, 差型: str, 路径: str, 时刻: str
) -> None:
    说明 = f"{路径}·" + ("双边金额在容差内" if 差型 == "对齐" else f"左{左金额}-右{右金额}")
    conn.execute("DELETE FROM 对齐差 WHERE 业务键=?", (业务键,))
    conn.execute(
        """INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻)
           VALUES (?,?,?,?,?,?)""",
        (业务键, 左金额, 右金额, 差型, 说明, 时刻),
    )


def _开处置(
    conn, 业务键: str, 形态: str, 键: str, 金额: int, 右代表: str, 时刻: str
) -> None:
    conn.execute(
        """INSERT INTO 左缺处置(业务键,形态,键,状态,路径,右事件编号,金额分,处置时,完成时)
           VALUES (?,?,?,?, '', ?,?,?, '')""",
        (业务键, 形态, 键, 待左, 右代表, 金额, 时刻),
    )


def _刷处置(conn, 业务键: str, 金额: int, 右代表: str) -> None:
    conn.execute(
        "UPDATE 左缺处置 SET 金额分=?, 右事件编号=? WHERE 业务键=? AND 状态=?",
        (金额, 右代表, 业务键, 待左),
    )


def _结处置(conn, 业务键: str, 状态: str, 路径: str, 时刻: str) -> None:
    conn.execute(
        "UPDATE 左缺处置 SET 状态=?, 路径=?, 完成时=? WHERE 业务键=? AND 状态=?",
        (状态, 路径, 时刻, 业务键, 待左),
    )


def _改右命运(conn, 业务键: str, 命运: str) -> None:
    conn.execute("UPDATE 右轨流水 SET 命运=? WHERE 业务键=?", (命运, 业务键))


# ------------------------------------------------------------------ 主链动作

def 材料化(
    conn, 业务键: str, 时刻: str | None = None, 形态: str | None = None
) -> dict | None:
    """仅右键立即择定形态并落地（幂等）。左已存在或无右键时返回 None。"""
    时刻 = 时刻 or 此刻()
    if 形态 is not None and 形态 not in 选项:
        raise ValueError(f"形态须为 {选项}")
    左金额, _, 左笔数 = _左汇总(conn, 业务键)
    if 左笔数 > 0:
        return None
    右金额, 右代表, 右笔数 = _右汇总(conn, 业务键)
    if 右笔数 == 0:
        return None

    待 = _待行(conn, 业务键)
    if 待:
        形态 = 待["形态"]  # 已有待左处置：沿用，绝不改形（防双落）
    elif 形态 is None:
        形态 = 取策略(conn)
    if 形态 not in 选项:
        形态 = 默认形态

    if 形态 == 形态缓冲:
        _写包(conn, 业务键, 右金额, 右代表, "核乙", 时刻)
        _写左缺差(conn, 业务键, 右金额, 差说明缓, 时刻)
        if 待:
            _刷处置(conn, 业务键, 右金额, 右代表)
        else:
            _开处置(conn, 业务键, 形态缓冲, 包键(业务键), 右金额, 右代表, 时刻)
        _改右命运(conn, 业务键, 右缓命运)
    else:
        _写悬(conn, 业务键, 右金额, 右代表, 时刻)
        _写左缺差(conn, 业务键, 右金额, 差说明悬, 时刻)
        if 待:
            _刷处置(conn, 业务键, 右金额, 右代表)
        else:
            _开处置(conn, 业务键, 形态悬账, 悬键(业务键), 右金额, 右代表, 时刻)
        _改右命运(conn, 业务键, 右悬命运)

    行 = _待行(conn, 业务键) or _最新行(conn, 业务键)
    return {
        "业务键": 业务键,
        "形态": 行["形态"],
        "键": 行["键"],
        "状态": 行["状态"],
        "金额分": 行["金额分"],
    }


def 收束(conn, 业务键: str, 时刻: str | None = None) -> dict | None:
    """左到后回放对齐：悬→悬账回正，缓→缓冲回放。无待左处置时返回 None。"""
    时刻 = 时刻 or 此刻()
    待 = _待行(conn, 业务键)
    if not 待:
        return None
    左金额, _, 左笔数 = _左汇总(conn, 业务键)
    if 左笔数 == 0:
        return None
    右金额, _, _ = _右汇总(conn, 业务键)
    差型 = "对齐" if abs(左金额 - 右金额) <= _容差(conn) else "金额不符"

    if 待["形态"] == 形态悬账:
        路径, 状态 = 路径悬账回正, 已回正
        _删悬(conn, 业务键)
    else:
        路径, 状态 = 路径缓冲回放, 已回放
        _闭包(conn, 业务键, 时刻)

    _写成对差(conn, 业务键, 左金额, 右金额, 差型, 路径, 时刻)
    _结处置(conn, 业务键, 状态, 路径, 时刻)
    # 左到后两边成对：右轨回归常规「已入右」，路径由左缺处置记录留痕。
    _改右命运(conn, 业务键, 右入命运)
    return {
        "业务键": 业务键,
        "形态": 待["形态"],
        "路径": 路径,
        "状态": 状态,
        "差型": 差型,
        "左金额分": 左金额,
        "右金额分": 右金额,
    }


def 刷新待左(conn, 业务键: str, 时刻: str | None = None) -> dict | None:
    """已有待左处置的键又到右键：只刷新金额与代表事件，绝不换形态。"""
    时刻 = 时刻 or 此刻()
    待 = _待行(conn, 业务键)
    if not 待:
        return None
    if _左汇总(conn, 业务键)[2] > 0:
        return None
    右金额, 右代表, 右笔数 = _右汇总(conn, 业务键)
    if 右笔数 == 0:
        return None
    if int(待["金额分"]) == 右金额 and 待["右事件编号"] == 右代表:
        return None
    if 待["形态"] == 形态缓冲:
        _写包(conn, 业务键, 右金额, 右代表, "核乙", 时刻)
        _写左缺差(conn, 业务键, 右金额, 差说明缓, 时刻)
        _改右命运(conn, 业务键, 右缓命运)
    else:
        _写悬(conn, 业务键, 右金额, 右代表, 时刻)
        _写左缺差(conn, 业务键, 右金额, 差说明悬, 时刻)
        _改右命运(conn, 业务键, 右悬命运)
    _刷处置(conn, 业务键, 右金额, 右代表)
    return {
        "业务键": 业务键,
        "形态": 待["形态"],
        "键": 待["键"],
        "状态": 待左,
        "金额分": 右金额,
    }


def 复核(conn, 业务键: str, 时刻: str | None = None) -> dict | None:
    """已处置键的后到事件复核：保持成对差与两侧金额同步，不换形态、不改路径。"""
    时刻 = 时刻 or 此刻()
    行 = _最新行(conn, 业务键)
    if not 行 or 行["状态"] == 待左:
        return None
    左金额, _, 左笔数 = _左汇总(conn, 业务键)
    右金额, _, 右笔数 = _右汇总(conn, 业务键)
    if 左笔数 == 0 or 右笔数 == 0:
        return None
    成对 = next(
        (d for d in _差行(conn, 业务键) if d["差型"] in ("对齐", "金额不符")), None
    )
    if (
        成对
        and int(成对["左金额分"] or 0) == 左金额
        and int(成对["右金额分"] or 0) == 右金额
    ):
        return None  # 金额未变：无需复核，避免无意义改写
    差型 = "对齐" if abs(左金额 - 右金额) <= _容差(conn) else "金额不符"
    _写成对差(conn, 业务键, 左金额, 右金额, 差型, 行["路径"], 时刻)
    conn.execute(
        "UPDATE 左缺处置 SET 金额分=?, 完成时=? WHERE 编号=?",
        (右金额, 时刻, 行["编号"]),
    )
    return {
        "业务键": 业务键,
        "形态": 行["形态"],
        "路径": 行["路径"],
        "差型": 差型,
        "左金额分": 左金额,
        "右金额分": 右金额,
    }


def _仅右键(conn, 业务键集: list[str] | None = None) -> list[str]:
    sql = f"""SELECT DISTINCT 右.业务键 FROM 右轨流水 右
              WHERE 右.命运 IN ({_占位(len(右有效命运))})
                AND NOT EXISTS (SELECT 1 FROM 左轨流水 左
                                WHERE 左.业务键=右.业务键 AND 左.命运='已入左')"""
    args: list = list(右有效命运)
    if 业务键集:
        sql += f" AND 右.业务键 IN ({_占位(len(业务键集))})"
        args += list(业务键集)
    sql += " ORDER BY 右.业务键"
    return [r["业务键"] for r in conn.execute(sql, tuple(args)).fetchall()]


def 待键(conn) -> set[str]:
    """待左处置的业务键集合；模块表尚未建时为空。"""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='左缺处置'"
    ).fetchone():
        return set()
    return {
        r["业务键"]
        for r in conn.execute(
            "SELECT 业务键 FROM 左缺处置 WHERE 状态=?", (待左,)
        ).fetchall()
    }


def 入库后处置(conn, 业务键集: list[str]) -> list[dict]:
    """右轨装载事务内调用：仅右键立即择定形态，与右命运同事务、同成败。

    已有待左处置的键只刷新金额，不换形态（防悬与缓冲双落）。
    """
    if not 表.有表(conn):
        return []
    out = []
    for k in 业务键集:
        if _左汇总(conn, k)[2] > 0:
            continue
        if _右汇总(conn, k)[2] == 0:
            continue
        if _待行(conn, k):
            r = 刷新待左(conn, k, 此刻())
            if r:
                r = {**r, "刷新": True}
        else:
            r = 材料化(conn, k, 此刻())
        if r:
            out.append(r)
    return out


def 入库后回放(conn, 业务键集: list[str]) -> list[dict]:
    """左轨装载事务内调用：左到即回放对齐（悬账回正 / 缓冲回放）。"""
    if not 表.有表(conn):
        return []
    out = []
    for k in 业务键集:
        r = 收束(conn, k, 此刻())
        if r:
            out.append(r)
    return out


def 入库后复核(conn, 业务键集: list[str]) -> list[dict]:
    """右轨装载事务内调用：已处置键的后到右键只复核，不重开形态。"""
    if not 表.有表(conn):
        return []
    out = []
    for k in 业务键集:
        r = 复核(conn, k, 此刻())
        if r:
            out.append(r)
    return out


def 编排(
    conn=None, 形态: str | None = None, 业务键集: list[str] | None = None
) -> dict:
    """单边入库后的对齐等待编排：先回放已到左，再补处置仅右键，最后复核已收束键。"""
    if 形态 is not None and 形态 not in 选项:
        raise ValueError(f"形态须为 {选项}")
    own = conn is None
    conn = conn or _连()
    表.确保(conn)
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        时刻 = 此刻()
        回放 = []
        for r in conn.execute(
            "SELECT DISTINCT 业务键 FROM 左缺处置 WHERE 状态=? ORDER BY 编号", (待左,)
        ).fetchall():
            k = r["业务键"]
            if 业务键集 and k not in 业务键集:
                continue
            out = 收束(conn, k, 时刻)
            if out:
                回放.append(out)

        处置 = []
        刷新 = []
        for k in _仅右键(conn, 业务键集):
            if _待行(conn, k):
                # 已有待左：只刷新金额，单独汇报，不算新处置
                out = 刷新待左(conn, k, 时刻)
                if out:
                    刷新.append(out)
                continue
            out = 材料化(conn, k, 时刻, 形态)
            if out:
                处置.append(out)

        复核件 = []
        sql = "SELECT DISTINCT 业务键 FROM 左缺处置 WHERE 状态<>?"
        args: list = [待左]
        if 业务键集:
            sql += f" AND 业务键 IN ({_占位(len(业务键集))})"
            args += list(业务键集)
        for r in conn.execute(sql, tuple(args)).fetchall():
            out = 复核(conn, r["业务键"], 时刻)
            if out:
                复核件.append(out)
        if own:
            conn.commit()
        return {
            "时刻": 时刻,
            "回放": 回放,
            "处置": 处置,
            "刷新": 刷新,
            "复核": 复核件,
            "回放数": len(回放),
            "处置数": len(处置),
            "刷新数": len(刷新),
            "复核数": len(复核件),
            "策略形态": 形态 or 取策略(conn),
        }
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def 处置右键(业务键: str, 形态: str | None = None) -> dict | None:
    """独立调用：仅右键立即处置（供右轨装载 / 手工触发）。"""
    表.建表()
    conn = _连()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        out = 材料化(conn, 业务键, 此刻(), 形态)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 回放右键(业务键: str) -> dict | None:
    """独立调用：左到后回放（供左轨装载 / 手工触发）。"""
    表.建表()
    conn = _连()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        out = 收束(conn, 业务键, 此刻())
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------ 只读接口

def _有效性(conn, 行) -> tuple[bool, list[str], dict]:
    业务键 = 行["业务键"]
    形态 = 行["形态"]
    状态 = 行["状态"]
    原因: list[str] = []
    悬 = _悬行(conn, 业务键)
    包 = _包行(conn, 业务键)
    差 = _差行(conn, 业务键)
    命运 = _右命运集(conn, 业务键)

    有悬 = bool(悬)
    包待左 = bool(包 and 包["状态"] == 待左)
    有包 = bool(包)
    左缺行 = [d for d in 差 if d["差型"] == 左缺差型]
    成对行 = [d for d in 差 if d["差型"] in ("对齐", "金额不符")]

    if 形态 == 形态悬账:
        引用 = {"表": "悬账单", "键": 悬键(业务键), "存在": 有悬}
    elif 形态 == 形态缓冲:
        引用 = {"表": "缓冲包", "键": 包键(业务键), "存在": bool(包)}
    else:
        引用 = {"表": "", "键": 行["键"], "存在": False}
        原因.append(f"未知形态:{形态}")

    if 状态 == 待左:
        if 形态 == 形态悬账:
            if not 有悬:
                原因.append("悬行缺失")
            if 有包:
                原因.append("悬与缓冲双落")
            期望待左命运 = {右悬命运}
        elif 形态 == 形态缓冲:
            if not 包待左:
                原因.append("缓冲包缺失或已回放")
            if 有悬:
                原因.append("缓冲与悬双落")
            期望待左命运 = {右缓命运}
        else:
            期望待左命运 = set()
        if 形态 in 选项 and not 左缺行:
            原因.append("对齐差缺左缺行")
        if 形态 in 选项 and 命运 != 期望待左命运:
            原因.append(
                f"右命运={','.join(sorted(命运)) or '无'}，期望{','.join(sorted(期望待左命运))}"
            )
    else:
        if 形态 == 形态悬账:
            if 有悬:
                原因.append("回正后悬行未结")
            if 有包:
                原因.append("回正后仍有缓冲包")
            路径描述 = 路径悬账回正
        elif 形态 == 形态缓冲:
            if 包待左:
                原因.append("回放后缓冲包仍待左")
            if 有悬:
                原因.append("回放后仍有悬行")
            路径描述 = 路径缓冲回放
        else:
            路径描述 = ""
        if 形态 in 选项:
            if not 成对行:
                原因.append(f"{路径描述}后缺成对差行")
            if 命运 & {右悬命运, 右缓命运}:
                原因.append("完成后仍有待左命运")
            if 右入命运 not in 命运:
                原因.append(
                    f"右命运={','.join(sorted(命运)) or '无'}，期望{右入命运}"
                )
        if 行["路径"] and 路径描述 and 行["路径"] != 路径描述:
            原因.append(f"路径={行['路径']}，期望{路径描述}")

    return (not 原因), 原因, {
        **引用,
        "有悬行": 有悬,
        "有缓冲包": 有包,
        "缓冲包待左": 包待左,
    }


def 处置详情(业务键: str) -> dict | None:
    表.建表()
    conn = _连()
    try:
        行 = _最新行(conn, 业务键)
        if not 行:
            return None
        valid, 原因, 引用 = _有效性(conn, 行)
        悬 = _悬行(conn, 业务键)
        包 = _包行(conn, 业务键)
        return {
            "业务键": 业务键,
            "形态": 行["形态"],
            "键": 行["键"],
            "悬或缓冲键": 行["键"],
            "状态": 行["状态"],
            "路径": 行["路径"],
            "valid": valid,
            "valid原因": 原因,
            "引用": 引用,
            "有悬行": 引用["有悬行"],
            "有缓冲包": 引用["有缓冲包"],
            "缓冲包待左": 引用["缓冲包待左"],
            "金额分": 行["金额分"],
            "右事件编号": 行["右事件编号"],
            "右命运": sorted(_右命运集(conn, 业务键)),
            "处置时": 行["处置时"],
            "完成时": 行["完成时"],
            "悬行": dict(悬) if 悬 else None,
            "缓冲包": dict(包) if 包 else None,
            "对齐差": _差行(conn, 业务键),
        }
    finally:
        conn.close()


def 处置列表(业务键: str | None = None) -> list[dict]:
    表.建表()
    conn = _连()
    try:
        if 业务键:
            keys = [业务键]
        else:
            keys = [
                r["业务键"]
                for r in conn.execute(
                    "SELECT 业务键, MAX(编号) AS m FROM 左缺处置 GROUP BY 业务键 ORDER BY m DESC"
                ).fetchall()
            ]
        out = []
        for k in keys:
            行 = _最新行(conn, k)
            if not 行:
                continue
            valid, 原因, 引用 = _有效性(conn, 行)
            out.append(
                {
                    "业务键": k,
                    "形态": 行["形态"],
                    "键": 行["键"],
                    "悬或缓冲键": 行["键"],
                    "状态": 行["状态"],
                    "路径": 行["路径"],
                    "valid": valid,
                    "valid原因": 原因,
                    "有悬行": 引用["有悬行"],
                    "有缓冲包": 引用["有缓冲包"],
                    "金额分": 行["金额分"],
                    "右事件编号": 行["右事件编号"],
                    "处置时": 行["处置时"],
                    "完成时": 行["完成时"],
                }
            )
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------ 对拍 / 核对

def _核对(conn) -> tuple[dict, list[str]]:
    计数 = {
        "待悬": 0,
        "待缓": 0,
        "回正": 0,
        "回放": 0,
        "悬行右": conn.execute(
            "SELECT COUNT(*) AS c FROM 悬账单 WHERE 悬边='右'"
        ).fetchone()["c"],
        "包待左": conn.execute(
            "SELECT COUNT(*) AS c FROM 缓冲包 WHERE 状态=?", (待左,)
        ).fetchone()["c"],
        "包已回放": conn.execute(
            "SELECT COUNT(*) AS c FROM 缓冲包 WHERE 状态=?", (已回放,)
        ).fetchone()["c"],
        "右悬": conn.execute(
            "SELECT COUNT(*) AS c FROM 右轨流水 WHERE 命运=?", (右悬命运,)
        ).fetchone()["c"],
        "右缓": conn.execute(
            "SELECT COUNT(*) AS c FROM 右轨流水 WHERE 命运=?", (右缓命运,)
        ).fetchone()["c"],
    }
    问题: list[str] = []
    keys = [
        r["业务键"]
        for r in conn.execute(
            "SELECT 业务键, MAX(编号) AS m FROM 左缺处置 GROUP BY 业务键 ORDER BY m"
        ).fetchall()
    ]
    for k in keys:
        行 = _最新行(conn, k)
        if 行["状态"] == 待左:
            计数["待悬" if 行["形态"] == 形态悬账 else "待缓"] += 1
        else:
            计数["回正" if 行["状态"] == 已回正 else "回放"] += 1
        _, 原因, _ = _有效性(conn, 行)
        for e in 原因:
            问题.append(f"{k}/{行['形态']}:{e}")

    # 双落扫描：同一键同时存在右悬行与待左缓冲包
    for r in conn.execute(
        """SELECT 悬.业务键 FROM 悬账单 悬
           JOIN 缓冲包 包 ON 包.业务键=悬.业务键 AND 包.状态='待左'
           WHERE 悬.悬边='右'"""
    ).fetchall():
        问题.append(f"{r['业务键']}:悬与缓冲双落")

    # 仅右键未处置扫描（未进入本模块形态的双成功风险）
    for r in conn.execute(
        """SELECT DISTINCT 右.业务键 FROM 右轨流水 右
           WHERE 右.命运 IN ('已入右','已回正','已回放')
             AND NOT EXISTS (SELECT 1 FROM 左轨流水 左
                             WHERE 左.业务键=右.业务键 AND 左.命运='已入左')
             AND NOT EXISTS (SELECT 1 FROM 左缺处置 处
                             WHERE 处.业务键=右.业务键 AND 处.状态='待左')"""
    ).fetchall():
        问题.append(f"{r['业务键']}:仅右键未处置")
    return 计数, 问题


def 核对(conn=None) -> list[str]:
    own = conn is None
    conn = conn or _连()
    try:
        _, 问题 = _核对(conn)
        return 问题
    finally:
        if own:
            conn.close()


def 跑对拍() -> dict:
    表.建表()
    conn = _连()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        时刻 = 此刻()
        计数, 问题 = _核对(conn)
        通过 = len(问题) == 0
        说明 = (
            f"待悬{计数['待悬']}/待缓{计数['待缓']}/回正{计数['回正']}/回放{计数['回放']}"
            f" 悬行{计数['悬行右']}/包{计数['包待左']} 自洽"
            if 通过
            else "问题:" + ";".join(问题[:6])
        )
        conn.execute(
            """INSERT INTO 左缺对拍(时刻,待悬,待缓,回正,回放,右悬,右缓,是否通过,说明,问题)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                时刻,
                计数["待悬"],
                计数["待缓"],
                计数["回正"],
                计数["回放"],
                计数["右悬"],
                计数["右缓"],
                1 if 通过 else 0,
                说明,
                json.dumps(问题, ensure_ascii=False),
            ),
        )
        conn.commit()
        return {
            "时刻": 时刻,
            "是否通过": 通过,
            "说明": 说明,
            "问题": 问题,
            "计数": 计数,
            "悬引用": "悬账单（悬集）",
            "缓引用": "缓冲包（包键）",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 对拍列表(限制: int = 50) -> list[dict]:
    表.建表()
    conn = _连()
    try:
        rows = conn.execute(
            "SELECT * FROM 左缺对拍 ORDER BY 编号 DESC LIMIT ?", (限制,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["问题"] = json.loads(d.get("问题") or "[]")
            except ValueError:
                d["问题"] = []
            out.append(d)
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------ 概览 / 策略

def 卡片(当前: str) -> list[dict]:
    return [
        {
            "形态": 形态悬账,
            "标题": "当场右悬",
            "写入": "悬账单（悬边=右）+ 对齐差（左缺）；不写缓冲表",
            "读取": "结论读悬集",
            "右命运": 右悬命运,
            "左到后": 路径悬账回正,
            "选中": 当前 == 形态悬账,
        },
        {
            "形态": 形态缓冲,
            "标题": "右留缓冲物化包",
            "写入": "缓冲包（待左）+ 对齐差（左缺）；不写悬账",
            "读取": "结论读包集（读引用不同）",
            "右命运": 右缓命运,
            "左到后": 路径缓冲回放,
            "选中": 当前 == 形态缓冲,
        },
    ]


def 概览() -> dict:
    表.建表()
    conn = _连()
    try:
        当前 = 取策略(conn)
        计数, 问题 = _核对(conn)
        最近 = conn.execute(
            "SELECT * FROM 左缺对拍 ORDER BY 编号 DESC LIMIT 1"
        ).fetchone()
        return {
            "名称": "左缺右留缓冲",
            "策略": 当前,
            "策略键": 策略键,
            "选项": list(选项),
            "卡片": 卡片(当前),
            "计数": 计数,
            "一致": len(问题) == 0,
            "问题": 问题,
            "最近对拍": dict(最近) if 最近 else None,
            "说明": "仅右轨有键时立即择定悬账或缓冲形态，右轨命运同票；左到后按形态回放对齐。",
        }
    finally:
        conn.close()


def 设形态(形态: str | None) -> dict:
    if 形态 not in 选项:
        raise ValueError(f"形态须为 {选项}")
    目录.改轨规({策略键: 形态})
    return 概览()
