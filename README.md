# brokers

US real estate brokerages with a **verified** headcount of 35 to 75 agents and a callable
office phone, built from free sources only. Headcounts come from state regulator rosters
(active individual licensees linked to the firm). Phones come from the regulator's firm
record, Overture Maps places and the firm's own website.

* `output/leads_verified.csv`: firms with a verified headcount and a phone (the call list)
* `output/no_phone.csv`: firms with a verified headcount but no confirmed phone
* `output/sources_checked.csv`: all 50 states plus DC, with which rosters are free, link licensees to firms, and were usable
* `output/REPORT.md`: counts by state, confidence split, best phone sources, and what could not be verified
* `output/firms_headcount_35_75.csv`, `headcount_source_log.csv`, `phone_match_log.csv`, `duplicates_dropped.csv`: audit trail
* `pipeline/run_all.sh`: reruns everything
