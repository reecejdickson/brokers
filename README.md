# brokers

Pipeline to find US real estate brokerages with a **verified** headcount of 35 to 75
agents and a callable office phone. The headcount must come from a regulator roster;
the phone comes from Overture Maps places and the firm's own website.

* `output/REPORT.md`: results of the latest run, what was blocked, and how to re-run
* `output/sources_checked.csv`: all 50 states plus DC, with which publish free rosters that link licensees to firms
* `output/leads_verified.csv`, `output/no_phone.csv`: deliverables
* `pipeline/run_all.sh`: runs the full pipeline (Overture extract, then headcount, then phones)
