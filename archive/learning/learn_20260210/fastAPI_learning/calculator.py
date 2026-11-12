'''
Author: lww
Date: 2026-03-29 14:11:54
LastEditors: 
LastEditTime: 2026-03-29 14:12:29
FilePath: /agent_study_workspace/learn_20260210/calculator.py
Description: 

'''
from fastapi import FastAPI, Query, HTTPException

app = FastAPI(title="计算器 API")

@app.get("/calc/add")
async def add(a: float = Query(...), b: float = Query(...)):
    return {"result": a + b, "operation": "add"}

@app.get("/calc/sub")
async def subtract(a: float = Query(...), b: float = Query(...)):
    return {"result": a - b, "operation": "subtract"}

@app.get("/calc/mul")
async def multiply(a: float = Query(...), b: float = Query(...)):
    return {"result": a * b, "operation": "multiply"}

@app.get("/calc/div")
async def divide(a: float = Query(...), b: float = Query(...)):
    if b == 0:
        raise HTTPException(status_code=422, detail="除数不能为0")
    return {"result": a / b, "operation": "divide"}

# 测试:
# http://localhost:8000/calc/add?a=1&b=2  → {"result": 3.0, "operation": "add"}
# http://localhost:8000/calc/div?a=10&b=0  → 422 错误