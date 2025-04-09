import sys
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.dream_types import UpdateDreamRequest

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)


#edream_client.add_keyframe_to_playlist(
#    playlist_uuid="14bdc320-2c06-41e4-8a90-639385c491d9",
#    keyframe_uuid="9d62ceec-e120-4c15-9e27-26a829f3c30b",
#)
#exit(1)

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
    for k in playlist['playlistKeyframes']:
        if k['keyframe']['name'] == id:
            return k['keyframe']['uuid']
    k = edream_client.add_keyframe_to_playlist(playlist, id)
    return k['uuid']

if True:
    # first clear all the keyframes on this playlist
    print('\ndeleting')
    for k in playlist['playlistKeyframes']:
        print(k['keyframe']['name'])
        edream_client.delete_keyframe(k['keyframe']['uuid'])
    # fetch it again
    playlist = edream_client.get_playlist(PLAYLIST_UUID)

# then add new ones based on the names of the dreams/sheep
print('adding keyframes')
for i in playlist['items']:
    if i['type'] == 'dream': # why not PlaylistItemType.DREAM instead
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
            start_keyframe = assure_keyframe(start_id)
            end_keyframe = assure_keyframe(end_id)
            if d['startKeyframe'] and d['startKeyframe']['uuid'] == start_keyframe and d['endKeyframe'] and d['endKeyframe']['uuid'] == end_keyframe:
                print('skipping')
                continue
            edream_client.update_dream(
                d['uuid'],
                UpdateDreamRequest(startKeyframe=start_keyframe,
                                   endKeyframe=end_keyframe))
        else:
            print(f"parse error on {d['name']}")
