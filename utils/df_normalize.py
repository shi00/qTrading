"""DataFrame 读取归一化工具（DAT-10/11）。

统一将数据库 Numeric 列读取得到的 Python ``Decimal`` 对象归一化为
``float64``，消除跨层 Decimal 类型污染并降低区间预载内存占用。
"""

import pandas as pd


def normalize_decimal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将「全 Decimal/None 的 object 列」向量化转 float64（DAT-10/11）。

    仅转换 ``infer_dtype == "decimal"`` 的列；字符串/混合/全空/int64/float64
    列均不受影响。原地修改并返回 df（``Decimal('NaN')`` → NaN 天然处理）。
    """
    for col in df.columns:
        ser = df[col]
        if ser.dtype == object and pd.api.types.infer_dtype(ser, skipna=True) == "decimal":
            df[col] = ser.astype("float64")
    return df
