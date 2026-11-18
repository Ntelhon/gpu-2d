/* SPDX-License-Identifier: GPL-2.0-or-later
 * DOOM on G2D: PS/2 scancode set 2 -> DOOM key events (doom_keys.c). */
#ifndef DOOM_KEYS_H
#define DOOM_KEYS_H

typedef struct {
    unsigned char ext, brk, pause;
} doom_keys_t;

void doom_keys_reset(doom_keys_t *s);
/* Feed one byte from the keyboard; returns 1 with an event (pressed 1 = down, 0 = up; key = DOOM key code). */
int doom_keys_feed(doom_keys_t *s, unsigned char b, int *pressed, unsigned char *key);

#endif
