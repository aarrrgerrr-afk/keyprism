"""
Roblox Piano Key Mappings
Based on classic Virtual Piano layouts.
"""

# 61-key classic - this is the exact string used by most Roblox pianos
# Maps MIDI note 36 (C2) to 96 (C7) = 61 notes
KEYS_61 = "1!2@34$5%6^78*9(0qQwWeErtTyYuiIoOpPasSdDfgGhHjJklLzZxcCvVbBnm"
# Alternative variant with capital I/U for black keys
KEYS_61_ALT = "1!2@34$5%6^78*9(0qQwWeErtTyYuUIioOpPaSsdDfgGhJjklLzZxcCvVbBnm"

# 88-key extended - covers A0 (21) to C8 (108)
# Uses extra symbols for extended range. This matches Nano / MIDIPlusPlus layouts.
KEYS_88_BASE = "1!2@34$5%6^78*9(0qQwWeErtTyYuUIioOpPaSsDdFfGgHhJjKkLlZzXxCcVvBbNnMm"
KEYS_88_EXTENDED = "[];\'\\,./`-="  # extra 13 + some variants
# Build a true 88-char map - 61 + 27 = 88
KEYS_88 = KEYS_61 + "[];,./`-=\\'qQ" + "xX" + "11111"  # will be trimmed/constructed properly below

# Proper 88-key mapping used by most bots: we construct 88 unique typable chars
# This is a popular community standard
KEYS_88_STANDARD = (
    "1!2@34$5%6^78*9(0qQwWeErRtTyYuUiIoOpP" +
    "aSsDdFfGgHhJjKkLlZzXxCcVvBbNnMm" +
    "[];\'\\,./`-=<>?:\"{}|_+"  # this brings to 88
)
# Ensure 88 length
KEYS_88_STANDARD = (KEYS_88_STANDARD + "QWERTYUIOPASDF")[:88]

# The most reliable 88 mapping (used by HuMidi / AutoMidiPlayer)
KEYS_88_FULL = list("1234567890qwertyuiopasdfghjklzxcvbnm") + \
               list("!@#$%^&*()QWERTYUIOPASDFGHJKLZXCVBNM") + \
               list("[];\\',./`-=<>?:\"{}|_+")
KEYS_88_FULL = ''.join(KEYS_88_FULL[:88])

# Final mapping dicts
MAPPING_61 = {36 + i: c for i, c in enumerate(KEYS_61)}
MAPPING_61_ALT = {36 + i: c for i, c in enumerate(KEYS_61_ALT)}

# For 88: MIDI 21-108
MAPPING_88 = {21 + i: c for i, c in enumerate(KEYS_88_FULL)}

def get_key_for_note(midi_note: int, transpose: int = 0, mode: str = "61", wrap_octave: bool = True):
    """
    Convert MIDI note number to Roblox piano key character.
    - mode: "61" or "88"
    - wrap_octave: if True, wrap notes outside range by octaves (like Nano does)
    """
    note = midi_note + transpose

    if mode == "61":
        base = 36
        top = base + 60  # inclusive
        keys = KEYS_61
        if wrap_octave:
            # Wrap by octaves until in range
            while note < base:
                note += 12
            while note > top:
                note -= 12
        if base <= note <= top:
            return keys[note - base]
        return None
    else:  # 88
        base = 21
        top = base + 87
        keys = KEYS_88_FULL
        if wrap_octave:
            while note < base:
                note += 12
            while note > top:
                note -= 12
        if base <= note <= top:
            idx = note - base
            if 0 <= idx < len(keys):
                return keys[idx]
        return None

# Shift handling for pynput
SHIFT_MAP = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '<': ',', '>': '.', '?': '/',
    '~': '`',
    'Q': 'q', 'W': 'w', 'E': 'e', 'R': 'r', 'T': 't',
    'Y': 'y', 'U': 'u', 'I': 'i', 'O': 'o', 'P': 'p',
    'A': 'a', 'S': 's', 'D': 'd', 'F': 'f', 'G': 'g',
    'H': 'h', 'J': 'j', 'K': 'k', 'L': 'l', 'Z': 'z',
    'X': 'x', 'C': 'c', 'V': 'v', 'B': 'b', 'N': 'n', 'M': 'm',
    'U': 'u', 'I': 'i',  # dup for safety
}

if __name__ == "__main__":
    print(f"61 len: {len(KEYS_61)} -> {KEYS_61}")
    print(f"88 len: {len(KEYS_88_FULL)} -> {KEYS_88_FULL}")
    print(f"61 C2 (36) = {get_key_for_note(60, 0, '61')} (Middle C should be around 't'/'T')")
    # middle C = 60
    for n in range(36, 97, 6):
        print(n, get_key_for_note(n))
