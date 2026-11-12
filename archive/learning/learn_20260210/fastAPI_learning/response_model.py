'''
Author: lww
Date: 2026-04-02 11:20:52
LastEditors: 
LastEditTime: 2026-04-02 11:21:01
FilePath: /agent_study_workspace/learn_20260210/fastAPI_learning/response_model.py
Description: learn response model in fastAPI

'''
# 响应模型

from fastapi import Response, FastAPI
from typing import Optional
from pydantic import BaseModel

app = FastAPI()

class ItemResponse(BaseModel):
    id: int
    name: str
    price: float
    description: Optional[str] = None

@app.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(item_id: int):
    return {
        "id": item_id,
        "name": "test",
        "price": 9.99,
    }

