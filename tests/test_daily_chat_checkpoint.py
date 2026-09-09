"""日常聊天节点的 Checkpointer 连续对话测试。"""

import unittest

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentContext
from app.agent.nodes.daily_chat import daily_chat
from app.agent.nodes.finalize_turn import finalize_turn
from app.agent.state import AgentState


class DailyChatCheckpointTest(unittest.IsolatedAsyncioTestCase):
    """验证消息由图状态保存，并且按 thread_id 隔离。"""

    async def test_restores_history_for_same_thread_only(self) -> None:
        model_inputs: list[list] = []

        def respond(prompt) -> str:
            messages = prompt.to_messages()
            model_inputs.append(messages)
            has_name_history = any(
                isinstance(message, HumanMessage)
                and "我叫 zkq" in str(message.content)
                for message in messages[:-1]
            )
            if "我叫什么" in str(messages[-1].content):
                return "你叫 zkq。" if has_name_history else "我还不知道你的名字。"
            return "好的，我记住了，你叫 zkq。"

        builder = StateGraph(state_schema=AgentState, context_schema=AgentContext)
        builder.add_node("daily_chat", daily_chat)
        builder.add_node("finalize_turn", finalize_turn)
        builder.add_edge(START, "daily_chat")
        builder.add_edge("daily_chat", "finalize_turn")
        builder.add_edge("finalize_turn", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        context = {"llm_client": RunnableLambda(respond)}

        await graph.ainvoke(
            {"input_text": "你好，我叫 zkq，请记住我的名字"},
            config={"configurable": {"thread_id": "conversation-a"}},
            context=context,
        )
        same_thread_result = await graph.ainvoke(
            {"input_text": "你记得我叫什么吗？"},
            config={"configurable": {"thread_id": "conversation-a"}},
            context=context,
        )
        other_thread_result = await graph.ainvoke(
            {"input_text": "你记得我叫什么吗？"},
            config={"configurable": {"thread_id": "conversation-b"}},
            context=context,
        )

        self.assertEqual(same_thread_result["output_text"], "你叫 zkq。")
        self.assertEqual(
            other_thread_result["output_text"],
            "我还不知道你的名字。",
        )
        self.assertEqual(len(same_thread_result["messages"]), 4)
        self.assertIsInstance(same_thread_result["messages"][0], HumanMessage)
        self.assertIsInstance(same_thread_result["messages"][1], AIMessage)
        self.assertEqual(len(model_inputs[0]), 2)
        self.assertEqual(len(model_inputs[1]), 4)
        self.assertEqual(len(model_inputs[2]), 2)


if __name__ == "__main__":
    unittest.main()
