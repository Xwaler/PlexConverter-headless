FROM linuxserver/ffmpeg:latest
RUN apt update && apt install -y git mediainfo python3 python3-pip && \
    git clone https://github.com/Xwaler/PlexConverter-headless.git && \
    python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r PlexConverter-headless/requirements.txt
ENV PYTHONUNBUFFERED 1
