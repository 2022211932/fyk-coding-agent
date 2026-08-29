FYK Coding Agent

Git仓库：https://github.com/2022211932/fyk-coding-agent

运行要求：Python 3.10+。执行“python -m venv .venv”，激活环境后运行“python -m pip install -e .”。将DeepSeek密钥写入环境变量DEEPSEEK_API_KEY，然后使用“fyk-agent -w 项目目录 "编程任务"”；直接运行“fyk-agent -w 项目目录”进入类似Claude Code的常驻命令行。可视化模式另需Node.js 22+，首次执行“cd web; npm install; cd ..”，以后运行“fyk-agent --web”。点击顶部工作区路径可浏览本机并选择项目，程序会记住最近项目。

本项目直接调用DeepSeek V4 Pro的OpenAI兼容tool calling接口，不使用任何Agent框架或服务端文件、代码执行工具。自行实现模型HTTP客户端、对话历史、上下文裁剪、工具协议解析、Agent循环、终止条件、错误重试和本地执行。

特色功能：终端与Web控制台均实时展示模型步骤、工具调用和命令输出；所有路径限制在工作区内，拒绝访问.env、私钥和Git内部文件；写入与命令默认需要人工批准，Web界面可明确切换自动审批；文件写入前自动保存快照并支持撤销；命令带超时、输出截断并移除敏感环境变量；JSONL日志记录执行过程。控制台接口仅监听本机并用随机令牌鉴权。

作者：冯逸康
