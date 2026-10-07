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

> **更新于 2026-10-07 后段（B1/B2/B4/B5/B6/B7 全部完成 + 坏数据扫描已部署）**

| 项 | 值 |
|---|---|
| **可以 `-Deploy` 吗** | ✅ **可以**。工作区已无半成品；已部署 `dll sha256 = F10558B390467E1F...`（272 KB） |
| 会话 A 当前在做什么 | 坏数据扫描（已实现 + 离线对照 38/38；**真机待验**）|
| 未提交改动 | 见 `git status`（本轮：`BadDataSpec.cs`/`BadDataScanner.cs`/`tools/baddataspec/` 等）|
| `src/BadDataSpec.cs` | **已从 21,181 B 半成品变为完整判据层**（就是 B6 里那个 `changedSources`）|

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

## 三、给会话 B 的**警告**：暂时不要 `build.ps1 -Deploy`

会话 A 现在有**未完成的新增文件** `src/BadDataSpec.cs`：

- 它是**判据层**（纯函数），**还没有被任何 method 调用**；
- 部署它会**无害但无用**（多一个未被引用的类型）；
- 真正的问题是：**会话 A 接下来还要改 `bl_mcp.py` 接线**，
  若在中间态部署，游戏里跑的就是**半成品**。

⇒ **等到本文件 §一 的"工作区未提交文件"清空（或明确标注"可部署"）再 `-Deploy`。**

**会话 B 的现有用法不受影响**：你测的是 RBM 的 DLL，
BlBridge 的 DLL 只是工具 —— 但**若你要用 `bl_read_config` / `bl_apply_config`，
本轮的 B1 修复正好让它从"静默骗你"变成"明确报错"**（见下）。

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
| 1 | **B2 修复**：战役内 `allowAnyState=true` 直接拒绝（或先测 `Game.Current.GameType is Campaign`），并把"战役内会崩"写进参数说明 | 测试表 B2 |
| 2 | `stale_source` 文案区分"改的是 BlBridge 还是别的项目" + 附 `changedSources` 的 mtime | 测试表 B6 |
| 3 | `bl_patches` 描述加"**在册 ≠ 生效**；`Prepare` 返回 false 的补丁不会出现在这里" | 测试表 B4 |
| 4 | `bl_read_config` 增加 `path` 参数（让调用方指定配置文件） | 测试表 B1 建议② |
| 5 | B5 复核：`sessionDiagnosis.verdict` 与 `buildCheck` 并列是否易误读 | 测试表 B5 |
| 6 | 坏数据扫描（`BadDataSpec.cs` 已就位，缺数据访问层 + method + MCP 工具） | 用户需求 |

---

## 六、纪律（两边共用）

1. **改完前别 `-Deploy`**；改完在 §一 更新状态牌。
2. **看到 `changedSources` 里有自己没改的文件 ⇒ 先查是不是另一个会话**（B6 教的）。
3. **提交前跑全量闸门**（`AGENTS.md` §三），尤其 `check_repo_encoding.py` 与
   `bl_check_dispatch.py`（后者会抓 toolCount 漂移）。
4. 用户用测试表沟通 ⇒ **修复完成后，回写到测试表的状态列**。
