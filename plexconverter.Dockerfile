FROM python:3.8-slim
ENV PYTHONUNBUFFERED 1
RUN apt update && apt install -y ffmpeg git && \
    git clone https://github.com/Xwaler/PlexConverter-headless.git && \
    pip install --no-cache-dir -r PlexConverter-headless/requirements.txt
