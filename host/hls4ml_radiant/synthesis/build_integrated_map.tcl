# Follow-up map/PAR attempt for the intentionally pin-rich validation top.
# Expected result: synthesis succeeds, Map rejects the 428 exposed I/O cells
# on the QFN72 package. The real Science wrapper must keep these nets internal.
set root [file normalize [file dirname [info script]]]
set project integrated_ml_tap_map
set part LIFCL-17-9SG72C
set top ml_tap_peripheral_top
prj_create -name $project -impl impl_1 -dev $part -synthesis "synplify"
foreach source [list \
    "$root/../gateware/ml_feature_engine.sv" \
    "$root/../gateware/ml_sample_tap.sv" \
    "$root/../gateware/ml_trigger_fsm.sv" \
    "$root/../gateware/ml_tap_peripheral.sv" \
    "$root/../gateware/radiant_hls4ml_canary_generated.sv" \
    "$root/../gateware/radiant_hls4ml_canary.sv" \
] { prj_add_source $source }
prj_add_source "$root/integrated.sdc"
prj_set_impl_opt -impl impl_1 "top" $top
prj_set_impl_opt -impl impl_1 "VerilogStandard" "System Verilog"
prj_save
prj_run Synthesis -impl impl_1
prj_run Map -impl impl_1
prj_close
