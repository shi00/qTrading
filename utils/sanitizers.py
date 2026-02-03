"""
数据脱敏工具集 - Data Sanitizers

提供敏感数据脱敏功能,确保日志中不泄露:
- API Token
- 加密密钥
- 文件路径
- DataFrame实际数据
"""

import re
import pandas as pd
from typing import Any, Optional


class DataSanitizer:
    """统一的数据脱敏工具类"""
    
    @staticmethod
    def sanitize_token(token: str) -> str:
        """
        Token脱敏处理
        
        输入: "tushare_abc123xyz789"
        输出: "tus***789"
        
        Args:
            token: 原始token字符串
            
        Returns:
            脱敏后的token
        """
        if not token or not isinstance(token, str):
            return "***"
        
        # 短token直接全部隐藏
        if len(token) < 8:
            return "***"
        
        # 标准格式: 前3位 + *** + 后4位
        return f"{token[:3]}***{token[-4:]}"
    
    @staticmethod
    def sanitize_dataframe(df: Optional[pd.DataFrame], max_cols: int = 5) -> str:
        """
        DataFrame安全摘要
        
        仅记录形状和列名,不泄露实际数据
        
        Args:
            df: DataFrame对象
            max_cols: 最多显示的列数
            
        Returns:
            安全的摘要字符串
        """
        if df is None:
            return "None"
        
        if not isinstance(df, pd.DataFrame):
            return f"{type(df).__name__}"
        
        if df.empty:
            return "DataFrame(empty)"
        
        # 仅显示前N列
        cols_display = list(df.columns[:max_cols])
        if len(df.columns) > max_cols:
            cols_display.append("...")
        
        return (
            f"DataFrame(shape={df.shape}, "
            f"cols={cols_display})"
        )
    
    @staticmethod
    def sanitize_error(exception: Exception, show_traceback: bool = False) -> str:
        """
        异常信息脱敏
        
        移除文件路径,避免暴露系统结构
        
        Args:
            exception: 异常对象
            show_traceback: 是否包含堆栈(仅用于DEBUG)
            
        Returns:
            脱敏后的错误信息
        """
        msg = str(exception)
        
        # 移除 Windows路径: D:\path\to\file.py
        msg = re.sub(r'[A-Z]:\\[^\'"\s]+', '<PATH>', msg)
        
        # 移除 Unix路径: /path/to/file.py
        msg = re.sub(r'/[\w/\-\.]+\.py', '<PATH>', msg)
        
        # 如果需要堆栈,也要脱敏
        if show_traceback and hasattr(exception, '__traceback__'):
            import traceback
            tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
            # 过滤敏感路径
            tb_clean = [re.sub(r'[A-Z]:\\[^\'"\s]+', '<PATH>', line) for line in tb_lines]
            return '\n'.join(tb_clean)
        
        return msg
    
    @staticmethod
    def sanitize_dict(data: dict, sensitive_keys: list = None) -> dict:
        """
        字典脱敏处理
        
        Args:
            data: 原始字典
            sensitive_keys: 需要脱敏的键列表
            
        Returns:
            脱敏后的字典副本
        """
        if sensitive_keys is None:
            sensitive_keys = ['token', 'password', 'api_key', 'secret', 'key']
        
        result = {}
        for k, v in data.items():
            # 检查key是否敏感
            if any(sensitive in k.lower() for sensitive in sensitive_keys):
                if isinstance(v, str):
                    result[k] = DataSanitizer.sanitize_token(v)
                else:
                    result[k] = "***"
            else:
                # DataFrame特殊处理
                if isinstance(v, pd.DataFrame):
                    result[k] = DataSanitizer.sanitize_dataframe(v)
                else:
                    result[k] = v
        
        return result
    
    @staticmethod
    def sanitize_args(*args, sensitive_patterns: list = None, **kwargs) -> tuple:
        """
        函数参数脱敏
        
        用于装饰器自动脱敏参数
        
        Args:
            *args: 位置参数
            sensitive_patterns: 敏感参数名模式
            **kwargs: 关键字参数
            
        Returns:
            (脱敏后的args, 脱敏后的kwargs)
        """
        if sensitive_patterns is None:
            sensitive_patterns = ['token', 'password', 'key', 'secret']
        
        # 关键字参数脱敏
        clean_kwargs = DataSanitizer.sanitize_dict(kwargs, sensitive_patterns)
        
        # 位置参数转为安全表示(避免大对象)
        clean_args = []
        for arg in args:
            if isinstance(arg, pd.DataFrame):
                clean_args.append(DataSanitizer.sanitize_dataframe(arg))
            elif isinstance(arg, str) and len(arg) > 100:
                clean_args.append(f"{arg[:50]}...(truncated)")
            else:
                clean_args.append(repr(arg)[:100])
        
        return tuple(clean_args), clean_kwargs
