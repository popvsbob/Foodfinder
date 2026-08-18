from dotenv import load_dotenv, find_dotenv
import os
from langchain.chat_models import init_chat_model
# 自动在env中寻找.env文件
load_dotenv(find_dotenv())

model = init_chat_model(
    model= os.getenv("LLM_QWEN_MAX"),
    model_provider="openai"
)