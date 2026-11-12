from subprocess import CompletedProcess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Todo API", description="A simple API for managing todo items", version="1.0.0")

class todoCreate(BaseModel):
    title: str
    description: Optional[str] = None

class todoUpdate(BaseModel):
    completed: bool

class todo(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    completed: bool = False

todos_db = {}
next_id = 1

# POST /todos - Create a new todo item
@app.post("/todos", response_model=todo, status_code=201)
async def create_todo(todo: todoCreate):
    global next_id
    new_todo = todo(
        id=next_id,
        title=todo.title,
        description=todo.description,
        completed=False
    )
    todos_db[next_id] = new_todo
    next_id += 1
    return new_todo

# GET /todos - Get all todo items
@app.get("/todos", response_model=list[todo])
async def get_todos():
    return list(todos_db.values())

# GET /todos/{id} - Get a specific todo item by ID
@app.get("/todos/{todo_id}", response_model=todo)
async def get_todo(todo_id: int):
    if todo_id not in todos_db:
        raise HTTPException(status_code=404, detail="Todo item not found")
    return todos_db[todo_id]

# PUT /todos/{id} - Update a specific todo item by ID
@app.put("/todos/{todo_id}", response_model=todo)
async def update_todo(todo_id: int, update: todoUpdate):
    if todo_id not in todos_db:
        raise HTTPException(status_code=404, detail="Todo item not found")
    
    todo = todos_db[todo_id]
    todo.completed = update.completed
    todos_db[todo_id] = todo
    return todo

# DELETE /todos/{id} - Delete a specific todo item by ID
@app.delete("/todos/{todo_id}", status_code=204)
async def delete_todo(todo_id: int):
    if todo_id not in todos_db:
        raise HTTPException(status_code=404, detail="Todo item not found")
    del todos_db[todo_id]

