# ContextEngine

ContextEngine 是可供不同 Agent 复用的上下文编译内核。它不读取具体数据库，
也不认识 Data Agent、知识库或智能助手的业务对象；调用方先把候选内容转换成
ContextItem，引擎再完成统一处理。

## 能力

~~~text
gather()    标准化候选项并去重
isolate()   按用户、会话、Agent、节点和任务作用域过滤
select()    按相关性、新近性、重要性和 Token 预算选择
structure() 按 task/state/evidence/history 等分区组织
compress()  超预算时确定性截断并保留来源
compile()   执行完整流程并生成模型消息
trace()     返回本次选择、丢弃和预算使用记录
~~~

## 最小用法

~~~python
from app.context_engine import ContextEngine, ContextItem, ContextPolicy, ContextRequest

engine = ContextEngine()
request = ContextRequest(
    current_input="那 11 月呢？",
    user_id="user-1",
    conversation_id="conversation-1",
    agent_id="data-agent",
    token_budget=2000,
)
items = [
    ContextItem(
        item_id="turn-001-question",
        content="用户上一轮询问了 2017 年销售额下降最明显的月份。",
        source_type="conversation_message",
        source_ref="conversation_messages/turn-001",
        scope={"user_id": "user-1", "conversation_id": "conversation-1"},
        section="history",
    ),
    ContextItem(
        item_id="turn-001-result",
        content="上一轮结果：2017 年 12 月下降最明显。",
        source_type="analysis_output",
        source_ref="turn_outputs/turn-001",
        scope={"user_id": "user-1", "conversation_id": "conversation-1"},
        section="evidence",
        importance=0.9,
    ),
]
compiled = engine.compile(
    request,
    items,
    system_instructions="你是数据分析助手。",
    policy=ContextPolicy(
        section_titles={"history": "相关历史", "evidence": "结果证据"},
        protected_source_types=frozenset({"analysis_output"}),
    ),
)

messages = compiled.to_messages()
debug_payload = compiled.to_dict()
~~~

## 接入边界

~~~text
业务 Agent / ContextSource
    -> ContextItem
    -> ContextEngine.compile()
    -> CompiledContext
    -> Agent Adapter 转换成具体框架消息
~~~

ContextEngine 不负责保存长期记忆、执行数据库查询、生成 SQL 或判断业务路由。
未来接入 Data Agent、企业知识库或智能助手时，只需要分别实现候选项转换和策略，
不需要修改这个通用内核。

`ContextRequest.token_budget` 是可分配给上下文候选项的预算，不是模型的完整上下文
窗口。调用方需要先为系统提示、当前用户输入和模型输出预留空间，再把剩余预算交给
ContextEngine。`CompiledContext.token_usage` 会分别报告上下文项、结构化上下文、系统
提示、当前输入和最终消息的估算 Token 数，方便接入层校准预算。

默认 Token 计算器是无依赖估算器。若某个 Agent 需要精确预算，可以在初始化时
注入与目标模型匹配的 tokenizer：

~~~python
engine = ContextEngine(token_counter=my_model_tokenizer)
~~~
