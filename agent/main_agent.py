from agent.subagents.rag_agent import rag_agent
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.network_search_agent import network_search_agent
from langgraph.checkpoint.memory import InMemorySaver   # LangGraph的短期记忆

# main_agent tool导入
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content

from deepagents import create_deep_agent    # deepagent

from agent.llm import model
from agent.prompts import main_agent_content

from api.monitor import monitor, monitor_callback
import asyncio
import uuid
import shutil
from pathlib import Path

from api.context import set_session_context, reset_session_context, set_thread_context

from langchain_core.messages import AIMessage

# 创建主智能体
main_agent = create_deep_agent(
   model = model,
   system_prompt=main_agent_content['system_prompt'],
   tools= [generate_markdown,convert_md_to_pdf,read_file_content],
   checkpointer=InMemorySaver(),
   subagents=[
       database_query_agent,
       network_search_agent,
       rag_agent
   ]
)



# 2.执行主agent
"""
    1.执行主智能体，选择异步，对应多个客户端
    2.
"""

project_root_path = Path(__file__).parents[1].resolve() # 绝对解析路径标识以及软连接



def _process_stream_chunk(chunk):
    
    # 解析每个节点的输出
    for node_name,state in chunk.items():
        if not state or "messages" not in state:
            continue
        # 提取最新的消息
        messages=state["messages"]
       
        if isinstance(messages, list) and messages:
            # 对话历史的最后一条信息
            last_message=messages[-1]
            # 找到封装LangGraph中的model节点
            if node_name=='model':
                # 如果大模型决定调用工具
                if last_message.tool_calls:
                    # 遍历模型想调用的所有工具（它可能同时决定调用两个）
                    for tool_call in last_message.tool_calls:
                        # 示例
                        """
                            tool_call = {
                                name: task
                                args:{
                                    subagent_type:子智能体的名字
                                    description:子智能体的描述
                                }
                            }                                
                        """
                        # task表示调用子智能体
                        if tool_call['name']=='task':
                            # 调用子智能体，子智能体的名字，描述
                            
                            monitor.report_assistant(
                                tool_call['args']['subagent_type'],
                                {'description':tool_call['args']['description']})

                # 如果是答案
                elif last_message.content:
                    print(f"主智能体执行结果，最终结果：{last_message.content[:100]}")
                    monitor.report_task_result(last_message.content)


async def run_deep_agent(query,session_id):
    """
        流式+异步执行主智能体
        query: 前端提问的问题
        session_id: 每个前端会话对应的标识 
    （1.存储session_id ContextVars 2.session_id 给他创建对应的output输出地址）
    """
    print(f"当前会话的main_agent开始执行! 会话id:{session_id}")

    # 1. 准备工作
    # 会话存储的文件夹
    session_dir=project_root_path/"output"/f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)  # 如果文件夹不存在，则创建
    session_dir_str=str(session_dir).replace("\\",'/')  # 路径中的路径替换
    
    # 相对文件夹
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace("\\","/")


    # 处理上传文件
    uploaded_dir=project_root_path / "updated" / f"session_{session_id}"
    uploaded_prompt=""  # 如果有上传文件，就需要拼接文件位置
    # [修复] 必须用 .exists() 判断：Path 对象永远为真（truthy），
    # 原 if uploaded_dir 判断无效，且 iterdir() 对不存在的目录会抛 FileNotFoundError，
    # 发生在 try 块之前会被 create_task 静默吞掉，导致前端收不到任何事件
    if uploaded_dir.exists():
        # 读取文件夹
        files = [ f.name  for f in uploaded_dir.iterdir()  if f.is_file()]
        if files:
            # 如果有文件，就批量复制文件
            for filename in files:
                # 将上传的文件批量复制到session_dir，方便前端读取
                shutil.copy2(uploaded_dir / filename, session_dir / filename)
            # 构建需要拼接的提示词
            uploaded_prompt=(f"\n    [已上传文件] 已加载到工作目录:\n" +
                             "\n".join([f"    - {f}" for f in files]) +
                             "\n    请优先使用工具（read_file_content）读取并参考这些文件。")
    
    # 将当前会话的对应的session_id session_dir 存储到contextVars 
    # [后续工具获取，socket -> 推送消息] 2.调用monitor给前端推送session_dir信息
    session_dir_token= set_session_context(session_dir_str)  # 存储的当前会话对应的文件夹地址
    session_id_token = set_thread_context(session_id)  #获取当前会话的session_id对应socket
    monitor.report_session_dir(session_dir_str)  # 当前会话对应的文件夹地址推送给起前端！

    # 2.执行主智能体

    # 构建提示词
    path_instruction=f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {uploaded_prompt}

    规则:
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    # 执行
    config={
        "configurable":{
            "thread_id":session_id
        },
        # 挂载回调式埋点：LangChain 自动在工具开始/结束/失败时推送事件给前端，
        # 替代各工具函数内手动调用的 monitor.report_tool
        "callbacks":[monitor_callback]
    }
    try:
        # astream: 异步生成器，像流水线一样逐个吐出 Agent 的思考片段
        async for chunk in main_agent.astream({
            "messages":[
                {
                    "role":"user","content":query+path_instruction
                }
            ]
        },config=config):
            # 实时处理每一个片段 (上报前端)
            _process_stream_chunk(chunk)

    except Exception as e :
        # 报错推送错误信息给前端
        monitor._emit("error",f"执行主智能发生异常信息：{str(e)}")
    finally:
        # 释放存储的地址和session_id
        reset_session_context(session_dir_token, session_id_token)
   