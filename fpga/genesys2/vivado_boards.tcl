# SPDX-License-Identifier: Apache-2.0
# Run a Vivado batch script with the Digilent board files available (Phase 7).
# Usage: vivado -mode batch -source fpga/genesys2/vivado_boards.tcl -tclargs <script.tcl> <script args...>
# G2D_BOARD_REPO: the board_files directory of github.com/Digilent/vivado-boards (MIT), which Vivado 2024.2 does
# not ship for the Genesys2 (board part digilentinc.com:genesys2:part0:1.1, used by Cheshire's IP scripts).
set_param board.repoPaths [list $::env(G2D_BOARD_REPO)]
set script [lindex $argv 0]
set argv [lrange $argv 1 end]
set argc [llength $argv]
source $script
