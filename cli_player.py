#!/usr/bin/env python3
"""
CLI version - no GUI needed, pure terminal
Usage: python cli_player.py song.mid --speed 1.0 --transpose 0 --mode 61 --delay 3
"""
import argparse
import time
from pathlib import Path
from engine import MidiParser, RobloxPianoPlayer

def main():
    parser = argparse.ArgumentParser(description="Roblox Piano Auto Player - CLI")
    parser.add_argument("midi_file", help="Path to .mid file")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed 0.25-2.0")
    parser.add_argument("--transpose", type=int, default=0, help="Transpose -12 to 12")
    parser.add_argument("--mode", choices=["61","88"], default="61", help="61 or 88 keys")
    parser.add_argument("--delay", type=int, default=3, help="Focus delay seconds")
    parser.add_argument("--list-tracks", action="store_true", help="Just list tracks and exit")
    parser.add_argument("--tracks", type=str, default="", help="Comma separated track indices to enable, e.g. 0,1")
    args = parser.parse_args()

    if not Path(args.midi_file).exists():
        print(f"File not found: {args.midi_file}")
        return

    print(f"Loading {args.midi_file}...")
    mp = MidiParser(args.midi_file)
    info = mp.get_track_info()
    print(f"Tracks: {len(info)} | Ticks: {mp.ticks_per_beat} | Length: {mp.duration:.1f}s")
    for t in info:
        print(f"  [{t['index']}] {t['name']} - {t['notes']} notes")

    if args.list_tracks:
        return

    enabled = None
    if args.tracks:
        enabled = [int(x.strip()) for x in args.tracks.split(",") if x.strip().isdigit()]

    print(f"\nParsing with mode={args.mode}, transpose={args.transpose}, speed={args.speed}...")
    events = mp.parse(transpose=args.transpose, mode=args.mode, enabled_tracks=enabled)
    if not events:
        print("No playable notes found after mapping!")
        return

    stats = mp.get_stats(events)
    print(f"Playable notes: {stats['notes']} | Duration: {stats['duration']:.1f}s at 1.0x -> {stats['duration']/args.speed:.1f}s at {args.speed}x")
    print(f"First notes: {[e.key_char for e in events[:10]]}")

    print(f"\n>>> You have {args.delay}s to focus Roblox piano window! <<<")
    print("Press Ctrl+C to abort")

    player = RobloxPianoPlayer()

    def progress(cur, total, tm):
        if tm < 0:
            print(f"\rStarting in {-int(tm)}... FOCUS ROBLOX NOW! [{cur}/{total}]", end="", flush=True)
        else:
            pct = cur/total*100 if total else 0
            print(f"\rPlaying {cur}/{total} ({pct:.1f}%) t={tm:.1f}s key={events[cur-1].key_char if cur>0 and cur<=len(events) else ''}   ", end="", flush=True)
            if cur>=total:
                print("\nFinished!")

    try:
        player.play(events, speed=args.speed, focus_delay=args.delay, on_progress=progress)
        # wait
        while player.is_playing:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping...")
        player.stop()

if __name__ == "__main__":
    main()
