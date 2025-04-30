import sys
import os
import pprint
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.dream_types import UpdateDreamRequest
from edream_sdk.types.playlist_types import PlaylistItemType

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
LOOPLESS_PLAYLIST_UUID = os.getenv("LOOPLESS_PLAYLIST_UUID")

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

playlist = edream_client.get_playlist(PLAYLIST_UUID)
loopless_playlist = edream_client.get_playlist(LOOPLESS_PLAYLIST_UUID)


if True:
    # clear the list out
    print('\ndeleting current dreams')
    for i in loopless_playlist['items']:
        if i['type'] == 'dream':
            d = i['dreamItem']
            print(d['name'])
            edream_client.delete_item_from_playlist(uuid=LOOPLESS_PLAYLIST_UUID,
                                                    playlist_item_id=i['id'])

# copy the list of dreams, except the loops

for i in playlist['items']:
    if i['type'] == 'dream':
        d = i['dreamItem']
        print(d['name'])
        # parse it according to the sheep naming system:
        # https://github.com/scottdraves/electricsheep/wiki/Protocol
        parts = d['name'].split('=')
        if len(parts) == 4:
            gen = parts[0]
            id = f"{gen}={parts[1]}"
            start_id = f"{gen}={parts[2]}"
            end_id = f"{gen}={parts[3]}"
            print(f"  id: {id}")
            print(f"  start_id: {start_id}")
            print(f"  end_id: {end_id}")
            if start_id == end_id:
                print('skipping loop')
                continue
            print('add it ' + d['uuid'])
            # should first check if it exists
            edream_client.add_item_to_playlist(playlist_uuid=LOOPLESS_PLAYLIST_UUID,
                                               type=PlaylistItemType.DREAM,
                                               item_uuid=d['uuid'])
        else:
            print(f"parse error on {d['name']}")
