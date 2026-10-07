# tools/data/ —— 为什么这里是空的

这个目录**故意不包含数据文件**。不是漏了，是刻意。

## 背景

`bl_lexicon` 是一个**崩溃词典读取器**：按异常类型或一段崩溃文本，匹配出人话描述、
常见场景与修复建议 —— 补的是 `bl_crash --deep` / `bl_exceptions` 只能给符号栈、
说不出「该怎么办」的那一层。

词条数据本身（57 条异常翻译 / 84 条诊断规则 / 43 条已知问题，含中英简繁）派生自
一份**反编译源码树**，其上游作品**没有任何许可声明**（`LICENSE` / `COPYING` / `NOTICE` 全无）。

## 为什么不入库

无许可声明 ⇒ 著作权法默认「**保留所有权利**」。这**不是**公有领域、**不是**开源。

| | 上游许可 | 能否随本 MIT 仓公开再分发 |
|---|---|---|
| 本仓库抄录的 BUTR/Bannerlord.GABS、pardeike/GABP | **MIT** | ✅ 能（保留版权行即可，见 `LICENSE` / `NOTICE`） |
| 崩溃词典的上游 | **无（默认保留全部权利）** | ❌ **不能** |

⇒ 所以本仓库只放**读取器**（`tools/bl_lexicon.py`），**不放数据**。
`.gitignore` 里有对应护栏（`/tools/data/lexicon/`），防止一次 `git add -A` 误提交。

> ⚠️ 别把这两件事混为一谈：`LICENSE` + `NOTICE` 已正确处理了 MIT 上游的义务，
> **不能**据此类推"这个数据也能发"。MIT 才给的权利，无声明不给。

## 怎么用

把数据放在**仓库之外**，用环境变量指过去：

```powershell
$env:BLBRIDGE_LEXICON_DIR = "D:\某处\lexicon"
python tools\bl_lexicon.py --status                          # 先确认装没装上
python tools\bl_lexicon.py --type NullReferenceException     # 按异常类型查
python tools\bl_lexicon.py --text "access violation 0xC0000005"
```

目录里需要四个文件：`exceptions.json` / `diagnostic-rules.json` / `known-issues.json` / `meta.json`。

## 缺数据时的行为（刻意设计）

`bl_lexicon` **不会**在缺数据时返回空结果冒充「没有匹配」。它会：

- 报 `installed: false` + **可读的原因**（目录不存在／缺哪个文件）；
- CLI 退出码 **1**；
- 文本输出里明说「数据不随本仓库分发」以及怎么装。

理由：把「没装数据」混淆成「没有匹配」，会让调用方以为"这次崩溃没问题"——
这比报错危险得多。

## 口径（读结论前先看）

- 匹配是**短语子串**（`MatchAny` 任一命中 / `MatchAll` 全部命中 / `ExcludeAny` 命中即排除），
  **不是语义匹配** ⇒ **匹配不到不等于没问题**，只说明这份有限词条里没有对得上的短语。
- `priority` 只用于排序，**不是置信度**。
- 词条 `zh` 缺失时会回退英文，并**如实标注** `langFellBackToEn` —— 不拿英文冒充中文。
- 词条里的 `sourceRepairAction`（如 `smartRepair`）是**上游模组自己的修复按钮**，
  BlBridge **没有**对应动作 ⇒ 只作信息，**不是可执行动作**。
