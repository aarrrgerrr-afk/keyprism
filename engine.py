"""
MIDI Engine for Roblox Piano Auto Player
Like Nano MIDI - parses MIDI and converts to timed key events
"""
import time
import threading
from dataclasses import dataclass
from typing import List, Callable, Optional
import mido
from mapping import get_key_for_note, KEYS_61, KEYS_88_FULL, SHIFT_MAP

@dataclass
class NoteEvent:
    time: float  # seconds from start
    midi_note: int
    velocity: int
    key_char: Optional[str]
    is_note_on: bool

class MidiParser:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.midi = mido.MidiFile(file_path)
        self.ticks_per_beat = self.midi.ticks_per_beat
        self.tracks = len(self.midi.tracks)
        self.duration = self.midi.length
        
    def get_track_info(self):
        """Return track names and note counts"""
        info = []
        for i, track in enumerate(self.midi.tracks):
            name = f"Track {i+1}"
            note_count = 0
            for msg in track:
                if msg.type in ('track_name',):
                    name = msg.name
                if msg.type == 'note_on' and msg.velocity > 0:
                    note_count += 1
            info.append({"index": i, "name": name, "notes": note_count})
        return info

    def parse(self, transpose: int = 0, mode: str = "61", 
              ignore_drums: bool = True, 
              enabled_tracks: Optional[List[int]] = None,
              min_velocity: int = 1,
              wrap_octave: bool = True) -> List[NoteEvent]:
        """
        Parse MIDI into timed NoteEvents
        """
        events = []
        tempo = 500000  # default 120bpm microsec per beat
        tempo_map = []  # (abs_tick, tempo)
        
        # Build merged event list with absolute ticks
        all_msgs = []
        for track_idx, track in enumerate(self.midi.tracks):
            if enabled_tracks is not None and track_idx not in enabled_tracks:
                continue
            abs_tick = 0
            for msg in track:
                abs_tick += msg.time
                # Clone with track info
                all_msgs.append((abs_tick, track_idx, msg))
        
        # Sort by tick
        all_msgs.sort(key=lambda x: x[0])
        
        abs_tick = 0
        last_tick = 0
        current_time = 0.0
        
        for tick, track_idx, msg in all_msgs:
            delta_ticks = tick - last_tick
            delta_sec = mido.tick2second(delta_ticks, self.ticks_per_beat, tempo)
            current_time += delta_sec
            last_tick = tick
            
            if msg.type == 'set_tempo':
                tempo = msg.tempo
            
            if msg.type == 'note_on' and msg.velocity >= min_velocity:
                # Ignore drums (channel 9)
                if ignore_drums and getattr(msg, 'channel', 0) == 9:
                    continue
                key_char = get_key_for_note(msg.note, transpose, mode, wrap_octave)
                if key_char:
                    events.append(NoteEvent(
                        time=current_time,
                        midi_note=msg.note,
                        velocity=msg.velocity,
                        key_char=key_char,
                        is_note_on=True
                    ))
            # We ignore note_off for Roblox (piano has no sustain hold same as press, we just tap)
            # But we keep for potential future sustain logic
        
        # Sort by time
        events.sort(key=lambda e: e.time)
        return events

    def get_stats(self, events: List[NoteEvent]):
        if not events:
            return {"notes": 0, "duration": 0, "bpm": 120}
        duration = events[-1].time - events[0].time if len(events) > 1 else 0
        return {
            "notes": len(events),
            "duration": duration,
            "ticks_per_beat": self.ticks_per_beat,
            "tracks": self.tracks,
        }


class RobloxPianoPlayer:
    def __init__(self, on_note_callback: Optional[Callable[[str], None]] = None):
        self.on_note_callback = on_note_callback
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()
        self._pause_flag.set()  # not paused initially
        self._thread: Optional[threading.Thread] = None
        self.is_playing = False
        self.current_events: List[NoteEvent] = []
        self.speed = 1.0
        
        # Lazy import keyboard controller
        self._kb_controller = None

    def _get_controller(self):
        if self._kb_controller is None:
            try:
                from pynput.keyboard import Controller, Key
                self._kb_controller = Controller()
                self._Key = Key
            except ImportError:
                # Fallback to pyautogui
                self._kb_controller = None
        return self._kb_controller

    def press_char(self, char: str):
        """Press a single character handling shift"""
        if not char:
            return
        
        ctrl = self._get_controller()
        if ctrl is None:
            # try pyautogui
            try:
                import pyautogui
                pyautogui.press(char)
                return
            except:
                return

        from pynput.keyboard import Key
        try:
            # Handle shift chars
            if char in SHIFT_MAP or char.isupper():
                base = SHIFT_MAP.get(char, char.lower())
                # press shift + base
                with ctrl.pressed(Key.shift):
                    if base == '\\':
                        ctrl.press('\\')
                        ctrl.release('\\')
                    else:
                        ctrl.press(base)
                        ctrl.release(base)
            else:
                ctrl.press(char)
                ctrl.release(char)
        except Exception as e:
            # Fallback type
            try:
                ctrl.type(char)
            except:
                pass

        if self.on_note_callback:
            try:
                self.on_note_callback(char)
            except:
                pass

    def play(self, events: List[NoteEvent], speed: float = 1.0,
             chord_threshold_ms: float = 15,
             focus_delay: float = 3.0,
             loop: bool = False,
             on_progress: Optional[Callable[[int, int, float], None]] = None):
        """Start playback in a background thread"""
        if self.is_playing:
            self.stop()
            time.sleep(0.2)
        
        self.current_events = events
        self.speed = speed
        self._stop_flag.clear()
        self._pause_flag.set()
        self.is_playing = True

        def run():
            # Focus delay - give user time to switch to Roblox
            if focus_delay > 0:
                for i in range(int(focus_delay), 0, -1):
                    if self._stop_flag.is_set():
                        self.is_playing = False
                        return
                    if on_progress:
                        on_progress(0, len(events), -i)  # negative = countdown
                    time.sleep(1)

            if not events:
                self.is_playing = False
                return

            # Group events that are very close in time into chords
            grouped = []
            current_group = [events[0]]
            for ev in events[1:]:
                if (ev.time - current_group[0].time) * 1000 <= chord_threshold_ms:
                    current_group.append(ev)
                else:
                    grouped.append(current_group)
                    current_group = [ev]
            grouped.append(current_group)

            total_groups = len(grouped)

            while True:
                start_real = time.time()
                start_event_time = events[0].time

                for idx, group in enumerate(grouped):
                    if self._stop_flag.is_set():
                        break

                    # Handle pause
                    while not self._pause_flag.is_set():
                        if self._stop_flag.is_set():
                            break
                        time.sleep(0.05)

                    # Timing
                    target_event_time = group[0].time
                    elapsed_event = (target_event_time - start_event_time) / self.speed
                    elapsed_real = time.time() - start_real
                    sleep_time = elapsed_event - elapsed_real
                    if sleep_time > 0:
                        # Sleep in small chunks to allow stop/pause responsiveness
                        slept = 0
                        while slept < sleep_time:
                            if self._stop_flag.is_set():
                                break
                            chunk = min(0.02, sleep_time - slept)
                            time.sleep(chunk)
                            slept += chunk
                            # check pause while sleeping
                            while not self._pause_flag.is_set():
                                if self._stop_flag.is_set():
                                    break
                                time.sleep(0.05)
                                # recalc after pause
                                elapsed_real = time.time() - start_real
                                sleep_time = elapsed_event - elapsed_real
                                if sleep_time < 0:
                                    break

                    if self._stop_flag.is_set():
                        break

                    # Play chord - press all keys in group quickly
                    for ev in group:
                        if ev.key_char:
                            self.press_char(ev.key_char)
                            # tiny gap for chord clarity
                            if len(group) > 1:
                                time.sleep(0.001)

                    if on_progress:
                        on_progress(idx + 1, total_groups, target_event_time)

                if self._stop_flag.is_set():
                    break
                if not loop:
                    break

            self.is_playing = False
            if on_progress:
                on_progress(total_groups, total_groups, events[-1].time if events else 0)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_flag.clear()

    def resume(self):
        self._pause_flag.set()

    def stop(self):
        self._stop_flag.set()
        self._pause_flag.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self.is_playing = False

    def toggle_pause(self):
        if self._pause_flag.is_set():
            self.pause()
        else:
            self.resume()
