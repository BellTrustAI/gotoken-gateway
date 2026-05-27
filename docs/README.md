# gotoken-gateway

统一 AI API 代理网关。新加坡服务器 `47.237.163.119` 作为 Bedrock / OpenAI / Gemini 代理入口。

## 快速开始

```bash
cd deploy
cp .env.example .env
# 编辑 .env 填入 GATEWAY_API_KEY + AWS 凭据
docker compose up -d --build
```

## API

### 认证方式

支持两种鉴权方式（等效）：

```
Authorization: Bearer <token>     # OpenAI 风格
x-api-key: <token>                 # Anthropic 风格
```

Token 可通过管理后台创建（管理密钥或 API Token 均可）。

### `POST /v1/chat/completions` — OpenAI 兼容 Chat

```json
{
  "model": "claude-haiku-4.5",
  "messages": [{"role": "user", "content": "hello"}],
  "max_tokens": 512,
  "temperature": 0.7
}
```

请求/响应格式与 OpenAI Chat Completions API 一致。
Model 名自动匹配对应 provider，无需手动指定。

### `POST /v1/messages` — Anthropic Messages 兼容

```json
{
  "model": "claude-haiku-4.5",
  "max_tokens": 512,
  "messages": [{"role": "user", "content": "hello"}],
  "system": "optional system prompt"
}
```

请求/响应格式与 Anthropic Messages API 一致。
支持 `x-api-key` 和 `Authorization: Bearer` 两种认证。

### `GET /v1/models` — OpenAI 兼容模型列表

### `GET /healthz` — 健康检查（无需认证）

### 内部接口（需要 provider 参数）

| 端点 | 说明 |
|------|------|
| `POST /chat` | 需指定 `provider` 字段 |
| `GET /models?provider=bedrock` | 按 provider 列出模型 |

## 管理后台

访问 `/gateway` 管理 provider 凭据和 API Token：

- 输入管理密码解锁
- **AWS Bedrock / OpenAI / Gemini** — 管理 provider 凭据和模型清单
- **Tokens** — 为下游客户端（如 One API）创建/撤销 API Token
- **使用指南** — API 接入说明和 curl 示例
- 凭据和 Token 保存到 `config.json`（docker volume 持久化）
- 保存后即时生效，无需重启

## One API / 上游渠道接入

在 One API 面板中添加渠道：

| 场景 | 渠道类型 | Base URL | 密钥 |
|------|----------|----------|------|
| Claude 模型 | Anthropic Claude | `https://gogogoai.hk` | API Token |
| 其他模型 | OpenAI | `https://gogogoai.hk` | API Token |

## 部署

### 服务器信息

| 项目 | 值 |
|------|------|
| IP | `47.237.163.119`（阿里云新加坡） |
| 用户 | `root` |
| 项目路径 | `/opt/gotoken-gateway` |
| 域名 | `gogogoai.hk` |
| 网关端口 | `8789`（仅本地 `127.0.0.1`，不对外开放） |
| 反代 | Caddy 2.9（宿主机直接安装） |
| 仓库 | `https://github.com/BellTrustAI/gotoken-gateway` |

### 初次部署

```bash
# 1. SSH 登录
ssh root@47.237.163.119

# 2. 安装 Docker（如未安装）
cat > /etc/yum.repos.d/docker-ce.repo << 'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/centos/9/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://download.docker.com/linux/centos/gpg
EOF
dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable docker --now

# 3. 部署项目
mkdir -p /opt/gotoken-gateway/deploy
# 上传代码到 /opt/gotoken-gateway/
# 上传 deploy/.env deploy/config.json

# 4. 构建启动
cd /opt/gotoken-gateway
docker compose build
docker compose up -d

# 5. 验证
curl -s http://127.0.0.1:8789/healthz
# 期望: {"status":"ok"}
```

### docker-compose.yml

```yaml
services:
  gateway:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: gotoken-gateway
    ports:
      - "127.0.0.1:8789:8789"
    env_file:
      - deploy/.env
    volumes:
      - ./deploy/config.json:/app/config.json
    restart: unless-stopped

networks:
  default:
    driver: bridge
```

### Caddy 反向代理

Caddy 直接安装在宿主机，配置 `/etc/caddy/Caddyfile`：

```caddy
gogogoai.hk {
    encode zstd gzip

    reverse_proxy 127.0.0.1:8789 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
        flush_interval -1
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    log {
        output file /var/log/caddy/gateway-access.log
    }
}
```

修改后重载：

```bash
systemctl reload caddy
```

### Caddy 安装（新服务器）

```bash
# 下载二进制
curl -fsSL https://github.com/caddyserver/caddy/releases/download/v2.9.1/caddy_2.9.1_linux_amd64.tar.gz \
  | tar -xz -C /usr/local/bin/ caddy

# 创建用户和目录
useradd -r -d /var/lib/caddy -m -s /sbin/nologin caddy
mkdir -p /etc/caddy /var/log/caddy

# systemd service
cat > /etc/systemd/system/caddy.service << 'SVC'
[Unit]
Description=Caddy Web Server
After=network.target
[Service]
User=root
Group=root
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Restart=on-failure
LimitNOFILE=1048576
LimitNPROC=512
[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload && systemctl enable caddy --now
```

### 网络架构

```
用户 → gogogoai.hk:443 → Caddy (宿主机)
                            ↓ reverse_proxy 127.0.0.1:8789
                         Gateway (Docker 容器, bridge 网络)
```

- 8789 端口**不需**在安全组放开，所有流量通过 Caddy 443 端口转发
- Gateway 容器使用独立 bridge 网络，不依赖外部 Docker 网络

### 日常更新部署

```bash
# === 方式 A：git pull（推荐） ===
ssh root@47.237.163.119 "cd /opt/gotoken-gateway && git pull && docker compose up -d --build"

# === 方式 B：本地 scp（无需服务器访问 GitHub） ===
scp app/provider_config.py root@47.237.163.119:/opt/gotoken-gateway/app/
scp app/auth.py root@47.237.163.119:/opt/gotoken-gateway/app/
scp app/admin_router.py root@47.237.163.119:/opt/gotoken-gateway/app/
scp app/static/gateway.html root@47.237.163.119:/opt/gotoken-gateway/app/static/

ssh root@47.237.163.119 "cd /opt/gotoken-gateway && docker compose up -d --build"

# === 验证 ===
curl -s -o /dev/null -w '%{http_code}' https://gogogoai.hk/healthz
# 期望: 200
```

### DNS

| 域名 | 类型 | 值 |
|------|------|-----|
| gogogoai.hk | A | 47.237.163.119 |

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
