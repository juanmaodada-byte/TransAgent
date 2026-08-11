# Golden Fixture Manifest

D1 测试基础设施 — 每份 fixture 的预期结构。

## 1. kubernetes_deployment.md

**用户场景**: Kubernetes 技术博客翻译

| 属性 | 预期值 |
|------|--------|
| 标题数量 | 3 个（1x h1, 1x h2, 2x h3） |
| 无序列表 | 3 项（Key Concepts 下） |
| 有序列表 | 1 项（Best Practices 下 4 项） |
| fenced code | 2 个（YAML + bash） |
| 行内代码 | > 5 个 |
| 版本号 | v1.19.0, v1.16.x-v1.18.x |
| 命令 | kubectl apply, kubectl rollout status, kubectl get pods |
| 关键不可译内容 | apiVersion, Deployment, RollingUpdate, Pod, ReplicaSets, StatefulSets |

## 2. docker_tutorial.md

**用户场景**: Docker 教程翻译

| 属性 | 预期值 |
|------|--------|
| 标题数量 | 多个（h1, h2, h3 混合） |
| 表格 | 1 个（Basic Commands，5 行） |
| fenced code | 3 个（bash, dockerfile, yaml） |
| URL | https://www.docker.com, https://get.docker.com, https://docs.docker.com, https://hub.docker.com |
| 行内代码 | docker run, docker build, docker ps, docker stop, docker rm 等 |
| 命令 | curl -fsSL, docker run -d -p, docker build -t, docker-compose |
| 版本号 | 1:25, 18-alpine, v1.0.0, 7-alpine, '3.8' |

## 3. rest_api.md

**用户场景**: REST API 文档翻译

| 属性 | 预期值 |
|------|--------|
| 标题数量 | 多个（h1, h2, h3） |
| 表格 | 2 个（Parameters, Error Codes） |
| JSON 块 | 2 个（request, response） |
| URL | https://api.example.com/v1 |
| cURL 命令 | 1 个（POST /users） |
| 文件路径 | ~/.config/myapp/config.yaml |
| API key | sk-xxxxxxxxxxxxxxxx |
| 错误码 | INVALID_EMAIL, USERNAME_TAKEN, RATE_LIMITED |

## 4. tech_whitepaper.md

**用户场景**: 长文技术白皮书翻译（触发分块）

| 属性 | 预期值 |
|------|--------|
| 标题数量 | 多级（h1 x1, h2 x6, h3 x5） |
| 长段落 | 多个超过 200 字的段落 |
| 字符数 | > 2000 characters |
| 分块触发 | 当 max_tokens 设置足够小时应触发多 chunk |

## 5. cloud_native_mixed.docx

**用户场景**: 云原生混合文档翻译（暴露顺序问题）

| 属性 | 预期顺序 |
|------|----------|
| 元素类型顺序 | heading(h1) → paragraph → heading(h2) → paragraph → list(4 items) → heading(h3) → paragraph → code → heading(h2) → paragraph → table(4×4) → paragraph → heading(h2) → paragraph → code(YAML) → paragraph → heading(h2) → image → heading(h2) → code(mermaid) → heading(h2) → paragraph → paragraph |
| 标题数 | 7 个 |
| 段落数 | 9 个（不含标题） |
| 表格 | 1 个（4 行 × 4 列） |
| 代码块 | 3 个（shell, YAML, mermaid） |
| 图片 | 1 个（PNG） |
| 列表 | 4 项无序列表 |
