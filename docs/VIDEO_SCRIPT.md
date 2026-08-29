# 两分钟演示脚本

建议录屏分辨率 1920x1080，终端字体不小于 18px。提前安装项目并设置环境变量，视频中不要打开 `.env`、控制台密钥页或环境变量详情。

## 0:00-0:15 项目介绍

画面展示 README 的架构图和工具表。

讲解词：

> 这是 FYK Coding Agent。它不使用任何 Agent 框架，直接调用 DeepSeek 的原生工具接口；模型循环、上下文、文件工具、命令执行、审批和撤销都由项目本地实现。

## 0:15-0:30 展示初始失败

```powershell
python scripts/prepare_demo.py
cd demo-workspace
python -m unittest -v
cd ..
```

指出 5 项测试中有 4 项失败。

## 0:30-1:30 Agent 自主修复

```powershell
fyk-agent -y -w demo-workspace
```

在出现 `❯` 后输入：

```text
修复 slugify，使全部测试通过；不要修改测试，并在结束前运行完整测试。
```

可以加速模型等待部分，但保留终端中的这些节点：

- `list_files`；
- 多个 `read_file`；
- 首次 `run_command: failed`；
- `edit_file: ok`；
- 第二次 `run_command: ok`；
- 最终总结。

讲解词：

> 这是类似 Claude Code 的常驻命令行，多轮指令共享上下文。模型只能提出结构化工具调用，本地工具先检查路径和参数，再执行并把结果送回模型。首次测试失败是 Agent 获取诊断信息，之后它精确编辑实现并进行回归测试。

## 1:30-1:50 安全与可审计性

展示 `.fyk-agent/events.jsonl`，不要展示任何环境变量。

讲解词：

> 所有写入都有写前快照，可以撤销；事件日志记录模型轮次、工具结果和耗时。敏感环境变量不会传给命令子进程，Agent 也无法读取 `.env` 或访问工作区之外的路径。

## 1:50-2:00 收尾

重新运行：

```powershell
cd demo-workspace
python -m unittest -v
```

讲解词：

> 最终 5 项测试全部通过，形成了读取、诊断、修改和验证的完整编程闭环。
