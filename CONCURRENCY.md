# ⚠️ 共享仓库并发约定（两个 DSH 会话）

> **背景**：同一份 BlBridge 仓库被**两个会话**同时使用：
> - **会话 A（BlBridge 开发）** —— 改 `src/*.cs`、`tools/*.py`
> - **会话 B（三合一 MOD 测试）** —— 用 BlBridge 的工具测 RBM，一般不碰 BlBridge 源码
>
> 用户用 `E:\Document\BlBridge-缺陷记录-测试阶段.md` 做两边的沟通媒介。
>
> **本文件是给两个会话的并发约定 + 当前状态牌。**

---

## 一、当前状态牌（会话 A 维护，改完请更新）

> **更新于 2026-10-10（战役观察者轮：工具 54→76 + CrashGuard 记账缺口；**已部署**）**
> ⚠️ **本节由 2026-10-10 会话（记为会话 A'）重写** —— 原状态牌停留在 2026-10-08，
> 而其后已有多次部署与提交（见 `git log`）。**状态牌是声明不是事实**（这条教训此前已记过一次）。

### ✅ 本轮改动**已 `-Deploy`** —— 现役 DLL 为 `7492E6218274E62E`

| 项 | 值 |
|---|---|
| **可以 `-Deploy` 吗** | ✅ **已部署且一致**。现役 DLL `7492E6218274E62E`（328.5 KB）＝ 仓库源码构建产物；`bl_check_dispatch.py` **79/79 通过**、`bl_selftest.py` **全绿** |
| **本轮交付** | **战役事件观察者**（9 个 `CampaignEvents` 处理器，事件流**非轮询**）+ **6 个刺激器**（宣战/和平/时间流速/存档/回主菜单）+ **3 个遭遇对话工具** + **战役模式判定**（`isSandbox`/`isStoryMode`/`campaignType`）+ **CrashGuard 记账缺口修复** |
| **工具数** | **54 → 79**（`module/mcp/manifest.json` 的 `toolCount` 已同步为 79）|
| ★ **两个真 bug 已修** | ① **dup-key**：`"type"` 撞名 ⇒ 同一 JSON 两个同名键 ⇒ 事件类型被静默改写成 `town`/`castle`（101 行中招）；② **`index` 参数**发成字符串 ⇒ `Jmini.Num` 只扫裸数字读不到（**同 `multiplier` 的坑，本项目已重犯两次**）|
| ★★ **CrashGuard 记账缺口** | 两段式记账（回调入队 + `Drain()` 下一帧写盘）在**"吞完同一 tick 就崩"**时丢账 ⇒ 修复前 `swallowed=0`（**看起来像守卫没干活**）。改为**决定性事件立即落盘**（`Jw.TryWriteGuardLog` 返回 `bool`）。**A/B 验收：0 条 → 20 条 + 1 条熔断** ✅ |
| **契约测试** | `bl_selftest.py` 新增 **⑯ 组**（7 项）锁住上述形态；**注入回归已验证会变红并精确点名** |

### ⚠️ 本轮给会话 B 的两条**环境提醒**（不是 BlBridge 的改动）

1. **`Modules\RBM\` 是 Vortex 的符号链接部署** —— 实测 136 个链接**全部可读出内容**，
   但 **PowerShell 报 `Length=0` 是"链接自身"的显示特性、不代表空文件**。
   ⇒ 判"部署成功/失败"**必须 `ReadAllBytes` 实读**，不能只看 `Length`/`Get-Item`。
2. **`DiplomacyModel` 被 `BellumCivile` 覆盖** ⇒ 该 mod 组合下 `MakePeace` **不可达**
   （DLL 含 `BlockWhitePeacePatch`/`CivilWarMakePeacePatch`）⇒ **别把它当 BlBridge 的工具缺陷**。

### ⚠️ `bl_selftest.py` 现有 **2 项环境相关失败**（★ 非本轮引入，如实标注）

`A4b ★真机: RBM 真档两种形式的计数与独立正则吻合` / `A4b ★真机: 要改的三个 RBM 开关读得到`

**根因**：这两条判据读 **真档** `Documents\…\Configs\RBM\config.xml`，并要求
**形式②（`<Option …/>`）与形式①（`<Tag>V</Tag>`）都非空**。
而 Vortex 于 **2026-10-10 23:15:58 重装 RBM** ⇒ 该文件被**重置为出厂态**：
形式② = **0**、形式① = 206（`<Warbandlord>` 段与那三个开关**都不在了**）。

**已核实与本轮改动无关**（三条独立证据）：
① 本轮对 `bl_selftest.py` 的 diff **不落在 A4b 区域**（改动在 91 / 1418 / 2638+，A4b 在 ~2051）；
② 直接用**独立正则**核对文件内容 ⇒ `n_opt=0`，**与任何代码版本无关**；
③ 本轮新增的 **⑯ 组 7/7 全绿**。

★ **这条判据本身有隐含前提："游戏跑过一次"**（形式②由游戏运行时写入）。
⇒ 建议后续把它改成 **「文件存在则核对自洽、不满足前提则报 `[ENV]` 而非 FAIL」**（尚未实施）。

### ★★ B11：探测失败被报成"已死"（与 B8/B9/B10 同族：报告与事实相反）

`tools/bl_mcp.py` 的 `_pid_alive()` 只检查 `stdout`、**不看 `returncode`**：

```python
out = subprocess.run([...tasklist...], stdout=PIPE, ...).stdout   # 非 0 退出时 stdout 是空的
return ('"%d"' % n) in out                                        # ⇒ 返回 False（= 已死）
```

**实测**（本机 `tasklist` 被安全策略拦成 `ERROR: Access denied`，rc=1、stdout 空）：

```
_pid_alive(一个明明活着的进程) = False     ← 应为 True
```

**消费点 `bl_mcp.py`**（等待循环）：`_pid_is_game(pid) is False` ⇒ **立刻 `_drop_pending()` 并报
`process_exited`** ⇒ **游戏在跑，控制通道却把每个请求当"进程已退出"作废**（本该防幽灵战斗的
`_drop_pending` 反向误伤）。

**修复**：非 0 退出 ⇒ 回 `None`（**未知**，归因码走 `probe_unknown`）；`_pid_image_name` 同修；
探针抽成可注入的 `_tasklist_query()`。

**★★ 连带面（本轮最值得记的一条）**：`build_check()` 里原本只在 `alive is False` 时早退，
`None` 只加一条 note **然后继续往下比**。修好"失败 ⇒ None"后这条分支**第一次真的可达** ⇒
会对**根本没在跑**的游戏报 `game_running_other_build`（"进程内 DLL 与磁盘不同"）。
已改成与"已退出"同级早退（带 `probeUnknown: true`）。
⇒ **教训（B8 同款）：修正/放宽一处闸门 = 打开一条以前锁着的路，必须回头看那条路上有没有旧洞。**

### ★ 备份不再被静默删除（`build.ps1`）

原"只留最新 3 份"按 **mtime** 剪枝，而**文件名时间戳 = 部署时刻、mtime = DLL 构建时刻**（两者错开）
⇒ 04:04:52 那次部署把 `bak_20261008_003506` 与 `bak_20261008_004143` **一起删掉**，
而这两份正是下面那张旧表里"`scan_bad_data` = None"的**唯一反例样本** ⇒ 结论**无法就地复算**。
⇒ 现改为 **归档**到 `<模块>\bak_archive\`（保留 3 份在旁 + 其余入档，逐份报 sha256，**不覆盖**同名）
★ **纪律：被引用的证据样本必须留存，不能让构建脚本回收它。**

> ### ⚠️ 下面这张 00:45 的备份表**已整体过期**（保留原文以示"曾经记过什么"）
> 实测现状：`.bak_...003506` 与 `.bak_...004143` **在盘上已不存在**；现存 3 份备份是
> `004523`(`F10558B390467E1F`) / `033242`(`4EC10B4F6CDEBAE1`) / `040452`(`94F721E47FC1B16F`)，
> 且当时标为"现役"的 `4EC10B4F` **现在只是备份** —— 现役是 `31B97ED6`（286720 B @ 04:04:52）。
> ⇒ 结论层不受影响，但**这张表的行不能再当事实引用**。

---

<details>
<summary><b>▼ 以下是 <code>36938c8</code> 那一轮的旧状态牌（历史，勿当现状引用 —— 尤其是"未提交改动：无"与"strict-drift exit=0"两条）</b></summary>

| 项 | 值 |
|---|---|
| **可以 `-Deploy` 吗** | ✅ **已部署**（2026-10-08 04:04:52）。**新 DLL sha256 = `31B97ED6DB2AE7FA…`，286720 B**。★ 部署时 48 个副本 `.py` **全部与仓库一致**（漂移已消除） |
| 会话 A 当前在做什么 | 本轮**全部交付完成**：A5 判据 / A3 物品全队伍 / A4+A4b config `path` 与双格式 / R1 `bl_get_perk` / **B8+B9+B10 三条写路径缺陷修复** |
| 提交 | **`36938c8`**（主体）+ **`8e97e23`**（状态牌）+ **`d0d9bd8`**（推送状态），工作区 clean。★ **已推送成功**：`032b58c..d0d9bd8 main -> main`，**`main` 与 `origin/main` 同步（ahead 0）**。（推送前曾连续 3 次 `Failed to connect to github.com:443`，网络恢复后重推即成功。）|
| 未提交改动 | **无**（`git status` 干净）|
| ★ **部署状态自检** | `bl_check_deploy_consistency.py` → **全部通过**（A/B/C/D 四段）；`--strict-drift` → **exit=0**（48 个副本一致）|
| ★ **全局闸门** | `bl_selftest.py` / `bl_check_dispatch.py`(+`--selftest`) / `bl_check_gabp_names.py --selftest` / `bl_patches_selftest.py` / `bl_metrics_selftest.py` / `baddataspec/run_selftest.py`(52/52) / A5(含 `--selftest`) / `check_repo_encoding.py` ⇒ **10/10 绿** |

</details>

> ★ 上面那轮**唯一仍然有效**的结论是：`31B97ED6` 那份 DLL 与**它自己那套**源码/清单一致
> （A5 至今复跑仍全过）。其余（提交号、`strict-drift exit=0`、"未提交改动：无"）**都已被本轮取代**。

### ★★ 三条写路径缺陷（B8/B9/B10）—— 上一轮最重要的交付，**已修 + 已部署 + 已提交**

共同点：**报告与事实不符**（不会崩，只会让人得出**与磁盘相反**的结论）。
详见 `docs/config-tool-safety.md`（三起事故档案）与 `E:\Document\BlBridge-缺陷记录-测试阶段.md`（B8/B9/B10）：

| # | 缺陷 | 危害 | 回归断言 |
|---|---|---|---|
| **B8** | `apply_config` 成功分支返回**裸 dict**（派发解包 `ValueError`）| ★★★ 派发报错，**但文件已写盘**（异常在返回**之后**）⇒ 以为失败、实际已改。★ HEAD 里就有，但**长期不可达**（无 `path` 前总走 B1 错误分支），**由 A4 的 `path=` 激活** | **8 条派发级**断言 + 返回形状断言 |
| **B9** | 写回**无条件 LF** ⇒ 静默把 CRLF 改成 LF-only（真档 `CRLF=1007 → 0`）| ★★ 只报"改了 N 个键"，实际**全文行尾被重写** | 写前探测原行尾并原样拼回；`fileFacts`；**CRLF 不变 + LF 不变双向断言** |
| **B10** | ★ `bl_apply_rts_config` **不转发 `path`** | ★★★ 传副本 ⇒ **实际写真档**（A 线自测踩中，已逐字节还原）| ★ 通用断言 `assert_param_retargets()`（**双向**：副本变 + 默认真档 sha 不变）|

★ 由此立的**项目级纪律**：**凡"新增参数"必配"参数被转发"的断言** ——
不能只测"函数接受这个参数"，要测"这个参数**真的改变了目标**"。

### ✅ 本轮交付明细（已部署，`dllSha256 = 31b97ed6db2ae7fa…`）

| 项 | 内容 | 判据入口 |
|---|---|---|
| **A3** | `bl_scan_bad_data` 物品扫描扩到**全队伍 + 全聚落**（含与 `ItemRoster` 并列的 `Settlement.Stash`）；新增 **`coverage`**（正常覆盖口径，与 `skipped` 异常分开）+ finding 带来源 `@party/…`/`@stash/…` | `baddataspec/run_selftest.py`（**52/52**）。★ 真机（B 线代跑）：**1918 个 roster / 11201 个条目**，与清理器独立实现逐个对上 |
| **A4 / A4b** | `bl_read_config` / `bl_apply_config` 加 **`path`**：**严格只读**（不碰任何 mod 的 parse 函数）、回显绝对路径 + `pathSource`、写前备份、写后回读、`restartRequired`、`fileFacts`；指定不存在路径**明确报错不回退**。★ 支持 RBM 的**两种格式并存**（`<Option>` 468 + `<Tag>` 215）：形式①路径用**祖先标签名**（`Enabled` 四重碰撞已区分），**写回保持原格式** | `bl_selftest.py`（A4 11 条 + A4b 11 条）。★ 真机：`keyCount={total:689,option:468,bare:221}`，三个目标开关均读得到 |
| **R1** | 🆕 **`bl_get_perk`**：只读运行时 Perk 值（C7 那 6 个），Primary/Secondary **分开返回**；★ 判据必须是**三态** | `bl_selftest.py`（16 条 R1 断言）。★ **真机 V2 PASS 6/6**（B 线代跑）：`_oneHandedWayOfTheSword` P/S 两向都变、`_bowDeadshot` Primary **恒 0.002**（本就不该变）|

★ **R1 的关键发现（写进工具描述与自测了）**：反编译实测 6 个 Perk 的**原版 Primary 全是 0.002、Secondary 全是 0.005**，
而 C7 对 Primary/Secondary 赋**同一个**目标值 ⇒ `_bowDeadshot`/`_crossbowMightyPull`/`_throwingUnstoppableForce` 的
**Primary 本来就不该变**（原版 0.002 == 目标 0.002）。按"两次必须不同"判会把**正确**判成工具无效。

★ **部署后**：`bl_get_perk` / `perk_not_found` / `get_perk_failed` 三个符号已确认在 DLL 内（双编码并集判定）。

### ★ 部署一致性：已核（2026-10-08，**取代旧状态牌的哈希**）

**⚠️ 旧状态牌写的 `dll sha256 = F10558B390467E1F...`（272 KB）已不准** —— 实测那已不是现役 DLL，
而是备份文件 `bin\...\BlBridge.dll.bak_20261008_004523`。**现役 DLL 是 `4EC10B4F`**：

| 文件（`Modules\BlBridge\bin\Win64_Shipping_Client\`） | 字节 | mtime | sha256（前 16） |
|---|---|---|---|
| **`BlBridge.dll`（现役）** | **279552** | **2026-10-08 00:45:23** | **`4EC10B4F6CDEBAE1`** ✅ 与清单一致 |
| `BlBridge.dll.bak_20261008_003506` | 265728 | 00:29:55 | `A55AA4530F66D699` |
| `BlBridge.dll.bak_20261008_004143` | 265728 | **00:35:06** | `706A591D5AFC033A` ← 即旧记录里"00:35:06 那份" |
| `BlBridge.dll.bak_20261008_004523` | 278528 | 00:41:43 | `F10558B390467E1F` ← 即旧状态牌写的那个哈希 |

**三条已核**（判据可复现：`python tools\bl_check_deploy_consistency.py`）：

1. **`dllSha256` 一致**：现役 DLL `4ec10b4f6cdebae1…` == `build_manifest.json` 的 `dllSha256`（`dllBytes: 279552`，
   `builtUtc: 2026-10-07T16:45:23Z`）。
2. **源码 0 差异**：清单 `sources` 的 **47 个哈希 vs 磁盘 `src/` = 0 个不一致**，且清单里没有磁盘上不存在的文件。
3. **关键符号齐全**：现役 DLL 内 `get_hero` / `scan_bad_data` / `IsCampaignActive` / `unsupported_in_campaign`
   **全部命中**（★ **两种编码取并集**：`#Strings`=UTF-8 存类型/字段/方法名，`#US`=UTF-16LE 存字面量）。

#### ★ 旧结论"部署 DLL 不含 `get_hero` ⇒ 需重新部署"= **已证伪（UTF-8-only 假阴性）**

那条结论**本身就是 `ab13ec9` 那条教训的又一次复发**：

- 在 DLL 里搜 ASCII/UTF-8 形式的 `get_hero` **必然搜不到** —— 它是**字符串字面量**，存在 **`#US` 堆（UTF-16LE）**；
- 实测（两种编码分别判定）：
  | 文件 | `get_hero` | `scan_bad_data` | `IsCampaignActive` | `unsupported_in_campaign` |
  |---|---|---|---|---|
  | **现役 `4EC10B4F`** | `utf16` ✅ | `utf16` ✅ | `utf8` ✅ | `utf16` ✅ |
  | `.bak_...003506` | `utf16` ✅ | **`None`** ❌ | `utf8` ✅ | `utf16` ✅ |
  | `.bak_...004143`（00:35:06） | `utf16` ✅ | **`None`** ❌ | `utf8` ✅ | `utf16` ✅ |
- ⇒ 00:35:06 那份**含 `get_hero`**；它真正**缺的是 `scan_bad_data`**（坏数据扫描，`9da2fc2` 引入）。
- ⇒ **结论：无部署阻塞。** 现役 DLL 构建于 00:45:23，**晚于** `e28b6f1`(00:37:33)，且符号齐全。

#### 部署副本 vs 仓库 `tools/` 的漂移（如实记录）

| 文件 | 字节 | mtime | sha256（前 16） | `_dll_has_utf8`（双编码修正） |
|---|---|---|---|---|
| 仓库 `tools\bl_mcp.py` | 259258 | 01:11:33 | `F77EB33DCC9992C5` | ✅ 有 |
| 部署 `Modules\BlBridge\mcp\bl_mcp.py` | 247455 | 00:40:08 | `8B49C5D6635DC20D` | ❌ **无** |

- 部署副本**缺 `0020355` + `ab13ec9`**（即**内容级/双编码核验**那两个提交）。
- ★ **后果**：**若从部署副本跑 `bl_build_check`，用的是"只搜 UTF-16"的旧逻辑** ⇒ 会**对类型名/字段名假阴性**。
- ★ **不阻塞现场**：本会话的实际工具链走**仓库 `tools/`**（见下）。

#### ★ 现场层走哪个（已核）

`C:\Users\LCGX\.dsh\profiles\desktop\cordis.patch.yml`：

- `mcp-blbridge`（独立挂载）**`disabled: true`**；
- 活的是 **`mcp-chain`** → `python E:\Document\bannerlord-mcp-suite\bl_chain.py`；
- `bl_chain.py:160` `BLBRIDGE_DIR = ...\CodeBuddy\20260923171333\BlBridge`，
  `bl_chain.py:177` `args = [BLBRIDGE_DIR, "tools", "bl_mcp.py"]`
  ⇒ **现场层跑的是仓库 `tools/bl_mcp.py`（最新）**，不是部署副本。
- 旁证：`chain_status` 的 `blbridge` 上游 = **64 个工具**，`connected: true`。

> ⇒ **两条链均不阻塞**：C# 侧（DLL）已最新且符号齐全；Python 侧走仓库 `tools/`，也是最新。
> ⇒ **但部署副本的漂移是真的**，会误导"从 `Modules\` 跑 build_check"的人 —— 已知，暂不重新部署（改完前不 `-Deploy`）。

### 已修完的（对隔壁测试表的回写口径）

| # | 状态 |
|---|---|
| **B1** | ✅ 已修 + 自测（4 条判据含反向对照）|
| **B2** | ✅ **真机验证通过**（战役内 `unsupported_in_campaign` + 进程存活；对照 `false` ⇒ `wrong_state`）|
| **B3** | ✅ 既有纪律（按 PID 核对）|
| **B4** | ✅ `bl_patches` 返回加 `scope`（**在册 ≠ 生效**；`Prepare()` 返 false 不出现）|
| **B5** | ✅ 复核结论：**非矛盾，是作用域不同** ⇒ 返回加 `scopes`；刻意不加互斥提示 |
| **B6** | ✅ `changedSourcesDetail` 附 `mtimeUtc`/`sizeBytes` + 写明"`stale_source` 永远是 BlBridge 自己" |
| **B7** | ✅ `bl_get_hero` 已实现 + **真机验证**；★ 真机抓到并修了 2 个 bug（同伴静默漏报 1→4、`stringId` 双重引号）|

> ⚠️ **旧状态牌的"B1 未提交、暂不要 Deploy"已作废** —— 那是 B1 修复进行中的中间态记录，
> 隔壁会话据此提出的疑问是对的。现已全部提交/验证，可安全部署。


---

## 二、关于 B6 的澄清（重要）

测试表 B6 写：

> 改动文件 `BadDataSpec.cs`（21174 B，**23:39:00** 创建）**不是本次工作产生的** ——
> 疑似**并发写入**（其他会话/工具在同一源码树工作）。

**结案：那不是"异常并发"，而是会话 A（本会话）正在写的文件。**

- 逐字吻合：**21,174 B / 23:39:00**
- 用途：坏数据扫描器的**判据层**（纯 BCL，可离线单测）

⇒ **B6 的"疑似并发写入"应改为"跨会话协作（已知）"。**
但 B6 的**做法完全正确**：`buildCheck` 报出"有我没改过的文件" ⇒ 先确认再决定重编。
**这条护栏这次真的起了作用**，建议保留并强化（B6 自己的建议）。

### B6 附带建议：`changedSources` 旁附 mtime

会话 A **认同**，但**本轮不实现**（改动面在 `BuildInfo`/`bl_mcp` 的状态链路，
与当前 B1 修复不是同一处，混在一起会让 diff 难审）。
记在这里，作为待办。

---

## 三、给会话 B 的提示：`-Deploy` 的现状（★ 本条已订正，2026-10-08）

> **订正**：本节原先的标题是"**警告**：暂时不要 `build.ps1 -Deploy`"，
> 理由是"`src/BadDataSpec.cs` 是**未完成**的新增文件、还没有被任何 method 调用"。
> **该理由已过期**：`BadDataSpec.cs` 已是**完整判据层**且**已被接线**
> （`bl_scan_bad_data` 已实现并真机验证 13/13）。
> ⇒ **§一 状态牌为准：可以 `-Deploy`**，且**当前无需**重新部署（见 §一 的部署一致性三条已核）。

**会话 B 的现有用法不受影响**：你测的是 RBM 的 DLL，
BlBridge 的 DLL 只是工具 —— 但**若你要用 `bl_read_config` / `bl_apply_config`，
B1 修复已让它从"静默骗你"变成"明确报错"**（见下）。

> ⚠️ **仍然保留的一条纪律**（与部署无关，别混淆）：**改完前别 `-Deploy`**（§六 第 1 条）。
> 现在的状态是"**改完了**"，所以可以部署；只是**没必要**重新部署（现役产物已是最新且已核）。

**★ 另外提醒（A5 发现）**：`Modules\BlBridge\mcp\bl_mcp.py` **确实漂移**（缺 `0020355`/`ab13ec9`）。
若**从部署副本**（`Modules\BlBridge\mcp\`）跑 `bl_build_check`，用的是**旧的"只搜 UTF-16"**逻辑
⇒ 会**对类型名/字段名假阴性**。**要核源码链请从仓库 `tools\` 跑**
（这也正是它自己的 `sourceCheck: skipped_no_src_dir` 在提示的事）。

---

## 四、会话 A 已完成的修复：B1

| 项 | 内容 |
|---|---|
| 症状 | `bl_read_config` / `bl_apply_config` 硬编码读 `Modules\Warbandlord\config.xml`，而该模块**无 SubModule.xml、0 DLL**（已软卸载），只剩 31,721 B 残留档 ⇒ **无消费者** |
| 后果 | 改它 = "改了没生效" ⇒ **把工具问题误判成 MOD 问题** |
| 修法 | ① 检测模块是否真装好（`SubModule.xml` 或 ≥1 DLL）；② 不满足则**明确拒绝**；③ 返回里说清**操作的是哪个模块** |
| 实测 | 两个工具现在都返回 `ok:false` + `error:"target_module_not_installed"` + `evidence:{hasSubModuleXml:false, dllCount:0}` |
| 对应测试表 | B1 的三条建议①③已做；②（加 `path` 参数）**未做**，见待办 |

---

## 五、待办（会话 A）

| # | 项 | 来源 |
|---|---|---|
| 1 | ✅ **已完成**（`4f9b00a`）：战役内 `allowAnyState=true` 直接拒绝（`IsCampaignActive()`），并已写进参数说明 | 测试表 B2 |
| 2 | ✅ **已完成**：`changedSourcesDetail` 附 `mtimeUtc`/`sizeBytes` + 写明"`stale_source` 永远是 BlBridge 自己" | 测试表 B6 |
| 3 | ✅ **已完成**：`bl_patches` 返回加 `scope`（"**在册 ≠ 生效**；`Prepare` 返回 false 不出现"） | 测试表 B4 |
| 4 | ⏳ **本轮做**（A4）：`bl_read_config` / `bl_apply_config` 增加 `path` 参数 | 测试表 B1 建议② |
| 5 | ✅ **已完成**：B5 复核结论为**作用域不同（非矛盾）** ⇒ 返回加 `scopes`，刻意不加互斥提示 | 测试表 B5 |
| 6 | ✅ **已完成**（`9da2fc2`）：坏数据扫描 `bl_scan_bad_data`（只读）+ 真机验证 13/13 | 用户需求 |
| 7 | 🆕 ⏳ **本轮做**（A3）：坏数据扫描的**物品扫描扩到全队伍 + 聚落**（现只覆盖玩家队伍 `ItemRoster`） | 日志 §8 未做项 |
| 8 | ✅ **本轮做**（A5）：`tools/bl_check_deploy_consistency.py` —— 部署产物↔源码一致性**机器判据**（含反向对照 + 双编码并集） | 本轮假阻塞的复盘 |
| 9 | ⏳ **待清理（已知漂移）**：`Modules\BlBridge\mcp\bl_mcp.py` 落后于仓库 `tools\bl_mcp.py`；下次部署会自然消除 | A5 发现 |

---

## 六、纪律（两边共用）

1. **改完前别 `-Deploy`**；改完在 §一 更新状态牌。
2. **看到 `changedSources` 里有自己没改的文件 ⇒ 先查是不是另一个会话**（B6 教的）。
3. **提交前跑全量闸门**（`AGENTS.md` §三），尤其 `check_repo_encoding.py` 与
   `bl_check_dispatch.py`（后者会抓 toolCount 漂移）。
4. 用户用测试表沟通 ⇒ **修复完成后，回写到测试表的状态列**。
