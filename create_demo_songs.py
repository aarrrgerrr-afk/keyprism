"""Generate a starter MIDI library (recognizable melodies) into midi_files/."""
import mido
from mido import Message, MetaMessage, MidiFile, MidiTrack

def note(name):
    """Note name -> MIDI number (C4 = 60)."""
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
             "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
             "A#": 10, "Bb": 10, "B": 11}
    letter = name[:-1]
    octave = int(name[-1])
    return (octave + 1) * 12 + names[letter]

def make_song(path, tempo, melody, track_names=None, velocities=None):
    """melody: list of (note_name_or_None, beats). None = rest (no note)."""
    mid = MidiFile(ticks_per_beat=480)
    beat = 480
    if track_names is None:
        track_names = ["Piano"]
    if velocities is None:
        velocities = [80] * len(track_names)
    tracks = []
    for i, name in enumerate(track_names):
        t = MidiTrack()
        t.append(MetaMessage("track_name", name=name, time=0))
        if i == 0:
            t.append(MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))
        t.append(Message("program_change", program=0, time=0))
        tracks.append(t)
        mid.tracks.append(t)
    for note_name, beats in melody:
        dur = max(1, int(beats * beat))
        for t in tracks:
            if note_name is None:
                t.append(Message("note_on", note=60, velocity=0, time=dur))
                t.append(Message("note_off", note=60, velocity=0, time=0))
                continue
            t.append(Message("note_on", note=note(note_name), velocity=velocities[0], time=0))
            t.append(Message("note_off", note=note(note_name), velocity=0, time=dur))
    mid.save(path)
    print("Saved", path)

if __name__ == "__main__":
    import os
    os.makedirs("midi_files", exist_ok=True)

    # Ode to Joy (Beethoven) — 90 bpm
    ode = [("E4",1),("E4",1),("F4",1),("G4",1),("G4",1),("F4",1),("E4",1),("D4",1),
           ("C4",1),("C4",1),("D4",1),("E4",1),("E4",1.5),("D4",0.5),("D4",2),
           ("E4",1),("E4",1),("F4",1),("G4",1),("G4",1),("F4",1),("E4",1),("D4",1),
           ("C4",1),("C4",1),("D4",1),("E4",1),("D4",1.5),("C4",0.5),("C4",2)]
    make_song("midi_files/demo_ode_to_joy.mid", 90, ode)

    # Happy Birthday
    hb = [("G4",0.75),("G4",0.25),("A4",1),("G4",1),("C5",1),("B4",2),
          ("G4",0.75),("G4",0.25),("A4",1),("G4",1),("D5",1),("C5",2),
          ("G4",0.75),("G4",0.25),("G5",1),("E5",1),("C5",1),("B4",1),("A4",2),
          ("F5",0.75),("F5",0.25),("E5",1),("C5",1),("D5",1),("C5",2)]
    make_song("midi_files/demo_happy_birthday.mid", 100, hb)

    # Jingle Bells (verse)
    jb = [("E4",1),("E4",1),("E4",2),("E4",1),("E4",1),("E4",2),("E4",1),("G4",1),
          ("C4",1.5),("D4",0.5),("E4",2),("F4",1),("F4",1),("F4",1.5),("F4",0.5),
          ("F4",1),("E4",1),("E4",1),("E4",0.5),("E4",0.5),("E4",1),("D4",1),("D4",1),
          ("E4",1),("D4",1.5),("G4",0.5),("C4",2)]
    make_song("midi_files/demo_jingle_bells.mid", 112, jb)

    # Fur Elise (opening) — with a simple left-hand bass track
    fe_melody = [("E5",0.5),("D#5",0.5),("E5",0.5),("D#5",0.5),("E5",0.5),("B4",0.5),
                 ("D5",0.5),("C5",0.5),("A4",1),("C4",0.5),("E4",0.5),("A4",0.5),
                 ("B4",1),("E4",0.5),("G#4",0.5),("B4",0.5),("C5",1),("E4",0.5),
                 ("E5",0.5),("D#5",0.5),("E5",0.5),("D#5",0.5),("E5",0.5),("B4",0.5),
                 ("D5",0.5),("C5",0.5),("A4",1),("C4",0.5),("E4",0.5),("A4",0.5),
                 ("B4",1),("E4",0.5),("C5",0.5),("B4",0.5),("A4",1)]
    make_song("midi_files/demo_fur_elise.mid", 108, fe_melody,
              track_names=["Melody", "Bass"], velocities=[85, 65])

    # Scale & arpeggio practice
    scale = []
    for i in range(16):
        scale.append((f"C{3 + i % 3}", 0.5))
    for n in ("C5", "G4", "E4", "C4", "E4", "G4", "C5"):
        scale.append((n, 1))
    make_song("midi_files/demo_scale.mid", 96, scale)

    print("Done —", len(os.listdir("midi_files")), "files in midi_files/")
