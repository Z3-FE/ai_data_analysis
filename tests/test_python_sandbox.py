"""受限 Python 计算执行测试。"""

import unittest
from decimal import Decimal

from app.agent.python_sandbox import execute_python_calculation


class PythonSandboxTest(unittest.IsolatedAsyncioTestCase):
    """验证动态字段、数据库数值和危险代码的基本行为。"""

    async def test_executes_code_against_runtime_field_names(self) -> None:
        result = await execute_python_calculation(
            "def calculate(rows):\n"
            '    return {"drop": rows[0]["sales_total"] - rows[1]["sales_total"]}',
            [
                {"period_label": "2017-02", "sales_total": Decimal(80)},
                {"period_label": "2017-01", "sales_total": Decimal(100)},
            ],
        )

        self.assertEqual(result, {"drop": -20.0})

    async def test_rejects_imports(self) -> None:
        with self.assertRaisesRegex(ValueError, "禁止导入"):
            await execute_python_calculation(
                "import os\ndef calculate(rows):\n    return {}", []
            )

    async def test_allows_decimal_compatibility_import(self) -> None:
        result = await execute_python_calculation(
            "from decimal import Decimal\n"
            "def calculate(rows):\n"
            "    return {\"value\": float(Decimal(str(rows[0][\"value\"]))) * 2}",
            [{"value": "10.5"}],
        )

        self.assertEqual(result, {"value": 21.0})

    async def test_rejects_code_outside_calculate(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能定义一个 calculate"):
            await execute_python_calculation(
                "value = 1\ndef calculate(rows):\n    return {}", []
            )

    async def test_common_builtin_isinstance_is_available(self) -> None:
        result = await execute_python_calculation(
            "def calculate(rows):\n"
            "    return {\"is_number\": isinstance(rows[0][\"value\"], (int, float))}",
            [{"value": 1}],
        )

        self.assertEqual(result, {"is_number": True})

    async def test_allows_safe_iteration_and_exception_handling(self) -> None:
        result = await execute_python_calculation(
            "def calculate(rows):\n"
            "    try:\n"
            "        row_iter = iter(rows)\n"
            "        return {\"count\": len(list(row_iter))}\n"
            "    except Exception:\n"
            "        return {\"count\": 0}",
            [{"value": 1}, {"value": 2}],
        )

        self.assertEqual(result, {"count": 2})


if __name__ == "__main__":
    unittest.main()
