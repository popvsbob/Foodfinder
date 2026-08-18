import uuid
import json
import asyncio
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project root to sys.path
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

# Import agent runner and monitor
# 注意：agent.main_agent 导入时会初始化 main_agent，这可能需要几秒钟
from agent.main_agent import run_deep_agent
from api.monitor import hub

# 挂载输出目录，以便前端访问生成的静态文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# 定义上传目录 updated
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 后台任务引用池：asyncio.create_task 返回的对象必须保存强引用，
# 否则可能被垃圾回收导致任务中途静默消失（Python 官方文档明确警告）
_background_tasks: set = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    服务生命周期管理（替代已废弃的 @app.on_event("startup")）。
    启动时把主事件循环绑定到 StreamHub，
    使任何线程里的 monitor 都能线程安全地向 SSE 队列投递消息。
    """
    loop = asyncio.get_running_loop()
    hub.set_loop(loop)
    print(f"[Server] StreamHub bound to loop: {id(loop)}")
    yield  # 服务运行期间
    # 关闭前清理残留的后台任务
    for task in _background_tasks:
        task.cancel()


app = FastAPI(title="DeepAgents API", lifespan=lifespan)

# 配置 CORS
# 注意：allow_origins=["*"] 与 allow_credentials=True 组合违反 CORS 规范，浏览器会拒绝；
# 因此显式列出前端开发服务器地址
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite 开发服务器
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态目录：把 URL 前缀 /outputs 绑定到磁盘 output/ 文件夹。
# 前端收到 session_created 事件后会拼出 http://host:8100/outputs/xxx 的 URL
# 来预览 Agent 生成的文件，没有这行挂载，浏览器访问这些 URL 全部 404。
app.mount("/outputs", StaticFiles(directory=output_dir), name="outputs")


class TaskRequest(BaseModel):
    query: str
    thread_id: Optional[str] = None


@app.post("/api/task")
async def run_task(request: TaskRequest):
    # 1. [ID 初始化]
    thread_id = request.thread_id or str(uuid.uuid4())

    # 2. [后台执行] 异步运行 Agent，不阻塞主线程
    # 保存强引用防止任务被 GC；结束后自动从池中移除
    task = asyncio.create_task(run_deep_agent(request.query, thread_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # 3. [立即响应]
    return {"status": "started", "thread_id": thread_id}


@app.get("/api/stream/{thread_id}")
async def sse_stream(thread_id: str):
    """
    SSE 实时推送接口（Server-Sent Events，替代原 WebSocket）。

    为什么用 SSE 替代 WebSocket：
    1. 本项目前端只需要"服务端 -> 客户端"的单向推送，WebSocket 的双向能力用不上
    2. 纯 HTTP 协议，无需维护连接字典/握手/心跳；浏览器 EventSource 原生自动重连
    3. 消息投递统一走 StreamHub 的 asyncio.Queue，monitor 无需再判断事件循环

    数据格式：每条消息为 "data: {JSON}\\n\\n"，JSON 结构与原 WebSocket 完全一致。
    """
    queue = hub.subscribe(thread_id)

    async def event_generator():
        # 首条消息：告知前端连接已建立（前端会自动忽略未知事件类型）
        yield f"data: {json.dumps({'type': 'connected', 'event': 'connected', 'message': 'SSE连接已建立', 'data': {}}, ensure_ascii=False)}\n\n"
        try:
            while True:
                payload = await queue.get()
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            # 客户端断开时 StreamingResponse 会取消本生成器，在此清理队列
            hub.unsubscribe(thread_id)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 1. [目录准备] 确保上传目录存在
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    # 2. [保存] 遍历并写入文件
    for file in files:
        # [安全] 只保留文件名部分，剥离任何目录成分，
        # 防止恶意文件名（如 "../../evil.sh"）逃出会话目录
        safe_name = Path(file.filename).name
        if not safe_name:
            continue
        file_path = target_dir / safe_name
        # 使用二进制模式写入，支持各种文件格式 (图片、PDF、文本等)
        # shutil.copyfileobj 高效复制文件流，避免一次性加载大文件到内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(safe_name)

    # 3. [响应] 返回成功保存的文件列表
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    # 1. [安全检查] 路径解析与越权校验
    try:
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 必须确保请求的文件在 output 目录下
        if not abs_path.is_relative_to(output_abs):
            raise HTTPException(status_code=403, detail="拒绝访问: 只能下载输出目录下的文件")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="无效的路径参数")

    # 2. [存在性检查]
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 3. [响应] 返回文件流 (浏览器自动触发下载)
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、时间、下载链接）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    # 1. [调试] 打印请求路径
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 2. [解析] 获取绝对路径对象
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 3. [安全] 检查路径是否越界 (Path Traversal Check)
        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            raise HTTPException(status_code=403, detail="拒绝访问: 只能访问输出目录下的文件")

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        raise HTTPException(status_code=400, detail=f"路径无效: {e}")

    # 4. [检查] 目录是否存在
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="目录不存在")

    files = []
    try:
        # 5. [遍历] 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                # 计算相对路径，生成下载 URL
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "path": str(file_path),
                    # "url": f"/outputs/{url_path}",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime
                })

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # 6. [排序] 按修改时间倒序排列 (最新的在前)
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8100, reload=True)
