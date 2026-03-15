# 民族音乐问答助手（DeepSeek + Python）

## 目录结构

- `main.py`：后端服务与 DeepSeek 调用
- `templates/index.html`：网页入口
- `static/app.js`：聊天交互逻辑（含“正在思考”提示）
- `static/style.css`：界面样式
- `prompt.txt`：系统提示词（已预置，可自行修改）
- `api_key.txt`：DeepSeek API Key（只读第一行；已被 `.gitignore` 忽略）
- `references/`：参考资料文件夹（`.txt`/`.md`，用于辅助回答）
- `rag_index.json`：RAG 索引文件（自动生成；已被 `.gitignore` 忽略）
- `sessions/`：会话缓存（自动生成；已被 `.gitignore` 忽略）
- `requirements.txt`：依赖

## 使用方式（Windows）

1. 填写 API Key  
   打开 `api_key.txt`，把你的 DeepSeek API Key 放在第一行，保存。

2. 安装依赖  

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. 启动服务  

   ```powershell
   py -m uvicorn main:app --host 127.0.0.1 --port 8000
   ```

4. 打开网页  
   浏览器访问：`http://127.0.0.1:8000/`

## 在 PyCharm 里运行

直接运行 `main.py` 会启动服务，默认 `127.0.0.1:8000`。

如果出现端口占用（WinError 10048），说明已有另一个服务占用了 8000：
- 停掉已运行的服务（对应的终端/控制台里按 Ctrl+C）
- 或者给 PyCharm 的 Run Configuration 添加环境变量 `PORT=8001`（同时浏览器访问 `http://127.0.0.1:8001/`）

## 修改提示词

直接编辑 `prompt.txt`。后端每次请求都会读取该文件，因此修改后无需重启服务即可生效。

## 参考资料辅助

把你的参考资料（建议纯文本）放到 `references/` 目录下（支持 `.txt`、`.md`）。系统会按用户最新问题检索相关片段，并把片段作为“参考资料摘录”注入到模型输入中：
- 优先依据参考资料作答
- 若资料覆盖不足，会先说明再补充通用知识回答
- 引用资料时会标注（来源: 文件名#段落号）

## RAG 索引构建/更新（脚本）

为了避免每次请求都扫描与重建检索结构，你可以用脚本把 `references/` 预先构建为本地索引文件 `rag_index.json`：

```powershell
py rag_cli.py build
```

查看索引统计：

```powershell
py rag_cli.py stats
```

临时检索验证：

```powershell
py rag_cli.py search "侗族大歌 多声部 特点"
```

当你往 `references/` 新增/修改资料后，重新执行 `build` 即可更新索引。

默认情况下服务也会在索引缺失或检测到资料更新时自动重建（可通过环境变量 `RAG_AUTO_BUILD=0` 关闭自动重建）。

## 会话上下文复用

前端与后端会使用 `session_id` 进行会话复用：后端会缓存近期对话，并在对话变长时自动生成“会话摘要”，后续请求只携带必要上下文，从而减少 token 消耗。
