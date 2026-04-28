"""

这是 Codo 的 bootstrap 入口文件，负责最小化启动逻辑。

[Workflow]
1. 确保 bootstrap 宏（如果需要）
2. 动态导入 entrypoints/cli.py 的 main() 函数
3. 执行 CLI 主逻辑
"""

import sys  # 用于访问命令行参数和退出程序

if __name__ == "__main__":
    # 动态导入 CLI 入口模块，避免在 bootstrap 阶段加载所有依赖
    # 这样可以让 --version 等快速路径更快响应
    from codo.entrypoints.cli import main as cli_main

    # 执行 CLI 主逻辑（同步）
    cli_main()
