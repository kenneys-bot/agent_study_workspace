"""规则相关的领域模块（M4，契约 §2 RuleVersion、§3 知识与规则）。

子模块：

- :mod:`bank_assistant.rules.lifecycle`：状态机、严重级别与适用范围窗口的纯逻辑；
- :mod:`bank_assistant.rules.schema`：规则内容与契约 §4 证据 JSON 的校验；
- :mod:`bank_assistant.rules.service`：草稿/提交/发布/退役的落库与授权；
- :mod:`bank_assistant.rules.demo_seed`：合成演示规则集的校验与幂等导入。

演示规则全部为虚构内容（``demo_only=true``），不对应任何真实银行规则。
"""
