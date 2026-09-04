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


