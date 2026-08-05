"""
MiMo Vision MCP Server

一个支持图像输入的小米 MiMo Vision LLM 的 MCP 服务器。
通过标准 MCP 协议对外暴露工具，调用小米 MiMo 的 OpenAI 兼容接口完成多模态对话。

认证方式：
    - HTTP 模式（streamable-http）：客户端通过请求头 X-Mimo-Api-Key 传入 API Key
    - stdio 模式：通过环境变量 MIMO_API_KEY 传入 API Key

环境变量（见 .env.example）：
    MIMO_API_KEY    stdio 模式下必填，HTTP 模式下可选（可由请求头替代）
    MIMO_BASE_URL   可选，API 根地址，默认 https://api.xiaomimimo.com/v1
    MIMO_MODEL      可选，默认模型，默认 mimo-v2.5
    MIMO_MAX_TOKENS 可选，默认最大生成 token 数，默认 1024
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

# 加载 .env 文件（若存在）
load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
API_KEY = os.getenv("MIMO_API_KEY", "")
BASE_URL = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/")
DEFAULT_MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5")
DEFAULT_MAX_TOKENS = int(os.getenv("MIMO_MAX_TOKENS", "1024"))
CHAT_URL = f"{BASE_URL}/chat/completions"

# 传输模式配置：stdio（本地 MCP 客户端）| streamable-http（远程/Docker 部署）
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

# 允许的图片 MIME 类型（OpenAI 兼容接口通常接受这些）
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

mcp = FastMCP(
    "mimo-vision",
    instructions=(
        "小米 MiMo Vision 视觉语言模型。可通过图像 URL 或本地图片路径进行多模态对话，"
        "支持图像描述、图像问答、OCR 识别等任务。"
    ),
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _is_url(value: str) -> bool:
    """判断给定字符串是否为 http/https URL。"""
    return value.startswith(("http://", "https://"))


def _mime_of(path: Path) -> str:
    """根据文件扩展名推断 MIME 类型，失败时回退为 image/png。"""
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime in _ALLOWED_IMAGE_TYPES:
        return mime
    # 兜底：按扩展名常见映射处理
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


def _local_image_to_data_url(path: str) -> str:
    """将本地图片文件编码为 data URL，供视觉模型读取。"""
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise ValueError(f"找不到图片文件: {file_path}")

    with open(file_path, "rb") as f:
        data = f.read()

    mime = _mime_of(file_path)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _get_api_key() -> str:
    """获取 API Key：优先从 HTTP 请求头 X-Mimo-Api-Key 获取，回退到环境变量。"""
    try:
        request = get_http_request()
        if request is not None:
            key = request.headers.get("x-mimo-api-key", "")
            if key:
                return key
    except Exception:
        pass
    return API_KEY


def _call_mimo(messages: list[dict[str, Any]], model: str, max_tokens: int) -> str:
    """调用小米 MiMo /chat/completions OpenAI 兼容接口。"""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置 API Key。请通过请求头 X-Mimo-Api-Key 或环境变量 MIMO_API_KEY 提供。"
        )

    payload = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        resp = client.post(CHAT_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"MiMo 返回内容解析失败: {data}")


def _build_messages(
    image: str,
    text: str,
    system: str | None = None,
) -> list[dict[str, Any]]:
    """构造多模态 messages。image 可以是 URL 或本地路径。"""
    messages: list[dict[str, Any]] = []

    if system:
        messages.append({"role": "system", "content": system})

    image_ref = image if _is_url(image) else _local_image_to_data_url(image)
    user_content = [
        {
            "type": "image_url",
            "image_url": {"url": image_ref},
        },
        {"type": "text", "text": text},
    ]
    messages.append({"role": "user", "content": user_content})
    return messages


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def describe_image(
    image: str,
    prompt: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """描述一张图片的内容。支持传入图片 URL 或本地图片文件路径。

    Args:
        image: 图片的 http/https URL，或本地图片文件绝对路径（如 C:/path/to/img.png）。
        prompt: 可选的提问/描述指令，默认“请描述这张图片的内容”。
        model: 可选，覆盖默认模型（默认为环境变量 MIMO_MODEL 或 mimo-v2.5）。
        max_tokens: 可选，覆盖默认最大生成 token 数。
    """
    text = prompt or "请描述这张图片的内容。"
    messages = _build_messages(image, text)
    return _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


@mcp.tool()
def chat_with_image(
    image: str,
    question: str,
    model: str | None = None,
    max_tokens: int | None = None,
    system_prompt: str | None = None,
) -> str:
    """针对图片进行多模态问答。

    Args:
        image: 图片的 http/https URL，或本地图片文件绝对路径。
        question: 针对图片的问题。
        model: 可选，覆盖默认模型。
        max_tokens: 可选，覆盖默认最大生成 token 数。
        system_prompt: 可选，自定义 system 提示词。
    """
    messages = _build_messages(image, question, system=system_prompt)
    return _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


@mcp.tool()
def ocr_image(
    image: str,
    model: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """识别图片中的文字（OCR）。支持图片 URL 或本地图片文件路径。

    Args:
        image: 图片的 http/https URL，或本地图片文件绝对路径。
        model: 可选，覆盖默认模型。
        max_tokens: 可选，覆盖默认最大生成 token 数。
    """
    text = "请识别这张图片中的所有文字并原样输出。"
    messages = _build_messages(image, text)
    return _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    if MCP_TRANSPORT == "stdio":
        mcp.run()
    elif MCP_TRANSPORT in ("http", "streamable-http"):
        mcp.run(
            transport="streamable-http",
            host=MCP_HOST,
            port=MCP_PORT,
        )
    else:
        raise ValueError(
            f"不支持的传输模式: {MCP_TRANSPORT}，请使用 stdio 或 streamable-http"
        )


if __name__ == "__main__":
    main()