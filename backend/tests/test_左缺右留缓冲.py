import os
import sqlite3
import threading
from pathlib import Path

_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.services import 左装载, 右装载, 对齐, 结论, 目录
from app.modules.左缺右留缓冲 import 服务 as 左缺

K = "ORD-900"


def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()
    目录.改轨规({"左缺形态": "悬账"})


def _右(键=K, 金额=1000, 编号="R1", 时="2026-11-23T10:00:01Z"):
    return 右装载.装一批(
        {
            "事件": [
                {
                    "事件编号": 编号,
                    "业务键": 键,
                    "金额分": 金额,
                    "源键": "核乙",
                    "事件时": 时,
                }
            ]
        }
    )


def _左(键=K, 金额=1000, 编号="L1", 时="2026-11-23T10:00:00Z"):
    return 左装载.装一批(
        {
            "事件": [
                {
                    "事件编号": 编号,
                    "业务键": 键,
                    "金额分": 金额,
                    "源键": "渠甲",
                    "事件时": 时,
                }
            ]
        }
    )


def test_悬形态仅右入库立即有悬无缓冲():
    r = _右()
    assert r["左缺处置"], r
    d = 左缺.处置详情(K)
    assert d["形态"] == "悬账"
    assert d["状态"] == "待左"
    assert d["有悬行"] is True
    assert d["有缓冲包"] is False
    assert d["悬或缓冲键"] == "悬-" + K
    assert d["右命运"] == ["右悬"]
    assert d["valid"] is True, d["valid原因"]
    # 只读接口给出 business key / 悬或缓冲键 / valid
    assert d["业务键"] == K and d["valid"] is True


def test_缓冲形态仅右入库立即有包无悬():
    目录.改轨规({"左缺形态": "缓冲"})
    _右()
    d = 左缺.处置详情(K)
    assert d["形态"] == "缓冲"
    assert d["有悬行"] is False
    assert d["有缓冲包"] is True
    assert d["缓冲包待左"] is True
    assert d["悬或缓冲键"] == "缓-" + K
    assert d["右命运"] == ["右留缓冲"]
    assert d["valid"] is True, d["valid原因"]


def test_再左同键两边均可对齐但路径不同():
    # 悬形态：左到后悬账回正
    _右()
    _左()
    d = 左缺.处置详情(K)
    assert d["状态"] == "已回正"
    assert d["路径"] == "悬账回正"
    assert d["valid"] is True, d["valid原因"]
    assert [x["差型"] for x in d["对齐差"]] == ["对齐"]
    assert not d["有悬行"]

    # 缓冲形态：新键左到后缓冲回放
    目录.改轨规({"左缺形态": "缓冲"})
    _右(键="ORD-901", 编号="R2")
    _左(键="ORD-901", 编号="L2")
    d2 = 左缺.处置详情("ORD-901")
    assert d2["状态"] == "已回放"
    assert d2["路径"] == "缓冲回放"
    assert d2["valid"] is True, d2["valid原因"]
    assert [x["差型"] for x in d2["对齐差"]] == ["对齐"]
    # 两边都对齐，但路径与读引用不同
    assert d["路径"] != d2["路径"]
    assert d["悬或缓冲键"].startswith("悬-")
    assert d2["悬或缓冲键"].startswith("缓-")


def test_待左期间不得悬与缓冲双落():
    _右()
    d = 左缺.处置详情(K)
    assert d["有悬行"] and not d["有缓冲包"]
    # 策略切到缓冲后再来同键右键：沿用原形态，不改形、不双落
    目录.改轨规({"左缺形态": "缓冲"})
    _右(金额=1200, 编号="R2")
    d = 左缺.处置详情(K)
    assert d["形态"] == "悬账"
    assert d["有悬行"] is True
    assert d["有缓冲包"] is False
    assert d["valid"] is True, d["valid原因"]
    assert 左缺.核对() == []


def test_处置并发不得双落():
    _右()
    d = 左缺.处置详情(K)
    键 = d["悬或缓冲键"]
    assert 键 in ("悬-" + K, "缓-" + K)
    # 清空后双线程按不同形态抢处置，只允许一个落地
    db = _tmp / "对拍.db"
    c = sqlite3.connect(db)
    c.execute("DELETE FROM 左缺处置 WHERE 业务键=?", (K,))
    c.execute("DELETE FROM 缓冲包 WHERE 业务键=?", (K,))
    c.execute("DELETE FROM 悬账单 WHERE 业务键=?", (K,))
    c.execute("DELETE FROM 对齐差 WHERE 业务键=?", (K,))
    c.execute("UPDATE 右轨流水 SET 命运='已入右' WHERE 业务键=?", (K,))
    c.commit()
    c.close()

    out = []
    b = threading.Barrier(2)

    def 抢(形态):
        b.wait()
        try:
            左缺.处置右键(K, 形态)
            out.append(形态)
        except Exception as e:  # 允许输家报错，但不得双落
            out.append(f"err:{type(e).__name__}")

    ts = [threading.Thread(target=抢, args=(m,)) for m in ("悬账", "缓冲")]
    [t.start() for t in ts]
    [t.join() for t in ts]

    c = sqlite3.connect(db)
    悬 = c.execute("SELECT COUNT(*) FROM 悬账单 WHERE 业务键=?", (K,)).fetchone()[0]
    包 = c.execute(
        "SELECT COUNT(*) FROM 缓冲包 WHERE 业务键=? AND 状态='待左'", (K,)
    ).fetchone()[0]
    处置 = c.execute(
        "SELECT COUNT(*) FROM 左缺处置 WHERE 业务键=? AND 状态='待左'", (K,)
    ).fetchone()[0]
    c.close()
    assert 悬 + 包 == 1, (悬, 包, out)
    assert 处置 == 1, (处置, out)
    d = 左缺.处置详情(K)
    assert d["valid"] is True, d["valid原因"]
    assert not (d["有悬行"] and d["有缓冲包"])


def test_基线对齐不覆盖模块留痕():
    _右()
    _左()
    左缺.处置详情(K)
    对齐.跑对齐()
    d = 左缺.处置详情(K)
    assert d["状态"] == "已回正"
    assert d["valid"] is True, d["valid原因"]
    g = 结论.跑一次()
    assert g["是否通过"] is True, g


def test_编排补处置与回放():
    _右()
    左缺.处置详情(K)
    r = 左缺.编排()
    assert r["回放数"] == 0
    _左()
    r = 左缺.编排()
    d = 左缺.处置详情(K)
    assert d["状态"] == "已回正"


def test_模块对拍与卡片():
    _右()
    p = 左缺.跑对拍()
    assert p["是否通过"] is True, p["问题"]
    o = 左缺.概览()
    assert o["策略"] in ("悬账", "缓冲")
    形态集 = {c["形态"] for c in o["卡片"]}
    assert 形态集 == {"悬账", "缓冲"}
    悬卡 = next(c for c in o["卡片"] if c["形态"] == "悬账")
    缓卡 = next(c for c in o["卡片"] if c["形态"] == "缓冲")
    assert "悬账单" in 悬卡["写入"] and "不写缓冲表" in 悬卡["写入"]
    assert "缓冲包" in 缓卡["写入"] and "不写悬账" in 缓卡["写入"]
    assert 悬卡["读取"] != 缓卡["读取"]
