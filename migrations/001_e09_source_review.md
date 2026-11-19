# E09 source review

Primary source: [Ferrara et al., “Ethosomes for Curcumin and Piperine Cutaneous Delivery to Prevent Environmental-Stressor-Induced Skin Damage,” Antioxidants 13(1), 91 (2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC10812558/), DOI 10.3390/antiox13010091.

The paper's methods report size/dispersity characterization for microfluidic flow-rate conditions and a separate patch test on 20 healthy volunteers for single-application skin irritation/tolerability of ET-CUR and ET-PIP. It also reports ex vivo human skin explants. The volunteer test assesses skin compatibility; it is not a clinical efficacy trial. Therefore E09-F01–F06 retain “Physicochemical only,” while the two drug-loaded ET-CUR/ET-PIP records E09-F07 and E09-F08 retain a human-volunteer study label with the limitation explicitly stated in `Evidence_Location`. The source lists treatment as topical and makes no claim of therapeutic efficacy from the patch test.

The correction is applied by `001_e09_study_type_correction.sql` and covered by an offline regression test. Retrieved dates remain unset because the v4 source did not record when this paper was retrieved; no retrieval date is invented.
