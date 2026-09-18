# Standalone generated hls4ml core characterization at the real 40 MHz domain.
set root [file normalize [file dirname [info script]]]
set project radiant_canary_core
set part LIFCL-17-9SG72C
set top __myproject__myproject
if {[file exists "$project.rdf"]} {
    prj_open "$project.rdf"
    prj_close
    file delete -force "$project.rdf" impl_1
}
prj_create -name $project -impl impl_1 -dev $part -synthesis "synplify"
prj_add_source "$root/../gateware/radiant_hls4ml_canary_generated.sv"
prj_add_source "$root/core.sdc"
prj_set_impl_opt -impl impl_1 "top" $top
prj_set_impl_opt -impl impl_1 "VerilogStandard" "System Verilog"
prj_save
prj_run Synthesis -impl impl_1
prj_close
