"""受限执行 LLM 生成的纯 Python 数据计算代码。

执行边界：

调用方
    -> 传入只定义 calculate(rows) 的源码和完整查询结果
AST 校验
    -> 拒绝非 Decimal 导入、全局语句、私有属性和函数外代码
子进程执行
    -> 使用受限 builtins、CPU/文件/输出限制
结果返回
    -> 只接受 JSON 结果

这是当前分析链路的实验性隔离方案。生产环境仍需要容器、cgroup 或
其他更强的进程隔离能力，不能把本模块视为完整的安全边界。
"""

import asyncio
import json
import sys

from fastapi.encoders import jsonable_encoder

_RUNNER = r"""
# 运行器在独立 Python 进程中执行，避免直接污染 Agent 服务进程。
import ast
import decimal
import json
import resource
import sys

payload = json.loads(sys.stdin.read())
code = payload["code"]
rows = payload["rows"]

# 先做语法和结构检查，再设置资源限制和执行命名空间。
tree = ast.parse(code)

for node in ast.walk(tree):
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        raise ValueError("计算代码禁止导入模块或修改全局作用域")
    if isinstance(node, ast.Import):
        raise ValueError("计算代码禁止导入模块或修改全局作用域")
    if isinstance(node, ast.ImportFrom):
        allowed_decimal_import = (
            node.module == "decimal"
            and node.level == 0
            and len(node.names) == 1
            and node.names[0].name == "Decimal"
            and node.names[0].asname is None
        )
        if not allowed_decimal_import:
            raise ValueError("计算代码禁止导入模块或修改全局作用域")
    if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
        raise ValueError("计算代码禁止访问私有属性")

top_level = [
    node
    for node in tree.body
    if not isinstance(node, ast.FunctionDef)
    and not (
        isinstance(node, ast.ImportFrom)
        and node.module == "decimal"
        and node.level == 0
        and len(node.names) == 1
        and node.names[0].name == "Decimal"
        and node.names[0].asname is None
    )
]
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
if top_level or len(functions) != 1 or functions[0].name != "calculate":
    raise ValueError("计算代码只能定义一个 calculate(rows) 函数")

# Linux 下补充地址空间限制；macOS 对该限制的支持不可靠。
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
if sys.platform.startswith("linux"):
    address_space_limit = 256 * 1024 * 1024
    resource.setrlimit(
        resource.RLIMIT_AS,
        (address_space_limit, address_space_limit),
    )
resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))

# 只暴露计算常用的无副作用内置函数。Decimal 是数据库数值常见的
# 兼容类型，仅允许代码导入这一种标准库对象；其他模块仍不可用。
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "decimal" and level == 0 and tuple(fromlist) == ("Decimal",):
        return decimal
    raise ImportError("计算代码只能导入 decimal.Decimal")


safe_builtins = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "Exception": Exception, "float": float,
    "int": int, "isinstance": isinstance, "iter": iter,
    "len": len,
    "list": list, "max": max, "min": min, "range": range,
    "round": round, "set": set, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "zip": zip,
    "__import__": safe_import,
}
namespace = {"__builtins__": safe_builtins}
exec(compile(tree, "<analysis-calculation>", "exec"), namespace)
result = namespace["calculate"](rows)
print(json.dumps(result, ensure_ascii=False, default=str))
"""


async def execute_python_calculation(code: str, rows: list[dict]) -> object:
    """在隔离子进程中执行 calculate(rows)，返回 JSON 结果。

调用方不应依赖 Python 返回任意对象；运行器会统一将结果编码成 JSON，
这样分析任务可以把结果保存到 TaskResult 并传递给后续依赖任务。
"""
    # 使用 isolated、无 site 包的 Python 进程，降低外部环境对计算的影响。
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        "-c",
        _RUNNER,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # jsonable_encoder 负责把 Decimal 等数据库类型转换为可传输值。
    payload = json.dumps(
        {"code": code, "rows": jsonable_encoder(rows)},
        ensure_ascii=False,
    ).encode()
    # 超时后主动结束子进程，避免失控计算继续占用服务资源。
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=4)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ValueError("Python 计算执行超时") from None

    # 将子进程错误压缩到有限长度，避免异常信息污染 SSE 响应。
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise ValueError(f"Python 计算执行失败：{message[-1000:]}")
    if len(stdout) > 1024 * 1024:
        raise ValueError("Python 计算结果超过 1MB 限制")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Python 计算结果不是有效 JSON") from exc
