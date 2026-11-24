from fastapi import APIRouter, HTTPException
from app.services import 目录, 左装载, 右装载, 对齐, 结论, 夹具, 统计, 说明
from app.modules.左缺右留缓冲 import 服务 as 左缺
from app.modules.左缺右留缓冲 import NAME as 左缺名

路由 = APIRouter()

@路由.get("/api/概览")
def api_概览():
    return 目录.概览()

@路由.get("/api/轨规")
def api_轨规():
    return 目录.轨规()

@路由.post("/api/轨规")
def api_改轨规(体: dict):
    return 目录.改轨规(体)

@路由.get("/api/源登记")
def api_源():
    return 目录.源列表()

@路由.get("/api/左轨")
def api_左():
    return 目录.左列表()

@路由.get("/api/右轨")
def api_右():
    return 目录.右列表()

@路由.get("/api/对齐差")
def api_差():
    return 目录.差列表()

@路由.get("/api/悬账单")
def api_悬():
    return 目录.悬列表()

@路由.get("/api/对拍结论")
def api_结论列表():
    return 目录.结论列表()

@路由.get("/api/夹具")
def api_夹具():
    return 夹具.列表()

@路由.post("/api/左轨/装载")
def api_左装(体: dict):
    return 左装载.装一批(体)

@路由.post("/api/右轨/装载")
def api_右装(体: dict):
    return 右装载.装一批(体)

@路由.post("/api/左轨/夹具/{name}")
def api_左夹(name: str):
    try:
        return 左装载.装一批(夹具.读取(name))
    except FileNotFoundError:
        raise HTTPException(404, name)

@路由.post("/api/右轨/夹具/{name}")
def api_右夹(name: str):
    try:
        return 右装载.装一批(夹具.读取(name))
    except FileNotFoundError:
        raise HTTPException(404, name)

@路由.post("/api/对齐/跑")
def api_对齐():
    return 对齐.跑对齐()

@路由.post("/api/对拍结论/跑")
def api_结论():
    return 结论.跑一次()


@路由.get("/api/统计/左命运")
def api_左命运():
    return 统计.左命运()

@路由.get("/api/统计/右命运")
def api_右命运():
    return 统计.右命运()

@路由.get("/api/统计/差型")
def api_差型():
    return 统计.差型分布()

@路由.get("/api/统计/悬边")
def api_悬边():
    return 统计.悬边分布()


@路由.get("/api/仓情")
def api_仓情():
    return 说明.仓情()


# ---------------------------------------------------------------- 左缺右留缓冲
# 独立主链：仅右轨有键时择定悬账或缓冲，右轨命运同票；左到后回放对齐。

@路由.get("/api/左缺/概览")
def api_左缺概览():
    return 左缺.概览()


@路由.get("/api/左缺/处置")
def api_左缺处置列表(业务键: str | None = None):
    件 = 左缺.处置列表(业务键)
    return {"件": 件, "条数": len(件)}


@路由.get("/api/左缺/处置/{key}")
def api_左缺详情(key: str):
    d = 左缺.处置详情(key)
    if not d:
        raise HTTPException(404, f"无左缺处置:{key}")
    return d


@路由.post("/api/左缺/形态")
def api_左缺形态(体: dict):
    try:
        return 左缺.设形态(体.get("形态"))
    except ValueError as e:
        raise HTTPException(422, str(e))


@路由.post("/api/左缺/编排")
def api_左缺编排(体: dict | None = None):
    体 = 体 or {}
    try:
        return 左缺.编排(形态=体.get("形态"), 业务键集=体.get("业务键集"))
    except ValueError as e:
        raise HTTPException(422, str(e))


@路由.post("/api/左缺/处置/{key}")
def api_左缺处置单(key: str, 体: dict | None = None):
    体 = 体 or {}
    try:
        r = 左缺.处置右键(key, 体.get("形态"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not r:
        raise HTTPException(409, f"不可处置（左已到或无右键）:{key}")
    return r


@路由.post("/api/左缺/回放/{key}")
def api_左缺回放(key: str):
    r = 左缺.回放右键(key)
    if not r:
        raise HTTPException(409, f"不可回放（无待左处置或左未到）:{key}")
    return r


@路由.post("/api/左缺/对拍")
def api_左缺对拍():
    return 左缺.跑对拍()


@路由.get("/api/左缺/对拍")
def api_左缺对拍列表():
    return 左缺.对拍列表()


# 同一组只读/动作接口的全名别名：题面按模块目录名探测时亦可命中。
@路由.get("/api/左缺右留缓冲/概览")
def api_全名概览():
    return 左缺.概览()


@路由.get("/api/左缺右留缓冲/处置")
def api_全名处置列表(业务键: str | None = None):
    件 = 左缺.处置列表(业务键)
    return {"件": 件, "条数": len(件)}


@路由.get("/api/左缺右留缓冲/处置/{key}")
def api_全名详情(key: str):
    d = 左缺.处置详情(key)
    if not d:
        raise HTTPException(404, f"无左缺处置:{key}")
    return d


@路由.post("/api/左缺右留缓冲/形态")
def api_全名形态(体: dict):
    try:
        return 左缺.设形态(体.get("形态"))
    except ValueError as e:
        raise HTTPException(422, str(e))


@路由.post("/api/左缺右留缓冲/编排")
def api_全名编排(体: dict | None = None):
    体 = 体 or {}
    try:
        return 左缺.编排(形态=体.get("形态"), 业务键集=体.get("业务键集"))
    except ValueError as e:
        raise HTTPException(422, str(e))


@路由.post("/api/左缺右留缓冲/对拍")
def api_全名对拍():
    return 左缺.跑对拍()


@路由.get("/api/左缺右留缓冲/对拍")
def api_全名对拍列表():
    return 左缺.对拍列表()


@路由.get("/api/空桩")
def api_空桩():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "modules"
    out = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").is_file():
                out.append(p.name)
    return {"空桩": out, "条数": len(out), "已实现": [左缺名]}

