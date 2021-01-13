FROM linuxserver/ffmpeg:latest
COPY requirements.txt /tmp/requirements.txt
RUN apt update && apt install -y mediainfo python3 python3-pip && \
    python3 -m pip install --upgrade pip && \
    python3 -m pip install --no-cache-dir -r /tmp/requirements.txt
ENV PYTHONUNBUFFERED 1