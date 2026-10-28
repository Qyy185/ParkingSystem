"""
车牌工具层
===========

纯字符串处理 + 校验，不含业务逻辑，也不碰 HTTP。

两个职责：
1. `normalize(plate)` —— 归一化，让「黑a 12345」「黑A-12345」「黑Ａ12345」
   统一成同一个字符串。否则同一辆车会被当成不同的车。
2. `is_valid(plate)` —— 校验归一化后的车牌是否符合中国大陆车牌格式。
"""

import re

# 中国大陆车牌：省份简称 + 字母 + 5~6 位 alphanumeric
# 前置可选字符：挂车号牌（京A挂XXXXX）、使馆/领馆牌（使/领）、警车/军车（WJ/军/空）
_PROVINCE = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"

_PATTERN = re.compile(
    r"^(?:[" + _PROVINCE + r"][A-Z][A-Z0-9]{5}"      # 普通蓝牌
    r"|[" + _PROVINCE + r"][A-Z][A-Z0-9]{6}"        # 新能源绿牌
    r"|[" + _PROVINCE + r"][A-Z]挂[A-Z0-9]{5}"     # 挂车
    r"|WJ[" + _PROVINCE + r"][A-Z0-9]{4}"          # 警牌
    r"|使[0-9]{3,4}|领[0-9]{3,4}"                   # 使馆 / 领馆
    r"|[港澳][A-Z][0-9]{4})$"
)

# 全角转半角需要的偏移量
_FULLWIDTH_OFFSET = 0xFEE0

# 允许出现在车牌里的分隔符，剥掉
_SEPARATORS = " \t\u3000-·—－_."


def normalize(plate):
    """把用户输入的车牌归一化成标准形式。

    做的事：
      · 去首尾空白
      · 全角字符转半角（Ａ→A，１２３→123）
      · 去掉空格和常见分隔符（内部的分隔符也去）
      · 字母转大写

    返回归一化后的字符串；输入为空或全是分隔符时返回 ""。
    """
    if not plate:
        return ""

    s = str(plate).strip()
    if not s:
        return ""

    # 全角 → 半角
    out = []
    for ch in s:
        code = ord(ch)
        if _FULLWIDTH_OFFSET <= code <= _FULLWIDTH_OFFSET + 0xFF:
            out.append(chr(code - _FULLWIDTH_OFFSET))
        else:
            out.append(ch)
    s = "".join(out)

    # 去掉分隔符
    for sep in _SEPARATORS:
        s = s.replace(sep, "")

    # 字母转大写
    return s.upper()


def is_valid(plate):
    """校验车牌格式是否合法。

    传原始输入或归一化后的字符串都可以 —— 内部会先归一化再判断。
    """
    s = normalize(plate)
    if not s:
        return False
    return bool(_PATTERN.match(s))


def invalid_reason(plate):
    """车牌不合法时给一句人话原因，合法则返回 ""。

    给前端提示用，别让用户只看到一句「格式错误」。
    """
    s = normalize(plate)
    if not s:
        return "车牌号不能为空"
    if len(s) > 8:
        return "车牌号太长了（最多 8 位）"
    if _PATTERN.match(s):
        return ""
    return "车牌格式不正确，示例：黑A12345"


def mask(plate):
    """脱敏显示，保留车牌首尾，中间打码。

    后台流水表用，避免把完整车牌号暴露在屏幕上。
    """
    s = normalize(plate)
    if len(s) <= 3:
        return s
    return s[:2] + "*" * (len(s) - 3) + s[-1]
