FROM linuxserver/ffmpeg:latest

ENV PYTHONUNBUFFERED 1

RUN apt update && \
    apt install -y mediainfo python3 python3-pip python3-venv

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install -r /tmp/requirements.txt
