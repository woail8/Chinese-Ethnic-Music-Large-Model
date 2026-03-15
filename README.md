# 民族音乐问答助手（DeepSeek + Python + Web）

一个面向“民族音乐”领域的 Web 聊天应用：
- 支持网页聊天气泡与“思考中”交互提示
- 支持参考资料检索增强（RAG）与“全量资料（对比）”模式
- 支持会话复用与自动摘要，减少重复 token
- 支持展示每次回复的 token 用量，并可查看本次请求的完整提示词（messages）

## 快速开始（Windows）

1）填写 API Key  
编辑 `api_key.txt` 第一行，替换为你的 DeepSeek API Key。

2）安装依赖

```powershell
py -m pip install -r requirements.txt
```

3）启动服务

```powershell
py -m uvicorn main:app --host 127.0.0.1 --port 8000
```

4）打开网页  
浏览器访问：`http://127.0.0.1:8000/`

## 目录结构

- `main.py`：FastAPI 后端、RAG 注入、会话复用、token 统计、提示词查看接口
- `templates/index.html`：网页入口（含资料模式选择、查看提示词按钮）
- `static/app.js`：聊天逻辑、Markdown 渲染、token 展示、提示词弹窗
- `static/style.css`：样式与背景纹样
- `static/motifs.svg`：民族音乐/乐器纹样背景
- `prompt.txt`：系统提示词（限定民族音乐领域，可自行修改）
- `api_key.txt`：API Key（只读第一行；已被 `.gitignore` 忽略）
- `references/`：参考资料（`.txt`/`.md`）
- `rag_index.json`：RAG 索引（自动生成；已忽略）
- `sessions/`：会话缓存（自动生成；已忽略）
- `rag_store.py`：RAG 索引与检索逻辑
- `rag_cli.py`：RAG 操作脚本
- `session_store.py`：会话落盘与读取
- `requirements.txt`：依赖

## 参考资料（RAG / 全量对比）

把资料放到 `references/`（支持 `.txt`、`.md`），系统会从这些资料中辅助回答。

网页里“资料模式”有两种：
- `RAG（推荐）`：对全量资料建立索引，从中检索最相关片段注入提示词，token 更省
- `全量资料（对比）`：把 `references/` 内容拼接进提示词（可能截断），token 最耗

注意：全量模式不会把 20 万字无限制塞进模型输入。为了防止超出上下文，会按字符上限截断（由 `FULL_REF_MAX_CHARS` 控制，默认 20000）。

## 构建/更新 RAG 索引（脚本）

当你新增/修改 `references/` 的资料后，运行：

```powershell
py rag_cli.py build
```

查看索引统计：

```powershell
py rag_cli.py stats
```

检索验证：

```powershell
py rag_cli.py search "侗族大歌 多声部 特点"
```

服务端默认会在索引缺失或检测到资料更新时自动重建（可通过 `RAG_AUTO_BUILD=0` 关闭自动重建）。

## token 用量与提示词查看

- 每条助手回复气泡下方会展示本次请求 token（提示/回复/总计）
- 点击页面“查看提示词”，可查看本次请求发给模型的完整 messages（含系统提示、资料注入、会话摘要等），并支持复制

## PyCharm 运行

可以直接运行 `main.py` 启动服务（默认端口由环境变量 `PORT` 控制）。

常见问题：`WinError 10048` 端口占用  
- 停掉已运行的服务（终端里 Ctrl+C）
- 或者在 Run Configuration 里设置环境变量 `PORT=8001`，然后访问 `http://127.0.0.1:8001/`

## 环境变量

- `PORT`：服务端口（默认 8002；用 uvicorn 启动时由 uvicorn 参数决定）
- `HOST`：服务监听地址（默认 127.0.0.1）
- `RAG_AUTO_BUILD`：是否自动重建索引（默认 1；设为 0 关闭）
- `FULL_REF_MAX_CHARS`：全量资料模式注入字符上限（默认 20000）
