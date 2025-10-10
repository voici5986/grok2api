"""Grok API 响应处理器模块"""

import json
import uuid
import time
import asyncio
from typing import Iterator
from app.core.config import setting
from app.core.exception import GrokApiException
from app.core.logger import logger
from app.models.openai_schema import (
    OpenAIChatCompletionResponse,
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionMessage,
    OpenAIChatCompletionChunkResponse,
    OpenAIChatCompletionChunkChoice,
    OpenAIChatCompletionChunkMessage
)
from app.services.grok.image_cache import image_cache_service


def _safe_run_async(coro):
    """安全地运行异步协程，无论是否有事件循环"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果循环正在运行，使用 ensure_future 调度任务
            return asyncio.ensure_future(coro)
        else:
            # 循环存在但未运行，直接运行
            return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有事件循环，创建新的
        return asyncio.run(coro)


class GrokResponseProcessor:
    """Grok API 响应处理器"""

    @staticmethod
    def process_response(response, auth_token: str) -> OpenAIChatCompletionResponse:
        """处理非流式响应"""
        try:
            for chunk in response.iter_lines():
                if not chunk:
                    continue

                data = json.loads(chunk.decode("utf-8"))

                # 错误检查
                if error := data.get("error"):
                    raise GrokApiException(
                        f"API错误: {error.get('message', '未知错误')}",
                        "API_ERROR",
                        {"code": error.get("code")}
                    )

                # 提取模型响应
                model_response = data.get("result", {}).get("response", {}).get("modelResponse")
                if not model_response:
                    continue

                # 检查 modelResponse 中的错误
                if error_msg := model_response.get("error"):
                    raise GrokApiException(
                        f"模型响应错误: {error_msg}",
                        "MODEL_ERROR"
                    )

                # 构建响应内容
                model = model_response.get("model")
                content = model_response.get("message", "")

                # 添加生成的图片
                if images := model_response.get("generatedImageUrls"):
                    for img in images:
                        # 下载并缓存图片
                        try:
                            cache_path = _safe_run_async(image_cache_service.download_image(f"/{img}", auth_token))
                            if cache_path:
                                # 使用本地缓存路径
                                img_path = img.replace('/', '-')
                                base_url = setting.global_config.get("base_url", "")
                                img_url = f"{base_url}/images/{img_path}" if base_url else f"/images/{img_path}"
                                content += f"\n![Generated Image]({img_url})"
                            else:
                                # 缓存失败，使用原始链接
                                content += f"\n![Generated Image](https://assets.grok.com/{img})"
                        except Exception as e:
                            logger.warning(f"[Processor] 缓存图片失败: {e}")
                            content += f"\n![Generated Image](https://assets.grok.com/{img})"

                # 返回OpenAI格式响应
                return OpenAIChatCompletionResponse(
                    id=f"chatcmpl-{uuid.uuid4()}",
                    object="chat.completion",
                    created=int(time.time()),
                    model=model,
                    choices=[OpenAIChatCompletionChoice(
                        index=0,
                        message=OpenAIChatCompletionMessage(
                            role="assistant",
                            content=content
                        ),
                        finish_reason="stop"
                    )],
                    usage=None
                )

            raise GrokApiException("无响应数据", "NO_RESPONSE")

        except json.JSONDecodeError as e:
            raise GrokApiException(f"JSON解析失败: {e}", "JSON_ERROR") from e

    @staticmethod
    def process_stream(response, auth_token: str) -> Iterator[str]:
        """处理流式响应"""
        is_image = False
        is_thinking = False
        thinking_finished = False
        chunk_index = 0
        model = None
        filtered_tags = setting.grok_config.get("filtered_tags", "").split(",")

        def make_chunk(content: str, finish: str = None):
            """生成OpenAI格式的响应块"""
            chunk_data = OpenAIChatCompletionChunkResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                created=int(time.time()),
                model=model or "grok-4-mini-thinking-tahoe",
                choices=[OpenAIChatCompletionChunkChoice(
                    index=chunk_index,
                    delta=OpenAIChatCompletionChunkMessage(
                        role="assistant",
                        content=content
                    ) if content else {},
                    finish_reason=finish
                )]
            ).model_dump()
            # SSE 格式返回
            return f"data: {json.dumps(chunk_data)}\n\n"

        try:
            for chunk in response.iter_lines():
                logger.debug(f"[Processor] 接收到数据块: {len(chunk)} bytes")
                if not chunk:
                    continue

                try:
                    data = json.loads(chunk.decode("utf-8"))

                    # 错误检查
                    if error := data.get("error"):
                        error_msg = error.get('message', '未知错误')
                        logger.error(f"[Processor] Grok API返回错误: {error_msg}")
                        yield make_chunk(f"Error: {error_msg}", "stop")
                        yield "data: [DONE]\n\n"
                        return

                    # 提取响应数据
                    grok_resp = data.get("result", {}).get("response", {})
                    logger.debug(f"[Processor] 解析响应数据: {len(grok_resp)} 字段")
                    if not grok_resp:
                        continue

                    # 更新模型名称
                    if user_resp := grok_resp.get("userResponse"):
                        if m := user_resp.get("model"):
                            model = m

                    # 检查生成模式
                    if grok_resp.get("imageAttachmentInfo"):
                        is_image = True

                    # 获取token
                    token = grok_resp.get("token", "")

                    # 图片模式
                    if is_image:
                        if model_resp := grok_resp.get("modelResponse"):
                            # 生成图片链接并缓存
                            content = ""
                            for img in model_resp.get("generatedImageUrls", []):
                                try:
                                    # 异步下载并缓存图片（不阻塞）
                                    _safe_run_async(image_cache_service.download_image(f"/{img}", auth_token))
                                    # 使用本地缓存路径
                                    img_path = img.replace('/', '-')
                                    base_url = setting.global_config.get("base_url", "")
                                    img_url = f"{base_url}/images/{img_path}" if base_url else f"/images/{img_path}"
                                    content += f"![Generated Image]({img_url})\n"
                                except Exception as e:
                                    logger.warning(f"[Processor] 缓存图片失败: {e}")
                                    content += f"![Generated Image](https://assets.grok.com/{img})\n"
                            yield make_chunk(content.strip(), "stop")
                            return
                        elif token:
                            yield make_chunk(token)
                            chunk_index += 1

                    # 对话模式
                    else:
                        # 过滤 list 格式的 token
                        if isinstance(token, list):
                            continue

                        # 过滤特定标签
                        if any(tag in token for tag in filtered_tags if token):
                            continue

                        # 获取当前状态
                        current_is_thinking = grok_resp.get("isThinking", False)
                        message_tag = grok_resp.get("messageTag")

                        # 跳过后续的 thinking
                        if thinking_finished and current_is_thinking:
                            continue

                        # 检查 toolUsageCardId
                        if grok_resp.get("toolUsageCardId"):
                            if web_search := grok_resp.get("webSearchResults"):
                                if current_is_thinking:
                                    # 添加搜索结果到 token
                                    for result in web_search.get("results", []):
                                        title = result.get("title", "")
                                        url = result.get("url", "")
                                        preview = result.get("preview", "")
                                        preview_clean = preview.replace("\n", "") if isinstance(preview, str) else ""
                                        token += f'\n- [{title}]({url} "{preview_clean}")'
                                    token += "\n"
                                else:
                                    # 有 webSearchResults 但 isThinking 为 false
                                    continue
                            else:
                                # 没有 webSearchResults
                                continue

                        if token:
                            content = token

                            # header 在 token 后换行
                            if message_tag == "header":
                                content = f"\n\n{token}\n\n"

                            # is_thinking 状态切换
                            if not is_thinking and current_is_thinking:
                                content = f"<think>\n{content}"
                            elif is_thinking and not current_is_thinking:
                                content = f"\n</think>\n{content}"
                                thinking_finished = True

                            yield make_chunk(content)
                            chunk_index += 1
                            is_thinking = current_is_thinking

                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"[Processor] 解析chunk失败: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"[Processor] 处理chunk出错: {e}")
                    continue

            # 发送结束块
            yield make_chunk("", "stop")
            # 发送流结束标记
            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"[Processor] 流式处理严重错误: {e}")
            yield make_chunk(f"处理错误: {e}", "error")
            # 发送流结束标记
            yield "data: [DONE]\n\n"