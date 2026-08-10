import os
from typing import TypedDict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

load_dotenv()


groq_key = os.getenv("GROQ_API_KEY")
# Initialize ChatGroq (using Llama 70B)
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.1,
    groq_api_key=groq_key
)
print(llm.invoke("Will thor Defeat DOOM in secret wars?"))  