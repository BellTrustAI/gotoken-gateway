# Usage 透传规范

> 适用范围:`gotoken-gateway` 所有计费相关路由。
> 目标:让下游(gotoken-api / OneAPI 类)的"上游返回"计费模式能拿到完整 token 详情,避免漏算或错算。

## 背景

下游按"上游返回"计费时,会从 gateway 响应体里 unmarshal `usage` 字段。如果 gateway 响应里只有最基础的 `prompt_tokens / completion_tokens`,下游就拿不到:

- prompt cache 命中(`cached_tokens` / `cache_read_input_tokens`)
- reasoning / thinking 输出(`reasoning_tokens` / `thoughtsTokenCount`)
- 多模态输入分类(`image_tokens` / `audio_tokens`)
- prompt cache 创建(`cache_creation_input_tokens`)

下游会按全价或最低价计算,要么 **少收用户的钱**(reasoning/image input 漏算),要么 **多收用户的钱**(cache hit 没折扣)。

## 设计

每个 provider 在内部 `ChatResponse` / `ImageGenerateResponse` 上挂一个 `raw_usage: dict | None` 字段,直接保留上游 SDK 返回的 usage 原始结构。

router 层在出口处把 `raw_usage` 按目标 API 标准映射成对应形态:

- 出口为 OpenAI Chat Completions → `_openai_chat_usage()`
- 出口为 Anthropic Messages → `_anthropic_usage()`
- 出口为 OpenAI Responses → `_openai_responses_usage()`
- 出口为 Gemini 原生 → `_gemini_usage_metadata()`
- 出口为 OpenAI Images → 直接透传 raw_usage(无需映射,Azure 已是 OpenAI 形态)

## 各 provider 的 raw_usage 形态

### Azure (chat / responses / images)

OpenAI SDK 回的对象,`_to_jsonable` 转成 dict 后已是 OpenAI 标准:

```json
{
  "prompt_tokens": 12,
  "completion_tokens": 11,
  "total_tokens": 23,
  "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
  "completion_tokens_details": {
    "reasoning_tokens": 0,
    "audio_tokens": 0,
    "accepted_prediction_tokens": 0,
    "rejected_prediction_tokens": 0
  }
}
```

图像接口返回略有不同(input/output 命名 + details):

```json
{
  "input_tokens": 16,
  "output_tokens": 196,
  "total_tokens": 212,
  "input_tokens_details": {"image_tokens": 0, "text_tokens": 16},
  "output_tokens_details": {"image_tokens": 196, "text_tokens": 0}
}
```

### Bedrock (Anthropic Claude)

`response_body['usage']` 原生 Anthropic 形态:

```json
{
  "input_tokens": 14,
  "output_tokens": 12,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0
}
```

### Gemini (Vertex AI)

SDK 的 `response.usage_metadata` 转成 dict 后(snake_case):

```json
{
  "prompt_token_count": 6,
  "candidates_token_count": 0,
  "cached_content_token_count": 0,
  "thoughts_token_count": 46,
  "total_token_count": 52
}
```

注意 `candidates_token_count` 不包含 `thoughts_token_count`,两者要分别透传给下游,否则 thinking 输出会漏算。

## 出口映射规则

### OpenAI Chat (`/v1/chat/completions`)

| 上游 | 映射 |
|---|---|
| Azure | 直接透传 raw_usage,补齐 prompt/completion/total_tokens 的默认值 |
| Bedrock | `prompt_tokens = input + cache_creation + cache_read`,`prompt_tokens_details = {cached_tokens: cache_read, cache_creation_input_tokens: cache_creation}` |
| Gemini | `prompt_tokens = prompt_token_count`,`completion_tokens = candidates_token_count`,`completion_tokens_details.reasoning_tokens = thoughts_token_count`,`prompt_tokens_details.cached_tokens = cached_content_token_count` |

### Anthropic Messages (`/v1/messages`)

| 上游 | 映射 |
|---|---|
| Bedrock | 直接透传 input/output/cache_*(原生 Anthropic 形态) |
| Azure | `cached_tokens` → `cache_read_input_tokens` |
| Gemini | `cached_content_token_count` → `cache_read_input_tokens` |

### OpenAI Responses (`/v1/responses`)

| 上游 | 映射 |
|---|---|
| Azure | 直接透传 raw_usage(已是 OpenAI Responses 形态) |
| 其他 | fallback 到基础 input/output/total |

### Gemini Native (`/v1beta/models/{model}:generateContent`)

只接 Gemini provider,字段名转成 camelCase:

```json
{
  "promptTokenCount": ...,
  "candidatesTokenCount": ...,
  "totalTokenCount": ...,
  "cachedContentTokenCount": ...,   // 仅在 cached>0 时输出
  "thoughtsTokenCount": ...          // 仅在 thoughts>0 时输出
}
```

### OpenAI Images (`/v1/images/generations` `/v1/images/edits`)

```json
{
  "created": 1780662957,
  "background": "opaque",
  "output_format": "png",
  "quality": "low",
  "size": "1024x1024",
  "data": [...],
  "usage": { ... 直接透传 Azure raw_usage ... }
}
```

`raw_meta`(background / output_format / quality / size 等)合并到顶层,与 OpenAI 官方响应保持一致。

## 路由全表

| 路由 | 是否计费 | usage 透传状态 |
|---|---|---|
| `POST /v1/chat/completions` | 是 | 完整 |
| `POST /v1/messages` | 是 | 完整 |
| `POST /v1/responses` | 是 | 完整 |
| `POST /v1beta/models/{m}:generateContent` | 是 | 完整 |
| `POST /v1beta/models/{m}:streamGenerateContent` | 是 | 完整(单事件吐出) |
| `POST /v1/images/generations` | 是 | 完整 |
| `POST /v1/images/edits` | 是 | 完整 |
| `POST /chat`(内部协议) | 否 | 不涉及下游计费 |
| `GET /healthz` | 否 | — |
| `GET /models` `GET /v1/models` `GET /v1beta/models` | 否 | — |

## 新增 provider / 路由 时的 checklist

1. provider 内部:返回的 `ChatResponse` / `ImageGenerateResponse` 必须填 `raw_usage`,直接保留上游 SDK 的 usage 对象 dump 后的 dict。**不要**自己手工拼一个最小集。
2. router 出口:不要直接 `{"prompt_tokens": x, "completion_tokens": y}`,要走对应的 `_xxx_usage()` helper。
3. 新增一种上游 → 在 helper 的 `if provider_name == "...":` 分支里加映射规则。
4. 新增一种出口协议 → 写一个新的 `_xxx_usage()` helper,定义清楚字段名。

## 验证方式

直接 curl gateway,对比 usage 字段是否包含 details。

```bash
curl -s -X POST 'https://gogogoai.hk/v1/chat/completions' \
  -H "Authorization: Bearer $GW_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.4","messages":[{"role":"user","content":"hi"}],"max_tokens":50}' \
  | jq .usage
```

期望输出包含 `prompt_tokens_details` 和 `completion_tokens_details`,而不是只有 `prompt_tokens / completion_tokens / total_tokens`。

## 历史背景

- 2026-06-05 初次发现 `/v1/images/generations` 计费每张图只算 $0.000005,排查后发现 gateway 响应 usage 里 `input_tokens=0`,下游兜底到最小值。
- 同一天系统排查所有 chat 路由,发现 azure / bedrock / gemini 都有不同程度的字段丢失:Gemini reasoning(thoughts)tokens 完全漏算最严重。
- 一次性修复见 commit `3b2faf9` (images) 和 `0a0bf1b` (chat / messages / responses / gemini)。
