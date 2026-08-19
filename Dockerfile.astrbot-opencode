FROM soulter/astrbot:latest

# 安装 opencode 所需的系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 opencode(AI 编程助手):优先官方安装脚本,失败不中断
RUN curl -fsSL https://opencode.ai/install | bash || true

# opencode 官方脚本默认安装到 ~/.opencode/bin
ENV PATH="/root/.opencode/bin:${PATH}"

# 若官方脚本未生效,回退到 npm 全局安装(镜像内可能无 npm,失败则跳过)
RUN command -v opencode >/dev/null 2>&1 \
    || (command -v npm >/dev/null 2>&1 && npm install -g opencode-ai) \
    || true

# opencode 默认工作目录(插件配置 oc_dir 的默认值)
RUN mkdir -p /workspace

WORKDIR /workspace