import sys
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

playlist = edream_client.get_playlist(PLAYLIST_UUID)

# look up a keyframe by its ID in the sheep namespace, which is a 5
# digit string (not a UUID). if it doesn't exist, then create it.
# return its UUID.
#
# i would love to have a query API to find by name. it should exist
# because the feed UI does this, but since that's not obvious, i'm
# going to just search the keyframes in this playlist sequentially
# which is horrible but should work.
def assure_keyframe(id):
    for k in playlist.playlistKeyframes:
        print(k)
        if k.keyframe.name == id:
            return k.keyframe.uuid
    print('none found')
    k = edream_client.add_keyframe_to_playlist(PLAYLIST_UUID, id)
    print(k)
    return k.uuid

for i in playlist.items:
    print(i.dreamItem.name)
    # parse it according to the sheep naming system:
    # https://github.com/scottdraves/electricsheep/wiki/Protocol
    parts = i.dreamItem.name.split('=')
    if len(parts) == 4:
        gen = parts[0]
        id = f"{gen}={parts[1]}"
        start_id = f"{gen}={parts[2]}"
        end_id = f"{gen}={parts[3]}"
        print(f"  id: {id}")
        print(f"  start_id: {start_id}")
        print(f"  end_id: {end_id}")
        start_keyframe = assure_keyframe(start_id)
        end_keyframe = assure_keyframe(end_id)
    else:
        print(f"parse error on {i.dreamItem.name}")
