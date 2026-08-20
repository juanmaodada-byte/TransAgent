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
        # 备选模型未配置 → 直接抛清晰错误，不做注定失败的切换
        if not cfg.backup_api_key:
            raise RuntimeError(
                f"主力模型失败且备选模型未配置(QWEN_API_KEY未设置): {e}") from e
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
    """带重试的LLM调用。

    D7加固（json_mode间歇空响应）：json_mode 重试耗尽后，改用普通模式重试
    （去掉 response_format——疑似空响应元凶），从返回文本中抽取JSON。
    """
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

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=cfg.request_timeout_seconds,  # D7.1：接线请求超时（此前是死配置·单次调用默认600s上限）
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # 单阶段重试：普通模式调用（不传 response_format·提示词约束JSON）+ json_mode 解析
    # D7: response_format=json_object 疑似导致模型间歇性返回空内容 → 弃用，
    #     靠提示词要求输出JSON + 正则抽取（普通模式返回真实文本·稳定很多）
    ok, result = await _retry_loop(
        client, model, messages, temperature, max_tokens,
        json_mode=json_mode, retries=cfg.max_retries, cfg=cfg)
    if ok:
        return result
    raise RuntimeError(f"重试{cfg.max_retries}次均失败: {result}")


async def _single_call(
    client, model: str, messages: list, temperature: float,
    max_tokens: int, json_mode: bool,
) -> str | object:
    """单次LLM调用：普通模式（不传 response_format·提示词约束JSON）；json_mode → 解析/抽取JSON。"""
    cfg = get_config().llm
    _extra = {}
    if cfg.reasoning_effort:  # D9.1：限制推理深度（空响应根因=推理烧光max_tokens）
        _extra["reasoning_effort"] = cfg.reasoning_effort
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,  # 暂不启用流式（SSE层实现）
        **_extra,
    )
    content = response.choices[0].message.content or ""
    # D7.1：空响应视为可重试失败。实测 deepseek-v4-flash 约半数调用 HTTP200 但 content 为空：
    #       此前非 json 模式把空串当成功 → 润色静默产出空终稿；json 模式靠 _extract_json 兜底重试。
    #       统一在此拦截 → 走重试 → 重试耗尽后由技能内部降级兜底（质检8.0基础分 / 润色交付初译稿）。
    if not content.strip():
        raise ValueError(f"LLM返回空内容 (model={model})")
    if json_mode:
        return _extract_json(content)
    return content


def _extract_json(content: str):
    """从文本中解析JSON：直接解析 → 失败则正则抽取 {…} 块（容忍围栏/附加文字）。"""
    import json
    import re
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"JSON解析失败: {content[:200]}")


async def _retry_loop(
    client, model: str, messages: list, temperature: float,
    max_tokens: int, json_mode: bool, retries: int, cfg,
) -> tuple:
    """重试循环：成功返回 (True, 结果)；耗尽返回 (False, 最后错误)。"""
    last_error = None
    for attempt in range(retries):
        try:
            result = await _single_call(
                client, model, messages, temperature, max_tokens, json_mode)
            return True, result
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                # D9.1：空响应（HTTP200 但 content 空）非限流·固定 1s 快速重试；
                #       其他错误（429/超时等）才用指数退避。
                if isinstance(e, ValueError) and "空内容" in str(e):
                    delay = 1.0
                else:
                    delay = cfg.retry_delay_seconds * (2 ** attempt)  # 1s → 2s → 4s
                print(f"[LLM] 第{attempt + 1}次重试失败，{delay}s后重试...")
                await asyncio.sleep(delay)
    return False, last_error


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

    client = AsyncOpenAI(
        api_key=cfg.primary_api_key, base_url=cfg.primary_base_url,
        timeout=cfg.request_timeout_seconds,  # D7.1：接线请求超时
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        _extra = {}
        if cfg.reasoning_effort:  # D9.1：限制推理深度（空响应根因=推理烧光max_tokens）
            _extra["reasoning_effort"] = cfg.reasoning_effort
        stream = await client.chat.completions.create(
            model=cfg.primary_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **_extra,
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception:
        # 降级到非流式
        text = await chat(system_prompt, user_message, temperature, max_tokens, stream=False)
        yield text
