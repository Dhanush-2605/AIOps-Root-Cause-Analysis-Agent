import os
from typing import TypedDict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END

# 1. Define the Shared State Schema
class WorkflowState(TypedDict):
    user_input: str
    generator_output: str
    evaluation: str

# 2. Initialize FastAPI and Groq Model
app = FastAPI(title="LangGraph Two-Agent Evaluator API")

# Initialize ChatGroq (using Llama 70B)
llm = ChatGroq(
    model="llama-3.3-70b-versatile",  # Or llama-3.1-70b-versatile depending on your Groq deployment
    temperature=0.1,
    
    api_key = os.getenv("GROQ_API_KEY"),

)

# 3. Define Agent Nodes
def generator_node(state: WorkflowState) -> dict:
    """Agent 1: Generates an answer to the user prompt."""
    prompt = f"Answer the following user question clearly and accurately:\n\n{state['user_input']}"
    response = llm.invoke(prompt)
    return {"generator_output": response.content}

def evaluator_node(state: WorkflowState) -> dict:
    """Agent 2: Strictly evaluates the generator's output."""
    prompt = f"""You are a strict evaluation agent. Analyze the original user question and the candidate answer provided.

User Request:
{state['user_input']}

Candidate Answer:
{state['generator_output']}

Evaluation Guidelines:
- Determine if the answer is factually correct, precise, and directly addresses the prompt.
- Start your response clearly with either [CORRECT] or [INCORRECT].
- Provide concise reasoning for your judgment.
"""
    response = llm.invoke(prompt)
    return {"evaluation": response.content}

# 4. Construct the LangGraph Workflow
builder = StateGraph(WorkflowState)

# Add nodes
builder.add_node("generator", generator_node)
builder.add_node("evaluator", evaluator_node)

# Add execution edges: START -> Generator -> Evaluator -> END
builder.add_edge(START, "generator")
builder.add_edge("generator", "evaluator")
builder.add_edge("evaluator", END)

# Compile graph
app_graph = builder.compile()

# 5. Define Request and Response Schemas
class QueryRequest(BaseModel):
    prompt: str

class QueryResponse(BaseModel):
    user_input: str
    generator_output: str
    evaluation: str

# 6. FastAPI Route
@app.post("/evaluate-prompt", response_model=QueryResponse)
async def process_prompt(request: QueryRequest):
    try:
        initial_state: WorkflowState = {
            "user_input": request.prompt,
            "generator_output": "",
            "evaluation": ""
        }
        
        # Execute the graph
        final_state = app_graph.invoke(initial_state)
        
        return QueryResponse(
            user_input=final_state["user_input"],
            generator_output=final_state["generator_output"],
            evaluation=final_state["evaluation"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



