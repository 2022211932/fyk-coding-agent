FYK Coding Agent

Git仓库：https://github.com/2022211932/fyk-coding-agent

运行要求：Python 3.10+。执行“python -m venv .venv”，激活环境后运行“python -m pip install -e .”。将DeepSeek密钥写入环境变量DEEPSEEK_API_KEY，然后使用“fyk-agent -w 项目目录 "编程任务"”；直接运行“fyk-agent -w 项目目录”可进入类似Claude Code的常驻命令行，多轮任务共享上下文，并支持/status、/history、/clear、/undo等命令。

本项目直接调用DeepSeek V4 Pro的OpenAI兼容tool calling接口，不使用任何Agent框架或服务端文件、代码执行工具。自行实现模型HTTP客户端、对话历史、上下文裁剪、工具协议解析、Agent循环、终止条件、错误重试和本地执行。

特色功能：所有路径限制在工作区内，拒绝访问.env、私钥和Git内部文件；写入与命令默认需要人工批准；精确文本替换在匹配数异常时拒绝修改；文件写入前自动保存快照，可用:undo撤销；命令带超时、输出截断并移除敏感环境变量；JSONL日志记录每轮执行和终止原因。项目使用Python标准库实现，无运行时第三方依赖。测试不调用真实模型，覆盖Agent循环、安全边界、工具错误、上下文压缩和回滚。

作者：冯逸康
