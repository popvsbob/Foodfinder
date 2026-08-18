# ===========提示词读取============
import yaml
from pathlib import Path
from conf import *

# 加载yaml格式提示词配置文件
def load_prompt(file_path):
    """
        读取并加载YAML格式的提示词配置文件
        Args:
            file_path (str/Path): YAML配置文件的路径
        Returns:
            dict: 解析后的YAML配置字典，包含主智能体和子智能体的提示词配置
    """

    # 打开UTF-8文件，避免中文乱码
    with open(file_path,"r",encoding="utf-8") as f:
        # 按安全加载
        return yaml.safe_load(f)
    
# 提示词配置文件的完整路径（实际文件为 conf/prompt/prompt.yml）
prompt_file_path = root_path / "conf" / "prompt" / "prompt.yml"

# 加载yaml配置文件内容
prompt_config_content=load_prompt(prompt_file_path)

# 打印内容，验证
print(f"prompt_config_content: {prompt_config_content}")

# 提取主智能体的配置
main_agent_config = prompt_config_content.get("main_agent", {})

# 提取子智能体的配置
sub_agents_config = prompt_config_content.get("sub_agents", {})

# 打印主智能体和子智能体配置
print(f"main_agent_config: {main_agent_config} , \nsub_agents_config: {sub_agents_config}")

# main_agent.py 中导入的是 main_agent_content，这里提供同名别名保持一致
main_agent_content = main_agent_config