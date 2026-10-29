"""
numpy兼容性补丁：解决numpy 1.26和旧版scipy的兼容性问题
在导入其他库之前先导入这个补丁
"""
import numpy as np

# 补充numpy 2.0移除的旧API，兼容旧版scipy
if not hasattr(np, 'long'):
    np.long = np.int64
if not hasattr(np, 'ulong'):
    np.ulong = np.uint64
if not hasattr(np, 'longlong'):
    np.longlong = np.int64
if not hasattr(np, 'ulonglong'):
    np.ulonglong = np.uint64
if not hasattr(np, 'uintc'):
    np.uintc = np.uint32
if not hasattr(np, 'intc'):
    np.intc = np.int32
