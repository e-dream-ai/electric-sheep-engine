#!/usr/bin/env python3
"""
Draw a directed graph showing how dreams connect to each other.

Reads MP4 files from a directory, looks up dream titles from the local
JSON cache, parses titles into 4 parts (gen=id=start=end), and draws
a directed graph from start keyframe to end keyframe for each dream.
"""

import os
import sys
import json
import re
import subprocess
import argparse
from pathlib import Path
from collections import defaultdict


MP4_DIR = "/Users/Shared/infinidream.ai/content/mp4"
JSON_DIR = "/Users/Shared/infinidream.ai/content/json/dream"


def extract_uuid_from_filename(filename):
    """Extract UUID from filename like '01a84eb2-063c-4d0d-bc88-4fccbcae23e3.mp4'"""
    uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    match = re.search(uuid_pattern, filename, re.IGNORECASE)
    return match.group() if match else None


def load_dream_name(uuid, json_dir):
    """Load dream name from the local JSON cache."""
    json_path = Path(json_dir) / f"{uuid}.json"
    if not json_path.exists():
        return None
    try:
        with open(json_path) as f:
            data = json.load(f)
        return data['data']['dreams'][0]['name']
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"Warning: could not read name from {json_path}: {e}")
        return None


def parse_title(name):
    """Parse a dream title into 4 parts: gen, id, start, end.

    Uses '=' as separator per the Electric Sheep naming system:
    https://github.com/scottdraves/electricsheep/wiki/Protocol
    Returns None if the name doesn't split into exactly 4 parts.
    """
    parts = name.split('=')
    if len(parts) != 4:
        return None
    gen = parts[0]
    dream_id = f"{gen}={parts[1]}"
    start_id = f"{gen}={parts[2]}"
    end_id = f"{gen}={parts[3]}"
    return gen, dream_id, start_id, end_id


def build_graph(mp4_dir, json_dir):
    """Build a directed graph from the MP4 files."""
    mp4_path = Path(mp4_dir)
    if not mp4_path.exists():
        print(f"Error: MP4 directory does not exist: {mp4_dir}")
        sys.exit(1)

    edges = []
    dreams = []
    nodes = set()
    skipped = 0
    no_json = 0
    added = 0

    for mp4_file in sorted(mp4_path.glob("*.mp4")):
        uuid = extract_uuid_from_filename(mp4_file.name)
        if not uuid:
            skipped += 1
            continue

        name = load_dream_name(uuid, json_dir)
        if not name:
            no_json += 1
            continue

        parsed = parse_title(name)
        if not parsed:
            skipped += 1
            continue

        gen, dream_id, start_id, end_id = parsed
        edges.append((start_id, end_id, dream_id))
        dreams.append((uuid, name))
        nodes.add(start_id)
        nodes.add(end_id)
        added += 1

    print(f"Added {added} edges, skipped {skipped} (bad format), {no_json} (no JSON)")
    return nodes, edges, dreams


def write_dot(nodes, edges, dot_path):
    """Write a graphviz DOT file."""
    edge_counts = defaultdict(int)
    for src, dst, _ in edges:
        edge_counts[(src, dst)] += 1

    with open(dot_path, 'w') as f:
        f.write('digraph dreams {\n')
        f.write('    rankdir=LR;\n')
        f.write('    node [shape=circle, style=filled, fillcolor=lightblue, fontsize=10];\n')
        f.write('    edge [color=gray40];\n')
        f.write('\n')

        for node in sorted(nodes):
            # use just the numeric id as the label
            label = node.split('=')[1] if '=' in node else node
            f.write(f'    "{node}" [label="{label}"];\n')

        f.write('\n')
        for (src, dst), count in sorted(edge_counts.items()):
            attrs = ''
            if count > 1:
                attrs = f' [label="{count}", penwidth={1 + count}]'
            elif src == dst:
                attrs = ''
            f.write(f'    "{src}" -> "{dst}"{attrs};\n')

        f.write('}\n')

    print(f"Wrote {dot_path}")


def render_dot(dot_path, output):
    """Render DOT file to an image using graphviz."""
    fmt = Path(output).suffix.lstrip('.')
    if fmt not in ('png', 'pdf', 'svg'):
        fmt = 'png'
    try:
        subprocess.run(['dot', f'-T{fmt}', dot_path, '-o', output], check=True)
        print(f"Saved graph to {output}")
    except FileNotFoundError:
        print("Error: 'dot' command not found. Install graphviz: brew install graphviz")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Draw a directed graph of dream connections")
    parser.add_argument("--mp4-dir", default=MP4_DIR,
                        help=f"Directory of MP4 files (default: {MP4_DIR})")
    parser.add_argument("--json-dir", default=JSON_DIR,
                        help=f"Directory of dream JSON files (default: {JSON_DIR})")
    parser.add_argument("--output", default="dream_graph.png",
                        help="Output image file (default: dream_graph.png)")
    parser.add_argument("--dot", default="dream_graph.dot",
                        help="Intermediate DOT file (default: dream_graph.dot)")
    parser.add_argument("--dreams", default=None,
                        help="Output text file listing UUID and title per dream")
    args = parser.parse_args()

    nodes, edges, dreams = build_graph(args.mp4_dir, args.json_dir)

    print(f"\nGraph: {len(nodes)} nodes, {len(edges)} edges")

    write_dot(nodes, edges, args.dot)
    render_dot(args.dot, args.output)

    if args.dreams:
        with open(args.dreams, 'w') as f:
            for uuid, name in dreams:
                f.write(f"{uuid} {name}\n")
        print(f"Wrote {len(dreams)} dreams to {args.dreams}")


if __name__ == "__main__":
    main()
