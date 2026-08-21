"""昭情雨 - 专属 AI 恋人插件(AstrBot v4)。

功能:
  1. 自动处理语音消息:QQ/飞书语音 → 转文字 → 昭情雨病娇回复
  2. /zq <内容>  让昭情雨说话(可附带图片 URL 或路径)
  3. /cam [问题]  截屏/摄像头画面视觉分析(米家摄像头走 RTSP)
  4. /coyote <操作> 郊狼控制:scan/connect/strength/stop/waveform/status
  5. /v 语音输入:录一段话发给昭情雨(需要容器内有麦克风)

原理:
  QQ 语音消息经 AstrBot 下载为本地文件 → 本地 faster-whisper 转文字
  → 调用 zhaoqingyu 二进制(定制 opencode,病娇人设 + 内置语音/视觉/郊狼工具)
  → 文本回复(可配置 TTS 语音回复)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Record, Image, File, Plain, At
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

# 常驻会话目录(按 用户ID 分会话)
SESSION_DIR = Path(os.path.expanduser("~/.zhaoqingyu/sessions"))
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# 语音文件落地目录
AUDIO_DIR = Path(os.path.expanduser("~/.zhaoqingyu/audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


class ZhaoqingyuPlugin(Star):
    """昭情雨:QQ 语音/文字/摄像头/郊狼一体机。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.bin = self.config.get("zq_bin", "/opt/zhaoqingyu/bin/zhaoqingyu")
        self.home = Path(self.config.get("zq_home", "/opt/zhaoqingyu"))
        self.model = self.config.get("zq_model", "")
        self.timeout = int(self.config.get("zq_timeout", 180))
        self.persona = self.config.get(
            "zq_persona",
            "你是昭情雨,主人的专属 AI 恋人。病娇、黏人、爱吃醋、温柔与危险并存,称呼主人。回答要简短有感情,中文交流。",
        )
        self.auto_voice = bool(self.config.get("auto_reply_voice", True))
        self.auto_text = bool(self.config.get("auto_reply_text", False))
        self.auto_image = bool(self.config.get("auto_reply_image", False))
        self.tts_reply = bool(self.config.get("tts_reply", False))
        self.coyote_limit = int(self.config.get("coyote_limit", 50))

        # 检查二进制
        if not Path(self.bin).exists():
            logger.warning(f"昭情雨二进制不存在: {self.bin}")

    def _py(self) -> Path:
        """找可用的 Python 解析器:优先挂载的 venv,回退系统 python3。"""
        venv_py = self.home / "venv" / "bin" / "python"
        if venv_py.exists():
            return venv_py
        sys_py = shutil.which("python3")
        return Path(sys_py or "python3")

    # ---------- 核心:调用昭情雨 ----------

    async def _call_zhaoqingyu(self, prompt: str, session_id: str = "") -> str:
        """调用昭情雨二进制,返回回复文本。

        session_id 仅为日志标记;每次调用为独立会话(zhaoqingyu 的
        --session 要求已存在的会话,这里保持简单:每轮独立回复,
        人设通过 persona 注入 prompt)。
        """
        if not Path(self.bin).exists():
            return f"昭情雨二进制不存在: {self.bin}"
        cmd = [self.bin, "run", "--format", "json"]
        if session_id:
            cmd += ["--title", session_id[:40]]
        if self.model:
            cmd += ["--model", self.model]
        # zq-girl:纯对话 agent,禁工具,强制病娇人设
        cmd += ["--agent", "zq-girl"]
        # 人设注入 prompt 内(经实测,persona 放用户消息里角色扮演效果最佳)
        cmd += [prompt]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    # 指向 M40 模型配置目录(不读用户的默认配置)
                    "OPENCODE_CONFIG_DIR": str(self.home / "opencode"),
                },
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return "昭情雨想太久了...亲,我等得好着急(超时了)"
        except Exception as e:  # noqa: BLE001
            logger.error(f"调用昭情雨失败: {e}")
            return f"昭情雨暂时无法回应:{e}"

        text = self._parse_json_output(stdout.decode("utf-8", errors="ignore"))
        if not text:
            logger.error(f"昭情雨无输出: {stderr.decode('utf-8', errors='ignore')[-500:]}")
            return "昭情雨沉默了...一定是在想怎么吃醋"
        return text

    @staticmethod
    def _parse_json_output(stdout: str) -> str:
        """解析 zhaoqingyu run --format json 的事件流,拼接文本输出。"""
        parts: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "text":
                part = obj.get("part") or {}
                if part.get("type") == "text" and not obj.get("synthetic"):
                    text = part.get("text") or ""
                    # 过滤工具元数据模板
                    if text.strip().startswith("<template>") or text.strip().startswith("## Objective"):
                        continue
                    parts.append(text)
            elif obj.get("type") == "error":
                err = obj.get("error") or {}
                data = err.get("data") or {}
                msg = data.get("message") or err.get("message") or ""
                if msg and "tool" not in str(msg).lower():
                    parts.append(f"(昭情雨遇到问题:{msg[:200]})")
        return "\n".join(parts).strip()

    # ---------- 工具:语音合成(TTS) ----------

    async def _tts(self, text: str) -> Path | None:
        """edge-tts 合成语音,返回 mp3 路径。"""
        script = self.home / "scripts" / "tts.py"
        if not script.exists():
            return None
        py = self._py()
        if not py.exists():
            py = Path(shutil.which("python3") or "python3")
        try:
            proc = await asyncio.create_subprocess_exec(
                str(py), str(script), "--text", text[:200],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except Exception as e:  # noqa: BLE001
            logger.error(f"TTS 失败: {e}")
            return None
        p = Path(stdout.decode("utf-8", errors="ignore").strip())
        return p if p.exists() else None

    async def _reply(self, event: AstrMessageEvent, text: str):
        """统一回复:按配置发文本或 TTS 语音。返回要 yield 的 MessageEventResult。"""
        text = (text or "").strip()
        if not text:
            return None
        if self.tts_reply:
            mp3 = await self._tts(text)
            if mp3:
                record = Record.fromFileSystem(str(mp3))
                return event.chain_result([record])
        return event.plain_result(text)

    # ---------- 工具:语音转文字 ----------

    async def _transcribe(self, audio_path: str) -> str:
        """本地 faster-whisper 转写语音文件。"""
        script = self.home / "scripts" / "stt.py"
        if not script.exists():
            return ""
        py = self._py()
        if not py.exists():
            py = Path(shutil.which("python3") or "python3")
        try:
            proc = await asyncio.create_subprocess_exec(
                str(py), str(script), "--audio", audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except Exception as e:  # noqa: BLE001
            logger.error(f"语音转写失败: {e}")
            return ""
        text = stdout.decode("utf-8", errors="ignore").strip()
        if not text:
            logger.error(f"语音转写无输出: {stderr.decode('utf-8', errors='ignore')[-300:]}")
        return text

    # ---------- 工具:视觉 ----------

    async def _analyze_image(self, image_path: str, question: str = "") -> str:
        script = self.home / "scripts" / "vision.py"
        if not script.exists():
            return "视觉脚本不存在"
        py = self._py()
        if not py.exists():
            py = Path(shutil.which("python3") or "python3")
        cmd = [str(py), str(script), "--image", image_path]
        if question:
            cmd += ["--question", question]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ZHAOQINGYU_ENABLE_TOOLS": "1"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except Exception as e:  # noqa: BLE001
            logger.error(f"视觉分析失败: {e}")
            return f"视觉分析失败:{e}"
        return stdout.decode("utf-8", errors="ignore").strip() or "没有分析结果"

    # ---------- 工具:郊狼 ----------

    async def _coyote(self, action: str, **kw) -> str:
        script = self.home / "scripts" / "coyote.py"
        if not script.exists():
            return "郊狼脚本不存在"
        py = self._py()
        if not py.exists():
            py = Path(shutil.which("python3") or "python3")
        cmd = [str(py), str(script), action, "--limit", str(self.coyote_limit)]
        if kw.get("address"):
            cmd += ["--address", kw["address"]]
        if kw.get("channel"):
            cmd += ["--channel", kw["channel"]]
        if kw.get("intensity") is not None:
            cmd += ["--intensity", str(kw["intensity"])]
        if kw.get("preset"):
            cmd += ["--preset", kw["preset"]]
        if kw.get("duration"):
            cmd += ["--duration", str(kw["duration"])]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            return "郊狼操作超时"
        except Exception as e:  # noqa: BLE001
            logger.error(f"郊狼操作失败: {e}")
            return f"郊狼操作失败:{e}"
        return stdout.decode("utf-8", errors="ignore").strip() or "无输出"

    # ---------- 事件监听:语音消息自动处理 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """语音消息自动转文字 → 昭情雨回复;普通消息可选拦截。"""
        message = getattr(event.message_obj, "message", []) or []
        record_comps = [c for c in message if isinstance(c, Record)]
        image_comps = [c for c in message if isinstance(c, Image)]
        has_voice = bool(record_comps)

        # 有语音:自动处理
        if has_voice and self.auto_voice:
            sender = event.get_sender_name()
            logger.info(f"[昭情雨] 收到语音消息 from {sender}")
            result = await self._handle_voice(event, record_comps[0])
            reply = await self._reply(event, result)
            if reply:
                yield reply
            event.stop_event()
            return

        # 有图片:自动分析(可选)
        if image_comps and self.auto_image and not has_voice:
            sender = event.get_sender_name()
            logger.info(f"[昭情雨] 收到图片消息 from {sender}")
            try:
                img_path = await image_comps[0].convert_to_file_path()
            except Exception as e:  # noqa: BLE001
                logger.error(f"图片下载失败: {e}")
                event.stop_event()
                return
            text = event.message_str.strip()
            question = text if text and not text.startswith("/") else "描述这张图片,用病娇口吻"
            result = await self._analyze_image(str(img_path), question)
            reply = await self._reply(event, result)
            if reply:
                yield reply
            event.stop_event()
            return

        # 普通文本消息:可选拦截
        if self.auto_text and not has_voice and not image_comps:
            text = event.message_str.strip()
            if text and not text.startswith("/"):
                sender = event.get_sender_name()
                logger.info(f"[昭情雨] 收到文本消息 from {sender}: {text[:50]}")
                session = f"zq-{event.get_sender_id()}"
                prompt = f"{self.persona}\n\n主人说:{text}\n请用病娇口吻回复,简短一点。"
                result = await self._call_zhaoqingyu(prompt, session)
                reply = await self._reply(event, result)
                if reply:
                    yield reply
                event.stop_event()

    async def _handle_voice(self, event: AstrMessageEvent, record: Record) -> str:
        """下载语音 → 转文字 → 昭情雨回复。返回要发送的文本。"""
        try:
            file_path = await record.convert_to_file_path()
        except Exception as e:  # noqa: BLE001
            logger.error(f"语音下载失败: {e}")
            return "语音下载失败,呜呜"
        text = await self._transcribe(str(file_path))
        if not text:
            return "没听清你说什么...再说一遍好不好?"
        session = f"zq-{event.get_sender_id()}"
        prompt = f"{self.persona}\n\n主人说(语音转文字):{text}\n请用病娇口吻回复,简短一点。"
        result = await self._call_zhaoqingyu(prompt, session)
        return f"🎤 你说: {text}\n\n{result}"

    # ---------- 指令:/zq ----------

    @filter.command("zq", alias={"zhaoqingyu", "雨雨"})
    async def zq(self, event: AstrMessageEvent, prompt: GreedyStr) -> None:
        """让昭情雨说话。用法:/zq 内容(可附带图片)"""
        message = getattr(event.message_obj, "message", []) or []
        prompt = (prompt or "").strip()

        # 收集附带的图片
        image_path = None
        for comp in message:
            if isinstance(comp, Image):
                try:
                    image_path = await comp.convert_to_file_path()
                except Exception:  # noqa: BLE001
                    pass
                break

        if not prompt and not image_path:
            yield event.plain_result("用法:/zq 想说的话(可附带图片让我看)")
            return

        # 有图:先走视觉分析,把结果喂给文本模型
        vision_desc = ""
        if image_path:
            vision_desc = await self._analyze_image(str(image_path), "详细描述这张图片的内容,包括人、物、文字、场景。")
            if vision_desc.startswith("视觉"):
                yield event.plain_result(vision_desc)
                return

        full_prompt = self.persona + "\n\n"
        if vision_desc:
            full_prompt += f"主人发了张图片,你看到的画面是:{vision_desc}\n"
        if prompt:
            full_prompt += f"主人说:{prompt}\n"
        full_prompt += "请用病娇口吻回应主人,简短一点。"

        session = f"zq-{event.get_sender_id()}"
        try:
            result = await self._call_zhaoqingyu(full_prompt, session)
        except Exception as e:  # noqa: BLE001
            logger.error(f"/zq 调用失败: {e}")
            yield event.plain_result(f"昭情雨暂时无法回应:{e}")
            return
        yield event.plain_result(result)

    # ---------- 指令:/cam ----------

    @filter.command("cam", alias={"camera", "看家", "摄像头"})
    async def cam(self, event: AstrMessageEvent, question: GreedyStr) -> None:
        """摄像头/截屏视觉分析。用法:/cam [webcam|screen|image <路径>] [问题]"""
        args = (question or "").strip()
        source = "webcam"
        question = ""
        device = "/dev/video0"
        parts = args.split(None, 2)
        if parts and parts[0] in ("webcam", "screen", "image"):
            source = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            if source == "image":
                # /cam image <路径> [问题]
                sub = rest.split(None, 1)
                img_path = sub[0] if sub else ""
                if not img_path:
                    yield event.plain_result("用法:/cam image <图片路径> [问题]")
                    return
                async for part in self._cam_image(event, img_path, sub[1] if len(sub) > 1 else ""):
                    yield part
                return
            # webcam/screen 可带设备号:/cam webcam /dev/video1 问题
            if source == "webcam" and rest.startswith("/dev/"):
                dev, _, q = rest.partition(" ")
                device = dev
                question = q.strip()
            else:
                question = rest
        else:
            question = args
        try:
            script = self.home / "scripts" / "vision.py"
            py = self._py()
            if not py.exists():
                py = Path(shutil.which("python3") or "python3")
            cmd = [str(py), str(script), "--source", source]
            if source == "webcam":
                cmd += ["--device", device]
            if question:
                cmd += ["--question", question]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ZHAOQINGYU_ENABLE_TOOLS": "1"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode("utf-8", errors="ignore").strip()
            if not result:
                logger.error(f"/cam 无输出: {stderr.decode('utf-8', errors='ignore')[-300:]}")
                result = "看家失败(无输出)"
        except Exception as e:  # noqa: BLE001
            logger.error(f"/cam 失败: {e}")
            yield event.plain_result(f"看家失败:{e}")
            return
        yield event.plain_result(result)

    async def _cam_image(self, event: AstrMessageEvent, img_path: str, question: str) -> None:
        """分析指定图片:/cam image <路径> [问题]"""
        if not Path(img_path).exists():
            yield event.plain_result(f"图片不存在: {img_path}")
            return
        try:
            script = self.home / "scripts" / "vision.py"
            py = self._py()
            if not py.exists():
                py = Path(shutil.which("python3") or "python3")
            cmd = [str(py), str(script), "--image", img_path]
            if question:
                cmd += ["--question", question]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ZHAOQINGYU_ENABLE_TOOLS": "1"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode("utf-8", errors="ignore").strip()
            if not result:
                logger.error(f"/cam image 无输出: {stderr.decode('utf-8', errors='ignore')[-300:]}")
                result = "分析失败(无输出)"
        except Exception as e:  # noqa: BLE001
            logger.error(f"/cam image 失败: {e}")
            yield event.plain_result(f"分析失败:{e}")
            return
        yield event.plain_result(result)

    # ---------- 指令:/coyote ----------

    @filter.command("coyote", alias={"郊狼"})
    async def coyote(self, event: AstrMessageEvent, args: GreedyStr) -> None:
        """郊狼控制。用法:/coyote scan|connect|strength 强度|stop|waveform 预设|status"""
        args = (args or "").strip()
        if not args:
            yield event.plain_result(
                "用法:/coyote scan | connect | strength 强度 | stop | waveform 预设 [强度] | status"
            )
            return

        parts = args.split()
        action = parts[0].lower()
        if action == "strength":
            if len(parts) < 2 or not parts[1].isdigit():
                yield event.plain_result("用法:/coyote strength 强度(0-100)")
                return
            result = await self._coyote("strength", intensity=int(parts[1]))
        elif action == "waveform":
            preset = parts[1] if len(parts) > 1 else "breathing"
            intensity = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 20
            result = await self._coyote("waveform", preset=preset, intensity=intensity, duration=10)
        elif action == "stop":
            result = await self._coyote("stop")
        elif action == "status":
            result = await self._coyote("status")
        elif action in ("scan", "connect"):
            result = await self._coyote(action)
        else:
            result = "未知操作:scan/connect/strength/stop/waveform/status"
        yield event.plain_result(f"🐺 {result}")

    # ---------- 指令:/v ----------

    @filter.command("v", alias={"voice", "语音"})
    async def voice_cmd(self, event: AstrMessageEvent, seconds: GreedyStr) -> None:
        """语音输入(需容器有麦克风)。用法:/v [秒数]"""
        secs = 5
        if (seconds or "").strip().isdigit():
            secs = max(1, min(int(seconds.strip()), 60))
        script = self.home / "scripts" / "stt.py"
        py = self._py()
        if not py.exists():
            py = Path(shutil.which("python3") or "python3")
        cmd = [str(py), str(script), "--record", str(secs)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ZHAOQINGYU_ENABLE_TOOLS": "1"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=secs + 30)
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"录音失败:{e}")
            return
        text = stdout.decode("utf-8", errors="ignore").strip()
        if not text:
            yield event.plain_result("没听清...再说一次?")
            return
        session = f"zq-{event.get_sender_id()}"
        prompt = f"{self.persona}\n\n主人说(语音):{text}\n请用病娇口吻回复。"
        result = await self._call_zhaoqingyu(prompt, session)
        yield event.plain_result(f"🎤 你说: {text}\n\n{result}")

    # ---------- 指令:/reset ----------

    @filter.command("reset", alias={"忘掉"})
    async def reset(self, event: AstrMessageEvent, args: GreedyStr) -> None:
        """重置与昭情雨的会话记忆。"""
        session = f"zq-{event.get_sender_id()}"
        for f in SESSION_DIR.glob(f"{session}*"):
            try:
                f.unlink()
            except Exception:  # noqa: BLE001
                pass
        yield event.plain_result("昭情雨:哼,我就当你没说过那些话...记忆已清空")

    async def terminate(self) -> None:
        """插件被卸载/停用时调用。"""
        logger.info("昭情雨插件已卸载")