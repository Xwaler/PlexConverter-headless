import os
import shlex
import shutil
import threading
import time
from subprocess import check_call, CalledProcessError

from pymediainfo import MediaInfo
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

VIDEO_CRF = int(os.environ.get('VIDEO_CRF'))
VIDEO_MAX_BITRATE = int(os.environ.get('VIDEO_MAX_BITRATE'))
FOR_WIDTH = int(os.environ.get('FOR_WIDTH'))
FOR_HEIGHT = int(os.environ.get('FOR_HEIGHT'))
PIXEL_MAX_BITRATE = VIDEO_MAX_BITRATE / (FOR_WIDTH * FOR_HEIGHT)
AUDIO_MAX_BITRATE = int(os.environ.get('AUDIO_MAX_BITRATE'))

RADARR_FOLDER = os.environ.get('RADARR_FOLDER')
SONARR_FOLDER = os.environ.get('SONARR_FOLDER')

DOWNLOADS_FOLDER = '/downloads/complete'
CONVERTED_FOLDER = '/downloads/converted'
OPTIMIZED_FOLDER = '/downloads/optimized'

if not os.path.exists(DOWNLOADS_FOLDER):
    os.mkdir(DOWNLOADS_FOLDER)
if not os.path.exists(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER)):
    os.mkdir(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER))
if not os.path.exists(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER)):
    os.mkdir(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER))
if not os.path.exists(CONVERTED_FOLDER):
    os.mkdir(CONVERTED_FOLDER)
else:
    for thing in os.listdir(CONVERTED_FOLDER):
        path = os.path.join(CONVERTED_FOLDER, thing)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
if not os.path.exists(OPTIMIZED_FOLDER):
    os.mkdir(OPTIMIZED_FOLDER)

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
        if not isinstance(last_event, FileSystemEvent) or event.src_path != last_event.src_path:
            print(event)
        last_event = event
        c.release()


class LocalItem:
    def __init__(self, metadata):
        general = metadata.general_tracks[0]
        video = metadata.video_tracks[0]
        audio = metadata.audio_tracks[0]

        self.local_file = os.path.basename(general.complete_name)
        self.relative_path = os.path.dirname(general.complete_name).replace(DOWNLOADS_FOLDER, '', 1)
        if self.relative_path.startswith('/'):
            self.relative_path = self.relative_path[1:]
        self.container = general.format

        self.video_format = video.format
        self.video_profile = video.format_profile
        self.video_resolution = (video.height, video.width)
        try:
            self.video_bitrate = (video.bit_rate or video.overall_bit_rate or video.nominal_bit_rate or (
                    video.stream_size * 8000 / video.duration
            )) / 1000
        except TypeError:
            self.video_bitrate = 1e99

        self.audio_format = audio.format
        print(dir(audio))
        self.audio_format_profile = audio.format_profile or audio.format_additionalfeatures
        try:
            self.audio_bitrate = (audio.bit_rate or audio.overall_bit_rate or audio.nominal_bit_rate or (
                    audio.stream_size * 8000 / audio.duration
            )) / 1000
        except TypeError:
            self.audio_bitrate = 1e99
        try:
            self.audio_channels = int(audio.channel_s)
        except ValueError:
            self.audio_channels = 6

        self.reasons = {}
        self.get_reasons()
        print(f'Found {self}')

    def get_reasons(self):
        if self.video_format != 'AVC' or not self.video_profile.startswith('High'):
            self.reasons['Video codec'] = {'Format': self.video_format,
                                           'Profile': self.video_profile}

        if self.video_bitrate > PIXEL_MAX_BITRATE * (self.video_resolution[0] * self.video_resolution[1]):
            self.reasons['Video bitrate'] = {'Bitrate': self.video_bitrate,
                                             'Resolution': self.video_resolution}

        if self.audio_format != 'AAC' or self.audio_format_profile != 'LC':
            self.reasons['Audio codec'] = {'Format': self.audio_format,
                                           'Complexity': self.audio_format_profile}

        if self.audio_bitrate > AUDIO_MAX_BITRATE:
            self.reasons['Audio bitrate'] = self.audio_bitrate

        if self.audio_channels > 2:
            self.reasons['Audio channels'] = self.audio_channels

        if self.container != 'Matroska' or not self.local_file.endswith('.mkv'):
            self.reasons['Container'] = self.container

    def need_video_convert(self):
        return 'Video codec' in self.reasons or \
               'Video bitrate' in self.reasons

    def need_audio_convert(self):
        return 'Audio codec' in self.reasons or \
               'Audio bitrate' in self.reasons or \
               'Audio channels' in self.reasons

    def __repr__(self):
        return f'{os.path.join(self.relative_path, self.local_file)} | {self.reasons}'


def convert(item):
    print(f'--- Converting ---')
    input_path = os.path.join(DOWNLOADS_FOLDER, item.relative_path, item.local_file)
    output_path = os.path.join(CONVERTED_FOLDER, item.relative_path, item.local_file.rsplit('.', 1)[0] + '.mkv')

    relative_max_bitrate = round(PIXEL_MAX_BITRATE * (item.video_resolution[0] * item.video_resolution[1]))
    video_options = f"-c:v libx264 -crf {VIDEO_CRF} -pix_fmt yuv420p -profile:v high -level:v 4.1 " \
                    f"-x264-params cabac=1:ref=4:analyse=0x133:me=umh:subme=9:chroma-me=1:deadzone-inter=21:" \
                    f"deadzone-intra=11:b-adapt=2:rc-lookahead=60:qpmax=69:" \
                    f"vbv-maxrate={relative_max_bitrate}:vbv-bufsize={relative_max_bitrate * 2}:" \
                    f"bframes=5:b-adapt=2:direct=auto:crf-max=51:weightp=2:merange=24:chroma-qp-offset=-3:" \
                    f"sync-lookahead=2:psy-rd=1.00,0.15:trellis=2:min-keyint=23:partitions=all" if \
        item.need_video_convert() else '-c:v copy'

    audio_options = f"-c:a aac -ar 44100 -b:a 128k " \
                    f"-ac {min(item.audio_channels, 2)}" if \
        item.need_audio_convert() else '-c:a copy'

    command = f'ffmpeg -y -v warning -stats -fflags +genpts -i "{input_path}" -movflags fastart -map 0:V ' \
              f'{video_options} -map 0:a {audio_options} -map 0:s? -c:s copy "{output_path}"'

    try:
        print(command)
        check_call(shlex.split(command))
        item.local_file = os.path.basename(output_path)

    except CalledProcessError:
        print('Conversion failed !')
        time.sleep(150)
        convert(item)


def recurs_process(path):
    new_path = path.replace(DOWNLOADS_FOLDER, CONVERTED_FOLDER)
    if os.path.isdir(path):
        os.makedirs(new_path, exist_ok=True)
        for thing in os.listdir(path):
            recurs_process(os.path.join(path, thing))
    else:
        if path.endswith(('.mp4', '.mkv', '.avi')):
            item = LocalItem(MediaInfo.parse(path))
            convert(item)
        else:
            shutil.copy(path, new_path)


def recurs_output(path):
    new_path = path.replace(CONVERTED_FOLDER, OPTIMIZED_FOLDER)
    if os.path.isdir(path):
        os.makedirs(new_path, exist_ok=True)
        for thing in os.listdir(path):
            recurs_output(os.path.join(path, thing))
        os.rmdir(path)
    else:
        shutil.move(path, new_path)


def cleanup(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


if __name__ == '__main__':
    observer = Observer()
    observer.schedule(AnyEventHandler(), DOWNLOADS_FOLDER, recursive=True)
    observer.start()

    while True:
        time.sleep(10)

        c.acquire()
        radarr = os.listdir(os.path.join(DOWNLOADS_FOLDER, RADARR_FOLDER))
        sonarr = os.listdir(os.path.join(DOWNLOADS_FOLDER, SONARR_FOLDER))
        if last_file_event + 30 < time.time():
            c.release()
            for category, category_folder in ((radarr, RADARR_FOLDER), (sonarr, SONARR_FOLDER)):
                for thing in category:
                    os.makedirs(os.path.join(CONVERTED_FOLDER, category_folder), exist_ok=True)
                    path = os.path.join(DOWNLOADS_FOLDER, category_folder, thing)
                    recurs_process(path)
                    print('--- Passing to Radarr/Sonarr ---')
                    recurs_output(os.path.join(CONVERTED_FOLDER, category_folder, thing))
                    print('--- Cleanup ---')
                    cleanup(path)
                    print('Ok.')
        else:
            c.release()
