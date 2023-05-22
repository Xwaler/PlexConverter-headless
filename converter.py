import os
import shlex
import shutil
import threading
import time
import subprocess
from subprocess import CalledProcessError, PIPE, DEVNULL
from typing import Dict, Optional

from pymediainfo import MediaInfo
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileOpenedEvent
from watchdog.observers import Observer

VIDEO_CRF = int(os.environ.get("VIDEO_CRF", 24))
FOR_WIDTH = int(os.environ.get("FOR_WIDTH", 1920))
FOR_HEIGHT = int(os.environ.get("FOR_HEIGHT", 1080))
VIDEO_MAX_BITRATE = int(os.environ.get("VIDEO_MAX_BITRATE", 3500))
AUDIO_MAX_BITRATE = int(os.environ.get("AUDIO_MAX_BITRATE", 256))

RADARR_FOLDER = os.environ.get("RADARR_FOLDER", "radarr")
SONARR_FOLDER = os.environ.get("SONARR_FOLDER", "sonarr")

DRY_RUN = os.environ.get("DRY_RUN", "False").lower() in ("1", "true")

PIXEL_MAX_BITRATE = VIDEO_MAX_BITRATE / (FOR_WIDTH * FOR_HEIGHT)

DOWNLOADS_FOLDER = "/downloads/complete"
CONVERTED_FOLDER = "/downloads/converted"
OPTIMIZED_FOLDER = "/downloads/optimized"
TEMPORARY_FOLDER = "/downloads/temporary"

if not os.path.exists(DOWNLOADS_FOLDER):
    os.mkdir(DOWNLOADS_FOLDER)
if not os.path.exists(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER)):
    os.mkdir(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER))
if not os.path.exists(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER)):
    os.mkdir(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER))
if not os.path.exists(CONVERTED_FOLDER):
    os.mkdir(CONVERTED_FOLDER)
if not os.path.exists(OPTIMIZED_FOLDER):
    os.mkdir(OPTIMIZED_FOLDER)
if not os.path.exists(TEMPORARY_FOLDER):
    os.mkdir(TEMPORARY_FOLDER)
else:
    for thing in os.listdir(TEMPORARY_FOLDER):
        path = os.path.join(TEMPORARY_FOLDER, thing)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

c = threading.Condition()
last_file_event = 0
last_event = None


class AnyEventHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        global last_file_event
        global last_event
        c.acquire()
        t = time.time()
        if t > last_file_event:
            last_file_event = t
        if not isinstance(event, FileOpenedEvent) and (
            not isinstance(last_event, FileSystemEvent)
            or event.src_path != last_event.src_path
        ):
            print(event)
        last_event = event
        c.release()


class LocalItem:
    def __init__(self, path: str):
        metadata = MediaInfo.parse(path)
        general = metadata.general_tracks[0]
        video = metadata.video_tracks[0] if metadata.video_tracks else None
        audio = metadata.audio_tracks[0] if metadata.audio_tracks else None

        self.bad_subtitles = [
            text_track
            for text_track in metadata.text_tracks
            if text_track.track_type
            if getattr(text_track, "format", "").upper() != "UTF-8"
            and getattr(text_track, "id", None) is not None
        ]

        self.local_file = os.path.basename(general.complete_name)
        self.relative_path = os.path.dirname(general.complete_name).replace(
            DOWNLOADS_FOLDER, "", 1
        )
        if self.relative_path.startswith("/"):
            self.relative_path = self.relative_path[1:]
        self.container = general.format

        self.video_format = getattr(video, "format", None)
        self.video_profile = getattr(video, "format_profile", None)
        if video:
            self.video_resolution = (video.height, video.width)
            try:
                self.video_bitrate = (
                    video.bit_rate
                    or video.overall_bit_rate
                    or video.nominal_bit_rate
                    or (video.stream_size * 8000 / video.duration)
                ) / 1000
            except TypeError:
                self.video_bitrate = 1e99
        else:
            self.video_resolution = None
            self.video_bitrate = 1e99

        self.audio_format = getattr(audio, "format", None)
        self.audio_format_profile = getattr(
            audio, "format_profile", getattr(audio, "format_additionalfeatures", None)
        )
        if audio:
            try:
                self.audio_bitrate = (
                    audio.bit_rate
                    or audio.overall_bit_rate
                    or audio.nominal_bit_rate
                    or (audio.stream_size * 8000 / audio.duration)
                ) / 1000
            except TypeError:
                self.audio_bitrate = 1e99
            try:
                self.audio_channels = int(audio.channel_s)
            except ValueError:
                self.audio_channels = 1e99
        else:
            self.audio_bitrate = 1e99
            self.audio_channels = 1e99

        self.reasons = {}
        self.get_reasons()

    def get_reasons(self) -> None:
        if self.video_format != "AVC" or not self.video_profile.startswith("High"):
            self.reasons["Video codec"] = {
                "Format": self.video_format,
                "Profile": self.video_profile,
            }

        if self.video_resolution is None or self.video_bitrate > PIXEL_MAX_BITRATE * (
            self.video_resolution[0] * self.video_resolution[1]
        ):
            self.reasons["Video bitrate"] = {
                "Bitrate": self.video_bitrate,
                "Resolution": self.video_resolution,
            }

        if self.audio_format != "AAC" or self.audio_format_profile != "LC":
            self.reasons["Audio codec"] = {
                "Format": self.audio_format,
                "Complexity": self.audio_format_profile,
            }

        if self.audio_bitrate > AUDIO_MAX_BITRATE:
            self.reasons["Audio bitrate"] = self.audio_bitrate

        if self.audio_channels > 2:
            self.reasons["Audio channels"] = self.audio_channels

        if self.container != "Matroska" or not self.local_file.endswith(".mkv"):
            self.reasons["Container"] = self.container

    def need_video_convert(self) -> bool:
        return "Video codec" in self.reasons or "Video bitrate" in self.reasons

    def need_audio_convert(self) -> bool:
        return (
            "Audio codec" in self.reasons
            or "Audio bitrate" in self.reasons
            or "Audio channels" in self.reasons
        )

    def get_converted_path(self) -> str:
        return os.path.join(
            CONVERTED_FOLDER,
            self.relative_path,
            self.local_file.rsplit(".", 1)[0] + ".mkv",
        )

    def is_not_already_converted(self) -> bool:
        return not os.path.exists(self.get_converted_path())

    def __repr__(self):
        return f"{os.path.join(self.relative_path, self.local_file)} | {self.reasons}"


def convert(item: LocalItem):
    print("--- Converting ---")
    input_path = os.path.join(DOWNLOADS_FOLDER, item.relative_path, item.local_file)
    output_path = os.path.join(
        TEMPORARY_FOLDER, item.local_file.rsplit(".", 1)[0] + ".mkv"
    )

    video_options = (
        f"-c:v libx264 -preset medium -crf {VIDEO_CRF} -pix_fmt yuv420p -profile:v high -level:v 4.1"
        if item.need_video_convert()
        else "-c:v copy"
    )

    audio_options = (
        f"-c:a aac -ar 44100 -b:a 128k -ac {min(item.audio_channels, 2)}"
        if item.need_audio_convert()
        else "-c:a copy"
    )
    bad_subtitles_options = " ".join(
        f"-map -0:s:{getattr(bad_subtitle, 'id')}"
        for bad_subtitle in item.bad_subtitles
    )
    command = (
        f'ffmpeg -y -v warning -stats -fflags +genpts -i "{input_path}" -movflags fastart -map 0:V '
        f'{video_options} -map 0:a {audio_options} -map 0:s? {bad_subtitles_options} -c:s copy "{output_path}"'
    )

    try:
        if os.path.exists(output_path):
            os.remove(output_path)
        print(command)
        result = subprocess.run(shlex.split(command), stdout=DEVNULL, stderr=PIPE)
        result.check_returncode()
        shutil.move(
            output_path,
            os.path.join(
                CONVERTED_FOLDER, item.relative_path, os.path.basename(output_path)
            ),
        )
    except CalledProcessError:
        print(f"Conversion failed !\n{result.stderr}")
        time.sleep(150)
        convert(item)


def will_be_long_running_task(category_folder: str, thing: str) -> bool:
    path = os.path.join(DOWNLOADS_FOLDER, category_folder, thing)
    return recurse_explore_complexity(path)


def recurse_explore_complexity(path: str) -> bool:
    if os.path.isdir(path):
        return any(
            recurse_explore_complexity(os.path.join(path, thing))
            for thing in os.listdir(path)
        )
    else:
        if not path.endswith((".mp4", ".mkv", ".avi")):
            return False
        item = LocalItem(path)
        return item.need_video_convert()


def process(category_folder: str, thing: str) -> None:
    if DRY_RUN:
        print(f"Dry run processing ({category_folder} | {thing})")
        time.sleep(30)
        print(f"Done ({category_folder} | {thing})")
        return
    os.makedirs(os.path.join(CONVERTED_FOLDER, category_folder), exist_ok=True)
    path = os.path.join(DOWNLOADS_FOLDER, category_folder, thing)
    recurs_process(path)
    print("--- Passing to Radarr/Sonarr ---")
    if os.path.isfile(path):
        output_folder = path.replace(DOWNLOADS_FOLDER, OPTIMIZED_FOLDER)
        os.makedirs(output_folder, exist_ok=True)
        converted_filename = thing.rsplit(".", 1)[0] + ".mkv"
        converted_file = os.path.join(
            CONVERTED_FOLDER, category_folder, converted_filename
        )
        output_file = os.path.join(output_folder, converted_filename)
        shutil.move(converted_file, output_file)
    else:
        converted_folder = os.path.join(CONVERTED_FOLDER, category_folder, thing)
        recurs_output(converted_folder)
    print("--- Cleanup ---")
    cleanup(path)
    print("Done.")


def recurs_process(path: str):
    new_path = path.replace(DOWNLOADS_FOLDER, CONVERTED_FOLDER)
    if os.path.isdir(path):
        os.makedirs(new_path, exist_ok=True)
        for thing in os.listdir(path):
            recurs_process(os.path.join(path, thing))
    else:
        if path.endswith((".mp4", ".mkv", ".avi")):
            item = LocalItem(path)
            if item.is_not_already_converted():
                print(f"Found {item}")
                convert(item)
        elif not os.path.exists(new_path):
            shutil.copy(path, new_path)


def recurs_output(path: str):
    new_path = path.replace(CONVERTED_FOLDER, OPTIMIZED_FOLDER)
    if os.path.isdir(path):
        os.makedirs(new_path, exist_ok=True)
        for thing in os.listdir(path):
            recurs_output(os.path.join(path, thing))
        os.rmdir(path)
    else:
        shutil.move(path, new_path)


def cleanup(path: str):
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


if __name__ == "__main__":
    observer = Observer()
    observer.schedule(AnyEventHandler(), DOWNLOADS_FOLDER, recursive=True)
    observer.start()

    fast_thread_key = "fast"
    long_thread_key = "long"
    threads: Dict[str, Optional[threading.Thread]] = {
        fast_thread_key: None,
        long_thread_key: None,
    }

    while True:
        time.sleep(10)

        c.acquire()
        radarr = os.listdir(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER))
        sonarr = os.listdir(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER))
        c.release()
        if last_file_event + 30 >= time.time():
            continue
        if all(thread is not None and thread.is_alive() for thread in threads.values()):
            # no process thread available, wait
            continue
        for category, category_folder in (
            (radarr, RADARR_FOLDER),
            (sonarr, SONARR_FOLDER),
        ):
            for thing in category:
                will_be_long_task = will_be_long_running_task(category_folder, thing)
                target_thread_key = (
                    long_thread_key if will_be_long_task else fast_thread_key
                )
                if (
                    threads[target_thread_key] is not None
                    and threads[target_thread_key].is_alive()
                ):
                    # not available
                    continue
                print(f"--- Starting {target_thread_key} running task ---")
                threads[target_thread_key] = threading.Thread(
                    target=process, args=[category_folder, thing]
                )
                threads[target_thread_key].start()
