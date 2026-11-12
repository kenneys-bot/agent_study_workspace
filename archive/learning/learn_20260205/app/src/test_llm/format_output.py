from pydantic import BaseModel, Field
from my_llm import llm

class Movie(BaseModel):
    title: str = Field(description="The title of the movie")
    director: str = Field(description="The director of the movie")
    year: int = Field(description="The year the movie was released")
    rating: float = Field(description="The rating of the movie(0-10)")

model_with_structured_output = llm.with_structured_output(Movie, include_raw=True)
response = model_with_structured_output.invoke("提供《黑客帝国》 的详细信息")
print(response)