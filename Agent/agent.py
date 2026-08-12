import os
from typing import TypedDict,Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv
import asyncio

import httpx


load_dotenv()

groq_key = os.getenv("GROQ_API_KEY")


# 2. Initialize FastAPI and Groq Model
# app = FastAPI(title="LangGraph Two-Agent Evaluator API")

# Initialize ChatGroq (using Llama 70B)
llm = ChatGroq(
    model="llama-3.3-70b-versatile",  # Or llama-3.1-70b-versatile depending on your Groq deployment
    temperature=0.1,
    
    groq_api_key=groq_key


)



async def fetch_random_user_api() -> dict:
    url = "https://randomuser.me/api/"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10.0)
        
        if response.status_code == 200:
            user = response.json()["results"][0]
            # Extract key fields so the payload is clean for the LLM
            return {
                "name": f"{user['name']['title']} {user['name']['first']} {user['name']['last']}",
                "gender": user["gender"],
                "age": user["dob"]["age"],
                "location": f"{user['location']['city']}, {user['location']['country']}",
                "email": user["email"],
                "phone": user["phone"],
                "nationality": user["nat"]
            }
        return {"error": f"Failed to fetch data (Status code: {response.status_code})"}


class UserSummaryState(TypedDict):
    raw_user_data: Optional[dict]
    summary: Optional[str]


async def fetch_user_node(state: UserSummaryState):
    """Node 1: Calls https://randomuser.me/api/ and saves profile to state."""
    print("--> Calling randomuser.me API...")
    data = await fetch_random_user_api()
    return {"raw_user_data": data}

async def summarize_node(state: UserSummaryState):
    """Node 2 (Generator): Summarizes the fetched user profile using an LLM."""
    print("--> Generating summary from raw user data...")
    raw_data = state.get("raw_user_data")
    
    prompt = f"""
    You are an assistant that creates quick user profile summaries.
    Here is raw JSON data fetched from the user database API:

    {raw_data}

    Please write a brief 2-3 sentence biography introducing this user.
    """
    
   
    response = await llm.ainvoke(prompt)
    
    return {"summary": response.content}


builder = StateGraph(UserSummaryState)

# Add nodes
builder.add_node("fetch_user", fetch_user_node)
builder.add_node("summarize_user", summarize_node)

# Flow: START -> fetch_user -> summarize_user -> END
builder.add_edge(START, "fetch_user")
builder.add_edge("fetch_user", "summarize_user")
builder.add_edge("summarize_user", END)

app = builder.compile()


async def main():
    # Pass empty state since the API generates a random user dynamically
    result = await app.ainvoke({})
    
    print("\n--- RAW DATA FROM API ---")
    print(result["raw_user_data"])
    
    print("\n--- FINAL SUMMARY GENERATED ---")
    print(result["summary"])

if __name__ == "__main__":
    asyncio.run(main())