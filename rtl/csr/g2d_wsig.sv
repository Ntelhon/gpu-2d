// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Write signature (v1.8, arch.md §9.1): CRC-32 (IEEE 802.3, reflected, polynomial 0xEDB88320, as zlib.crc32) over the
// bytes of every W beat, byte lanes 0..DB-1 in order, lanes with WSTRB = 0 counted as 0x00. The register reads the
// final XOR of the running state, so after a clear it reads 0 and after N bytes it equals zlib.crc32(those bytes).
// The state is restarted by clear_i (DBG_CTRL.SIG_CLEAR) and by reset, not by a soft reset. One beat per cycle: the
// per-beat update is a combinational XOR network over DATA_W bits.

module g2d_wsig #(
  parameter int unsigned DATA_W = 64,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clear_i,
  input  logic              beat_i,     // a W beat was accepted this cycle
  input  logic [DATA_W-1:0] data_i,
  input  logic [DB-1:0]     strb_i,
  output logic [31:0]       sig_o
);

  function automatic logic [31:0] crc_beat(logic [31:0] c, logic [DATA_W-1:0] d, logic [DB-1:0] s);
    logic [31:0] r;
    r = c;
    for (int unsigned b = 0; b < DB; b++) begin
      for (int unsigned i = 0; i < 8; i++) begin
        if (r[0] ^ (s[b] & d[8 * b + i])) r = (r >> 1) ^ 32'hEDB8_8320;
        else                              r = r >> 1;
      end
    end
    return r;
  endfunction

  logic [31:0] state_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= '1;
    end else if (clear_i) begin
      state_q <= '1;
    end else if (beat_i) begin
      state_q <= crc_beat(state_q, data_i, strb_i);
    end
  end

  assign sig_o = ~state_q;

endmodule
