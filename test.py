"""验证 StrEnum 的 value 和 _value_ 赋值行为。"""

from enum import StrEnum, Enum


class CorrectSource(Enum):
    TABLE_RECALL = ("table_recall", "表召回")

    def __new__(cls, value: str, description: str):
        member = object.__new__(cls)
        # member.value = value
        member.description = description
        return member


print("正确写法：", CorrectSource.TABLE_RECALL)

# try:
#     class WrongSource(StrEnum):
#         TABLE_RECALL = ("table_recall", "表召回")
#
#         def __new__(cls, value: str, description: str):
#             member = str.__new__(cls, value)
#             member.value = value
#             member.description = description
#             return member
# except Exception as exc:
#     print("错误写法：", type(exc).__name__, str(exc))
