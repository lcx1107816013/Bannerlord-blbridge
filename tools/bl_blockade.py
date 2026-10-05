#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关隘封锁数据的**离线候选生成器**（BlBridge 工具链的可选组件）。

它在做什么
----------
MapBlockade（城池关隘 mod）的封锁数据 `map_blockades.xml` 目前只能靠人工在
`MapBlockadesEditor.exe` 里点出来：选城 → 点面片 → 画蓝线 → 绑定 → 保存。
这个脚本把「哪几块面片该被这座城封锁」这一步**算出来**，输出结构化候选 + 有效性证据，
交给 AI（或人）裁决，再把裁决结果写成 mod 能直接吃的 `map_blockades.xml`。

为什么不让 LLM 直接判断
------------------------
一张图 10408 个面片、14463 个顶点，坐标全塞进上下文既不可能也没意义 —— LLM 擅长的是
"这两个候选冲突了，谁更应该占有这个隘口"这种**语义裁决**，不是"第 8734 号面片在不在通道上"
这种几何判定。所以分工是固定的：

    几何 / 图论（本脚本，确定性、可复现）  →  候选 + 冲突 + 有效性证据
    AI / 人（语义裁决）                    →  采纳哪些、谁占冲突面片

核心判据：什么叫"真隘口"
------------------------
不是"城门旁边那几块地"，而是：**封住它之后，某些方向真的过不去了**。
所以对每座城跑一次贪心最小封锁集 —— 按"多少条通路经过它"排序往集合里加面片，
每加一块就从城门做一次 BFS（绕开已封锁面片），看有多少个邻城变得不可达。
能切断的邻城比例就是 `effect`，加满 `maxFaces` 块仍切不断的，说明那座城根本不在隘口上，
应当交给 AI 判成"不设关隘"。

设计取舍（每条都对应一个真实的坑）
----------------------------------
1. **只读**。`map_topology*.xml` / `map_paths*.xml` / `map_blockades*.xml` 全部只读，
   本脚本**从不写回** mod 的 ModuleData（输出路径必须由 `--emit-xml` 显式给）。
   理由同 `bl_sage.py`：它的产出要能被人工复核，静默覆盖是灾难。
2. **软依赖**。拓扑缺 `v` 属性（v6 之前的旧版）时**明确报错并给出修复指引**（去游戏内 MCM
   重新导出），绝不猜、绝不降级成"随便找几个面片"。
3. **面片命中要有兜底**。`map_paths` 的坐标只保留 1 位小数（`272.1`），而拓扑顶点是 2 位
   （`741.95`）⇒ 采样点**可能落在面片边界外一丝**。所以命中流程是
   "网格候选 → 点在多边形 → 失败则回退最近质心（有距离上限）"，三段缺一不可；
   只做第一段会在边界上大量漏判，只做第三段会把河对岸的面片张冠李戴。
4. **fi 与 idx 是两套编号**。XML 里 `blocked_face` 写的是引擎面片号 `fi`，
   而邻接/BFS 用的是内部下标 `idx`。两者必须成对保存、成对转换 ——
   写反了文件能存、mod 能读，但封锁的是完全错误的地块，且**没有任何报错**。
5. **输出 UTF-8**（见 `bl_common.safe_streams()`），与项目其它工具同一口径。

用法
----
    python tools/bl_blockade.py --status                       # 拓扑/通路是否可用
    python tools/bl_blockade.py --candidates --top 20          # 人读摘要
    python tools/bl_blockade.py --candidates --json cand.json  # 机读全量（喂给 AI）
    python tools/bl_blockade.py --emit-xml out.xml             # 直接落盘
    python tools/bl_blockade.py --emit-xml out.xml --adopt adopt.json
    python tools/bl_blockade.py --candidates --city 突比力斯堡  # 只看一座城

`--adopt` 的 JSON 形状（`settlement id` → 决定）：::

    {"castle_A1": {"keep": true, "faces": [10820, 10821]},
     "castle_A2": {"keep": false}}

`faces` 省略 = 采纳本脚本给出的全部候选；`keep:false` = 该城不设关隘。

命令行返回码：0 正常 / 2 输入不可用 / 1 有 MISS 或全城都算不出候选。
"""
import argparse
import json
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import bl_common  # noqa: E402

# 拓扑与通路的默认探测位置（可用 --topology / --paths 覆盖）
DEFAULT_TOPOLOGY = os.path.join("G:\\", "mods", "Tools", "map_topology_base.xml")
DEFAULT_PATHS = os.path.join("G:\\", "mods", "Tools", "map_paths_base.xml")

# 地形权重：山口、浅滩才是关隘该待的地方；平原上硬设一个关隘没有说服力。
TERRAIN_WEIGHT = {
    "Mountain": 3.0,
    "MountainGrass": 2.5,
    "Fording": 2.0,
    "Forest": 1.2,
    "Steppe": 0.8,
    "Plain": 0.5,
    "Desert": 0.5,
    "Swamp": 0.6,
    "ShallowRiver": 1.5,
    "Bridge": 3.0,
}

# 面片命中失败时的"最近质心"兜底半径（地图坐标单位）。
# 拓扑面片尺寸约 5~10，取 15 足以吃到边界外一丝的采样点，又不至于跨过一条河。
NEAREST_FALLBACK_R = 15.0

# "这块地能不能走"的占据半径。
# 拓扑里的面片**并不铺满平面**——面片之间有重建残差造成的缝（v6 回退报告自己写了
# "重建残差 ~0.05-0.09"，但相邻面片组之间还有更大的接缝）。所以"采样点没命中任何面片"
# **不等于**这里是山或水，很可能只是踩在缝上。判定"不可行走"必须带一个半径：
# 缝宽远小于它、真实的水体/山体远大于它。
OCCUPY_R = 6.0


class TopoError(Exception):
    """拓扑/通路数据不可用（缺文件、版本不对、结构缺失）。抛出即终止，不降级。"""


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
class Topo(object):
    """地图拓扑：顶点 / 面片 / 城池节点 / 城际边。"""

    def __init__(self):
        self.verts = {}          # vid(int) -> (x, y)
        self.faces = []          # idx -> {fi, cx, cy, terrain, island, poly:[(x,y)]}
        self.fi_to_idx = {}      # fi(int) -> idx
        self.nodes = []          # [{id, name, type, x, y, gate_x, gate_y, kingdom...}]
        self.node_by_id = {}
        self.edges = []          # [(from_id, to_id)]
        self.src_xml = ""

    # ---------- 载入 ----------
    @staticmethod
    def load(path):
        if not os.path.isfile(path):
            raise TopoError("拓扑文件不存在：%s" % path)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as e:
            raise TopoError("拓扑 XML 解析失败：%s（%s）" % (path, e))

        t = Topo()
        t.src_xml = os.path.basename(path)

        for v in root.findall("vertices/v"):
            t.verts[int(v.get("id"))] = (float(v.get("x")), float(v.get("y")))

        for f in root.findall("faces/face"):
            vs = f.get("v")
            if not vs:
                raise TopoError(
                    "此拓扑为旧版（面片无 v6 顶点引用）。请在游戏内 MCM「调试」组重新点击"
                    "「导出地图拓扑 XML」生成 v6 拓扑后再载入：%s" % path)
            poly = [t.verts[int(i)] for i in vs.split(",") if int(i) in t.verts]
            if len(poly) < 3:
                continue
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            t.fi_to_idx[int(f.get("fi"))] = len(t.faces)
            t.faces.append({
                "fi": int(f.get("fi")),
                "cx": cx, "cy": cy,
                "terrain": f.get("terrain") or "",
                "island": f.get("island") or "",
                "poly": poly,
            })

        for n in root.findall("nodes/node"):
            nd = {
                "id": n.get("id"), "name": n.get("name") or "",
                "type": n.get("type") or "",
                "x": float(n.get("x") or 0), "y": float(n.get("y") or 0),
                "gate_x": float(n.get("gate_x") or n.get("x") or 0),
                "gate_y": float(n.get("gate_y") or n.get("y") or 0),
                "kingdom_id": n.get("kingdom_id") or "",
                "kingdom_name": n.get("kingdom_name") or "",
                "culture_name": n.get("culture_name") or "",
            }
            t.nodes.append(nd)
            t.node_by_id[nd["id"]] = nd

        for e in root.findall("edges/edge"):
            t.edges.append((e.get("from"), e.get("to")))

        if not t.faces:
            raise TopoError("拓扑里一个面片都没有：%s" % path)
        return t


def load_paths(path):
    """解析 map_paths*.xml（扁平 <paths><path from to><pt/>…</path></paths>）。"""
    if not os.path.isfile(path):
        raise TopoError("通路文件不存在：%s" % path)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise TopoError("通路 XML 解析失败：%s（%s）" % (path, e))
    out = []
    for p in root.findall("path"):
        pts = [(float(q.get("x")), float(q.get("y"))) for q in p.findall("pt")]
        if pts:
            out.append((p.get("from") or "", p.get("to") or "", pts))
    return out


# --------------------------------------------------------------------------- #
# 几何
# --------------------------------------------------------------------------- #
def _point_in_poly(x, y, poly):
    """射线法。poly 为 [(x,y)…]，闭合由调用方保证。"""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            t = (y - yi) / float(yj - yi)
            if x < xi + t * (xj - xi):
                inside = not inside
        j = i
    return inside


class FaceIndex(object):
    """面片命中的空间网格。

    三段式：网格候选 → 点在多边形 → 最近质心兜底（见模块 docstring 取舍 3）。
    """

    def __init__(self, topo, cell=12.0):
        self.topo = topo
        self.cell = cell
        self.grid = {}
        for idx, f in enumerate(topo.faces):
            k = (int(f["cx"] // cell), int(f["cy"] // cell))
            self.grid.setdefault(k, []).append(idx)

    def hit(self, x, y, use_fallback=True, max_r=None):
        cx = int(x // self.cell)
        cy = int(y // self.cell)
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(self.grid.get((cx + dx, cy + dy), ()))
        for idx in cand:
            f = self.topo.faces[idx]
            if _point_in_poly(x, y, f["poly"]):
                return idx
        if not use_fallback:
            return None
        # 兜底：最近质心。max_r 给定时它就是"占据半径"——超出即判定为不可行走。
        lim = NEAREST_FALLBACK_R if max_r is None else max_r
        best, bd = None, lim * lim
        for idx in cand:
            f = self.topo.faces[idx]
            d = (f["cx"] - x) ** 2 + (f["cy"] - y) ** 2
            if d < bd:
                bd, best = d, idx
        return best


def build_adjacency(topo):
    """面片邻接：两块面片共享 **≥2 个顶点** 才算相邻（共享 1 个顶点只是擦角，走不过去）。"""
    by_v = {}
    for idx, f in enumerate(topo.faces):
        poly = f["poly"]
        # 用顶点坐标做键（拓扑里顶点 id 与坐标一一对应，但坐标更稳）
        for p in poly:
            by_v.setdefault(p, []).append(idx)
    share = {}
    for p, ids in by_v.items():
        n = len(ids)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = ids[i], ids[j]
                if a == b:
                    continue
                k = (a, b) if a < b else (b, a)
                share[k] = share.get(k, 0) + 1
    adj = [[] for _ in topo.faces]
    for (a, b), c in share.items():
        if c >= 2:
            adj[a].append(b)
            adj[b].append(a)
    return adj


def bfs_reachable(adj, start, blocked):
    """从 start 出发、绕开 blocked（内部 idx 集合）的可达面片集合。"""
    if start in blocked:
        return set()
    seen = bytearray(len(adj))
    seen[start] = 1
    q = deque([start])
    out = {start}
    while q:
        cur = q.popleft()
        for nb in adj[cur]:
            if not seen[nb] and nb not in blocked:
                seen[nb] = 1
                out.add(nb)
                q.append(nb)
    return out


# --------------------------------------------------------------------------- #
# 候选生成
# --------------------------------------------------------------------------- #
class Generator(object):
    def __init__(self, topo, paths, radius=40.0, max_faces=4, adj=None,
                 gap_tol=2, min_width=5.0, max_width=45.0, min_shrink=0.02,
                 offsets=(-18, -12, -6, -3, 0, 3, 6, 12, 18)):
        self.topo = topo
        self.paths = paths
        self.radius = radius
        self.max_faces = max_faces
        self.gap_tol = gap_tol
        self.min_width = min_width
        self.max_width = max_width
        self.min_shrink = min_shrink
        self.offsets = offsets
        self.index = FaceIndex(topo)
        self.adj = adj if adj is not None else build_adjacency(topo)
        # 边境性：邻居里有没有别的王国。人工数据里 **88% 的已标记城是边境城**，
        # 未标记的只有 56% —— 这是"该不该设关隘"最强的单一信号（关隘的意义是挡敌国，
        # 腹地里的城设关隘没有战略价值）。
        self.neighbors = {}
        for frm, to, _pts in paths:
            self.neighbors.setdefault(frm, set()).add(to)
            self.neighbors.setdefault(to, set()).add(frm)
        # 通路：from/to -> [(其它端, [face idx…])]
        self.seqs = {}
        for frm, to, pts in paths:
            seq = []
            for (x, y) in pts:
                fi = self.index.hit(x, y)
                if fi is not None and (not seq or seq[-1] != fi):
                    seq.append(fi)
            if not seq:
                continue
            self.seqs.setdefault(frm, []).append((to, seq))
            self.seqs.setdefault(to, []).append((frm, list(reversed(seq))))

    def gate_face(self, nd):
        return self.index.hit(nd["gate_x"], nd["gate_y"])

    def narrowest_pass(self, nd, k=6):
        """返回最窄的 k 条候选弦（可能为空 list）。

        只按几何宽度排序——**宽度小不等于封锁有效**，真正的裁决在 `candidates()` 里
        用 BFS 实测"封住之后还走得到几个邻城"来做。几何只负责把候选范围缩小到可算。
        """
        """过城门做径向扫描，找**最窄的通道**。

        判据来自编辑器 `clip_line_to_land` 的真实语义：一条合格的城墙蓝线，两端要
        **恰好抵住不可行走地块**——只有那样它才真的把通道封死，否则敌人从线外面
        绕过去，封锁形同虚设。而拓扑里**只收录可行走面片**（walkable_face_count ==
        face_count），所以"不可行走"体现为扫描线**采不到任何面片**。

        于是：过城门朝各个方向画扫描线，取包含城门的那段**连续命中区间**，它的长度
        就是该方向的通道宽度；所有方向里最窄的那个 = 这座城的关口宽度。
        窄到一定程度，这条线就是天然隘口，线扫过的面片就是该封锁的面片。
        """
        gx, gy = nd["gate_x"], nd["gate_y"]
        best = None

        def scan(deg, off, step):
            """沿 deg 方向、垂直偏移 off 的一条弦做采样，返回该弦上最窄的连续命中段。"""
            a = math.radians(deg)
            dx, dy = math.cos(a), math.sin(a)
            px, py = -dy * off, dx * off          # 垂直方向，用来把弦推离城门
            ox, oy = gx + px, gy + py
            hits = []
            t = -self.radius
            while t <= self.radius:
                hits.append((t, self.index.hit(ox + dx * t, oy + dy * t,
                                               use_fallback=True, max_r=OCCUPY_R)))
                t += step
            run = self._run_through_zero(hits)
            if run is None:
                return None
            t0, t1, idxs, lo, hi = run
            width = t1 - t0
            if width < self.min_width or width > self.max_width:
                return None
            # 弦必须**两端都抵住不可行走地块**，而不是被扫描半径截断。
            # 这一条是命门：跑满整个半径的弦说明两边都是开阔地，在那儿砌墙敌人抬腿就
            # 绕过去了 —— 那不是隘口。（"不可行走"按 OCCUPY_R 判定，见该常量的说明。）
            if lo - 1 < 0 or hi + 1 >= len(hits):
                return None
            if hits[lo - 1][1] is not None or hits[hi + 1][1] is not None:
                return None
            return {"width": width, "deg": deg, "off": off,
                    "dx": dx, "dy": dy, "px": px, "py": py,
                    "t0": t0, "t1": t1, "faces": sorted(set(idxs))}

        # 粗扫：角度 10° 一档、偏移若干档 —— 先定位最窄处的大致位置。
        # 必须搜偏移：人工数据的城墙线**不穿过城门**（墙长中位数只有 4.7，
        # 且封锁面片距城门中位数 15.3），只扫过城门的弦会完全错过真正的窄口。
        seen = []
        for deg in range(0, 180, 10):
            for off in self.offsets:
                r = scan(deg, off, 1.2)
                if r:
                    seen.append(r)
        if not seen:
            return []
        seen.sort(key=lambda r: r["width"])
        # 细扫：对最窄的前 3 条在其附近 ±10° / ±6 精修
        refined = []
        for base in seen[:3]:
            for deg in range(base["deg"] - 10, base["deg"] + 11, 2):
                for k in range(-4, 5):
                    r = scan(deg % 180, base["off"] + k * 1.5, 0.5)
                    if r:
                        refined.append(r)
        seen.extend(refined)
        # 去重（同一批面片的弦只留最窄的一条），按宽度升序返回前 k 条
        out, sig = [], set()
        for r in sorted(seen, key=lambda r: r["width"]):
            s = tuple(r["faces"])
            if s in sig:
                continue
            sig.add(s)
            out.append(r)
            if len(out) >= k:
                break
        return out

    def _zoc(self, nd, gate):
        """控制区模式：城门口通路上最贴近的几块面片（无天然窄口时的兜底）。"""
        cover = {}
        for other, seq in self.seqs.get(nd["id"], []):
            for idx in seq[:8]:
                cover.setdefault(idx, set()).add(other)
        gx, gy = self.topo.faces[gate]["cx"], self.topo.faces[gate]["cy"]
        order = sorted(cover.keys(),
                       key=lambda i: (-len(cover[i]),
                                      (self.topo.faces[i]["cx"] - gx) ** 2
                                      + (self.topo.faces[i]["cy"] - gy) ** 2))
        chosen = order[:self.max_faces]
        if not chosen:
            return {"id": nd["id"], "name": nd["name"], "ok": False,
                    "reason": "该城在通路文件里没有任何可用段落"}
        blocked = set(chosen)
        before = len(bfs_reachable(self.adj, gate, set()))
        after = len(bfs_reachable(self.adj, gate, blocked))
        nbs = self.neighbors.get(nd["id"], set())
        nb_k = set(self.topo.node_by_id[o]["kingdom_id"]
                   for o in nbs if o in self.topo.node_by_id)
        foreign = sorted(k for k in nb_k if k and k != nd["kingdom_id"])
        return {
            "id": nd["id"], "name": nd["name"], "type": nd["type"],
            "kingdom": nd["kingdom_name"], "culture": nd["culture_name"],
            "gate": [round(nd["gate_x"], 2), round(nd["gate_y"], 2)],
            "ok": True, "mode": "zoc",
            "passWidth": None, "passDegree": None, "passOffset": None,
            "outline": None,
            "isBorder": bool(foreign), "foreignKingdoms": foreign,
            "neighborCount": len(nbs),
            "openness20": sum(1 for f in self.topo.faces
                              if (f["cx"] - nd["gate_x"]) ** 2
                              + (f["cy"] - nd["gate_y"]) ** 2 <= 400.0),
            "reachBefore": before, "reachAfter": after,
            "shrink": round(1.0 - after / float(before), 3) if before else 0.0,
            "destTotal": 0, "cutCount": 0, "effect": 0.0, "cutDestinations": [],
            "chosenFaces": [self.topo.faces[i]["fi"] for i in chosen],
            "chosenIdx": chosen,
            "candidates": [{
                "fi": self.topo.faces[i]["fi"], "idx": i,
                "x": round(self.topo.faces[i]["cx"], 2),
                "y": round(self.topo.faces[i]["cy"], 2),
                "terrain": self.topo.faces[i]["terrain"],
                "pathCount": len(cover.get(i, ())),
                "neighbors": len(self.adj[i]),
                "area": round(_poly_area(self.topo.faces[i]["poly"]), 2),
                "distToGate": round(math.hypot(self.topo.faces[i]["cx"] - gx,
                                               self.topo.faces[i]["cy"] - gy), 2),
                "score": float(len(cover.get(i, ()))),
            } for i in order],
        }

    def _run_through_zero(self, hits):
        """在采样序列里找包含 t≈0 的连续命中段。返回 (t_start, t_end, [face_idx])。"""
        n = len(hits)
        mid = None
        best_abs = None
        for i, (t, fi) in enumerate(hits):
            if fi is None:
                continue
            if best_abs is None or abs(t) < best_abs:
                best_abs, mid = abs(t), i
        if mid is None:
            return None
        lo = mid
        gap = 0
        while lo - 1 >= 0:
            if hits[lo - 1][1] is None:
                gap += 1
                if gap > self.gap_tol:
                    break
            else:
                gap = 0
            lo -= 1
        hi = mid
        gap = 0
        while hi + 1 < n:
            if hits[hi + 1][1] is None:
                gap += 1
                if gap > self.gap_tol:
                    break
            else:
                gap = 0
            hi += 1
        # 去掉尾部因 gap_tol 多吃的空采样
        while lo <= hi and hits[lo][1] is None:
            lo += 1
        while hi >= lo and hits[hi][1] is None:
            hi -= 1
        if lo > hi:
            return None
        idxs = [hits[i][1] for i in range(lo, hi + 1) if hits[i][1] is not None]
        return (hits[lo][0], hits[hi][0], idxs, lo, hi)

    def candidates(self, nd):
        """为单座城算候选封锁面片 + 有效性证据。返回 dict（AI 直接吃这个）。"""
        gate = self.gate_face(nd)
        if gate is None:
            return {"id": nd["id"], "name": nd["name"], "ok": False,
                    "reason": "城门坐标没命中任何面片"}

        gx, gy = self.topo.faces[gate]["cx"], self.topo.faces[gate]["cy"]

        # ---- 先看封不封得动：目的地基准 ----
        cover = {}
        dest_face = {}
        for other, seq in self.seqs.get(nd["id"], []):
            on = self.topo.node_by_id.get(other)
            if on is not None:
                of = self.index.hit(on["x"], on["y"])
                if of is not None:
                    dest_face[other] = of
            for idx in seq[:8]:
                cover.setdefault(idx, set()).add(other)
        total = len(dest_face)

        before = len(bfs_reachable(self.adj, gate, set()))

        # ---- 1) 候选弦（几何），再用 BFS 实测每条弦的封锁效果，取最优 ----
        # 这是本工具的核心：几何只负责把搜索空间缩小到能算，
        # "这里到底是不是关隘"由"封住之后还走得到几个邻城"说了算。
        chords = self.narrowest_pass(nd, k=6)
        best = None
        for pas in chords:
            cand = pas["faces"][:self.max_faces]
            if not cand:
                continue
            blocked = set(cand)
            reach = bfs_reachable(self.adj, gate, blocked)
            cut = sorted(k for k in dest_face if dest_face[k] not in reach)
            after = len(reach)
            eff = len(cut) / float(total) if total else 0.0
            shrink = 1.0 - after / float(before) if before else 0.0
            key = (eff, shrink, -pas["width"])
            item = {"pas": pas, "chosen": cand, "blocked": blocked,
                    "effect": eff, "shrink": shrink, "after": after,
                    "cut": cut, "key": key}
            if best is None or key > best["key"]:
                best = item

        if best is None or (best["effect"] <= 0.0
                            and best["shrink"] < self.min_shrink):
            # 封哪条弦都切不断 ⇒ 退到「控制区」模式：取城门口通路上最贴近的几块面片。
            # 这不是隘口，只是"这座城挡着这一小片地"—— 人工数据里大量标记其实是这种
            # （墙长中位数 4.7、封锁面片距城门中位数 15.3，很多根本不封死任何东西），
            # 所以必须如实标成 zoc，交给 AI 决定要不要，别冒充成隘口。
            return self._zoc(nd, gate)

        pas = best["pas"]
        chosen = best["chosen"]
        blocked = best["blocked"]
        after = best["after"]
        shrink = best["shrink"]
        cut_all = best["cut"]
        reach = bfs_reachable(self.adj, gate, blocked)

        # 4) 候选明细（含评分，供 AI 裁决时参考）
        detail = []
        for idx in pas["faces"]:
            f = self.topo.faces[idx]
            w = TERRAIN_WEIGHT.get(f["terrain"], 1.0)
            area = _poly_area(f["poly"])
            detail.append({
                "fi": f["fi"],
                "idx": idx,
                "x": round(f["cx"], 2), "y": round(f["cy"], 2),
                "terrain": f["terrain"],
                "pathCount": len(cover.get(idx, ())),
                "neighbors": len(self.adj[idx]),
                "area": round(area, 2),
                "distToGate": round(math.sqrt((f["cx"] - gx) ** 2 + (f["cy"] - gy) ** 2), 2),
                "score": round((len(cover.get(idx, ())) + 1) * w * (1.0 / (1.0 + area / 60.0)), 3),
            })
        detail.sort(key=lambda d: -d["score"])

        # 5) 城墙蓝线：就是这条最窄弦的两端（编辑器里人手拖出来的那条线）
        ox = nd["gate_x"] + pas["px"]
        oy = nd["gate_y"] + pas["py"]
        p0 = (round(ox + pas["dx"] * pas["t0"], 2), round(oy + pas["dy"] * pas["t0"], 2))
        p1 = (round(ox + pas["dx"] * pas["t1"], 2), round(oy + pas["dy"] * pas["t1"], 2))

        # 6) 战略特征（给 AI 裁决"该不该设关隘"用）
        nbs = self.neighbors.get(nd["id"], set())
        nb_kingdoms = set(self.topo.node_by_id[o]["kingdom_id"]
                          for o in nbs if o in self.topo.node_by_id)
        foreign = sorted(k for k in nb_kingdoms
                         if k and k != nd["kingdom_id"])
        openness = sum(1 for f in self.topo.faces
                       if (f["cx"] - nd["gate_x"]) ** 2
                       + (f["cy"] - nd["gate_y"]) ** 2 <= 400.0)

        return {
            "id": nd["id"],
            "name": nd["name"],
            "type": nd["type"],
            "kingdom": nd["kingdom_name"],
            "culture": nd["culture_name"],
            "gate": [round(nd["gate_x"], 2), round(nd["gate_y"], 2)],
            "ok": True,
            "mode": "pass",
            # 隘口几何：宽度越小越像关口；degree 是通道走向
            "passWidth": round(pas["width"], 2),
            "passDegree": pas["deg"],
            "passOffset": round(pas["off"], 2),
            "outline": [p0, p1],
            # 战略特征：AI 裁决"该不该设关隘"主要看这两个
            "isBorder": bool(foreign),
            "foreignKingdoms": foreign,
            "neighborCount": len(nbs),
            "openness20": openness,
            # 有效性证据
            "reachBefore": before,
            "reachAfter": after,
            "shrink": round(shrink, 3),
            "destTotal": total,
            "cutCount": len(cut_all),
            "effect": round(len(cut_all) / float(total), 3) if total else 0.0,
            "cutDestinations": cut_all,
            # 结果
            "chosenFaces": [self.topo.faces[i]["fi"] for i in chosen],
            "chosenIdx": chosen,
            "candidates": detail,
        }


def _poly_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def find_conflicts(results):
    """同一块面片被多座城claimed → 这些是 AI 必须裁决的（一个面片只能归属一座城）。"""
    owner = {}
    for r in results:
        if not r.get("ok"):
            continue
        for fi in r["chosenFaces"]:
            owner.setdefault(fi, []).append(r["id"])
    return dict((str(k), v) for k, v in owner.items() if len(v) > 1)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def emit_xml(path, topo, results, adopt=None, source_xml="", outline=True):
    """写成 MapBlockade 的 map_blockades.xml（version=5）。

    两种形态都合法（已对照编辑器 save_blockades 的实现确认）：
      * `<blocked_face>` 挂在 `<outline>` 下 = 绑定到某段城墙蓝线，**会渲染城墙**；
      * `<blocked_face>` 直接挂 `<settlement>` 下 = 未绑定，封锁生效但**不画墙**。

    `outline=True`（默认）时，凡是算出了最窄弦的城（mode=pass，结果里带 `outline`
    端点）都写成前一种 —— 城墙是大地图上唯一能直观看出"这里有关隘"的东西，
    只写 blocked_face 会让整份数据在肉眼层面完全不可见。
    只有 zoc 模式（没算出窄弦，outline=None）才退到后一种。
    """
    root = ET.Element("blockades", {
        "version": "5",
        "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_xml": source_xml or topo.src_xml,
    })
    # 硬不变量：**一块面片只能归属一座城**（mod 侧规则，编辑器里点第二座城会自动转移）。
    # 这个不变量必须由**写文件这一层**兜住 —— 裁决方（AI 或人）给出的 adopt 完全可能
    # 让两座城都想要同一块面片，指望它自己想清楚是不负责任的。
    # 裁决顺序 = results 的顺序（调用方负责按优先级排好），先到先得，后来者被剔除。
    n_set, n_face = 0, 0
    owner = {}
    dropped = []
    for r in results:
        if not r.get("ok"):
            continue
        dec = (adopt or {}).get(r["id"], {})
        if dec.get("keep") is False:
            continue
        faces = dec.get("faces") or r["chosenFaces"]
        if not faces:
            continue
        seg = r.get("outline") if outline else None
        if seg:
            # 城墙蓝线：编辑器里人手拖出来的那条线，这里用算出来的最窄弦两端。
            # 坐标格式照抄编辑器产物（2 位小数、| 分隔两端点）。
            st = ET.SubElement(root, "settlement", {"id": r["id"], "name": r["name"]})
            host = ET.SubElement(st, "outline", {
                "idx": "0",
                "coords": "%.2f,%.2f|%.2f,%.2f" % (seg[0][0], seg[0][1], seg[1][0], seg[1][1]),
            })
        else:
            st = ET.SubElement(root, "settlement", {"id": r["id"], "name": r["name"]})
            host = st
        for fi in sorted(set(int(x) for x in faces)):
            if fi in owner:
                dropped.append((fi, r["id"], owner[fi]))
                continue
            owner[fi] = r["id"]
            ET.SubElement(host, "blocked_face", {"fi": str(fi)})
            n_face += 1
        if not len(st):        # 面片全被别人占了 ⇒ 这座城一个空壳，别写进去
            root.remove(st)
            continue
        n_set += 1
    _write_text(path, _pretty(root))
    return n_set, n_face, dropped


def _pretty(root):
    """ElementTree 自带缩进（py3.9+）；不带 BOM，CRLF 与编辑器产物保持一致。"""
    try:
        ET.indent(root, space="  ", level=0)
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="utf-8").decode("utf-8")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"


def _write_text(path, text):
    """UTF-8 无 BOM + CRLF 写文件（与 MapBlockadesEditor 的产物口径一致）。

    行尾用 CRLF 是刻意的：编辑器写的就是 CRLF，保持一致可以避免后面用 diff 比对时
    整文件都是改动。别用 Set-Content（PS5.1 会加 BOM）—— 详见 AGENTS.md§一。
    """
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "wb") as fh:
        fh.write(data)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv=None):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(
        description="关隘封锁候选生成器（离线，只读拓扑/通路，输出候选与 map_blockades.xml）")
    ap.add_argument("--topology", default=DEFAULT_TOPOLOGY, help="map_topology_*.xml")
    ap.add_argument("--paths", default=DEFAULT_PATHS, help="map_paths_*.xml")
    ap.add_argument("--status", action="store_true", help="只报告输入是否可用")
    ap.add_argument("--candidates", action="store_true", help="生成候选（默认动作）")
    ap.add_argument("--json", help="把全量候选写成 JSON（喂给 AI 裁决）")
    ap.add_argument("--emit-xml", help="把结果写成 map_blockades.xml")
    ap.add_argument("--adopt", help="AI 裁决 JSON（见模块 docstring）")
    ap.add_argument("--no-outline", action="store_true",
                    help="不写城墙蓝线（只写 blocked_face 挂 settlement 下）：封锁仍生效，"
                         "但大地图上看不到城墙实体。默认写出蓝线")
    ap.add_argument("--city", help="只处理这一座城（id 或名字子串）")
    ap.add_argument("--types", default="castle,town",
                    help="参与的节点类型，默认 castle,town（村庄不设关隘，"
                         "这是 MapBlockade 编辑器自己的规则）")
    ap.add_argument("--top", type=int, default=0, help="人读摘要只显示前 N 座")
    ap.add_argument("--reference", help="已有的人工 map_blockades.xml —— 只做重合度对照，"
                                       "用于验证本工具的判据，不参与生成")
    ap.add_argument("--radius", type=float, default=22.0, help="城门候选半径，默认 22")
    ap.add_argument("--max-faces", type=int, default=4, help="每座城最多封几块，默认 4")
    args = ap.parse_args(argv)
    args.types = set(t for t in args.types.split(",") if t)

    try:
        topo = Topo.load(args.topology)
        paths = load_paths(args.paths)
    except TopoError as e:
        print("输入不可用：%s" % e)
        return 2

    if args.status:
        print("topology   : %s" % args.topology)
        print("faces      : %d" % len(topo.faces))
        print("vertices   : %d" % len(topo.verts))
        print("nodes      : %d" % len(topo.nodes))
        print("edges      : %d" % len(topo.edges))
        print("paths      : %d" % len(paths))
        return 0

    t0 = time.time()
    gen = Generator(topo, paths, radius=args.radius, max_faces=args.max_faces)

    wanted = [n for n in topo.nodes if n["type"] in args.types]
    if args.city:
        q = args.city
        wanted = [n for n in wanted if q == n["id"] or q in n["name"]]
        if not wanted:
            print("没有匹配的城池：%s（类型过滤=%s）" % (q, ",".join(args.types)))
            return 2

    results = []
    for i, nd in enumerate(wanted):
        results.append(gen.candidates(nd))
        if (i + 1) % 50 == 0:
            sys.stderr.write("  …%d/%d\r" % (i + 1, len(wanted)))
            sys.stderr.flush()
    sys.stderr.write("\n")
    elapsed = time.time() - t0

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    conflicts = find_conflicts(results)

    payload = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "topology": args.topology,
        "paths": args.paths,
        "params": {"radius": args.radius, "maxFaces": args.max_faces},
        "elapsedSec": round(elapsed, 1),
        "settlementCount": len(ok),
        "skipped": [{"id": r["id"], "name": r["name"], "reason": r.get("reason")} for r in bad],
        "conflicts": conflicts,
        "results": ok,
    }

    if args.json:
        _write_text(args.json, json.dumps(payload, ensure_ascii=False, indent=2))
        print("候选 JSON → %s" % args.json)

    if args.emit_xml:
        adopt = None
        if args.adopt:
            adopt = json.loads(open(args.adopt, "r", encoding="utf-8").read())
        n_set, n_face, dropped = emit_xml(args.emit_xml, topo, ok, adopt,
                                          source_xml=os.path.basename(args.topology),
                                          outline=not args.no_outline)
        print("map_blockades → %s（%d 座城 / %d 块面片）" % (args.emit_xml, n_set, n_face))
        if dropped:
            print("剔除了 %d 块重复归属的面片（一块面片只能归一座城）：" % len(dropped))
            for fi, loser, winner in dropped[:10]:
                print("   fi=%s：%s → 归 %s" % (fi, loser, winner))
        if not adopt:
            print("注意：未给 --adopt，写的是全部候选；"
                  "建议先 --json 出候选、AI 裁决后再 --adopt 落盘。")

    # 人读摘要
    if not args.json and not args.emit_xml:
        args.candidates = True
    if args.candidates or (not args.json and not args.emit_xml):
        n_pass = sum(1 for r in ok if r.get("mode") == "pass")
        print("城池 %d 座可用（真隘口 %d / 控制区 %d）/ %d 座跳过 / 冲突面片 %d 块 / 耗时 %.1fs"
              % (len(ok), n_pass, len(ok) - n_pass, len(bad), len(conflicts), elapsed))
        ordered = sorted(ok, key=lambda r: (r.get("mode") != "pass",
                                            r.get("passWidth") or 999.0))
        if args.top:
            ordered = ordered[:args.top]
        print("%-20s %-6s %-4s %6s %-5s %5s %6s  %s"
              % ("城池", "类型", "模式", "关宽", "边境", "封块", "effect", "被切断的方向"))
        for r in ordered:
            print("%-20s %-6s %-4s %6s %-5s %5d %6.2f  %s"
                  % (r["name"][:20], r["type"], r.get("mode"),
                     ("%.1f" % r["passWidth"]) if r.get("passWidth") else "—",
                     "是" if r.get("isBorder") else "否",
                     len(r["chosenFaces"]), r["effect"],
                     ", ".join(r["cutDestinations"][:3]) or "—"))
        weak = [r for r in ok if r.get("mode") != "pass"]
        if weak:
            print("\n扫不到天然窄口的城（%d 座）—— 只给了城门口控制区面片，"
                  "要不要设关隘应由 AI/人决定：" % len(weak))
            for r in weak[:12]:
                print("   %-20s %s 边境=%s" % (r["name"], r["id"],
                                               "是" if r["isBorder"] else "否"))
        if conflicts:
            print("\n冲突面片（多座城都想要，必须裁决）：")
            for fi, owners in list(conflicts.items())[:10]:
                print("   fi=%s ← %s" % (fi, ", ".join(owners)))
        if bad:
            print("\n跳过：")
            for r in bad[:8]:
                print("   %s：%s" % (r["name"] or r["id"], r.get("reason")))

    if args.reference:
        _report_reference(args.reference, ok, wanted)

    return 0 if ok else 1


def _report_reference(path, ok, wanted):
    """与已有的人工标记做重合度对照。

    这一步**不是**生成的一部分，是给判据本身做体检：人工标了 75 座，我们的算法说
    其中多少座是真隘口、我们额外挑出了多少座人工没标的。两边差得越多，说明判据越
    可疑——这时候应该改判据，而不是把差异当成算法"更聪明"。
    """
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        print("对照文件读不了：%s" % e)
        return
    human = set(s.get("id") for s in root.findall("settlement"))
    mine = set(r["id"] for r in ok if r.get("mode") == "pass")
    pool = set(n["id"] for n in wanted)
    both = human & mine & pool
    print("\n—— 与人工标记对照（%s）——" % os.path.basename(path))
    print("人工标记 %d 座 / 算法判为真隘口 %d 座 / 交集 %d 座" % (
        len(human & pool), len(mine), len(both)))
    if mine:
        print("命中率（人工标的里算法也认）：%.0f%%" % (100.0 * len(both) / max(1, len(human & pool))))
        print("精确率（算法认的里人工也标）：%.0f%%" % (100.0 * len(both) / max(1, len(mine))))
    extra = sorted(mine - human)[:10]
    miss = sorted((human & pool) - mine)[:10]
    if extra:
        print("算法多标（人工没有）：%s" % ", ".join(extra))
    if miss:
        print("算法漏标（人工有）  ：%s" % ", ".join(miss))


if __name__ == "__main__":
    sys.exit(_main())
