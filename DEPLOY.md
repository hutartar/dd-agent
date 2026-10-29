# 把 DD-Agent 部署到云服务器

目标：别人打开 `https://你的域名`，输入你设的密码就能用；你的电脑关机也不影响。

---

## 0. 需要准备的东西

| 东西 | 说明 |
|---|---|
| 一台云服务器 | 2 核 4G 起步；4 核 8G 更稳。Ubuntu 22.04 / 24.04 |
| 一个域名 | 解析到服务器 IP；国内服务器需 ICP 备案，香港/新加坡服务器免备案 |
| 一个模型接口 | DeepSeek / 硅基流动的 API Key，或者一台能跑 Ollama 的机器 |

> 如果资料绝对不能出服务器，就用 Ollama；7B 模型纯 CPU 跑一次报告要几十分钟，建议至少 8 核 16G，或者上 GPU。

---

## 1. 装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 重新登录一次，让 docker 组生效
```

验证：`docker run --rm hello-world`

---

## 2. 上传代码

在本地项目目录打包（排除结果和缓存）：

```bash
tar --exclude='web/results' --exclude='web/uploads' --exclude='output' \
    --exclude='__pycache__' --exclude='backup-*' \
    -czf dd-agent.tar.gz .
```

传到服务器并解压：

```bash
scp dd-agent.tar.gz user@你的服务器IP:/opt/
ssh user@你的服务器IP
cd /opt && mkdir dd-agent && tar -xzf dd-agent.tar.gz -C dd-agent && cd dd-agent
```

---

## 3. 配置

```bash
cp .env.example .env
nano .env
```

至少要改三处：

1. `DD_DOMAIN` —— 你的域名；
2. `DD_INITIAL_PASSWORD` —— 你想要的登录密码；
3. `DD_LLM_*` —— 选 API 或 Ollama。

---

## 4. 启动

```bash
docker compose up -d --build
docker compose logs -f app
```

看到 `DD-Agent 首次启动` 或 gunicorn 的 `Listening at` 就成了。

---

## 5. 域名解析

在域名控制台加一条 A 记录：

```
dd.example.com  →  你的服务器 IP
```

等 DNS 生效（几分钟），Caddy 会自动申请 HTTPS 证书。访问 `https://dd.example.com`，输密码登录。

---

## 6. 登录后先做三件事

1. **设置 → 管理员密码**：把初始密码改掉。只有这个密码能进设置页、改模型配置、改密码。
2. **设置 → 访客密码**：给别人的密码。别人用它登录后只能上传资料、看报告，**进不了设置页，也改不了任何密码**。留空保存就是关闭访客登录。
3. **设置 → 分析引擎**：选 Ollama 或 OpenAI 兼容 API，填地址/模型/Key，点「测试连接」，再点「保存」。改完立即生效，不需要重启。

---

## 7. 日常维护

```bash
# 看日志
docker compose logs -f app

# 重启
docker compose restart app

# 更新代码后重建
docker compose up -d --build

# 备份配置和结果
tar -czf backup-$(date +%F).tar.gz data web/results output
```

数据都在宿主机挂载目录里：

- `data/`：登录密码哈希、LLM 配置、session 密钥
- `web/results/`：每次分析的报告和图表
- `web/uploads/`：上传的 zip（分析完成后会自动删）

---

## 常见问题

**打不开 / 证书报错**
- 域名没解析对，或 80/443 没放行。云服务器安全组要开 80、443。
- 国内服务器没备案，Caddy 申请证书会失败。换香港服务器，或先用 `IP:5000` 临时访问（不推荐）。

**别人登录后能改我的密码吗？**

不能。只有管理员密码能进设置页；访客密码只能上传和看报告，改密码、改模型配置的接口对访客一律返回 403。

**登录密码忘了**

```bash
rm data/auth.json
docker compose restart app
docker compose logs app | grep 密码
```

会重新生成管理员随机密码（`DD_INITIAL_PASSWORD` 只在第一次初始化时生效）；访客密码会一起清掉，需要重新设置。

**报告里中文变方块**
镜像里装了 Noto CJK 字体；如果自己改过 Dockerfile，确认 `fonts-noto-cjk` 还在。

**一次分析很慢**
- 用 API 模式通常几分钟内；
- 用 Ollama CPU 模式会慢很多，属正常；
- 多个人同时上传会排队抢 CPU，演示阶段建议一次只跑一个。

**想换成别的模型**
设置页直接改就行：DeepSeek、硅基流动、通义、OpenAI 都走 OpenAI 兼容接口，填对应的 `base_url` 和 `model`。

---

## 安全提醒

- 这是演示系统，没有做多用户权限、任务队列和审计日志。给几个人用可以，别直接对公网开放注册。
- 上传的财务资料很敏感。用 API 模式意味着资料会发给接口方，先确认合规。
- `data/settings.json` 里存着 API Key 明文，服务器要限制登录权限。
