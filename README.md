# PlexConverter-headless

Converts video files dropped into the complete folder to H264 stereo and outputs into optimized.<br>
Used for post-processing movies/tv shows after downloading.

### Setup
Copy the Dockerfile to your system and indicate its path in your docker-compose.yml or Portainer stack.
Building the container will fetch the rest of the files from this repository.

### Compose
```
version: "2.1"
services:
  plexconverter:
    container_name: plexconverter
    image: plexconverter:latest
    build: 
      context: /path/to/config/plexconverter
      dockerfile: plexconverter.Dockerfile
    user: 1001:100 # plex user:users group
    command: /bin/sh -c "python PlexConverter-headless/converter.py"
    restart: unless-stopped
    environment:
      - TZ=Europe/Paris
      - VIDEO_CRF=27
      - VIDEO_MAX_BITRATE=2500
      - AUDIO_MAX_BITRATE=256
      - RADARR_FOLDER=radarr
      - SONARR_FOLDER=sonarr
    volumes:
      - /path/to/downloads/complete:/downloads
      - /path/to/config/plexconverter/converted:/converted
      - /path/to/config/plexconverter/normalized:/normalized
      - /path/to/downloads/optimized:/optimized
```
