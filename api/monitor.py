import asyncio
import datetime
import json
from typing import Any, Dict, Optional

from langchain_core.callbacks import BaseCallbackHandler

from api.context import get_thread_context


# =================================================================================================
# StreamHub：SSE 消息枢纽
# =================================================================================================
# 设计思路（对比旧 WebSocket 方案）：
#   旧方案：monitor 持有 WebSocket 连接字典，发送前要判断"当前在不在主事件循环"，
#           在则 create_task、不在则 run_coroutine_threadsafe —— 复杂且脆弱。
#   新方案：每个 thread_id 对应一个 asyncio.Queue。
#           - SSE 端点是唯一消费者：await queue.get() 拿消息推给浏览器
#           - monitor 是生产者：loop.call_soon_threadsafe(queue.put_nowait, msg)
#             这一行本身就是线程安全的（无论调用方在哪个线程、哪个循环），
#             彻底消除事件循环判断逻辑。
# =================================================================================================
class StreamHub:
    """管理每个会话(thread_id)的消息队列，实现 SSE 定向推送"""

    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """服务启动时绑定主事件循环（在 lifespan 中调用）"""
        self.loop = loop
        print(f"[StreamHub] 已绑定主事件循环: {id(loop)}")

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        """SSE 连接建立时调用：为该会话创建消息队列（新队列覆盖旧队列，自动丢弃过期连接）"""
        queue = asyncio.Queue()
        self._queues[thread_id] = queue
        return queue

    def unsubscribe(self, thread_id: str):
        """SSE 连接断开时调用：清理队列，防止内存泄漏"""
        self._queues.pop(thread_id, None)

    def publish(self, payload: Dict[str, Any], thread_id: str) -> bool:
        """
        线程安全地发布消息到指定会话的队列。
        无论调用方处于哪个线程/事件循环，call_soon_threadsafe 都能安全投递。
        返回 False 表示该会话当前没有活跃的 SSE 连接（消息被丢弃）。
        """
        queue = self._queues.get(thread_id)
        if queue is None:
            return False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(queue.put_nowait, payload)
            return True
        return False


# 全局唯一枢纽实例
hub = StreamHub()


# =================================================================================================
# ToolMonitor：统一的事件上报入口
# =================================================================================================
class ToolMonitor:
    """
    工具监控类，用于在 Agent 执行过程中上报进度和状态。
    设计为单例模式，可在任何模块中直接导入使用。

    使用示例:
        from api.monitor import monitor
        monitor.report_session_dir("/path/to/session")
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None):
        """内部发送方法：组装统一格式的事件，投递到当前会话的 SSE 队列"""
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat()
        }

        # 通过 ContextVar 找到当前会话对应的 thread_id，定向投递
        thread_id = get_thread_context()
        if thread_id:
            hub.publish(payload, thread_id)

        # 控制台保底输出（方便调试）
        print(f"[Monitor:{event_type}] {message}")

    def report_assistant(self, assistant_name: str, args: Dict[str, Any] = None):
        """报告正在调用的子智能体进度"""
        self._emit("assistant_call", f"正在调用助手: {assistant_name}",
                   {"assistant_name": assistant_name, "args": args})

    def report_task_result(self, result: str):
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, path: str):
        """报告任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


# 全局单例实例
monitor = ToolMonitor()


# =================================================================================================
# MonitorCallbackHandler：LangChain 回调式埋点（替代手动 report_tool）
# =================================================================================================
# 旧方式：在每个工具函数里手动调用 monitor.report_tool(...) —— 侵入式、易遗漏、只有开始没有结束。
# 新方式：把本 handler 传入 main_agent.astream(..., config={"callbacks": [handler]})，
#         LangChain 框架会在每次工具调用时自动回调，无需修改任何工具代码。
# =================================================================================================
class MonitorCallbackHandler(BaseCallbackHandler):
    """
    LangChain 回调处理器：自动捕获工具调用的 开始/结束/失败 三个阶段并推送给前端。
    """

    # 回调内部抛出的异常不应中断 Agent 执行
    raise_error: bool = False

    def _emit_tool_event(self, event: str, message: str, data: Dict[str, Any]):
        """带上当前会话上下文发送事件（回调运行在 Agent 任务链路内，ContextVar 可用）"""
        monitor._emit(event, message, data)

    @staticmethod
    def _parse_args(input_str: Optional[str], inputs: Optional[Dict]) -> Dict[str, Any]:
        """优先用框架给的 inputs 字典；否则把 JSON 字符串的 input_str 解析回字典"""
        if inputs:
            return inputs
        if input_str:
            try:
                parsed = json.loads(input_str)
                return parsed if isinstance(parsed, dict) else {"input": parsed}
            except (json.JSONDecodeError, TypeError):
                return {"input": input_str}
        return {}

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None,
                      tags=None, metadata=None, inputs=None, **kwargs):
        """工具开始执行 —— 对应前端的 tool_start 事件（沿用旧数据结构：tool_name + args）"""
        tool_name = (serialized or {}).get("name") or kwargs.get("name") or "未知工具"
        args = self._parse_args(input_str, inputs)
        self._emit_tool_event(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args}
        )

    def on_tool_end(self, output, *, run_id, parent_run_id=None,
                    tags=None, metadata=None, **kwargs):
        """工具执行完成 —— 新增能力：旧的手动埋点拿不到结束事件"""
        result = str(output)
        self._emit_tool_event(
            "tool_end",
            "工具执行完成",
            {"result": result[:2000]}  # 截断，防止超长结果撑爆推送
        )

    def on_tool_error(self, error: BaseException, *, run_id, parent_run_id=None,
                      tags=None, metadata=None, **kwargs):
        """工具执行失败 —— 新增能力：失败原因实时推送前端"""
        self._emit_tool_event(
            "tool_error",
            f"工具执行失败: {error}",
            {"error": str(error)}
        )


# 全局回调实例（供 main_agent 的 astream config 使用）
monitor_callback = MonitorCallbackHandler()
