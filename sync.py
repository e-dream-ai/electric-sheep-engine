import argparse
import subprocess
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
FLOCK_BEGIN_INDEX = int(os.getenv("FLOCK_BEGIN_INDEX", "0"))

parser = argparse.ArgumentParser()
parser.add_argument("--directory", "-d", default="sheep", help="local directory (default: sheep)")
parser.add_argument("--no-download", action="store_true", help="skip download phase")
parser.add_argument("--no-upload", action="store_true", help="skip upload phase")
args = parser.parse_args()
directory = args.directory

REMOTE = "sheep@v3d0.sheepserver.net:/sheep/hidef/"

# Download phase: selectively rsync videos from sheepserver
if not args.no_download:
    os.makedirs(directory, exist_ok=True)
    print("Listing remote files...")
    result = subprocess.run(
        ["rsync", "--list-only", REMOTE + "00248*.avi"],
        capture_output=True, text=True, check=True,
    )
    to_download = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        remote_size = int(parts[1].replace(",", ""))
        filename = parts[-1]
        name, _ = os.path.splitext(filename)
        name_parts = name.split('=')
        if len(name_parts) == 4:
            sheep_id = int(name_parts[1])
            if sheep_id < FLOCK_BEGIN_INDEX:
                continue
        local_path = os.path.join(directory, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
            continue
        to_download.append(filename)

    if to_download:
        print(f"Downloading {len(to_download)} files...")
        files_arg = "\n".join(to_download)
        subprocess.run(
            ["rsync", "-P", "--size-only", "--files-from=-", REMOTE, directory],
            input=files_arg, text=True, check=True,
        )
        print("Download complete.")
    else:
        print("No new files to download.")

# Upload phase
if not args.no_upload:
    edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

    playlist = edream_client.get_playlist(PLAYLIST_UUID)


    def already_uploaded(filename):
        name, _ = os.path.splitext(filename)
        for i in playlist['items']:
            if i['dreamItem']['name'] == name:
                return True
        return False


    def progress_handler(bytes_uploaded: int, total_bytes: int, percentage: float) -> None:
        print(f"Upload progress: {percentage:.2f}%")


    for filename in os.listdir(directory):
        name, _ = os.path.splitext(filename)
        parts = name.split('=')
        if len(parts) == 4:
            sheep_id = int(parts[1])
            if sheep_id < FLOCK_BEGIN_INDEX:
                continue
        f = os.path.join(directory, filename)
        if not already_uploaded(filename):
            print('upload ' + f)
            edream_client.add_file_to_playlist(
                uuid=PLAYLIST_UUID,
                file_path=f,
                progress_callback=progress_handler,
                progress_interval=1.0
            )
            print("Upload completed.")
