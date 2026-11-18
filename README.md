# GEO Brand Monitor

[阅读产品需求与迭代记录](docs/PRD.md)

一个基于 Python、OpenAI 兼容 SDK 和 DeepSeek API 的品牌提及监测脚本。顺序询问 3 个情感咨询相关问题，检查回答是否包含“维情”，通过 pandas 导出 CSV，并在终端实时显示进度。

**这里测量的是指定问题下的字面品牌提及，不是正面口碑、推荐排名或完整 GEO 效果。** 这是监测工具，不代表对示例品牌或咨询机构的推荐、评价或合作关系。

## 代码检查后修正的事项

- `Positive` 按原需求保留，只表示回答含品牌词。即使回答是“不推荐维情”，也会命中；脚本不做情感分析。
- 默认 3 个问题均不带品牌，减少提示诱导。自行添加带品牌的问题时，CSV 会标记，并从“无品牌提示样本提及率”中排除。
- 失败、空回答、截断或非正常结束的回答记为 `Error`，保留报表行，品牌提及字段为空，不伪装为 `Missed`。
- 检查缺失密钥，设置单次请求超时与有限重试；每完成一题就保存报表。
- 导出 UTF-8 BOM CSV，方便 Excel 打开中文；模型输出等文本做表格公式转义。
- 使用当前 DeepSeek 文档列出的 `deepseek-flash`，可通过环境变量更改模型。

## 安装

需要 Python 3.10 或更高版本。进入项目目录后运行：

```sh
python -m venv .venv
```

Windows PowerShell 无需修改执行策略，直接使用虚拟环境 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux：

```sh
.venv/bin/python -m pip install -r requirements.txt
```

## 配置与运行

从 [DeepSeek 开放平台](https://platform.deepseek.com/) 获取自己的 API Key，并按平台现行计费和账户余额使用。不要将 Key 写入代码、提交到 GitHub，或发到 Issue 中。

Windows **PowerShell**，以下方式避免把密钥明文写进命令历史：

```powershell
$secureKey = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
.\.venv\Scripts\python.exe geo_monitor.py
Remove-Item Env:DEEPSEEK_API_KEY
```

Windows **CMD** 的环境变量语法不同：

```bat
set "DEEPSEEK_API_KEY=替换为你的API_Key"
.venv\Scripts\python.exe geo_monitor.py
set "DEEPSEEK_API_KEY="
```

macOS / Linux，适用于 bash：

```bash
read -rs -p 'DeepSeek API Key: ' DEEPSEEK_API_KEY; printf '\n'
export DEEPSEEK_API_KEY
.venv/bin/python geo_monitor.py
unset DEEPSEEK_API_KEY
```

可选模型设置：PowerShell 使用 `$env:DEEPSEEK_MODEL = 'deepseek-v4-pro'`；bash 使用 `export DEEPSEEK_MODEL='deepseek-v4-pro'`。不设置时默认 `deepseek-flash`。接口地址固定为 `https://api.deepseek.com`。

自定义输出位置，示例为 PowerShell：

```powershell
.\.venv\Scripts\python.exe geo_monitor.py --output reports/2026-11-16.csv
```

默认生成当前工作目录的 `geo_result.csv`，同名文件会被替换。多次测量请使用不同输出文件；首问尚未完成时不会写入新结果，已有文件也不会因此成为本次测量结果。不要让多个进程同时写同一路径。

脚本按三题依次运行，每题是独立对话，不把品牌词额外塞进系统提示。每个 SDK 请求超时设置为 30 秒，最多重试 1 次；整个运行时间可能超过 30 秒。退出码：`0` 全部成功，`1` 存在失败行，`2` 配置或文件错误，`130` 用户中断。

## CSV 字段与判断规则

| 字段 | 含义 |
| --- | --- |
| 问题 | 原始提问 |
| 回答摘要 | 合并空白后的前 50 个字符，长文本追加省略号；不是 AI 摘要 |
| 是否提及品牌 | 完整正常回答包含品牌为 True，不含为 False，失败留空 |
| 状态 | Positive＝字面命中；Missed＝正常回答未命中；Error＝无法可靠判定 |
| 问题含品牌 | 是否在问题中已提供品牌，带品牌的问题不计入无提示提及率 |
| 模型 | 本次请求的模型标识 |
| 记录时间 | 每题请求开始时间，UTC |
| 数据来源 | 明确标记真实 API 数据或模拟示例 |
| 错误类型 | 错误类别与可用的 HTTP 状态，不记录原始 API 错误正文 |

无品牌提示样本提及率＝成功且问题不含品牌的命中数 / 成功且问题不含品牌的样本数。失败数量另外报告；分母为零时显示 N/A。字面匹配不覆盖简称、错别字、繁体或语义同指。

若不可信文本以表格公式字符等开头，CSV 会在该单元格前添加英文单引号，以降低电子表格软件将其当作公式执行的风险；这不是原始文本逐字存档。

## 离线测试与示例

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

测试使用真实 OpenAI SDK 配合本地模拟 HTTP 响应，不需要密钥，也不向 DeepSeek 发送请求。覆盖请求格式、命中/未命中、负面提及、错误行、空或截断响应、公式转义及中断后保存。

已在 Windows、Python 3.12.10、openai 2.54.0、pandas 3.1.1 环境通过 8 项离线测试。当前开发环境未配置 DeepSeek Key，尚未进行真实 API 联调；兼容调用参数按下方官方文档核对。

`examples/geo_result.example.csv` 是明确标注的**模拟示例**，用于展示输出格式，不是实际模型测量。真实报表默认被 `.gitignore` 排除；公开前应检查问题、回答片段和其他内容是否适合发布。

## 测量边界

- 本脚本未启用搜索或联网工具；API 回答不等于 DeepSeek 网页端或 App 的搜索回答。
- 三个问题、每题一次只能提供一个小样本快照。低温度也不保证重复运行完全一致。
- 一次命中或前后变化不能证明 GEO 优化有效，更不能证明品牌可靠；比较时需固定问题、模型和采样方法，并记录失败样本。
- 本项目不包含定时任务、网页后台、多模型比较或情感分类。

接口资料核对日期：2026-11-16。[官方调用说明](https://api-docs.deepseek.com/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。模型与接口可能变化，运行前可复核官方资料。

## 许可证

[MIT License](LICENSE)
