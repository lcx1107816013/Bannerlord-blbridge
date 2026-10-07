# -*- coding: utf-8 -*-
"""RTSCamera 配置的读写（B 方案：我们只当它的"参数管理员"，不碰它的代码）。

为什么不去抄 RTSCamera（用户提过"做进我们的 mod"）：
  * 分发包里**没有许可文件**（只有 CHANGELOG/README，且 README 只讲功能），许可只在 Nexus 页面；
    默认按"保留所有权利"处理 ⇒ 抄 DLL/反编译代码进我们要发布的包 = 侵权风险。
    功能可以自己实现，代码不能抄。
  * 体量上它是 267 KB IL + 自带 MissionLibrary/AgentComponent + 60~70 个配置项 + 自己的 MCM 页面 +
    战帆船只接管 —— 全抄等于重写一个中型 View/输入 mod，维护从此归我们。
  * 而用户要的东西（攻城也能有抬升上帝视角）**只需要改它的配置**就够了。
    实测（2026-09-25）：改这个 XML → **下一场战斗就生效**（RTSCamera 每场开始读一次配置），
    但它运行中会**把自己的配置覆写回去**（我们改的 ElevatedHeightInSiege=10 后来又被写回 0）
    ⇒ 所以"持久设置"要在游戏内 MCM 里改；而我们这套的定位是**开战前按需套用**。

配置文件形状（实测，非推测）：
  (user Documents)\\Mount and Blade II Bannerlord\\Configs\\RTSCamera\\RTSCameraConfig.xml
  扁平结构：<Key>value</Key>，UTF-8 **带 BOM**（原始文件 BOM=True，写回时必须保留）。

与 Warbandlord 那套（`bl_apply_config`）同纪律：形状校验 → 拒绝 XML 特殊字符 → 备份 →
写临时文件 → XML 解析校验 → 原子替换 → 回读核对。
"""

import io
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET

CONFIG_REL = os.path.join("Mount and Blade II Bannerlord", "Configs", "RTSCamera", "RTSCameraConfig.xml")

# 官方/社区文档都没写全的枚举，只标"已知值"，未知值只警告不拒绝（宁可放行 + 提醒，也不假装知道）
ENUM_HINTS = {
    "DefaultToFreeCamera": ["DeploymentStage", "Always", "Never"],
    # ⚠️ TriggerMode 只证实了 WhenOpeningOrderUI；Always/Never 这两个串在同 DLL 里存在，
    #    但没有证据表明本键接受它们 —— 所以标为未证实，写别的值时给警告。
    "ElevatedCameraTriggerMode": ["WhenOpeningOrderUI"],
}

# 预设：都是"我们实测过/可解释"的键，不猜新键
PRESETS = {
    "siege-god": {"ElevatedHeightInSiege": "10"},
    "free-always": {"DefaultToFreeCamera": "Always"},
    "elevated-always": {"ElevatedCameraTriggerMode": "Always"},
    "god-full": {"ElevatedHeightInSiege": "10",
                 "DefaultToFreeCamera": "Always",
                 "ElevatedCameraTriggerMode": "Always"},
}

_LINE_RE = re.compile(r"^(\s*)<([A-Za-z_][\w.\-]*)>(.*?)</\2>\s*$")


def documents_dir():
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Documents"),
                 os.path.join(home, "OneDrive", "Documents")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(home, "Documents")


def config_path():
    """RTSCamera 配置文件路径（可用 BLBRIDGE_RTS_CONFIG 覆盖，便于离线自测）。"""
    override = os.environ.get("BLBRIDGE_RTS_CONFIG")
    if override:
        return override
    return os.path.join(documents_dir(), CONFIG_REL)


def _read_text(path):
    raw = io.open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig") if bom else raw.decode("utf-8")
    return text, bom


def read(paths=None, path=None):
    """读全部键（或只读指定键）。返回 dict；缺失的键值为 None。"""
    path = path or config_path()
    if not os.path.isfile(path):
        raise IOError("找不到 RTSCamera 配置: %s" % path)
    text, _bom = _read_text(path)
    root = ET.fromstring(text.encode("utf-8"))
    out = {}
    for child in root:
        out[child.tag] = (child.text or "").strip()
    if paths:
        return dict((p, out.get(p)) for p in paths)
    return out


def apply(edits, dry_run=False, allow_missing=False, path=None):
    """edits: [{"key": "ElevatedHeightInSiege", "value": "10"}, ...]

    纪律与 Warbandlord 的 apply_config 一致（见模块 docstring）。`path` 只为离线自测而存在。
    """
    path = path or config_path()
    if not os.path.isfile(path):
        raise IOError("找不到 RTSCamera 配置: %s" % path)

    if not isinstance(edits, list) or not edits:
        raise ValueError('edits 必须是**非空数组**，形如 '
                         '[{"key": "ElevatedHeightInSiege", "value": "10"}]')
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or "key" not in e or "value" not in e:
            raise ValueError('edits[%d] 必须是 {"key": "...", "value": "..."} 形式的对象，实得：%r' % (i, e))
        v = e["value"]
        if isinstance(v, bool) or v is None or str(v).strip() == "":
            raise ValueError('edits[%d] 的 value 不能为空/布尔（key=%s）' % (i, e["key"]))
        if any(c in str(v) for c in "<>&\"'\n\r"):
            raise ValueError('edits[%d] 的 value 含 XML 特殊字符（< > & " \' 或换行），'
                             '会破坏配置文件（key=%s）' % (i, e["key"]))

    text, bom = _read_text(path)
    lines = text.splitlines()

    # ★★ 行尾风格必须**保留原样**（2026-10-08 修；与 bl_mcp.apply_config 同一缺陷）。
    #   实测：真实 `RTSCameraConfig.xml` 是 **CRLF + BOM**（3343 B / 57 个 CRLF）；
    #   旧写路径无条件用 "\n" 拼回 ⇒ **静默把整个文件改成 LF-only**，
    #   而返回里只说"改了 N 个键" ⇒ **报告与事实不符**。
    _raw = io.open(path, "rb").read()
    _n_crlf = _raw.count(b"\r\n")
    _n_lf = _raw.count(b"\n") - _n_crlf
    if _n_crlf > 0 and _n_lf == 0:
        line_ending, newline = "CRLF", "\r\n"
    elif _n_crlf == 0 and _n_lf > 0:
        line_ending, newline = "LF", "\n"
    elif _n_crlf == 0 and _n_lf == 0:
        line_ending, newline = "none", "\n"
    else:
        line_ending = "mixed"
        newline = "\r\n" if _n_crlf >= _n_lf else "\n"

    info = {}
    for idx, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m:
            info[idx] = (m.group(2), m.group(3).strip())

    want = dict((e["key"], str(e["value"])) for e in edits)
    changed, missing, warnings = {}, [], []
    new_lines = []
    for idx, line in enumerate(lines):
        if idx in info:
            key, oldval = info[idx]
            if key in want:
                m = _LINE_RE.match(line)
                new_lines.append("%s<%s>%s</%s>" % (m.group(1), key, want[key], key))
                changed[key] = {"old": oldval, "new": want[key]}
                hints = ENUM_HINTS.get(key)
                if hints and want[key] not in hints:
                    warnings.append("%s：新值 %r 不在已知取值 %s 里 —— 该键的合法值未经证实，请留意"
                                    % (key, want[key], "/".join(hints)))
                if re.match(r"^-?\d+(\.\d+)?$", oldval or "") and not re.match(r"^-?\d+(\.\d+)?$", want[key]):
                    warnings.append("%s：旧值 %r 是数字、新值 %r 不是 —— 确认该键允许非数值" % (key, oldval, want[key]))
                continue
        new_lines.append(line)

    for k in want:
        if k not in changed:
            missing.append(k)

    result = {"path": path, "changed": changed, "missing": missing, "dryRun": bool(dry_run)}
    if warnings:
        result["warnings"] = warnings
    if dry_run:
        return result
    if missing and not allow_missing:
        return dict(result, ok=False,
                    error="以下配置键不存在，未写入任何修改: %s（要新建键请显式传 allowMissing=true）" % ", ".join(missing))

    backup = path + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup)

    # ★ 按原文件行尾拼回（不是硬编码 "\n"）。
    body = newline.join(new_lines)
    if _raw.endswith(b"\n"):
        body += newline
    data = body.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    tmp = path + ".tmp"
    with io.open(tmp, "wb") as fh:
        fh.write(data)

    try:
        ET.parse(tmp)
    except Exception as exc:  # noqa: BLE001
        os.remove(tmp)
        return dict(result, ok=False, error="XML 校验失败，已放弃写入: %r" % (exc,))
    os.replace(tmp, path)

    check = read(list(want.keys()), path=path)
    # ★ 写后**字节级**事实（与 bl_mcp.apply_config 同口径）：行尾/BOM 是否守住、字节增量。
    _after = io.open(path, "rb").read()
    _a_crlf = _after.count(b"\r\n")
    _a_lf = _after.count(b"\n") - _a_crlf
    if _a_crlf > 0 and _a_lf == 0:
        after_ending = "CRLF"
    elif _a_crlf == 0 and _a_lf > 0:
        after_ending = "LF"
    elif _a_crlf == 0 and _a_lf == 0:
        after_ending = "none"
    else:
        after_ending = "mixed"
    out = dict(result, ok=True, backup=backup, verified=check)
    out["fileFacts"] = {
        "lineEndingBefore": line_ending,
        "lineEndingAfter": after_ending,
        "lineEndingPreserved": (line_ending == after_ending),
        "bomBefore": bool(bom),
        "bomAfter": _after.startswith(b"\xef\xbb\xbf"),
        "bytesBefore": len(_raw), "bytesAfter": len(_after),
        "bytesDelta": len(_after) - len(_raw),
        "crlfBefore": _n_crlf, "crlfAfter": _a_crlf,
        "note": ("`changed` 只说**键值**改了什么；本字段说**文件本身**改了什么 —— "
                 "两者必须一起看（防『只改 1 个键却重写整个文件行尾』这类报告与事实不符）。"),
    }
    if not out["fileFacts"]["lineEndingPreserved"]:
        out["warnings"] = (out.get("warnings") or []) + [
            "⚠️ 行尾风格**未保持**（%s → %s）" % (line_ending, after_ending)]
    return out


def apply_preset(name, dry_run=False, path=None):
    """按预设名写配置。返回 apply() 的结果；未知预设名抛 ValueError。"""
    if name not in PRESETS:
        raise ValueError("未知预设: %s（可用: %s）" % (name, ", ".join(sorted(PRESETS))))
    edits = [{"key": k, "value": v} for k, v in sorted(PRESETS[name].items())]
    out = apply(edits, dry_run=dry_run, path=path)
    out["preset"] = name
    return out
