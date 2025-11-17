import sys
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")

directory = sys.argv[1]

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

playlist = edream_client.get_playlist(PLAYLIST_UUID)


def already_uploaded(filename):
    name, _ = os.path.splitext(filename)
    for i in playlist['items']:
        if i['dreamItem']['name'] == name:
            return True
    return False


def progress_handler(percentage: float) -> None:
    print(f"Upload progress: {percentage:.2f}%")


for filename in os.listdir(directory):
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
