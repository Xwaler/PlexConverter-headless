FROM linuxserver/ffmpeg:latest

ENV PYTHONUNBUFFERED 1

RUN apt update && \
    apt install -y mediainfo python3 python3-pip

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install -r /tmp/requirements.txt
