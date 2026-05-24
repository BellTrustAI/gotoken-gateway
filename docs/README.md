# gotoken-gateway

统一 AI API 代理网关。新加坡服务器 `47.236.56.253` 作为 CheckAI 的 Bedrock / OpenAI / Gemini 代理入口。

## 快速开始

```bash
cd deploy
cp .env.example .env
# 编辑 .env 填入 GATEWAY_API_KEY + AWS 凭据
docker compose up -d
```

## API

### `POST /chat` — 统一聊天入口

```
Authorization: Bearer <GATEWAY_API_KEY>
```

```json
{
  "provider": "bedrock",
  "model": "claude-opus-4-7",
  "messages": [{"role": "user", "content": "hello"}],
  "max_tokens": 512,
  "temperature": 0.7,
  "stream": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider | string | 是 | `bedrock` / `openai` / `gemini` |
| model | string | 是 | 模型 ID，见下方模型清单 |
| messages | array | 是 | 标准 chat messages |
| max_tokens | int | 否 | 默认 512 |
| temperature | float | 否 | 默认 0.7 |
| stream | bool | 否 | 默认 false（暂未实现） |

### `GET /healthz` — 健康检查

### `GET /models?provider=bedrock` — 列出可用模型

```
Authorization: Bearer <GATEWAY_API_KEY>
```

## 管理后台

访问 `/gateway` 管理 provider 凭据和 API Token：

- 输入管理密码解锁
- **AWS Bedrock / OpenAI / Gemini** — 管理 provider 凭据和模型清单
- **Tokens** — 为下游客户端（如 One API）创建/撤销 API Token
- **使用指南** — API 接入说明和 curl 示例
- 凭据和 Token 保存到 `config.json`（docker volume 持久化）
- 保存后即时生效，无需重启

## 添加新 Provider

1. `app/providers/<name>.py` — 实现 `ChatProvider` 接口
2. `app/router.py` — 在 `_providers` 字典中注册

## 部署

### 服务器信息

| 项目 | 值 |
|------|------|
| IP | `47.236.56.253`（阿里云新加坡） |
| 用户 | `root` |
| 项目路径 | `/opt/gotoken-gateway` |
| 域名 | `gogogotoken.cn` |
| 网关端口 | `8789`（仅本地，不对外开放） |
| 仓库 | `https://github.com/BellTrustAI/gotoken-gateway` |

### 初次部署

```bash
# 1. SSH 登录
ssh root@47.236.56.253

# 2. 克隆仓库
git clone git@github.com:BellTrustAI/gotoken-gateway.git /opt/gotoken-gateway

# 3. 配置环境变量
cd /opt/gotoken-gateway/deploy
cp .env.example .env
vim .env  # 填入 GATEWAY_API_KEY

# 4. 启动（docker-compose.yml 已配置自动连接 deploy_checkai_net）
docker compose up -d --build
```

### Caddy 反向代理配置

Caddy 配置在 `/opt/checkai/deploy/Caddyfile`，gotoken 相关部分：

```caddyfile
gogogotoken.cn {
    encode zstd gzip
    reverse_proxy deploy-gateway-1:8789
}
```

修改后重载：`docker exec checkai_caddy caddy reload --config /etc/caddy/Caddyfile`

### 日常更新部署

```bash
# === 方式 A：git pull（推荐） ===
ssh root@47.236.56.253 "cd /opt/gotoken-gateway && git pull && cd deploy && docker compose up -d --build"

# === 方式 B：本地 scp（无需服务器访问 GitHub） ===
scp app/provider_config.py root@47.236.56.253:/opt/gotoken-gateway/app/
scp app/auth.py root@47.236.56.253:/opt/gotoken-gateway/app/
scp app/admin_router.py root@47.236.56.253:/opt/gotoken-gateway/app/
scp app/static/gateway.html root@47.236.56.253:/opt/gotoken-gateway/app/static/

ssh root@47.236.56.253 "cd /opt/gotoken-gateway/deploy && docker compose up -d --build"

# === 验证 ===
curl -s -o /dev/null -w '%{http_code}' https://gogogotoken.cn/healthz
# 期望: 200
```

### 网络架构

```
用户 → gogogotoken.cn:443 → Caddy (deploy_checkai_net)
                                ↓ reverse_proxy deploy-gateway-1:8789
                           Gateway (deploy_default + deploy_checkai_net)
                                ↓ 127.0.0.1:8789（仅宿主机内部）
```

- 8789 端口**不需**在安全组放开，所有流量通过 Caddy 443 端口转发
- Gateway 容器同时加入 `deploy_default` 和 `deploy_checkai_net` 两个网络

## 配置

| 变量 | 必填 | 说明 |
|------|------|------|
| `GATEWAY_API_KEY` | 是 | 管理密码 + API 鉴权 |
| `AWS_ACCESS_KEY_ID` | 否 | 首次启动 fallback（后续在 /gateway 管理） |
| `AWS_SECRET_ACCESS_KEY` | 否 | 同上 |
| `AWS_REGION` | 否 | 默认 `us-west-2` |
| `GATEWAY_CONFIG_PATH` | 否 | config.json 路径（默认仓库根） |

Provider 凭据和模型清单通过 `/gateway` 管理页面持久化到 `config.json`，
环境变量仅作为首次启动的 fallback。
