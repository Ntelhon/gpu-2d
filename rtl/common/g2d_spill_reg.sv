// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two-entry register slice (skid buffer): full throughput, registers both valid/data and ready, so no
// combinational path crosses it in either direction. clr_i drops the contents (soft reset).

module g2d_spill_reg #(
  parameter int unsigned WIDTH = 8,
  parameter bit          SKID  = 1'b1   // 0 (v1.7): one register, in_ready = empty or out_ready (data path only)
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clr_i,
  input  logic             in_valid_i,
  output logic             in_ready_o,
  input  logic [WIDTH-1:0] in_data_i,
  output logic             out_valid_o,
  input  logic             out_ready_i,
  output logic [WIDTH-1:0] out_data_o
);

  if (SKID) begin : g_skid
    logic             a_full_q, b_full_q;
    logic [WIDTH-1:0] a_data_q, b_data_q;

    // A is the output register, B the skid register (filled when A is stalled).
    assign out_valid_o = a_full_q;
    assign out_data_o  = a_data_q;
    assign in_ready_o  = ~b_full_q;

    logic a_take, a_fill, b_fill, b_drain;
    assign a_take  = out_valid_o & out_ready_i;            // A emptied this cycle
    assign b_drain = b_full_q & (a_take | ~a_full_q);      // B moves into A
    assign a_fill  = in_valid_i & in_ready_o & ~b_full_q & (a_take | ~a_full_q);
    assign b_fill  = in_valid_i & in_ready_o & a_full_q & ~a_take;

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        a_full_q <= 1'b0;
        b_full_q <= 1'b0;
      end else if (clr_i) begin
        a_full_q <= 1'b0;
        b_full_q <= 1'b0;
      end else begin
        a_full_q <= b_drain | a_fill | (a_full_q & ~a_take);
        b_full_q <= b_fill | (b_full_q & ~b_drain);
      end
    end

    always_ff @(posedge clk_i) begin
      if (b_drain)     a_data_q <= b_data_q;
      else if (a_fill) a_data_q <= in_data_i;
      if (b_fill)      b_data_q <= in_data_i;
    end

  end else begin : g_pipe
    logic             v_q;
    logic [WIDTH-1:0] d_q;
    assign out_valid_o = v_q;
    assign out_data_o  = d_q;
    assign in_ready_o  = ~v_q | out_ready_i;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)         v_q <= 1'b0;
      else if (clr_i)      v_q <= 1'b0;
      else if (in_ready_o) v_q <= in_valid_i;
    end
    always_ff @(posedge clk_i) begin
      if (in_ready_o && in_valid_i) d_q <= in_data_i;
    end
  end

endmodule
