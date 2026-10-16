# ICC Legal Tools 检索实测与接口记录

本文件记录 2026-10-16 对独立 `LegalToolsSearcher` 的公开接口实测。数字与字段是当日事实，不是稳定承诺；站点改版后按这里的三条路径重新核验。

## 1. 三个公开路径

| 操作 | 请求 | 得到什么 | 边界 |
| --- | --- | --- | --- |
| 搜索 | `GET https://legal-tools.org/api/ltddocs/search` | `results[].hit`、`results[].highlight`、`total`、`facets` | 索引元数据与正文索引片段，不是全文 |
| 精确编号 | `GET https://legal-tools.org/api/ltddocs/findOne` | 一个完整目录记录，含 `slug`、`documentUrl`、`pdfURL`、权限字段 | 官方编号不存在时返回 HTTP 404 |
| PDF | `GET https://www.legal-tools.org/doc/<slug>/pdf/` | 原始 PDF | 是否成功取决于记录与访问权限；只接受 `%PDF` 响应 |

三条路径都是匿名直接 HTTP。本 skill 不使用 Cookie、不执行网页 JavaScript，不抓取 `/doc/<slug>/` 的 JS shell 来寻找 PDF 链接。

## 2. 搜索 API 参数

请求参数：

```json
{
  "term": "command responsibility",
  "filter": "{\"from\":0,\"limit\":10,\"where\":{\"deleted\":false}}"
}
```

已验证的精确 `where` 字段：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| `organisation` | `International Criminal Court (ICC)` | 机构/法院 |
| `caseNumber` | `ICC-01/04-01/06` | 案件号 |
| `judicialDocumentType` | `judgement` | 司法文书类型 |
| `externalId` | `ICC-01/04-01/12-1-Red` | `findOne` 用于精确定位 |

`term` 是远端搜索表达式，不由本 skill 规范化。多词词项可能按相关性宽泛匹配，不能把 `total` 当作精确短语命中数。

## 3. 2026-10-16 实测计数

| 查询 | 过滤 | `total` |
| --- | --- | ---: |
| `Mudacumura confirmation of charges` | 无 | 275,650 |
| `command responsibility` | 无 | 46,662 |
| `command responsibility` | `organisation=International Criminal Court (ICC)` | 6,503 |
| `command responsibility` | `judicialDocumentType=judgement` | 2,274 |
| `zzzxnonexistent785132904` | 无 | 0 |
| `Mudacumura` | `caseNumber=ICC-01/04-01/12` | 13 |

这些数字说明两点：

1. `total=0` 是明确的零命中，不是访问失败；
2. 多词输入可以有很宽的总量，但前几条仍可能高度相关。需要确定性定位时使用精确编号或案件号过滤。

搜索 `ecfae0` 返回 0：slug 不是可靠的关键词索引字段。要取该文档，直接运行 `fetch ecfae0`，或先按它对应的官方编号运行 `document`。

## 4. `findOne` 实测

请求：

```text
GET https://legal-tools.org/api/ltddocs/findOne?filter={"where":{"externalId":"ICC-01/04-01/12-1-Red"}}
```

结果：

- HTTP 200；
- `title = Decision on the Prosecutor's Application under Article 58`
- `caseName_en = The Prosecutor v. Sylvestre Mudacumura`
- `caseNumber = ICC-01/04-01/12`
- `organisation = International Criminal Court (ICC)`
- `slug = ecfae0`
- `numberOfPages = 31`
- `requireDownloadPermission = false`
- `documentUrl` 与 `pdfURL` 的 API 值为 `http://www.legal-tools.org/...`，本 skill 对外统一改成 HTTPS。

对不存在的编号，`findOne` 返回 HTTP 404，响应体是 `MODEL_NOT_FOUND`。因此 skill 把 `status=not-found` 与 HTTP/网络错误分开报告。

## 5. PDF 路由实测

```text
HEAD https://www.legal-tools.org/doc/ecfae0/pdf/
```

结果：

- HTTP 200；
- `Content-Type: application/pdf`；
- `Content-Length: 2039576`；
- GET 响应以 `%PDF-1.3` 开头；
- `ltd-docs.s3.nl-ams.scw.cloud` 的原始 PDF 链接和上面的 `/doc/<slug>/pdf/` 都返回同一文件。

适配器使用 `/doc/<slug>/pdf/`，因为它是记录级稳定入口；`orignalPdfURL` 只作为元数据保留。

同日对 `ICC-01/04-01/12-1-Red` 执行完整 `fetch`：

- PDF 字节数 `2039576`；
- SHA-256 `e050bf3d0866285b531d4d3d95c8d40cf44d91e579504cc058a3df0136507358`；
- pypdf 提取 31 页，共 74,249 字符；
- `needs_ocr=false`。

## 6. 字段与来源边界

搜索记录中常见但语义不同的字段：

| 字段 | 含义 |
| --- | --- |
| `externalId` | 官方文书编号；`findOne` 的精确键 |
| `caseNumber` | 案件号；同一案件有多份文书 |
| `caseName_en` / `caseName` | 案件名，优先英文 |
| `source` | 文书来源或分庭类别 |
| `judicialDocumentType` | 机器可读的文书类型 |
| `documentTypeEN` | 面向读者的英文类型 |
| `confidentiality` | 当前记录标为 `public` 等 |
| `requireDownloadPermission` | 目录权限字段，不等于实际 PDF 请求结果 |
| `languageIds` | 不透明语言 ID，不推断语言名称 |
| `isOriginalLanguage` | 该版本是否为原文 |
| `isAuthoritativeLanguage` | 该版本是否为权威语言版本 |

结果中的 `highlight.docPages.text` 来自全文索引，但仍只是片段。引用段落时要下载 PDF，并记录 PDF 的真实页号；不要把 `highlight` 当作完整段落。

## 7. 未验证项

- 未声称搜索 API 或 PDF 路由的服务等级、长期稳定性或完整馆藏覆盖。
- 未穷举 `where` 字段；只把实测字段写入 CLI。高级条件可通过 `--where-json` 传入，但返回结果仍需按记录核验。
- 未把所有 PDF 实测为带文字层。扫描件会由 `pdf.needs_ocr` 标记。
- 未测试或绕过任何需登录、需订阅或 `requireDownloadPermission=true` 的访问控制。
