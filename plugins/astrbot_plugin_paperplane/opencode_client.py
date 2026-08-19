"""opencode 客户端:以子进程方式调用 opencode CLI,解析 JSON Lines 输出。

opencode 是 AI 编程助手,`opencode run --format json --prompt <msg>` 会输出
JSON Lines(每行一个 JSON 事件),本模块负责执行命令并提取最终回复文本。

仅使用 Python 标准库(asyncio + subprocess),不依赖第三方库。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field


@dataclass
class OpencodeConfig:
    """opencode 集成所需的配置项(与 _conf_schema.json 对应)。"""

    bin: str = "opencode"
    """opencode 可执行文件路径"""
    dir: str = "/workspace"
    """opencode 工作目录"""
    model: str = ""
    """模型名(留空使用默认)"""
    session: str = ""
    """会话 ID(留空每次新会话)"""
    attach_url: str = ""
    """附加到运行中的 opencode server 地址(留空则 CLI 直跑)"""
    timeout: int = 180
    """调用超时(秒)"""
    auto: bool = False
    """自动批准权限(危险)"""
    max_output_len: int = 3500
    """回复文本最大长度,超出截断"""


class OpencodeClient:
    """异步调用 opencode CLI 的客户端。"""

    def __init__(self, config: OpencodeConfig | None = None) -> None:
        self.config = config or OpencodeConfig()

    def _build_command(self, prompt: str) -> list[str]:
        """根据配置构造 opencode run 命令。

        注意:opencode run 的消息必须作为位置参数传入,`--prompt` 标志
        在 run 子命令下不可用(会打印帮助并退出)。
        """
        cmd = [self.config.bin, "run", prompt, "--format", "json"]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if self.config.session:
            cmd += ["--session", self.config.session]
        if self.config.attach_url:
            cmd += ["--attach", self.config.attach_url]
        if self.config.auto:
            cmd += ["--auto"]
        return cmd

    @staticmethod
    def _parse_output(stdout: str) -> str:
        """解析 opencode run --format json 的 JSON Lines 输出,拼接文本。

        事件行结构(已实测):
          {"type":"step_start", ...}
          {"type":"text", ..., "part":{"type":"text","text":"内容",...}}
          {"type":"step_finish", ..., "part":{"reason":"stop"|"error",...}}
        """
        texts: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 非 JSON 行(如警告)直接跳过
                continue
            if not isinstance(event, dict):
                continue
            # 结束事件且原因是 error,视为失败
            if event.get("type") == "step_finish":
                part = event.get("part") or {}
                if part.get("reason") == "error":
                    raise RuntimeError(f"opencode 执行失败: {part.get('error', '未知错误')}")
            # 文本事件
            if event.get("type") == "text":
                part = event.get("part") or {}
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
        result = "\n".join(texts).strip()
        if not result:
            raise RuntimeError("opencode 未返回任何文本结果")
        return result

    async def run(self, prompt: str) -> str:
        """异步执行 opencode,返回最终回复文本(已截断)。"""
        cmd = self._build_command(prompt)
        cwd = self.config.dir or None
        # 工作目录不存在时回退到用户主目录,避免启动失败
        if cwd and not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout,
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"opencode 调用超时({self.config.timeout}秒)") from e
        except FileNotFoundError as e:
            raise RuntimeError(
                f"找不到 opencode 可执行文件: {self.config.bin},请检查插件配置 oc_bin"
            ) from e

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode 退出码 {proc.returncode}: {stderr_str.strip() or '未知错误'}"
            )

        result = self._parse_output(stdout_str)
        if len(result) > self.config.max_output_len:
            result = result[: self.config.max_output_len] + "\n...(已截断)"
        return result