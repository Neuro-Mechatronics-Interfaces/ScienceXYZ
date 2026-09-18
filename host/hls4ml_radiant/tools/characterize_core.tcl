# Synthesize one generated core variant at the real 40 MHz (25 ns) SDC and
# report LUT/FF/Fmax/slack. Driven per-variant by characterize_sweep.ps1.
#
# Args (via -a): <variant_sv_abspath> <project_name> <workdir_abspath>
set sv_path   [lindex $argv 0]
set project   [lindex $argv 1]
set workdir   [lindex $argv 2]
set part      LIFCL-17-9SG72C
set top       __myproject__myproject

file mkdir $workdir
cd $workdir

# 25 ns / 40 MHz constraint on the single clk port.
set sdc [file join $workdir core25.sdc]
set fh [open $sdc w]
puts $fh "create_clock -name {clk} -period 25.0000 \[get_ports {clk}\]"
close $fh

if {[file exists "$project.rdf"]} {
    catch { prj_open "$project.rdf" ; prj_close }
    file delete -force "$project.rdf" impl_1
}
prj_create -name $project -impl impl_1 -dev $part -synthesis "synplify"
prj_add_source $sv_path
prj_add_source $sdc
prj_set_impl_opt -impl impl_1 "top" $top
prj_set_impl_opt -impl impl_1 "VerilogStandard" "System Verilog"
prj_save
prj_run Synthesis -impl impl_1
prj_close
