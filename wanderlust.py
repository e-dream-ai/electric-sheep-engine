import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from edream_sdk.client import create_edream_client
from edream_sdk.types.playlist_types import PlaylistItemType

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
API_KEY = os.getenv("API_KEY")
PLAYLIST_UUID = os.getenv("PLAYLIST_UUID")
LOOPLESS_PLAYLIST_UUID = os.getenv("LOOPLESS_PLAYLIST_UUID")

edream_client = create_edream_client(backend_url=BACKEND_URL, api_key=API_KEY)

print("Fetching playlists...")
start_time = time.time()
playlist = edream_client.get_playlist(PLAYLIST_UUID)
loopless_playlist = edream_client.get_playlist(LOOPLESS_PLAYLIST_UUID)
print(f"Fetched playlists in {time.time() - start_time:.2f}s")

print("\nParsing source playlist...")
target_dream_uuids_ordered = []
target_dream_uuids = set()
for order_index, i in enumerate(playlist['items']):
    if i['type'] == 'dream':
        d = i['dreamItem']
        # parse it according to the sheep naming system:
        # https://github.com/scottdraves/electricsheep/wiki/Protocol
        parts = d['name'].split('=')
        if len(parts) == 4:
            gen = parts[0]
            id = f"{gen}={parts[1]}"
            start_id = f"{gen}={parts[2]}"
            end_id = f"{gen}={parts[3]}"
            if start_id == end_id:
                print(f"  Skipping loop: {d['name']}")
                continue
            target_dream_uuids.add(d['uuid'])
            target_dream_uuids_ordered.append((order_index, d['uuid']))
            print(f"  Will include: {d['name']} ({d['uuid']})")
        else:
            print(f"  Parse error on {d['name']}")

current_dream_uuids = set()
current_items_to_delete = []
for i in loopless_playlist['items']:
    if i['type'] == 'dream':
        d = i['dreamItem']
        current_dream_uuids.add(d['uuid'])
        current_items_to_delete.append((i['id'], d['uuid'], d['name']))

to_delete = [(item_id, uuid, name) for item_id, uuid, name in current_items_to_delete 
              if uuid not in target_dream_uuids]
to_add = [(order_index, uuid) for order_index, uuid in target_dream_uuids_ordered 
          if uuid not in current_dream_uuids]

print(f"\nSummary:")
print(f"  Current items in loopless playlist: {len(current_dream_uuids)}")
print(f"  Target items (non-loops): {len(target_dream_uuids)}")
print(f"  Items to delete: {len(to_delete)}")
print(f"  Items to add: {len(to_add)}")

if len(to_delete) == 0 and len(to_add) == 0:
    print("\nPlaylists are already in sync! No changes needed.")
    sys.exit(0)

def delete_item(item_id, uuid, name):
    try:
        edream_client.delete_item_from_playlist(
            uuid=LOOPLESS_PLAYLIST_UUID,
            playlist_item_id=item_id
        )
        return (True, uuid, name, None)
    except Exception as e:
        return (False, uuid, name, str(e))

def add_item(order_index, dream_uuid):
    try:
        playlist_item = edream_client.add_item_to_playlist(
            playlist_uuid=LOOPLESS_PLAYLIST_UUID,
            type=PlaylistItemType.DREAM,
            item_uuid=dream_uuid
        )
        return (True, order_index, dream_uuid, playlist_item['id'], None)
    except Exception as e:
        return (False, order_index, dream_uuid, None, str(e))

if to_delete:
    print(f"\nDeleting {len(to_delete)} items in parallel...")
    delete_start = time.time()
    with ThreadPoolExecutor(max_workers=10) as executor:
        delete_futures = {
            executor.submit(delete_item, item_id, uuid, name): (item_id, uuid, name)
            for item_id, uuid, name in to_delete
        }
        
        delete_success = 0
        delete_failed = 0
        for future in as_completed(delete_futures):
            success, uuid, name, error = future.result()
            if success:
                delete_success += 1
                print(f"  Deleted: {name} ({uuid})")
            else:
                delete_failed += 1
                print(f"  Failed to delete {name} ({uuid}): {error}")
    
    print(f"Deleted {delete_success} items, {delete_failed} failed in {time.time() - delete_start:.2f}s")

if to_add:
    print(f"\nAdding {len(to_add)} items in parallel...")
    add_start = time.time()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        add_futures = {
            executor.submit(add_item, order_index, uuid): (order_index, uuid)
            for order_index, uuid in to_add
        }
        
        add_success = 0
        add_failed = 0
        for future in as_completed(add_futures):
            result = future.result()
            success = result[0]
            order_index = result[1]
            uuid = result[2]
            item_id = result[3]
            error = result[4]
            
            if success:
                add_success += 1
                print(f"  Added: {uuid}")
            else:
                add_failed += 1
                print(f"  Failed to add {uuid}: {error}")
    
    print(f"Added {add_success} items, {add_failed} failed in {time.time() - add_start:.2f}s")
    
    if len(to_add) > 0 or len(to_delete) > 0:
        print(f"\nReordering items to match source playlist order...")
        reorder_start = time.time()
        
        loopless_playlist_updated = edream_client.get_playlist(LOOPLESS_PLAYLIST_UUID)
        
        uuid_to_item_id = {}
        for item in loopless_playlist_updated['items']:
            if item['type'] == 'dream' and item['dreamItem']:
                uuid_to_item_id[item['dreamItem']['uuid']] = item['id']
        
        target_uuids_ordered = [uuid for _, uuid in target_dream_uuids_ordered]
        
        reorder_list = []
        for new_order, uuid in enumerate(target_uuids_ordered):
            if uuid in uuid_to_item_id:
                item_id = uuid_to_item_id[uuid]
                reorder_list.append({"id": item_id, "order": new_order})
        
        if reorder_list:
            edream_client.reorder_playlist(
                uuid=LOOPLESS_PLAYLIST_UUID,
                order=reorder_list
            )
            print(f"Reordered {len(reorder_list)} items in {time.time() - reorder_start:.2f}s")

total_time = time.time() - start_time
print(f"\nComplete! Total time: {total_time:.2f}s")