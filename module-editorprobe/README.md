# BannerSage.EditorProbe — 编辑器探针（游戏端）

游戏内模块，为 Archive 的 **6 个 `ba_editor_*` 工具**提供此前缺失的**游戏端后端**。
它监听 `127.0.0.1` 上一个**带 token 的单请求 JSON-lines** 端点，动作在主线程执行。

---

## 1. 它是什么，不是什么

| | |
|---|---|
| **是** | 骑砍进程内的 C# 模块，模块 id **`BannerlordSage.EditorProbe`**（**这个字符串不可改** —— Archive 客户端硬编码它） |
| **是** | Archive 编辑器客户端的对端：客户端的 `EDITOR_PROBE_ACTIONS` 15 个动作，这里逐条实现 |
| **不是** | BlBridge 的一部分（见 §2） |
| **不是** | MCP 工具。那 6 个 `ba_editor_*` **属于 Archive**，本模块只是它们的后端 |

---

## 2. 为什么不并进 `BlBridge.dll`

| | BlBridge | 本模块 |
|---|---|---|
| 编译期引用 | `bin/Win64_Shipping_Client` | `bin/Win64_Shipping_wEditor`（**Modding Kit**） |
| 第三方依赖 | **零**（SubModule 只依赖 Native/SandBoxCore/Sandbox/CustomBattle） | 硬依赖 Kit 装配 |

**合并的代价**：让零依赖的 Bridge 反过来依赖 Kit —— 拿**已能用的 79 个工具**去赌一个打包观感。
**所以：同仓、两个独立 `SubModule.xml`。** 没装 Kit 时本模块加载失败，**BlBridge 完全不受影响**
（实测：`build.ps1` 用非递归 `Get-ChildItem`，看不见 `src/EditorProbe/` 子目录；两个构建互不影响）。

**"可被其它项目调用"为什么不写进 `DependedModules`**：骑砍**不支持可选模块依赖**，
写了就变硬依赖。用**软依赖**表达 —— 照抄 BlBridge 的 `tools/bl_sage.py` 先例：
查得到就用，查不到如实报 `available=false`，**绝不阻断宿主**。

---

## 3. 契约（来自 Archive 客户端，逐条对齐）

```
请求 : {"token":"<会话 token>","action":"<动作>","args":{...}}\n
应答 : {"ok":true,"result":{...},"mainThread":N,"executedThread":M}\n
```

**15 个动作**：`status` `enter_editor` `open_scene` `entities` `place_probe` `move_probe`
`save_scene` `close_scene` `quit` `capture` `apply_layout` `layout_entities` `focus` `camera` `prefab_info`

### 会话文件（客户端逐字段校验，缺一即 `invalid_session`）

路径：`Modules/BannerlordSage.EditorProbe/editor_probe_session.json`

```json
{ "gameDir": "...", "outputDir": "...", "sceneDir": "...", "token": ">=8", "port": 1..65535 }
```

客户端另外**在磁盘上**核对：

- `sceneDir` 必须等于 `<gameDir>/Modules/BannerlordSage.EditorProbe/SceneObj/<sceneName>`
- `<sceneName>` 必须匹配 `^[a-zA-Z][a-zA-Z0-9_]{0,79}$`
- `bin/Win64_Shipping_Client/Version.xml` **与** `bin/Win64_Shipping_wEditor/Version.xml` **都**要是 `v1.4.8`

**这些条件在 `EditorProbeConfig.Initialize()` 里逐条断言**。理由：客户端任一条件不满足都只报
笼统的 `invalid_session`，**看不出是哪一个** —— 所以失败必须在这里就**响亮地说出是哪条**。

---

## 4. ★ 三条踩过的坑（都写进了代码注释）

### 4.1 引擎类型的 `!= null` 会抛异常

TaleWorlds 的 `Scene`/`Mission`/`MetaMesh`/`SceneView`/`GameEntity` 继承
`TaleWorlds.DotNet.NativeObject`，它**重载了 `==` / `!=`** 去比较原生指针，
而运算符要过**静态构造函数**、那个构造函数需要**引擎的原生函数表**。

⇒ 写 `if (scene != null)` **不是引用比较**；在游戏没托管这段代码时（离线测试、任何引擎起来之前的诊断路径）
会抛 `TypeInitializationException`。**这跟 null 检查的目的正好相反**：它本该是让"这里没有游戏"这条路**安全**的东西，
结果它自己就是抛异常的那个。

修法：所有引擎类型的 null 判断走 **`EngineNull.IsNull/IsNotNull`**（内部是 `ReferenceEquals`，
`System.Object` 的静态方法，**绑不到重载运算符**）。
（`x ?? y` 是**安全**的 —— `??` 发直接 IL null 判断，不走运算符。只有 `==`/`!=` 有这个问题。）

### 4.2 `MatrixFrame` 没有 `scale` 字段

缩放存在 **`Mat3` 的基向量**（`s`/`f`/`u`）里。所以缩放是**乘基向量**，不是赋一个分量：

```csharp
rotation.s = rotation.s * sx;
rotation.f = rotation.f * sy;
rotation.u = rotation.u * sz;
return new MatrixFrame(rotation, new Vector3...);   // 旋转用 Mat3.ApplyEulerAngles（**弧度**）
```

读回来用 `GetScaleVector()`。

### 4.3 模块 DLL 不在模块根目录

Bannerlord 从 **`Modules/<id>/bin/Win64_Shipping_Client/`** 加载程序集。
放模块根目录**静默不加载**。已对**本机每一个已装模块**核对，也和 BlBridge 自己一致。
`build-editorprobe.ps1 -Deploy` 现在写正确位置，并**删掉此前误放的根目录副本**。

---

## 5. 构建 / 部署 / 自检

```bash
# 编译（需要 Modding Kit，Steam app 1393600）
powershell -ExecutionPolicy Bypass -File .\build-editorprobe.ps1

# 编译 + 部署到 Modules/
powershell -ExecutionPolicy Bypass -File .\build-editorprobe.ps1 -Deploy

# 离线自检（63 项，不需要游戏在跑）
powershell -ExecutionPolicy Bypass -File .\tools\editorprobe-test\run.ps1
```

**没有 Kit 时**构建脚本会明确说"本模块需要 Steam app 1393600"并退出 2，
同时**指出 BlBridge 不受影响** —— 而不是给一个费解的错误。

---

## 6. ★ 已证明的 vs 尚未证明的

**离线断言 63/63 通过**，覆盖：JSON 解析/转义、15 个动作的对齐、
session 文件的**每一项客户端门禁**、**完整 TCP 往返**（含 `mainThread == executedThread` 这一条）、
**拒绝路径**（错 token / 未知动作 / 坏 JSON / 无编辑器时的特权动作）、以及 `status` 在无编辑器时**不抛且如实回答**。

**反向验证**（证明套件"能红"）：注入两类 bug 都**红得干净** ——
① 让 `executedThread` 报 socket 线程（`main=4 exec=3`）→ FAIL；
② 让未知动作被接受 → FAIL。恢复后重新全绿。

### 尚未证明的（**不假装完成**）

`enter_editor` / `close_scene` / `quit` **需要游戏真的在跑**，本机无法离线验证，**因此不算已验证**。
它们依赖 `MBEditor.EnterEditMode`，其签名穿过**内部** `[ScriptingInterfaceBase]` 接口，
所以走**反射**调用（这也让本文件在别的游戏版本上仍能**编译**）。绑定失败会**明确报出成员名**，
**不会伪造成功**。

其余动作（`status` `entities` `layout_entities` `apply_layout` `save_scene` `prefab_info` `capture`）
建立在**已从反编译源码核对过**的 API 上（`Scene.GetName/GetEntities/AddEntityWithMultiMesh/Read/SetOwnerThread`、
`MetaMesh.GetCopy`、`Utilities.TakeScreenshot`）。
`save_scene` 与 `capture` 的**引擎侧行为**仍未在运行中的编辑器里观察过。

---

## 7. 与主 Bridge 的关系

- `bl_capability_probe` **会报告本模块的状态**：`editorProbe.installed` / `moduleExists` / `dllExists`，
  并把它计入 `editorReady` 与 `blockers`。装好之后**不用改一行代码**，`editorReady` 就变成 `yes`。
- 客户端（Archive）取会话文件路径的方式是环境变量 **`BANNERSAGE_EDITOR_PROBE_SESSION`**，
  指向 `Modules/BannerlordSage.EditorProbe/editor_probe_session.json`。
