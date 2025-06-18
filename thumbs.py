import sys
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
import argparse
import requests

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")

parser = argparse.ArgumentParser(prog='thumbs')
parser.add_argument('--playlist_uuid', default=PLAYLIST_UUID)
parser.add_argument('--ranked', action='store_true', help='Download thumbnails from ranked playlists')
args = vars(parser.parse_args())
PLAYLIST_UUID = args['playlist_uuid']
RANKED = args['ranked']

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

os.makedirs("thumbnails", exist_ok=True)

# works on a dream or a playlistItem from a feed
def download_thumbnail(i, dream):
    thumbnail_url = dream.get('thumbnail')
    if not thumbnail_url:
        print(f"No thumbnail for dream {dream.get('uuid')}")
        return
    local_file_path = os.path.join("thumbnails", f"{i:05d}-{dream.get('uuid')}.jpg")
    try:
        response = requests.get(thumbnail_url, stream=True)
        if response.status_code == 200:
            with open(local_file_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            print(f"Downloaded thumbnail for {dream.get('uuid')} -> {local_file_path}")
        else:
            print(f"Failed to download thumbnail for {dream.get('uuid')}: HTTP {response.status_code}")
    except Exception as e:
        print(f"Error downloading thumbnail for {dream.get('uuid')}: {e}")


def download_thumbnails_from_playlist(playlist):
    for i, item in enumerate(playlist.get('items', [])):
        if item['type'] == 'dream':
            dream = item['dreamItem']
            download_thumbnail(i, dream)

if RANKED:
    print("Fetching ranked playlists...")
    ranked_feed = edream_client.feed.get_ranked_feed(take=40, skip=0)
    for i, feed_item in enumerate(ranked_feed['feed']):
        playlist = feed_item.get('playlistItem')
        if playlist:
            uuid = playlist.get('uuid', 'unknown')
            print(f"Processing playlist {uuid}")
            download_thumbnail(i, playlist)
else:
    playlist = edream_client.get_playlist(PLAYLIST_UUID)
    download_thumbnails_from_playlist(playlist) 
