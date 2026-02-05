from pydantic import SecretStr
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from env_utils import API_KEY, BASE_URL 

# 调用硅基流动api
# llm = ChatOpenAI(
#     model_name = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
#     temperature=1.3,
#     api_key=API_KEY,
#     base_url=BASE_URL
# )
# response = llm.invoke("用三句话简单介绍一下：机器学习的基本概念")
# print(type(response))
# print(response)

# llm = ChatDeepSeek(
#     model_name = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
#     temperature=1.3,
#     api_key=API_KEY,
#     api_base=BASE_URL
# ) 
# response = llm.invoke("用三句话简单介绍一下：机器学习的基本概念")
# print(type(response))
# print(response)

# for chunk in llm.stream("用三句话简单介绍一下：机器学习的基本概念"):
#     # print(type(chunk))
#     # print(chunk)
#     reasoning_steps = [r for r in chunk.content_blocks if r["type"] == "reasoning"]
#     print(reasoning_steps if reasoning_steps else chunk.text)

llm = ChatOpenAI(
    model="qwen/Qwen3-30B-A3B-Thinking-2507",
    api_key=SecretStr(API_KEY),
    base_url=BASE_URL,
)
