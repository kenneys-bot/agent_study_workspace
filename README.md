# Ubuntu 23.04+ 和 Python 3.11+ 

旨在防止用户使用 pip 在系统全局 Python 环境中安装包，以避免破坏系统依赖。

```
pip install <package> --break-system-packages
```

# Ubuntu24.04 and Python3.13 Install
## Dockerfile
```
FROM ubuntu:24.04

# 避免交互式安装提示
ENV DEBIAN_FRONTEND=noninteractive

# 更新并安装 Python 3.13 和常用工具
RUN apt-get update && apt-get install -y \
    wget \
    build-essential \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y python3.13 python3.13-venv python3.13-dev \
    && apt-get install -y python3-pip \
    && ln -sf /usr/bin/python3.13 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.13 /usr/bin/python \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 验证安装
RUN python --version && pip --version
```

## docker-compose.yml
```
services:
  python-ubuntu:
    image: homebrew/ubuntu24.04
    container_name: ubuntu24_04
    volumes:
      - ./app:/app  # 挂载本地目录
    working_dir: /app
    tty: true
    stdin_open: true
    command: /bin/bash  # 保持容器运行
    ports:
      - "8000:8000"  # 如果需要暴露端口
```

