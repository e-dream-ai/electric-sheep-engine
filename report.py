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

def compute_graph(playlist):
    succs = {}
    preds = {}
    for i in playlist['items']:
        if i['type'] == 'dream': # why not PlaylistItemType.DREAM instead
            d = i['dreamItem']
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
    return succs, preds

succs, preds = compute_graph(playlist)

print()
print("io balance ranking")

def count(d, i):
    return len(d.get(i, []))

def compare_keyframes(item):
    return count(succs, item) - count(preds, item)

io_balance = list(succs.keys())
io_balance.sort(key=compare_keyframes)
for i in io_balance:
    print(f"{i} {count(succs, i)} {count(preds, i)}")


# this only works on the main playlist, we hae to compute succs with a different source
main_playlist = edream_client.get_playlist(os.getenv("PLAYLIST_UUID"))
main_succs, _ = compute_graph(main_playlist)

singularities = []
for i in main_succs.keys():
    if i in main_succs[i]:
        continue
    singularities.append(i)

print()
print("singularity ranking")
singularities.sort(key=compare_keyframes)
for i in singularities:
    print(f"{i} {count(succs, i)} {count(preds, i)}")
