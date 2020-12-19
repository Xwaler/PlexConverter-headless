# PlexConverter-headless

Converts video files dropped into the complete folder to H264 stereo. Outputs into optimized.<br>
Used for post-processing after deluge.
```
version: "2.1"
services:
  plexconverter:
    container_name: plexconverter
    image: library/python:3.8-buster
    command: >
      /bin/sh -c "apt update && apt install -y git ffmpeg &&
                  git clone https://github.com/Xwaler/PlexConverter-headless.git &&
                  pip install -r PlexConverter-headless/requirements.txt &&
                  python -u PlexConverter-headless/converter.py"
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/Paris
      - MAX_VIDEO_WIDTH=1280
      - MAX_VIDEO_HEIGHT=720
      - AVERAGE_BITRATE=1100
      - MAX_BITRATE=1600
    volumes:
      - /path/to/downloads/complete:/downloads
      - /path/to/downloads/optimized:/optimized

```