from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


def test_langgraph_state_graph_runs_with_a_thread_id():
    graph = StateGraph(dict)
    graph.add_node("finish", lambda state: {**state, "visited": True})
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    compiled = graph.compile(checkpointer=InMemorySaver())

    result = compiled.invoke(
        {"visited": False},
        {"configurable": {"thread_id": "run-test"}},
    )

    assert result["visited"] is True
