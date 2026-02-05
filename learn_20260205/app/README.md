# 更改pip源为清华源
```
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

# 若安装LangGraph CLI出现报错，尝试以下方案：
```
pip install -upgrade "langgraph-cli[inmem]" --break-system-packages
```

1. 方案一：
先安装依赖，在尝试安装
```
# 先更新 setuptools 和 wheel
pip install --upgrade setuptools wheel --break-system-packages

# 再安装 forbiddenfruit
pip install forbiddenfruit --break-system-packages
```

2. 方案二：
使用pip的额外选项安装
```
pip install forbiddenfruit --no-build-isolation

# or

# pip install forbiddenfruit --no-deps
```