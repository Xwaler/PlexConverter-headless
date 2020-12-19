FROM python:3.8-buster
RUN apt update && apt install -y ffmpeg
RUN git clone https://github.com/Xwaler/PlexConverter-headless.git
RUN pip install -r PlexConverter-headless/requirements.txt
ENV PYTHONUNBUFFERED 1
