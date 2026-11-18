#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ELF64 (RISC-V, little endian) -> memh for tb_g2d_chs.sv: one line "<addr> <64-bit word>" (hex) per
8-byte word of every PT_LOAD segment (bytes outside the file image are zero in the DRAM model).
Prints the entry point (hex, no prefix) on stdout."""
import struct
import sys


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: elf2memh.py <in.elf> <out.memh>")
    data = open(sys.argv[1], "rb").read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        sys.exit("elf2memh: not a little-endian ELF64 file")
    e_entry, e_phoff = struct.unpack_from("<QQ", data, 0x18)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)
    words = {}
    for i in range(e_phnum):
        p_type, _flags, p_offset, _vaddr, p_paddr, p_filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", data, e_phoff + i * e_phentsize)
        if p_type != 1 or p_filesz == 0:          # PT_LOAD with file content
            continue
        seg = data[p_offset:p_offset + p_filesz]
        for k, byte in enumerate(seg):
            addr = p_paddr + k
            w = words.setdefault(addr & ~7, bytearray(8))
            w[addr & 7] = byte
    with open(sys.argv[2], "w") as f:
        for a in sorted(words):
            f.write("%x %016x\n" % (a, int.from_bytes(words[a], "little")))
    print("%x" % e_entry)


if __name__ == "__main__":
    main()
