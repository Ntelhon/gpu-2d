/* SPDX-License-Identifier: GPL-2.0-or-later
 * DOOM side project, step D3: PS/2 set-2 decoder (sw/apps/doom/doom_keys.c) against hand-written sequences. */
#include <stdio.h>
#include <string.h>

#include "doom_keys.h"
#include "doomkeys.h"

static int g_fail, g_checks;

/* bytes -> expected events, each "pressed key" pair */
static void check(const char *name, const unsigned char *in, int n, const int *want, int nwant)
{
    doom_keys_t s;
    int got[64], ng = 0, i, p;
    unsigned char k;
    doom_keys_reset(&s);
    for (i = 0; i < n; i++)
        if (doom_keys_feed(&s, in[i], &p, &k) && ng < 62) {
            got[ng++] = p;
            got[ng++] = k;
        }
    g_checks++;
    if (ng != 2 * nwant || memcmp(got, want, sizeof(int) * (size_t)ng) != 0) {
        g_fail++;
        printf("FAIL %s: got", name);
        for (i = 0; i < ng; i += 2)
            printf(" %s%02x", got[i] ? "+" : "-", got[i + 1]);
        printf("\n");
    }
}

#define CHECK(name, in, ...)                                                                             \
    do {                                                                                                  \
        static const unsigned char in_[] = {in};                                                          \
        static const int want_[] = {__VA_ARGS__};                                                         \
        check(name, in_, (int)sizeof in_, want_, (int)(sizeof want_ / sizeof want_[0]) / 2);              \
    } while (0)
#define B(...) __VA_ARGS__

int main(void)
{
    CHECK("letter make / break", B(0x1C, 0xF0, 0x1C), 1, 'a', 0, 'a');
    CHECK("digits", B(0x16, 0x45, 0xF0, 0x45), 1, '1', 1, '0', 0, '0');
    CHECK("arrows (E0)", B(0xE0, 0x75, 0xE0, 0xF0, 0x75, 0xE0, 0x6B), 1, KEY_UPARROW, 0, KEY_UPARROW, 1, KEY_LEFTARROW);
    CHECK("keypad arrows", B(0x75, 0x72, 0x6B, 0x74), 1, KEY_UPARROW, 1, KEY_DOWNARROW, 1, KEY_LEFTARROW, 1,
          KEY_RIGHTARROW);
    CHECK("fire / use / run", B(0x14, 0x29, 0x12, 0xF0, 0x14), 1, KEY_FIRE, 1, KEY_USE, 1, KEY_RSHIFT, 0, KEY_FIRE);
    CHECK("right ctrl / alt", B(0xE0, 0x14, 0xE0, 0x11, 0xE0, 0xF0, 0x11), 1, KEY_FIRE, 1, KEY_RALT, 0, KEY_RALT);
    CHECK("strafe , .", B(0x41, 0x49), 1, KEY_STRAFE_L, 1, KEY_STRAFE_R);
    CHECK("enter esc tab", B(0x5A, 0x76, 0x0D, 0xE0, 0x5A), 1, KEY_ENTER, 1, KEY_ESCAPE, 1, KEY_TAB, 1, KEY_ENTER);
    CHECK("F keys", B(0x05, 0x83, 0x07), 1, KEY_F1, 1, KEY_F7, 1, KEY_F12);
    CHECK("print screen fake shift ignored", B(0xE0, 0x12, 0xE0, 0x7C), 1, KEY_PRTSCR);
    CHECK("pause", B(0xE1, 0x14, 0x77, 0xE1, 0xF0, 0x14, 0xF0, 0x77, 0x1C), 1, KEY_PAUSE, 1, 'a');
    CHECK("self-test / ack bytes ignored", B(0xAA, 0xFA, 0x1C), 1, 'a');
    CHECK("unknown code then a key", B(0x08, 0xE0, 0x1F, 0x1D), 1, 'w');
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail != 0;
}
