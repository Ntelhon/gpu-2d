# SPDX-License-Identifier: Apache-2.0
# LibreLane PDN configuration for g2d_top on IHP SG13G2 with SRAM macros (v2.0 step C, docs/ASIC.md).
# LibreLane's default grid, plus a macro connection: the IHP SRAM macros bring VDD! / VDDARRAY! / VSS! out as
# vertical Metal4 stripes over the whole macro height and keep Metal5 and above free, while the default macro grid
# only connects TopMetal1 and TopMetal2. Horizontal Metal5 straps over each macro tie the Metal4 pins to the TopMetal1
# straps of the core grid.
source $::env(SCRIPTS_DIR)/openroad/common/pdn_cfg.tcl

add_pdn_stripe -grid macro -layer Metal5 -width 2.2 -spacing 2.0 -pitch 24.0 -offset 6.0
add_pdn_connect -grid macro -layers "Metal4 Metal5"
add_pdn_connect -grid macro -layers "Metal5 TopMetal1"
