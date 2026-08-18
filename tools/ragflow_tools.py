# 导入系统核心模块
import os
import logging
# 导入HTTP请求库（用于健康检查）
import requests
# 导入RAGFlow SDK核心类（用于操作RAGFlow助手/知识库）
from ragflow_sdk import RAGFlow
# 导入环境变量加载工具（用于读取.env文件中的配置）
from dotenv import load_dotenv
# 导入LangChain工具装饰器（用于将函数注册为Agent可调用的工具）
from langchain_core.tools import tool
from typing_extensions import Annotated

# 初始化日志器（用于记录工具运行日志）
logger = logging.getLogger(__name__)

# 导入类型注解（用于函数返回值/参数类型约束）
from typing import Tuple, Optional

# 1.加载ragflow配置
def load_ragflow_config():
    """
        从环境变量中获取RAGFlow连接配置
        Returns:
            dict: 包含RAGFlow连接所需的配置字典
    """
    load_dotenv()
    api_key=os.getenv("RAGFLOW_API_KEY")
    base_url=os.getenv("RAGFLOW_BASE_URL")
    return api_key,base_url

# 2.定义工具
# 获取聊天助手列表
def get_assistant_list():
    """
    【工具功能】获取 RAGFlow 中所有聊天助手信息
    适用场景：Agent 需要确认当前有哪些可用助手，及每个助手绑定的知识库
    返回：结构化字符串（助手名称+功能介绍+关联知识库）
    """
    # 工具埋点由 LangChain 回调自动完成
    api_key, base_url = load_ragflow_config()

    
    result=""
    try:
        rag=RAGFlow(api_key=api_key, base_url=base_url)
        # 遍历聊天助手
        for assistant in rag.list_assistants():
            knowledge_base=[]
            if assistant.datasets and isinstance(assistant.datasets, list):
                # 获取一个助手的所有知识库名称
                for dataset in assistant.datasets:
                    knowledge_base.append(dataset.name)
            # 格式化知识库名称（无则显示"无"）
            knowledge_base_str = "、".join(knowledge_base) if knowledge_base else "无"
            # 结构化拼接助手信息
            result += f"助手名称：{assistant.name}； 功能介绍：{assistant.description}； 关联知识库：{knowledge_base_str}\n"
        # 最后移除多余的结尾换行符
        if result:
            return result.rstrip("\n") 
        else: "未找到任何聊天助手"
    except Exception as e:
        return f"获取助手列表失败：{str(e)}" 

# 工具2：向助手提问
@tool
def ask_assistant(assistant_name: str, question: str) -> str:
    """
    【工具功能】向指定 RAGFlow 助手发起单次提问（临时会话，用完即删）
    """
    # 工具埋点由 LangChain 回调自动完成

    # 1.获取参数
    api_key, base_url = load_ragflow_config()

    # 2.提问
    try:
        rag = RAGFlow(api_key=api_key, base_url=base_url)
        # 筛选目标助手，拿第一个匹配结果
        assistants=rag.list_chats(name=assistant_name)
        if not assistants:
            return f"错误：未找到名为「{assistant_name}」的聊天助手"
        assistant = assistants[0]


        try:
            # 创建临时会话
            session=assistant.create_session(name="temp_session_for_single_ask")
            # 流式提问
            response = session.ask(question,stream=True)
            # 获取响应内容
            answer = ""
            for part in response:
                if part.content:
                    # 覆盖更新为完整答案（流式最后一段是完整内容）
                    answer=part.content

            # 自动删除临时会话（核心：避免会话堆积）
            if session and hasattr(session, "id"):
                assistant.delete_sessions(ids=[session.id])
            return answer if answer else "未获取到助手的回答"
        except Exception as e:
            return f"提问过程失败：{str(e)}"        


    except Exception as e:
        return f"RAGFlow 操作失败：{str(e)}"
