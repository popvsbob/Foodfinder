# ======================== 导入核心依赖 ========================

# 类型注解：增强代码提示和静态检查能力
from typing import  Literal
# LangChain 工具装饰器：将普通函数转为 Agent 可调用的工具
from langchain_core.tools import tool
# Tavily 官方客户端：实现网络搜索核心功能
from tavily import TavilyClient

# 系统/第三方依赖
import os  # 系统路径/环境变量处理
from dotenv import load_dotenv  # 加载 .env 文件中的环境变量

# ======================== 初始化配置 ========================
# 加载环境变量
load_dotenv()

# 初始化 Tavily 客户端，使用环境变量中的 API Key
# [安全] 初始化失败（如未配置 API Key）时置为 None，后续调用时返回明确错误，
# 避免在 import 阶段就崩溃阻断整个服务启动
try:
    tavily_client = TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))
except Exception:
    tavily_client = None

@tool
def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news","finace"] = "general",
    include_raw_content: bool = False
):
    """
        当 AI Agent 需要获取外部互联网的公开信息、时效性数据（如新闻、金融动态）时调用，
        替代传统搜索引擎，返回更适配大模型的结构化结果。
        参数说明：
            query: 搜索的核心问题/关键词，例如 "2026年AI行业政策"
            max_results: 控制返回结果数量，免费版建议不超过5
            topic: 限定搜索内容类型，提升结果相关性
            include_raw_content: 是否返回详细新闻，False简略版本 True详细版本
        返回值：
            dict: Tavily API 返回的结构化结果，包含以下核心字段：
                - query: 原始搜索词
                - results: 搜索结果列表，每个元素包含 url、content（摘要）、raw_content（原始内容，可选）等
            str: 初始化失败时返回错误提示字符串
    """

    if not tavily_client:
        raise RuntimeError("Tavily client is not initialized. Please check your API key.")
    # 工具埋点由 LangChain 回调自动完成
    try:
        results = tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )
        return results
    except Exception as e:
        raise e
    

