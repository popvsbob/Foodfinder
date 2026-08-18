import os
from dotenv import load_dotenv
from mysql.connector import connect,Error
from typing import Annotated,List
from langchain_core.tools import tool

load_dotenv()

# 1.加载数据库配置文件
def get_database_config():
    """
        从环境变量中获取数据库连接配置
        Returns:
            dict: 包含数据库连接所需的配置字典
    """
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL")
    }

    # 移除空值
    config = {k: v for k, v in config.items() if v is not None}
	
    # 校验核心配置是否存在
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    
    return config

#==========================具体工具====================================
"""
    1.工具1：查看数据库表
    2.工具2：列出数据
    3.工具3：生成sql
"""

# 2.工具1：查看数据库表
@tool
def list_sql_tables():
    """
    列出配置的 MySQL 数据库中所有可用的表。
    核心用途：
        AI Agent 需要查看数据库中有哪些表时调用，为后续执行 SQL 查询提供基础信息。
    返回值：
        str: 成功时返回 "可用数据表：表1, 表2, ..."；
             配置缺失时返回错误提示； 
             执行异常时返回具体错误信息。
    异常处理：
        捕获数据库连接/执行 SQL 时的所有 Error 异常，返回可读的错误信息，避免 Agent 崩溃。
    """
    # 工具埋点由 LangChain 回调自动完成
    # 获取数据库配置
    config = get_database_config()
    try:
        # 前置校验，确保必要配置
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "数据库配置不完整，请检查环境变量 MYSQL_USER、MYSQL_PASSWORD、MYSQL_DATABASE 是否正确设置。"
        # 数据库连接
        with connect(**config) as conn:
             with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                if not tables:
                    return "数据库中未找到任何数据表。"
                # 提取表名
                table_names = [table[0] for table in tables]
                # 返回表名
                return f"可用数据表：{', '.join(table_names)}"
             
    except Error as e:
        return f"列出数据表失败：{str(e)}"

# 工具2：获取数据表前100行数据
# [修复] 补上 @tool 装饰器：没有它函数无法被 Agent 识别和调用
@tool
def get_table_data(table_name):
    """
    读取指定 MySQL 数据表的前 100 行数据，返回 CSV 格式结果。
    cursor.description表示查询的元数据
    """
    # 工具埋点由 LangChain 回调自动完成
    # 获取数据库连接配置
    config = get_database_config()

    try:
        # 前置校验：确保数据库账号、密码、库名配置完整
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "错误：数据库配置缺失（请检查账号、密码、数据库名）。"
        # 建立数据库连接（with自动管理连接生命周期，无需手动关闭）
        with connect(**config) as conn:
            # 创建游标（执行SQL、获取结果的核心对象，with自动关闭）
            with conn.cursor() as cursor:
                # 对传入的表名做基础安全清洗，核心是移除 SQL 注入常用的危险字符（反引号、分号;），再按空格拆分只取第一部分，只保留表名的有效核心；比如恶意输入表名"users`; DROP TABLE orders;"，清洗后会变成 "users"，能避免注入风险（仅基础防护，需结合白名单 / 参数化查询更安全）。
                # 基础安全清洗：移除表名中的危险字符，降低SQL注入风险（仅基础防护）
                safe_table_name = table_name.replace("`", "").replace(";", "").split()[0]
                cursor.execute(f"SELECT * FROM {safe_table_name} LIMIT 100")
                if cursor.description is None:
                    return f"数据表 {table_name} 为空或表名无效。"
                # 获取列名,columns 示例结果：['id', 'name', 'age']
                columns=[desc[0] for desc in cursor.description]    
                rows=cursor.fetchall()  # 获取所有数据行
                # 将结果转化为csv格式， result 示例结果：['1,张三,25', '2,李四,30']
                result = [",".join(map(str, row)) for row in rows]
                # 构建csv表头, header 示例结果："id,name,age"
                header = ",".join(columns)
                # 返回CSV格式数据
                """
                # id,name,age
                # 1,张三,25
                # 2,李四,30
                """
                return f"{header}\n" + "\n".join(result)
    except Error as e:
        return f"读取数据表 {table_name} 失败：{str(e)}"
    

# 执行sql语句
@tool
def execute_sql_query(sql):
    """在 MySQL 数据库上执行自定义 SQL 查询。用于复杂查询、联接或特定数据检索。"""
    # 工具埋点由 LangChain 回调自动完成
    # 获取数据库连接配置（账号、密码、库名等）
    config = get_database_config()
    try:
        # 前置校验：确保核心数据库配置（账号、密码、库名）完整
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "错误：数据库配置缺失（请检查账号、密码、数据库名）。"
        # 建立数据库连接（with 语句自动管理连接生命周期，无需手动关闭）
        with connect(**config) as conn:
            # 创建游标对象（执行 SQL、获取结果/影响行数的核心对象）
            with conn.cursor() as cursor:
                # 执行传入的自定义 SQL 语句
                cursor.execute(sql)
                # 需要判断结果是查询语句，还是操作表语句
                # cursor.description 不为空 → 是查询类语句（DQL：SELECT/SHOW 等）
                if cursor.description is not None:
                    # 查询语句，获取列名和数据
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    if not rows:
                        return "查询成功，但未返回任何数据。涉及的表名:{','.join(columns)}"

                    # 将结果转化为 CSV 格式
                    result = [",".join(map(str, row)) for row in rows]
                    header = ",".join(columns)
                    return f"{header}\n" + "\n".join(result)
                # cursor.description 为空 → 是修改类语句（DML：INSERT/UPDATE/DELETE 等）
                else:
                    # 非查询语句，返回影响的行数
                    affected_rows = cursor.rowcount
                    return f"SQL 执行成功，影响行数：{affected_rows}"
    # 捕获所有数据库操作异常，返回中文错误提示
    except Error as e:
        # logger.error(f"Failed to execute query: {str(e)}")  # 若有日志模块可启用
        return f"执行 SQL 失败：{str(e)}"        