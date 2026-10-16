# LegalToolsSearcher 部署指引

独立的 ICC Legal Tools Database 检索 skill：目录检索、官方文书编号定位、PDF 下载和逐页文本提取。Legal Tools 是公开匿名来源，本 skill 不需要广外登录、浏览器、Cookie 或 API key。

仓库：<https://github.com/GDUFSICCSkills/LegalToolsSearcher>（若为私有仓库，需要组织成员权限或对私仓有读权限的令牌/部署密钥）。

## 1. 运行环境

| 组件 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 运行 CLI |
| pypdf | `>=5,<7` | 必需的 PDF 文本提取后端 |
| PyMuPDF | 可选 | 若存在则优先使用，通常对复杂 PDF 的文本提取更稳定 |
| 网络 | 可访问 `legal-tools.org`、`www.legal-tools.org`、`ltd-docs.s3.nl-ams.scw.cloud` | 搜索 API、文档页和 PDF 存储 |

不需要 Node.js、Chrome、LibVPN、`gdufs-auth-session` 或任何账号。

## 2. 安装

```powershell
git clone https://github.com/GDUFSICCSkills/LegalToolsSearcher.git "$env:USERPROFILE\.codex\skills\LegalToolsSearcher"
python -m pip install -r "$env:USERPROFILE\.codex\skills\LegalToolsSearcher\requirements.txt"
```

Codex 用户 `<skills-root>` 通常是 `%USERPROFILE%\.codex\skills`（macOS/Linux 为 `~/.codex/skills`）。`run.ps1` / `run.sh` 会依次探测 `venv/`、随附运行时和 PATH 上的 Python，并选择第一个带 `pypdf` 或 `pymupdf` 的解释器。

## 3. 自检

```powershell
& "$env:USERPROFILE\.codex\skills\LegalToolsSearcher\run.ps1" doctor
```

`doctor` 检查 Python、PDF 后端、Legal Tools 搜索 API 和一个真实 PDF 的 HEAD 路由。`--no-probe` 可跳过两个联网探测。整体 `ok: true` 只说明本机和公开路由可达，不代表某个文书有 PDF、可下载或已完成 OCR。

## 4. 使用

```powershell
$lt = "$env:USERPROFILE\.codex\skills\LegalToolsSearcher\run.ps1"

# 关键词检索；返回目录元数据与索引 highlight
& $lt search 'command responsibility' --limit 10 --out './lt-search.json'

# 精确过滤：机构、案件号、文书类型
& $lt search 'effective control' --organisation icc --case-number 'ICC-01/04-01/06' --document-type judgement --limit 10

# 没有关键词时，用 --all 明确表示只按过滤条件浏览
& $lt search --all --organisation ictr --limit 20

# 官方文书编号精确定位；不下载 PDF
& $lt document 'ICC-01/04-01/12-1-Red' --out './mudacumura.json'

# 下载并提取全文；--pdf-out 和 --text-out 都可选
& $lt fetch 'ICC-01/04-01/12-1-Red' --pdf-out './mudacumura.pdf' --text-out './mudacumura.txt' --out './mudacumura-fetch.json'

# 已有 slug 或 URL 时直接取 PDF
& $lt fetch 'ecfae0' --out './ecfae0.json'
& $lt fetch 'https://www.legal-tools.org/doc/ecfae0/' --out './ecfae0.json'
```

不加 `--out` 时报告直接打印到标准输出。`search` 的 `--organisation` 接受完整标签或别名：`icc`、`icty`、`ictr`、`irmct`、`icrc`、`toaep`。`--where-json` 可传额外的 JSON `where` 条件对象。

## 5. 报告字段

| 字段 | 含义 |
| --- | --- |
| `status` | `ok`、`not-found` 或 `error`；零命中仍是 `ok` |
| `total` | 搜索 API 返回的命中总数；多词查询可非常宽 |
| `results[].result_type` | `retrieved_metadata`，表示目录记录与索引片段，不是全文 |
| `results[].snippet` | 来自 `highlight` 的索引片段，去除 HTML 标签后保留 |
| `results[].pdf_url` | 规范化为 HTTPS 的 Legal Tools PDF 路由 |
| `document` | `document` 命令确认的目录记录 |
| `pdf.sha256` / `pdf.pages` | `fetch` 实际取得 PDF 后的哈希与页数 |
| `pdf.needs_ocr` | 所有页文本均为空时为 `true`，表示需要 OCR |
| `pages[].page` / `pages[].text` | PDF 的真实页码与文本层 |
| `text_out` | 带 `@@PAGE n@@` 页码标记的纯文本文件路径 |
| `evidence` | 实际请求 URL、HTTP 状态、内容类型、字节数、时间和哈希 |

引用时应记录：官方编号、案件号、文书类型、日期、PDF 页码，以及 `pdf.sha256`。搜索摘要、目录链接和 `document` 列表都不能代替 PDF 原文。

## 6. 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `status=not-found` | `findOne` 对官方编号返回 404 | 检查编号是否含翻译后缀、Red/Conf 等限定；改用案件号和关键词搜索 |
| `HTTP 403` / `HTTP 5xx` | 单次请求被拒绝或服务端故障 | 保留错误，不要当作零命中；稍后重试或查询官方原站 |
| `did not return a PDF` | PDF 路由返回 HTML、权限页或 schema 变化 | 查看报告中的 content-type 和响应预览；不要绕过权限控制 |
| `PDF extraction failed` | PDF 结构异常或缺少文本层 | 检查 `needs_ocr`；换用 PyMuPDF，或对 PDF 做 OCR |
| `total` 远大于预期 | Legal Tools 的远端 term 查询不是严格短语匹配 | 加 `--case-number` / `--document-type`，或先用 `document '<官方编号>'` |
| `No Python ... with pypdf or PyMuPDF` | 解释器缺 PDF 后端 | `python -m pip install -r requirements.txt` |

`languageIds` 是不透明 ID，本 skill 不将其翻译成语言名称。`download_permission_required=true` 只说明目录权限字段，仍以实际 PDF 请求的 HTTP 结果为准。

## 7. 更新

```powershell
git -C "$env:USERPROFILE\.codex\skills\LegalToolsSearcher" pull
```

本 skill 独立于 `gdufs-auth-session`、`HeinOnlineSearcher` 和 `EJILTalkSearcher`，不依赖同组织其它仓库。
