import chainlit as cl
from agent import app_graph
from langchain_core.messages import HumanMessage, AIMessage

@cl.on_chat_start
async def start():
    cl.user_session.set("messages", [])
    cl.user_session.set("account_plan", {})
    
    await cl.Message(
        content="👋 **Company Research Assistant**"
    ).send()

@cl.on_message
async def main(message: cl.Message):
    history = cl.user_session.get("messages")
    plan = cl.user_session.get("account_plan")
    
    history.append(HumanMessage(content=message.content))
    msg = cl.Message(content="")
    await msg.send()
    
    # Initialize steps to 0
    async for event in app_graph.astream_events(
        {"messages": history, "account_plan": plan, "steps": 0}, 
        version="v1"
    ):
        kind = event["event"]
        
        # 1. Chat Output
        if kind == "on_chat_model_stream" and event["metadata"].get("langgraph_node") == "researcher":
            content = event["data"]["chunk"].content
            if content:
                await msg.stream_token(content)
        
        # 2. Search Viz
        elif kind == "on_tool_start" and event["name"] == "tavily_search_results_json":
            await cl.Message(
                content=f"🌐 **Searching...**", 
                parent_id=msg.id, 
                author="System"
            ).send()

        # 3. Save Viz (Only show it once per turn to avoid spam)
        elif kind == "on_tool_start" and event["name"] == "update_plan_section":
            # We don't print anything here to keep the UI clean. 
            # The user just wants to see the final plan.
            pass

        # 4. Reset Viz
        elif kind == "on_tool_start" and event["name"] == "reset_memory":
            await cl.Message(content=f"🧹 **Context Cleared.**", parent_id=msg.id, author="System").send()
            cl.user_session.set("account_plan", {})
            cl.user_session.set("messages", [])

    history.append(AIMessage(content=msg.content))
    cl.user_session.set("messages", history)
    await msg.update()