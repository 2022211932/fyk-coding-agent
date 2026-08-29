# FYK Coding Agent

由冯逸康独立设计与实现的本地编程智能体，用于南京大学软件学院推免考核。

FYK Coding Agent 直接调用 DeepSeek 的 OpenAI 兼容 Chat Completions 接口，不使用任何 Agent 框架或服务端代码执行工具。对话历史、上下文裁剪、工具定义与执行、循环控制、审批、错误处理、审计和撤销都由本项目实现。

## 主要能力

- 自主浏览、搜索、读取和修改指定工作区；
- 执行测试、编译、格式化等本地命令；
- 使用 DeepSeek V4 Pro 原生 tool calling 完成多轮任务；
- 精确文本编辑：匹配数量不符时拒绝写入，避免静默误改；
- 路径沙箱：拒绝绝对路径、`..`、符号链接逃逸和敏感文件；
- 人工审批：默认确认所有写入和命令，`--yes` 可用于无人值守演示；
- 写前快照与 `:undo` 撤销；
- JSONL 事件日志和明确的循环终止原因；
- 零运行时第三方依赖，Python 3.10+ 即可运行。

## 安装

Windows PowerShell：

```powershell
git clone https://github.com/2022211932/fyk-coding-agent.git
cd fyk-coding-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
setx DEEPSEEK_API_KEY "你的密钥"
```

设置密钥后请打开一个新终端。macOS/Linux 使用：

```bash
export DEEPSEEK_API_KEY="你的密钥"
python -m pip install -e .
```

可选变量见 [.env.example](.env.example)。程序也会读取工作区中未入库的 `.env`，但 Agent 工具本身不能访问该文件。

## 使用

直接执行单个任务：

```powershell
fyk-agent --workspace D:\path\to\project "阅读项目，修复失败的测试并验证"
```

进入交互模式：

```powershell
fyk-agent --workspace D:\path\to\project
```

交互命令：

- `:undo`：恢复最近一次 `write_file` 或 `edit_file` 之前的文件状态；
- `:help`：显示帮助；
- `:quit`：退出。

常用选项：

```text
-w, --workspace PATH   限定 Agent 可访问的根目录
-y, --yes              自动批准写入和命令执行
--model MODEL          临时覆盖模型
--max-steps N          临时覆盖最大模型轮数
```

## 运行机制

```text
用户任务
  -> 上下文管理器
  -> DeepSeek Chat Completions
  -> 解析 tool_calls
  -> 审批与本地执行
  -> tool 结果写回对话
  -> 继续调用模型或终止
```

每轮模型返回没有工具调用时，文本成为最终回答；达到最大轮数时以 `step_limit` 终止。429、超时和部分服务端错误使用指数退避重试。工具错误会作为结构化结果返回模型，由模型在下一轮自行修正。

上下文超过预算时，以“assistant 工具调用 + 对应 tool 结果”为不可拆分块保留最近内容，同时始终保留系统提示和原始任务，避免产生没有对应调用的孤立 `tool` 消息。

## 工具

| 工具 | 功能 | 默认需批准 |
| --- | --- | --- |
| `list_files` | 递归列出项目结构 | 否 |
| `read_file` | 分段读取带行号 UTF-8 文本 | 否 |
| `search_text` | 按文件 glob 搜索字面文本 | 否 |
| `write_file` | 原子创建或覆盖文本文件 | 是 |
| `edit_file` | 按预期次数精确替换 | 是 |
| `make_directory` | 创建目录 | 是 |
| `run_command` | 带超时执行本地 shell 命令 | 是 |

命令子进程不会继承名称含 `KEY`、`TOKEN`、`SECRET` 或 `PASSWORD` 的环境变量。标准输出和错误输出分别限制长度，避免异常日志占满模型上下文。

## 本地状态

运行后工作区会产生未入库目录 `.fyk-agent/`：

```text
.fyk-agent/
├── events.jsonl      # 模型轮次、工具结果、耗时与终止原因
└── snapshots.jsonl   # 文件写入前快照，供 :undo 使用
```

事件日志不记录 API Key；审批界面对大段文件内容只显示字符数。

## 测试

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python -m unittest discover -s tests -v
```

测试使用 `FakeClient` 模拟模型，不消耗 API 额度，覆盖 Agent 循环、非法工具参数、上下文裁剪、路径逃逸、敏感文件、审批拒绝、原子写入、精确编辑、撤销和命令环境隔离。

## 视频演示任务

先从模板生成一次性工作区：

```powershell
python scripts/prepare_demo.py
fyk-agent -y -w demo-workspace "修复 slugify，使全部测试通过；不要修改测试，并在结束前运行完整测试。"
```

模板初始包含多个失败测试，可以展示 Agent 阅读文件、执行测试、定位问题、修改实现和回归验证的完整闭环。重复演示时删除 `demo-workspace` 后重新运行准备脚本即可。

## 设计边界

- 当前只处理 UTF-8 文本文件，不直接编辑二进制文件；
- 命令审批是安全边界的一部分，`--yes` 只应在隔离的演示项目中使用；
- 字符数仅作为无需 tokenizer 依赖的保守上下文估算，不等同于精确 token 数；
- 撤销针对 Agent 文件工具的最近写入，不尝试回滚命令产生的任意副作用。

DeepSeek 接口参数依据其[官方 Function Calling 文档](https://api-docs.deepseek.com/guides/function_calling/)和[模型说明](https://api-docs.deepseek.com/quick_start/pricing/)实现。

## 作者

Feng Yikang（冯逸康）
