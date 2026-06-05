# Image API 对接(`/v1/images/generations` `/v1/images/edits`)

## 概述

Gateway 暴露 OpenAI 标准的图像生成接口,内部转发到 Azure Foundry 的 v1 endpoint,目前接入 `gpt-image-2`。

```
客户端 → gotoken-api(渠道类型 OpenAI)→ gateway(/v1/images/*)→ Azure Foundry
```

## 下游(gotoken-api)渠道配置

新建 OpenAI 渠道:

| 字段 | 值 |
|---|---|
| 渠道类型 | **OpenAI**(不是 Azure) |
| Base URL | `https://gogogoai.hk`(末尾不带 `/v1`) |
| 密钥 | gateway 的 `api_tokens` 中的 token |
| 模型列表 | 加上 `gpt-image-2` |

为什么不能选 Azure 渠道:Azure 渠道会把 path 改写成 `/openai/deployments/{model}/{task}?api-version=...`,且 header 改成 `api-key: ...`。Gateway 暴露的是 OpenAI 兼容 path,这两套不一致会直接 404。

## Gateway 配置

`config.json` (服务器路径 `/opt/gotoken-gateway/deploy/config.json`,docker volume 挂载):

```json
{
  "azure": {
    "endpoint": "https://admin-7836-resource.services.ai.azure.com/openai/v1",
    "api_key": "<azure key>",
    "models": ["gpt-image-2", "..."]
  }
}
```

注意:`endpoint` 必须用 Azure 新版 v1 endpoint(以 `/openai/v1` 结尾),不是老的 `*.openai.azure.com/openai`。

## `/v1/images/generations`

### 请求

```bash
curl -X POST 'https://gogogoai.hk/v1/images/generations' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-image-2",
    "prompt": "a red fox in autumn forest",
    "n": 1,
    "size": "1024x1024",
    "output_format": "png",
    "output_compression": 100
  }'
```

### 响应

```json
{
  "created": 1780663414,
  "background": "opaque",
  "output_format": "png",
  "quality": "low",
  "size": "1024x1024",
  "data": [{"b64_json": "..."}],
  "usage": {
    "input_tokens": 16,
    "output_tokens": 196,
    "total_tokens": 212,
    "input_tokens_details": {"image_tokens": 0, "text_tokens": 16},
    "output_tokens_details": {"image_tokens": 196, "text_tokens": 0}
  }
}
```

## `/v1/images/edits`

`multipart/form-data` 形态,字段严格按 OpenAI 标准。

### 请求

```bash
curl -X POST 'https://gogogoai.hk/v1/images/edits' \
  -H "Authorization: Bearer $TOKEN" \
  -F 'model=gpt-image-2' \
  -F 'prompt=turn the red square blue with floating flowers' \
  -F 'image=@/tmp/red.png' \
  -F 'n=1' \
  -F 'size=1024x1024'
```

### 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `image` | 是 | 单文件;也接受多文件 `image[]` 或 `image[0]..image[N]`(对齐 gotoken-api adaptor) |
| `prompt` | 是 | |
| `mask` | 否 | 与 image 等大的 PNG,透明区被编辑 |
| `model` | 否(默认 `dall-e-2`) | 用 `gpt-image-2` |
| `n` `size` `quality` `response_format` `output_format` `output_compression` `background` `user` | 否 | 透传 |

### 响应

同 `/v1/images/generations`,`usage.input_tokens_details.image_tokens` 会包含输入图的 token 数。

## 计费验证

每张图实际成本 ~$0.005~$0.04(取决于 quality),以下游"上游返回"模式按 token 计算。

之前 bug 表现:每张图只计 $0.000005(因为 gateway 没把 usage 传出去)。修复后,gotoken-api 后台日志能看到合理的 input/output 数,例如:

```
(输入 15 + 图片输入 16 / 1M * $5 + 输出 196 / 1M * $30) × 倍率 = $0.006083
```

详情见 [usage_passthrough.md](./usage_passthrough.md)。

## 已知坑

1. **下游渠道千万不能选 Azure**,要选 OpenAI 兼容渠道(见上)。
2. **客户端调用图像模型必须用 `/v1/images/*`,不能用 `/v1/chat/completions`**,否则 Azure 会返 `400 The requested operation is unsupported`,gotoken-api 包装成 `502 Provider error`。
3. **`config.json` 是 docker volume 挂载**,本地代码仓库的不会进镜像,要改服务器上的那份(或走 `/gateway` 管理后台改,即时生效不用重启)。
