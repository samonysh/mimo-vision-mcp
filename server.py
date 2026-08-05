"""
MiMo Vision MCP Server

一个支持图像输入的小米 MiMo Vision LLM 的 MCP 服务器。
通过标准 MCP 协议对外暴露工具，调用小米 MiMo 的 OpenAI 兼容接口完成多模态对话。

认证方式：
    - HTTP 模式（streamable-http）：客户端通过请求头 X-Mimo-Api-Key 传入 API Key
    - stdio 模式：通过环境变量 MIMO_API_KEY 传入 API Key

图片上传（HTTP 模式）：
    - POST /upload        multipart 文件上传，返回 URL
    - POST /upload/base64 JSON base64 上传，返回 URL
    - upload_image MCP 工具：通过 MCP 协议上传 base64 图片
    - 上传的图片自动定期清理（默认 30 分钟过期）

环境变量（见 .env.example）：
    MIMO_API_KEY      stdio 模式下必填，HTTP 模式下可选（可由请求头替代）
    MIMO_BASE_URL     可选，API 根地址，默认 https://api.xiaomimimo.com/v1
    MIMO_MODEL        可选，默认模型，默认 mimo-v2.5
    MIMO_MAX_TOKENS   可选，默认最大生成 token 数，默认 1024
    UPLOAD_DIR        可选，上传文件目录，默认 /tmp/mimo-uploads
    UPLOAD_TTL        可选，上传文件过期时间（秒），默认 1800（30 分钟）
    CLEANUP_INTERVAL  可选，清理检查间隔（秒），默认 300（5 分钟）
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# 兼容两种包：优先使用 fastmcp（Docker/HTTP 模式），回退到 mcp.server.fastmcp（stdio 模式）
try:
    from fastmcp import FastMCP
    _HAS_FASTMCP = True
except ImportError:
    from mcp.server.fastmcp import FastMCP
    _HAS_FASTMCP = False

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

# 上传文件配置
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/mimo-uploads"))
UPLOAD_TTL = int(os.getenv("UPLOAD_TTL", "1800"))        # 30 分钟
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "300"))  # 5 分钟

# 允许的图片 MIME 类型（OpenAI 兼容接口通常接受这些）
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# 创建 FastMCP 实例（fastmcp 模式下启用 stateless_http）
_kwargs = {"stateless_http": True} if _HAS_FASTMCP else {}
mcp = FastMCP(
    "mimo-vision",
    instructions=(
        "小米 MiMo Vision 视觉语言模型。可通过图像 URL 或本地图片路径进行多模态对话，"
        "支持图像描述、图像问答、OCR 识别等任务。"
        "远程部署时，可使用 upload_image 工具先上传本地图片，再调用识别工具。"
    ),
    **_kwargs,
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
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
        if request is not None:
            key = request.headers.get("x-mimo-api-key", "")
            if key:
                return key
    except Exception:
        pass
    return API_KEY


def _save_upload(image_bytes: bytes, ext: str) -> str:
    """保存上传的图片字节到临时目录，返回可访问的 URL。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    filepath = UPLOAD_DIR / name
    filepath.write_bytes(image_bytes)
    # 返回相对 URL（通过 /uploads/ 静态文件服务访问）
    return f"/uploads/{name}"


async def _call_mimo(messages: list[dict[str, Any]], model: str, max_tokens: int) -> str:
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        resp = await client.post(CHAT_URL, headers=headers, json=payload)
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
    """构造多模态 messages。image 可以是 URL、本地路径或 data URL。"""
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
async def describe_image(
    image: str,
    prompt: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """描述一张图片的内容。支持传入图片 URL 或本地图片文件路径。

    Args:
        image: 图片的 http/https URL，或本地图片文件绝对路径（如 C:/path/to/img.png）。
        prompt: 可选的提问/描述指令，默认"请描述这张图片的内容"。
        model: 可选，覆盖默认模型（默认为环境变量 MIMO_MODEL 或 mimo-v2.5）。
        max_tokens: 可选，覆盖默认最大生成 token 数。
    """
    text = prompt or "请描述这张图片的内容。"
    messages = _build_messages(image, text)
    return await _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


@mcp.tool()
async def chat_with_image(
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
    return await _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


@mcp.tool()
async def ocr_image(
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
    return await _call_mimo(messages, model or DEFAULT_MODEL, max_tokens or DEFAULT_MAX_TOKENS)


@mcp.tool()
async def upload_image(image_data: str, filename: str | None = None) -> str:
    """上传 base64 编码的图片到服务器，返回可直接用于其他工具的 URL。

    适用于远程部署场景：客户端将本地图片编码为 base64 后通过 MCP 协议上传，
    返回的 URL 可直接传给 describe_image、chat_with_image、ocr_image 工具。
    上传的图片会在 30 分钟后自动删除。

    Args:
        image_data: base64 编码的图片数据，可带 data:image/xxx;base64, 前缀。
        filename: 可选，原始文件名，用于推断图片格式。
    """
    # 解析 data URL 前缀
    if "," in image_data and image_data.startswith("data:"):
        header, image_data = image_data.split(",", 1)
        ext = ".jpg"
        if "image/png" in header:
            ext = ".png"
        elif "image/webp" in header:
            ext = ".webp"
        elif "image/gif" in header:
            ext = ".gif"
    else:
        ext = Path(filename).suffix if filename else ".jpg"

    try:
        content = base64.b64decode(image_data)
    except Exception:
        raise ValueError("无效的 base64 数据")

    url_path = _save_upload(content, ext)
    # 返回完整 URL（基于请求 host）
    try:
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
        if request is not None:
            host = request.headers.get("host", f"localhost:{MCP_PORT}")
            scheme = request.headers.get("x-forwarded-proto", "http")
            return f"{scheme}://{host}{url_path}"
    except Exception:
        pass
    return f"http://localhost:{MCP_PORT}{url_path}"


# ---------------------------------------------------------------------------
# HTTP 上传端点 + 自动清理（仅 fastmcp 模式）
# ---------------------------------------------------------------------------
def _create_app():
    """创建带上传端点和自动清理的 Starlette 应用。"""
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import JSONResponse
    from starlette.staticfiles import StaticFiles

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    async def upload_file(request):
        """POST /upload — multipart 文件上传，返回 URL。"""
        form = await request.form()
        file = form.get("file")
        if file is None:
            return JSONResponse({"error": "No file provided"}, status_code=400)

        ext = Path(file.filename).suffix if file.filename else ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename

        content = await file.read()
        filepath.write_bytes(content)

        host = request.headers.get("host", f"localhost:{MCP_PORT}")
        scheme = request.headers.get("x-forwarded-proto", "http")
        url = f"{scheme}://{host}/uploads/{filename}"
        return JSONResponse({"url": url, "path": str(filepath)})

    async def upload_base64(request):
        """POST /upload/base64 — JSON base64 上传，返回 URL。"""
        data = await request.json()
        image_data = data.get("image", "")
        if not image_data:
            return JSONResponse({"error": "No image data provided"}, status_code=400)

        # 解析 data URL 前缀
        if "," in image_data and image_data.startswith("data:"):
            header, image_data = image_data.split(",", 1)
            ext = ".jpg"
            if "image/png" in header:
                ext = ".png"
            elif "image/webp" in header:
                ext = ".webp"
            elif "image/gif" in header:
                ext = ".gif"
        else:
            ext = data.get("ext", ".jpg")

        try:
            content = base64.b64decode(image_data)
        except Exception:
            return JSONResponse({"error": "Invalid base64 data"}, status_code=400)

        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename
        filepath.write_bytes(content)

        host = request.headers.get("host", f"localhost:{MCP_PORT}")
        scheme = request.headers.get("x-forwarded-proto", "http")
        url = f"{scheme}://{host}/uploads/{filename}"
        return JSONResponse({"url": url, "path": str(filepath)})

    async def cleanup_task():
        """定期清理过期的上传文件。"""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            for f in UPLOAD_DIR.iterdir():
                if f.is_file() and (now - f.stat().st_mtime) > UPLOAD_TTL:
                    f.unlink(missing_ok=True)

    mcp_app = mcp.http_app()

    app = Starlette(
        routes=[
            Route("/upload", upload_file, methods=["POST"]),
            Route("/upload/base64", upload_base64, methods=["POST"]),
            Mount("/uploads", app=StaticFiles(directory=str(UPLOAD_DIR))),
            Mount("/", app=mcp_app),
        ]
    )

    @app.on_event("startup")
    async def _on_startup():
        # 启动时清理旧文件
        for f in UPLOAD_DIR.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
        # 启动定期清理协程
        asyncio.create_task(cleanup_task())

    return app


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    if MCP_TRANSPORT == "stdio":
        mcp.run()
    elif MCP_TRANSPORT in ("http", "streamable-http"):
        if _HAS_FASTMCP:
            # fastmcp 模式：使用自定义 Starlette 应用（带上传端点）
            import uvicorn
            app = _create_app()
            uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
        else:
            # 回退模式：直接使用 mcp.run()
            mcp.run(
                transport="streamable-http",
                host=MCP_HOST,
                port=MCP_PORT,
                stateless_http=True,
            )
    else:
        raise ValueError(
            f"不支持的传输模式: {MCP_TRANSPORT}，请使用 stdio 或 streamable-http"
        )


if __name__ == "__main__":
    main()
