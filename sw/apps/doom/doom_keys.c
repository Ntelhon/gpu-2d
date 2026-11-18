/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * DOOM on G2D: PS/2 scancode set 2 -> DOOM key events (step D3). The keyboard bytes come from chs_ps2 (board) or a
 * test. Prefixes: E0 (extended key), F0 (break = release), E1 (Pause: E1 14 77 E1 F0 14 F0 77, press only).
 * Key choice as doomgeneric's platform layers: Ctrl fire, Space use, Shift run, Alt strafe; in addition ',' / '.'
 * strafe left / right (vanilla DOOM's keys), keypad 8 / 2 / 4 / 6 as arrows, letters and digits as lowercase ASCII.
 */
#include "doom_keys.h"

#include "doomkeys.h"

/* set-2 make code (no prefix) -> DOOM key; 0 = ignored */
static const unsigned char k_base[0x84] = {
    [0x1C] = 'a', [0x32] = 'b', [0x21] = 'c', [0x23] = 'd', [0x24] = 'e', [0x2B] = 'f', [0x34] = 'g', [0x33] = 'h',
    [0x43] = 'i', [0x3B] = 'j', [0x42] = 'k', [0x4B] = 'l', [0x3A] = 'm', [0x31] = 'n', [0x44] = 'o', [0x4D] = 'p',
    [0x15] = 'q', [0x2D] = 'r', [0x1B] = 's', [0x2C] = 't', [0x3C] = 'u', [0x2A] = 'v', [0x1D] = 'w', [0x22] = 'x',
    [0x35] = 'y', [0x1A] = 'z',
    [0x45] = '0', [0x16] = '1', [0x1E] = '2', [0x26] = '3', [0x25] = '4', [0x2E] = '5', [0x36] = '6', [0x3D] = '7',
    [0x3E] = '8', [0x46] = '9',
    [0x0E] = '`', [0x4E] = KEY_MINUS, [0x55] = KEY_EQUALS, [0x5D] = '\\', [0x54] = '[', [0x5B] = ']', [0x4C] = ';',
    [0x52] = '\'', [0x4A] = '/', [0x41] = KEY_STRAFE_L, [0x49] = KEY_STRAFE_R,
    [0x66] = KEY_BACKSPACE, [0x29] = KEY_USE, [0x0D] = KEY_TAB, [0x5A] = KEY_ENTER, [0x76] = KEY_ESCAPE,
    [0x12] = KEY_RSHIFT, [0x59] = KEY_RSHIFT, [0x14] = KEY_FIRE, [0x11] = KEY_LALT, [0x58] = KEY_CAPSLOCK,
    [0x77] = KEY_NUMLOCK, [0x7E] = KEY_SCRLCK,
    [0x05] = KEY_F1, [0x06] = KEY_F2, [0x04] = KEY_F3, [0x0C] = KEY_F4, [0x03] = KEY_F5, [0x0B] = KEY_F6,
    [0x83] = KEY_F7, [0x0A] = KEY_F8, [0x01] = KEY_F9, [0x09] = KEY_F10, [0x78] = KEY_F11, [0x07] = KEY_F12,
    [0x75] = KEY_UPARROW, [0x72] = KEY_DOWNARROW, [0x6B] = KEY_LEFTARROW, [0x74] = KEY_RIGHTARROW,   /* keypad */
    [0x79] = '+', [0x7B] = KEY_MINUS,
};

/* E0-prefixed make codes */
static unsigned char k_ext(unsigned char c)
{
    switch (c) {
    case 0x75: return KEY_UPARROW;
    case 0x72: return KEY_DOWNARROW;
    case 0x6B: return KEY_LEFTARROW;
    case 0x74: return KEY_RIGHTARROW;
    case 0x14: return KEY_FIRE;       /* right Ctrl */
    case 0x11: return KEY_RALT;       /* right Alt (AltGr) */
    case 0x5A: return KEY_ENTER;      /* keypad Enter */
    case 0x4A: return '/';            /* keypad / */
    case 0x70: return KEY_INS;
    case 0x71: return KEY_DEL;
    case 0x6C: return KEY_HOME;
    case 0x69: return KEY_END;
    case 0x7D: return KEY_PGUP;
    case 0x7A: return KEY_PGDN;
    case 0x7C: return KEY_PRTSCR;     /* second half of E0 12 E0 7C */
    default: return 0;                /* E0 12 (fake shift), GUI keys, ... */
    }
}

void doom_keys_reset(doom_keys_t *s)
{
    s->ext = 0;
    s->brk = 0;
    s->pause = 0;
}

int doom_keys_feed(doom_keys_t *s, unsigned char b, int *pressed, unsigned char *key)
{
    unsigned char k;
    if (s->pause) {                   /* the rest of E1 14 77 E1 F0 14 F0 77 */
        if (--s->pause == 0) {
            *pressed = 1;
            *key = KEY_PAUSE;
            return 1;
        }
        return 0;
    }
    switch (b) {
    case 0xE0: s->ext = 1; return 0;
    case 0xF0: s->brk = 1; return 0;
    case 0xE1: s->pause = 7; s->ext = 0; s->brk = 0; return 0;
    case 0xAA: case 0xFA: case 0xEE: case 0xFC: case 0xFD: case 0xFE: case 0xFF: case 0x00:   /* self-test / ack / errors */
        if (!s->ext && !s->brk)
            return 0;
        break;
    default: break;
    }
    k = s->ext ? k_ext(b) : (b < sizeof k_base ? k_base[b] : 0);
    *pressed = !s->brk;
    s->ext = 0;
    s->brk = 0;
    if (!k)
        return 0;
    *key = k;
    return 1;
}
