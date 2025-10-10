# Grok2API

基于 FastAPI 重构的 Grok2API，适配最新的Web调用格式，支持流式对话、图像生成、图像编辑、联网搜索、深度思考，支持号池并发调用和自动负载均衡。


### 接口说明
> 同 OpenAI 官方接口一致，API 请求使用通过 Authorization header 进行认证

| 方法 | 端点 | 描述 | 认证 |
|------|------|------|------|
| POST | `/v1/chat/completions` | 创建聊天对话，支持流式和非流式响应，兼容OpenAI格式 | ✅ |
| GET | `/v1/models` | 获取支持的所有模型列表 | ✅ |
| GET | `/v1/models/{model_id}` | 获取特定模型信息 | ✅ |
| GET | `/images/{img_path}` | 获取生成的图片文件 | ❌ |


<details>
<summary>其余管理接口</summary>

| 方法 | 端点 | 描述 | 认证 |
|------|------|------|------|
| GET | /login | 管理员登录页面 | ❌ |
| GET | /manage | 管理控制台页面 | ❌ |
| POST | /api/login | 管理员登录认证 | ❌ |
| POST | /api/logout | 管理员登出 | ✅ |
| GET | /api/tokens | 获取Token列表 | ✅ |
| POST | /api/tokens/add | 批量添加Token | ✅ |
| POST | /api/tokens/delete | 批量删除Token | ✅ |
| GET | /api/settings | 获取系统配置 | ✅ |
| POST | /api/settings | 更新系统配置 | ✅ |
| GET | /api/cache/size | 获取缓存大小 | ✅ |
| POST | /api/cache/clear | 清理图片缓存 | ✅ |
| GET | /api/stats | 获取统计信息 | ✅ |

</details>

<br>

### 使用说明

#### 调用次数说明

- 普通账号（Basic）可免费使用 **80 次/20 小时**，Super 账号配额尚未确定（我没有号就没测）。
- 系统自动监控并负载均衡各账号调用次数，可在**管理页面**直观查看各账户实时用量和状态。

#### 图像生成说明

- 只需在对话内容中输入如“给我画一个月亮”即可自动触发生成图片。
- 每次生成会以 **Markdown 格式返回两张图片**，计 4 次额度。
- **由于 Grok 的图片直链受限（403），系统会自动将图片缓存到本地。要确保图片能正常显示，请务必正确设置 Base Url。**

#### 关于 x_statsig_id

- `x_statsig_id` 是 Grok 用于识别机器人请求的 Token，目前网络上已经有相关的逆向分析资料，有兴趣可自行查阅。
- 如果您不确定如何获取 x_statsig_id，建议不要修改配置文件中的该参数，保持默认值即可，无需关注。
- 项目最初借鉴了 [VeroFess/grok2api](https://github.com/VeroFess/grok2api) 的实现，曾通过 Camoufox 尝试绕过原项目的 Playwright 获取 x_statsig_id 时触发的 403。起初尚可使用，但近期 Grok 已对未登录用户的 x_statsig_id 做了限制，因此已弃用改方法，当前直接使用固定值以兼容调用。

#### docker-compose 部署

```
services:
  grok2api:
    image: ghcr.io/chenyme/grok2api:latest
    ports:
      - "8000:8000"
    volumes:
      - grok_data:/app/data
      - ./logs:/app/logs

volumes:
  grok_data:
```

<br>

### 可用模型
> 所有模型均可进行图像生成，每次计次为 4 次

| 模型名称              | 调用计次 | 可用账户      | 图像生成/编辑 | 深度思考 | 联网搜索 |
|----------------------|---------|--------------|--------------|----------|----------|
| `grok-3-fast`        | 1       | Basic/Super  | ✅           | ❌       | ✅       |
| `grok-4-fast`        | 1       | Basic/Super  | ✅           | ✅       | ✅       |
| `grok-4-fast-expert` | 4       | Basic/Super  | ✅           | ✅       | ✅       |
| `grok-4-expert`      | 4       | Basic/Super  | ✅           | ✅       | ✅       |
| `grok-4-heavy`       | 1       | Super        | ✅           | ✅       | ✅       |

<br>

### 参数说明
> 所有参数请在启动服务后，进入 `/login` 路由后登陆管理进行配置

| 参数名 | 位置 | 必填 | 说明 | 默认值 |
|---------|---------|------|------|--------|
| admin_username | global | 否 | 管理后台登录用户名 | "admin" |
| admin_password | global | 否 | 管理后台登录密码 | "admin" |
| log_level | global | 否 | 日志级别：DEBUG/INFO/WARNING/ERROR | "INFO" |
| temp_max_size_mb | global | 否 | 图片缓存目录最大容量(MB)，超过后自动清理 | 500 |
| base_url | global | 否 | 服务基础URL，用于生成图片链接，保护服务器IP | "" |
| api_key | grok | 否 | API访问密钥，提高安全性，可留空 | "" |
| proxy_url | grok | 否 | HTTP代理服务器地址，访问Grok服务 | "" |
| cf_clearance | grok | 否 | Cloudflare安全令牌，绕过人机验证 | "" |
| x_statsig_id | grok | 是 | Grok反机器人检测唯一标识符 | "ZTpUeXBlRXJyb3I6IENhbm5vdCByZWFkIHByb3BlcnRpZXMgb2YgdW5kZWZpbmVkIChyZWFkaW5nICdjaGlsZE5vZGVzJyk=" |
| filtered_tags | grok | 否 | 过滤响应标签，多个用逗号分隔 | "xaiartifact,xai:tool_usage_card,grok:render" |
| temporary | grok | 否 | 会话模式：true(临时)/false | true |

<br>

### 注意事项
⚠️ 本项目仅供学习和研究目的，请遵守相关使用条款。

<br>

---

<br>

此项目基于其他 Grok2API 项目进行学习重构，感谢 [LINUX DO](https://linux.do)、[VeroFess/grok2api](https://github.com/VeroFess/grok2api)、[xLmiler/grok2api_python](https://github.com/xLmiler/grok2api_python) 的项目大佬