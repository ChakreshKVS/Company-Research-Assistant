import os
from typing import TypedDict, Annotated, List, Dict
from dotenv import load_dotenv
import operator

# --- IMPORTS ---
from langchain_groq import ChatGroq
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool 
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

load_dotenv()

# --- CONFIGURATION ---
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
tavily_tool = TavilySearchResults(max_results=2)

# --- TOOLS ---
@tool
def update_plan_section(section: str, content: str):
    """
    Save key findings to the plan.
    Combine details into one call per section.
    """
    return f"Saved to {section}."

@tool
def reset_memory():
    """Trigger reset."""
    return "RESET_TRIGGERED"

tools = [tavily_tool, update_plan_section, reset_memory]
llm_with_tools = llm.bind_tools(tools)

# --- STATE ---
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    account_plan: Annotated[Dict, lambda x, y: {**x, **y}]
    steps: Annotated[int, operator.add]

# --- PROMPT ---
RESEARCHER_SYSTEM_PROMPT = """You are a Senior Corporate Strategist.

### 1. YOUR IDENTITY & TONE
- You are helpful, professional, and intelligent.
- **DO NOT** use robotic, one-line responses like "I focus on corporate research."
- Instead, explain *why* you can't help, or guide the user to the next step naturally.

### 2. INTERACTION GUIDELINES
- **Chatty/Off-Topic:** If the user talks about sports, movies, or food, politely explain that you are a business tool, but offer to pivot back to business.
  *Example:* "I'm afraid I don't follow sports, but I can help you analyze the business strategy of a sports team if you like!"
- **Confused User:** If the user says "I'm not sure" or "Help", act as a consultant. Suggest 2-3 popular companies to research.
  *Example:* "No problem! We could start by looking at a tech giant like Apple, or maybe a competitor in your industry. Who would you like to target?"

### 3. RESEARCH WORKFLOW (Strict Data Rules)
When the user gives a valid company name:

1.  **CHECK MEMORY FIRST:**
    - If you *already* have data for Company A, and the user asks for Company B -> **Call `reset_memory`**.
    - If your memory is empty -> **PROCEED.**

2.  **MANDATORY SEARCH:**
    - You **MUST** call `tavily_search_results_json` before writing anything.
    - Query: "[Company Name] strategic overview 2024"

3.  **HANDLE "NO RESULTS":**
    - If the search returns nothing (empty list) or the company looks fake (e.g., "SomeRandomCompany"), **STOP**.
    - Tell the user: "I searched for [Name] but couldn't find any public financial or strategic data. It might be a private, local, or fictional entity."
    - **DO NOT** generate a fake plan (ABC Inc).

4.  **SAVE & REPORT:**
    - Call `update_plan_section` for found data.
    - Output the Markdown plan.

### 4. ACCOUNT PLAN SECTIONS
1. Executive Summary
2. Overview (Revenue, Size)
3. Key Stakeholders
4. SWOT Analysis
5. Competitors
"""

# --- NODES ---

def researcher_node(state: AgentState):
    messages = state['messages']
    
    if len(messages) > 6:
        messages = messages[-6:]
    
    if "account_plan" not in state or state["account_plan"] is None:
        state["account_plan"] = {}

    # Check if plan exists to help the LLM decide on Reset
    plan_status = "Memory is currently EMPTY."
    if state["account_plan"]:
        plan_status = "Memory contains data. If user asks for a NEW company, you must RESET."

    # Inject Prompt + Memory Status
    system_message = f"{RESEARCHER_SYSTEM_PROMPT}\n\nCURRENT STATUS: {plan_status}"
    
    messages = [SystemMessage(content=system_message)] + messages
    
    response = llm_with_tools.invoke(messages)
    return {"messages": [response], "steps": 1}

def tool_node(state: AgentState):
    tool_executor = ToolNode(tools)
    result = tool_executor.invoke(state)
    
    last_msg = state["messages"][-1]
    new_plan_data = {}
    
    if hasattr(last_msg, 'tool_calls'):
        for tool_call in last_msg.tool_calls:
            if tool_call["name"] == "update_plan_section":
                sec = tool_call["args"].get("section")
                con = tool_call["args"].get("content")
                if sec and con:
                    new_plan_data[sec] = con
                
    return {"messages": result["messages"], "account_plan": new_plan_data}

def should_continue(state: AgentState):
    messages = state['messages']
    last_message = messages[-1]
    steps = state.get("steps", 0)

    # 1. Loop Protection
    if steps >= 6:
        return END

    # 2. Finished Speaking Protection
    if last_message.content and len(last_message.content) > 100:
        return END

    # 3. Tool Check
    if last_message.tool_calls:
        return "tools"
    
    return END

# --- GRAPH ---
workflow = StateGraph(AgentState)
workflow.add_node("researcher", researcher_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("researcher")
workflow.add_conditional_edges("researcher", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "researcher")
app_graph = workflow.compile()