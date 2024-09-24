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

print(playlist)

for filename in os.listdir(directory):
    f = os.path.join(directory, filename)
    print(f)
    edream_client.add_file_to_playlist(uuid=PLAYLIST_UUID, file_path=f)
