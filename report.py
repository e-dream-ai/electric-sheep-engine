import sys
import os
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.dream_types import UpdateDreamRequest
import argparse
import pprint

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")

parser = argparse.ArgumentParser(prog='keyframe')
parser.add_argument('--playlist_uuid', default=PLAYLIST_UUID)

args = vars(parser.parse_args())
PLAYLIST_UUID = args['playlist_uuid']

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

playlist = edream_client.get_playlist(PLAYLIST_UUID)

succs = {}
preds = {}


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
            s = succs.get(start_id, [])
            if end_id in s:
                print('duplicate succ ' + end_id)
            succs[start_id] = s + [end_id]
            p = preds.get(end_id, [])
            if start_id in p:
                print('duplicate pred ' + start_id)
            preds[end_id] = p + [start_id]
        else:
            print(f"parse error on {d['name']}")

