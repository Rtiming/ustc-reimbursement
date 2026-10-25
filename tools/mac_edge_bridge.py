"""历史Mac导入入口；原生工作页实现统一维护在native_finance_bridge。"""
import sys
import native_finance_bridge as _implementation

if __name__ == '__main__':
    _implementation._mac()
    _implementation.main()
else:
    # 保持既有import/patch引用与唯一实现同一模块身份。
    sys.modules[__name__] = _implementation
