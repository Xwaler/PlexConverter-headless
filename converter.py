import os
import shutil
import time
import shlex
import threading

from watchdog.observers import Observer
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from subprocess import check_call, CalledProcessError
from ffprobe_wrapper import FFProbe

MAX_VIDEO_WIDTH = int(os.environ.get('MAX_VIDEO_WIDTH'))
MAX_VIDEO_HEIGHT = int(os.environ.get('MAX_VIDEO_HEIGHT'))
AVG_BITRATE = int(os.environ.get('AVERAGE_BITRATE'))
MAX_BITRATE = int(os.environ.get('MAX_BITRATE'))

DOWNLOADS_FOLDER = '/downloads'
CONVERTED_FOLDER = '/converted'
NORMALIZED_FOLDER = '/normalized'
OPTIMIZED_FOLDER = '/optimized'

if not os.path.exists(DOWNLOADS_FOLDER):
    os.mkdir(DOWNLOADS_FOLDER)
if not os.path.exists(CONVERTED_FOLDER):
    os.mkdir(CONVERTED_FOLDER)
if not os.path.exists(NORMALIZED_FOLDER):
    os.mkdir(NORMALIZED_FOLDER)
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
        self.relative_path = os.path.dirname(metadata.path_to_video).replace(DOWNLOADS_FOLDER, '', 1)
        if self.relative_path.startswith('/'):
            self.relative_path = self.relative_path[1:]
        self.local_file = os.path.basename(metadata.path_to_video)

        video = metadata.video[0]
        audio = metadata.audio[0]

        self.video_codec = video.codec_name
        self.video_profile = video.profile.lower()
        self.audio_codec = audio.codec_name
        if self.audio_codec == 'aac':
            self.audio_profile = audio.profile.lower()
        self.audio_channels = audio.channels
        self.video_resolution = (int(video.height), int(video.width))
        self.bitrate = int(metadata.metadata['bitrate'][:-5])
        self.framerate = str(video.framerate)
        self.container = self.local_file[-3:]

        self.reasons = {}
        self.get_reasons()
        print(f'Found {self}')

    def get_reasons(self):
        if self.video_codec != 'h264' or self.video_profile != 'high':
            self.reasons['Video codec'] = {'Codec': self.video_codec,
                                           'Profile': self.video_profile}

        if self.audio_codec != 'aac':
            self.reasons['Audio codec'] = self.audio_codec
        elif self.audio_codec == 'aac' and self.audio_profile != 'lc':
            self.reasons['Audio codec'] = {'Codec': self.audio_codec,
                                           'Profile': self.audio_profile}

        if self.audio_channels not in ('1', '2'):
            self.reasons['Audio channels'] = self.audio_channels

        if self.bitrate > MAX_BITRATE:
            self.reasons['High bitrate'] = {'Bitrate': self.bitrate,
                                            'Resolution': self.video_resolution}

        elif self.video_resolution[0] < MAX_VIDEO_HEIGHT and self.video_resolution[1] < MAX_VIDEO_WIDTH:
            self.reasons['Low resolution'] = self.video_resolution

        if self.container != 'mkv':
            self.reasons['Container'] = self.container

        if self.framerate != 'NTSC' and self.framerate != 'PAL' and (
                int(self.framerate[:-1]) > 30):
            self.reasons['Framerate'] = self.framerate

    def need_video_convert(self):
        return 'Video codec' in self.reasons or \
               'High bitrate' in self.reasons or \
               'Framerate' in self.reasons or \
               'Low resolution' in self.reasons

    def need_audio_convert(self):
        return 'Audio codec' in self.reasons or \
               'Audio channels' in self.reasons

    def __repr__(self):
        return f'{os.path.join(self.relative_path, self.local_file)} | {self.reasons}'


def convert(item):
    print(f'--- Converting ---')
    input_path = os.path.join(DOWNLOADS_FOLDER, item.relative_path, item.local_file)
    output_path = os.path.join(CONVERTED_FOLDER, item.local_file.rsplit('.', 1)[0] + '.mkv')

    nvenc = 'CUDA' in os.environ['PATH']
    video_options = '-c:v h264_nvenc -preset slow -rc:v vbr_hq -cq:v 19' if nvenc \
        else '-c:v libx264 -preset slow'
    try:
        audio_channels = min(int(item.audio_channels), 2)
    except ValueError:
        audio_channels = 2

    if item.need_video_convert():
        command = f'ffmpeg -y -v warning -stats -fflags +genpts -i "{input_path}" -movflags fastart -map 0 ' \
                  f'-pix_fmt yuv420p -vf scale={MAX_VIDEO_WIDTH}:-2:flags=lanczos ' \
                  f'{video_options} -profile:v high -level:v 4.1 -qmin 16 ' \
                  f'-c:s srt "{output_path}"'

    elif item.need_audio_convert():
        command = f'ffmpeg -y -v warning -stats -fflags +genpts -i "{input_path}" -movflags fastart -map 0 ' \
                   f'-c:v copy -c:a aac -ac {audio_channels} -c:s srt "{output_path}"'

    else:
        command = f'ffmpeg -y -v warning -stats -fflags +genpts -i "{input_path}" -movflags fastart -map 0 ' \
                  f'-c:v copy -c:a copy -c:s srt "{output_path}"'

    try:
        print(command)
        check_call(shlex.split(command))
        item.local_file = os.path.basename(output_path)

    except CalledProcessError:
        print('Conversion failed !')
        time.sleep(150)
        convert(item)


def normalize(item):
    print(f'--- Normalizing ---')
    input_path = os.path.join(CONVERTED_FOLDER, item.local_file)
    output_path = os.path.join(NORMALIZED_FOLDER, item.relative_path, item.local_file)

    command = f'ffmpeg-normalize "{input_path}" -f -v -pr -c:a aac -b:a 128k -ar 48000 -o "{output_path}"'

    try:
        print(command)
        check_call(shlex.split(command))
        os.remove(input_path)

    except CalledProcessError:
        print('Normalization failed !')
        time.sleep(150)
        normalize(item)


def output():
    print('--- Passing to Radarr/Sonarr ---')
    for thing in os.listdir(NORMALIZED_FOLDER):
        shutil.move(os.path.join(NORMALIZED_FOLDER, thing),
                    os.path.join(OPTIMIZED_FOLDER, thing))


def cleanup(download_thing):
    print('--- Cleanup ---')
    if os.path.isdir(download_thing):
        shutil.rmtree(download_thing)
    else:
        os.remove(download_thing)
    print('Ok.')


def recurs_process(path):
    if os.path.isdir(path):
        os.makedirs(path.replace(DOWNLOADS_FOLDER, NORMALIZED_FOLDER), exist_ok=True)
        for thing in os.listdir(path):
            recurs_process(os.path.join(path, thing))
    else:
        if path.endswith(('.mp4', '.mkv', '.avi')):
            item = LocalItem(FFProbe(path))
            convert(item)
            normalize(item)
        else:
            shutil.move(path, path.replace(DOWNLOADS_FOLDER, NORMALIZED_FOLDER))


if __name__ == '__main__':
    observer = Observer()
    observer.schedule(AnyEventHandler(), DOWNLOADS_FOLDER, recursive=True)
    observer.start()

    while True:
        time.sleep(10)

        c.acquire()
        content = os.listdir(DOWNLOADS_FOLDER)
        if last_file_event + 30 < time.time():
            c.release()
            for thing in content:
                if thing.rsplit('.', 1)[0] not in [x.rsplit('.', 1)[0] for x in os.listdir(OPTIMIZED_FOLDER)]:
                    path = os.path.join(DOWNLOADS_FOLDER, thing)
                    recurs_process(path)
                    output()
                    cleanup(path)
        else:
            c.release()
