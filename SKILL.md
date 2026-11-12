---
name: legal-tools-searcher
description: 独立检索 ICC Legal Tools Database（legal-tools.org），支持匿名关键词检索、官方文书编号精确定位、元数据与索引摘要查看，以及按 slug 或官方编号下载 PDF 并提取全文页文本。用于查找 ICC、ICTY、ICTR、IRMCT、ICRC、TOAEP 等 Legal Tools 馆藏中的判例、裁定、书状和法院文件。
---

# LegalToolsSearcher

> **`<skill-dir>`** = 本 SKILL.md 所在目录。核心脚本为 `<skill-dir>/scripts/legal_tools_search.py`；Windows 用 `<skill-dir>/run.ps1`，macOS/Linux 用 `<skill-dir>/run.sh`。部署与排错见 [README.md](README.md)。

这个 skill 只做 ICC Legal Tools 一件事：

- `search`：调用公开 JSON API 检索索引元数据和文书正文索引，返回题名、官方编号、案件号、案件名、法院/机构、文书类型、日期、页数、语言标记、权限字段、索引 highlight 和稳定链接；
- `document`：按官方文书编号（`externalId`）精确调用 `findOne`，先确认目录记录、slug、PDF 链接和权限字段，不下载全文；
- `fetch`：按 `/doc/<slug>/` URL、字母数字 slug 或官方文书编号下载 PDF，提取逐页文本，可另存 PDF 与带 `@@PAGE n@@` 页码标记的纯文本；
- `doctor`：检查 Python、PDF 文本后端、Legal Tools 检索 API 和 PDF 路由。

Legal Tools 是公开匿名来源，不需要广外 VPN、`gdufs-auth-session`、浏览器、Cookie 或 API key。

## 先分清三层结果

1. `search` 的 `results[].result_type=retrieved_metadata` 是目录记录和索引片段，**不是全文**；
2. `document` 只确认官方编号对应的记录和链接，不证明 PDF 已取得；
3. `fetch` 只有 `status=ok`、`pdf.sha256` 和逐页文本同时存在时，才表示取得 PDF 并完成文本提取。扫描件可能出现 `pdf.needs_ocr=true`。

`documentUrl` 和 `pdfURL` 在 API 中可能写成 `http://`；适配器统一输出 `https://`。`languageIds` 是不透明 ID，不能据其反推语言名称；`isAuthoritativeLanguage` 只说明该版本本身的属性。

## 命令

```powershell
# 关键词检索；结果是元数据与 highlight，不是全文
& '<skill-dir>/run.ps1' search 'command responsibility' --limit 10 --out './legal-tools-search.json'

# 精确短语 / 全词 AND / 任一词 OR / NEAR 邻近检索
& '<skill-dir>/run.ps1' search 'command responsibility' --match phrase --limit 10
& '<skill-dir>/run.ps1' search 'command responsibility' --match near --near-distance 5 --limit 10

# 用案件号、机构和文书类型收窄
& '<skill-dir>/run.ps1' search 'effective control' --organisation icc --case-number 'ICC-01/04-01/06' --document-type judgement --limit 10

# 官方文书编号精确定位，不下载 PDF
& '<skill-dir>/run.ps1' document 'ICC-01/04-01/12-1-Red' --out './mudacumura-document.json'

# 按官方编号下载 PDF，同时保存页文本
& '<skill-dir>/run.ps1' fetch 'ICC-01/04-01/12-1-Red' --pdf-out './mudacumura.pdf' --text-out './mudacumura.txt' --out './mudacumura-fetch.json'

# 已有搜索结果 URL 或 slug 时直接取 PDF
& '<skill-dir>/run.ps1' fetch 'https://www.legal-tools.org/doc/ecfae0/' --out './ecfae0-fetch.json'
& '<skill-dir>/run.ps1' fetch 'ecfae0' --out './ecfae0-fetch.json'

# 部署自检
& '<skill-dir>/run.ps1' doctor
```

`search` 的关键字可省略，但要与 `--all` 和一个显式过滤条件一起使用。`--organisation` 接受已验证的精确标签，也接受 `icc`、`icty`、`ictr`、`irmct`、`icrc`、`toaep` 别名。`--case-number` 映射到精确的 `caseNumber`，`--document-type` 映射到精确的 `judicialDocumentType`；更细的 `where` 条件用 `--where-json` 传 JSON 对象。

通用边界：`--limit` 为 1..50，`--offset` 从 0 开始，`--timeout` 为 1..1800 秒；`fetch --max-mb` 为 1..1000。所有报告写入 UTF-8 JSON。

## 检索行为与结果边界

- Legal Tools 的 `term` 由远端搜索服务解释。多词查询不一定等于精确短语；实测 `Mudacumura confirmation of charges` 的 `total` 很宽，但前两条是目标记录。需要确定性定位时使用 `document '<官方编号>'`，或至少加 `--case-number` / `--document-type`。
- `--match raw|phrase|all|any|near` 把用户输入转换成站点远端搜索语法：`phrase` 加引号，`all` 用 AND，`any` 用 OR，`near` 用 NEAR 或 NEAR/n；`query_expression` 会回显实际提交的表达式。实测 `command responsibility` 原文 46,657 条、精确短语 3,396 条、NEAR 52,705 条、NEAR/5 46,747 条，说明这些操作符确实被远端识别。
- `results[].snippet` 来自 `highlight`，用于定位，不能代替原文引用。引用段落时使用 `fetch` 返回的页文本和真实 PDF 页码。
- 找不到高位阶直接判例时，应围绕同一规则、条文、事实模式、程序异议和相关案件号继续检索。先用书状或程序记录确认争点已提交，再读取裁判全文判断处理状态；若争点确已进入裁判视野但分庭未支持也未否定，可列为“明示未决”“以其他理由回避”或“仅提及未处理”，同时说明提出位置、最终处理及未裁判原因。这是检索线索，不是支持性权威；snippet 不足，不能证明争点曾被提出也不能据此推定 ICC 留有空间。
- `total=0` 且 `status=ok` 是已确认零命中；HTTP 403/404/5xx、schema 变化和 40 MiB JSON 上限都是检索错误，不能改写成“没有资料”。
- `document` 的 `status=not-found` 表示该 `externalId` 没有精确目录记录；不排除同一文书的其他编号、语言版本或上级卷宗存在。
- PDF 下载失败、非 PDF 响应或超过 `--max-mb` 都保留为 `status=error`。不绕过权限、验证码或访问控制；`download_permission_required=true` 只作为元数据报告。

## 参考

- 2026-10-16 实测 endpoint、过滤字段、计数、slug 与 PDF 响应：[references/legal-tools-search.md](references/legal-tools-search.md)
- 部署、环境与排错：[README.md](README.md)
