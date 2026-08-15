import mido
from mido import Message, MidiFile, MidiTrack

mid = MidiFile(ticks_per_beat=480)
track = MidiTrack()
mid.tracks.append(track)

track.append(Message('program_change', program=0, time=0))
track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(90), time=0))

# Simple demo melody - "Twinkle Twinkle" + chord demo
notes = [
    (60, 480), (60, 480), (67, 480), (67, 480), (69, 480), (69, 480), (67, 960),
    (65, 480), (65, 480), (64, 480), (64, 480), (62, 480), (62, 480), (60, 960),
    # C major chord
    (60, 0), (64, 0), (67, 960),
]

for note, dur in notes:
    track.append(Message('note_on', note=note, velocity=80, time=0))
    track.append(Message('note_off', note=note, velocity=0, time=dur if dur>0 else 0))

mid.save('midi_files/demo_twinkle.mid')
print("Saved demo_twinkle.mid")

# Also create a more interesting one with two tracks
mid2 = MidiFile(ticks_per_beat=480)
t1 = MidiTrack()
t2 = MidiTrack()
mid2.tracks.append(t1)
mid2.tracks.append(t2)
t1.append(mido.MetaMessage('track_name', name='Melody', time=0))
t2.append(mido.MetaMessage('track_name', name='Bass', time=0))
t1.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(120), time=0))

melody = [(72, 240), (74, 240), (76, 480), (76, 240), (74, 240), (72, 480)]
bass = [(36, 960), (38, 960), (40, 960)]

for note, dur in melody:
    t1.append(Message('note_on', note=note, velocity=90, time=0))
    t1.append(Message('note_off', note=note, time=dur))
for note, dur in bass:
    t2.append(Message('note_on', note=note+12, velocity=70, time=0))
    t2.append(Message('note_off', note=note+12, time=dur))

mid2.save('midi_files/demo_two_tracks.mid')
print("Saved demo_two_tracks.mid")
