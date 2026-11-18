// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Request throttle for one AXI address channel (v1.9, arch.md §4.2.1, CSR MEM_QOS).
// Sits between an engine's AR (or AW) handshake and the port:
//   port valid = engine valid & allow,  engine ready = port ready & allow.
// allow = open | held. open: fewer than max_out_i bursts outstanding (0 = no limit here) and the gap counter
// has run out. held: a request is waiting on the bus (valid without ready), so allow stays 1 until it is
// taken (AXI: a valid request is never withdrawn, also when the CSR changes meanwhile).
// Outstanding = request handshakes minus done_i pulses (last R beat / B response of a burst).
// After each handshake the next one comes at least gap_i + 1 cycles later.
// With max_out_i = 0 and gap_i = 0, allow is 1 in every cycle (cycle-identical to no throttle).

module g2d_axi_qos #(
  parameter int unsigned CNT_W = 9   // outstanding counter width (must hold the engine's build-time limit)
) (
  input  logic       clk_i,
  input  logic       rst_ni,
  input  logic       clr_i,          // soft reset (bus idle)
  input  logic [4:0] max_out_i,
  input  logic [7:0] gap_i,
  input  logic       valid_i,        // engine request valid
  input  logic       ready_i,        // port ready
  input  logic       done_i,         // one burst completed
  output logic       allow_o
);

  logic [CNT_W-1:0] out_q;
  logic [7:0]       gap_q;
  logic             held_q;
  logic             open, hs;

  assign open    = ((max_out_i == '0) || (out_q < CNT_W'(max_out_i))) && (gap_q == '0);
  assign allow_o = open | held_q;
  assign hs      = valid_i & ready_i & allow_o;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      out_q  <= '0;
      gap_q  <= '0;
      held_q <= 1'b0;
    end else if (clr_i) begin
      out_q  <= '0;
      gap_q  <= '0;
      held_q <= 1'b0;
    end else begin
      out_q  <= out_q + CNT_W'(hs) - CNT_W'(done_i);
      if (hs)               gap_q <= gap_i;
      else if (gap_q != '0) gap_q <= gap_q - 8'd1;
      held_q <= valid_i & allow_o & ~ready_i;
    end
  end

endmodule
