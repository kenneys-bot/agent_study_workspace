from langchain.agents import create_agent
from agent.my_llm import llm

from langchain.tools import tool

@tool("calculateAdd", parse_docstring=True, description="计算两个数的和")
def calculateAdd(
    a: float,
    b: float,
    operation: str,
) -> float:
    """
    工具函数：计算两个数的和

    Args:
        a: 第一个数
        b: 第二个数
        operation: 运算类型，只能是add,sub,mul,div

    Returns:
        float: 两个数的和
    """
    print(f"调用calculateAdd工具函数，第一个数：{a}，第二个数：{b}，运算类型：{operation}")
    result = 0.0
    match operation:
        case "add":
            result = a + b
        case "sub":
            result = a - b
        case "mul":
            result = a * b
        case "div":
            if b != 0:
                result = a / b
            else:
                raise ValueError("除数不能为零")
        case _:
            raise ValueError("运算类型错误")

    return result



def send_email(
    to: str,
    subject: str,
    body: str,
) -> str:
    """
    发送一封电子邮件。

    :param to: 收件人邮箱地址，例如 "user@example.com"。
    :param subject: 邮件主题。
    :param body: 邮件正文内容。
    :return: 无返回值，仅代表发送操作。
    """

    return f"邮件已发送至{to}"

agent = create_agent(
    model=llm,
    tools=[send_email, calculateAdd],
    system_prompt="你是一个个人辅助助手。请合理使用工具函数，完成用户的需求。"
)
