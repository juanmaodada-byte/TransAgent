"""
LLM客户端
=========
Vibe Coder A | v1.0 | 2026-08-06

职责：封装DeepSeek V4 Flash API调用（兼容OpenAI SDK）。
      自动重试、指数退避、备选模型切换。

使用：
    from transagent.backend.core.llm_client import chat
    response = await chat(system_prompt, user_message, temperature=0.3)
"""

import asyncio
import time
from transagent.backend.config import get_config, LLMConfig


async def chat(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    json_mode: bool = False,
    stream: bool = False,
) -> str | object:
    """
    调用LLM（DeepSeek V4 Flash为主力）。

    Args:
        system_prompt: 系统提示词
        user_message: 用户消息/待翻译文本
        temperature: 温度（策略0.3·术语0.2·主译0.2·质检0.3·润色0.4）
        max_tokens: 最大输出token
        json_mode: 是否要求JSON格式输出
        stream: 是否流式返回

    Returns:
        非流式返回文本字符串，流式返回 async generator
        如果json_mode=True，自动解析为dict并返回
    """
    cfg = get_config().llm

    # 主力模型尝试
    try:
        return await _call_with_retry(
            cfg, system_prompt, user_message, temperature, max_tokens,
            json_mode, stream, use_backup=False,
        )
    except Exception as e:
        print(f"[LLM] 主力模型 DeepSeek 失败: {e}")
        # 切换备选模型
        try:
            print("[LLM] 切换到备选模型 通义千问...")
            return await _call_with_retry(
                cfg, system_prompt, user_message, temperature, max_tokens,
                json_mode, stream, use_backup=True,
            )
        except Exception as e2:
            raise RuntimeError(f"主力+备选模型均失败: {e} / {e2}")


async def _call_with_retry(
    cfg: LLMConfig,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    stream: bool,
    use_backup: bool,
) -> str | object:
    """带重试的LLM调用"""

    if use_backup:
        model = cfg.backup_model
        api_key = cfg.backup_api_key
        base_url = cfg.backup_base_url
    else:
        model = cfg.primary_model
        api_key = cfg.primary_api_key
        base_url = cfg.primary_base_url

    if not api_key:
        raise ValueError(f"API Key未设置: {'DEEPSEEK_API_KEY' if not use_backup else 'QWEN_API_KEY'}")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    extra_kwargs: dict = {}
    if json_mode:
        extra_kwargs["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(cfg.max_retries):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,  # 暂不启用流式（SSE层实现）
                **extra_kwargs,
            )
            content = response.choices[0].message.content or ""

            if json_mode:
                import json
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # 尝试提取JSON块
                    import re
                    match = re.search(r'\{[\s\S]*\}', content)
                    if match:
                        return json.loads(match.group(0))
                    raise ValueError(f"JSON解析失败: {content[:200]}")

            return content

        except Exception as e:
            last_error = e
            if attempt < cfg.max_retries - 1:
                delay = cfg.retry_delay_seconds * (2 ** attempt)  # 1s → 2s → 4s
                print(f"[LLM] 第{attempt + 1}次重试失败，{delay}s后重试...")
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"重试{cfg.max_retries}次均失败: {last_error}")

    raise RuntimeError(f"重试{cfg.max_retries}次均失败: {last_error}")


async def chat_stream(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
):
    """
    流式LLM调用——返回 async generator，逐token yield文本。
    用于SSE推送给前端实现流式展示。
    """
    cfg = get_config().llm
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=cfg.primary_api_key, base_url=cfg.primary_base_url)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        stream = await client.chat.completions.create(
            model=cfg.primary_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception:
        # 降级到非流式
        text = await chat(system_prompt, user_message, temperature, max_tokens, stream=False)
        yield text
