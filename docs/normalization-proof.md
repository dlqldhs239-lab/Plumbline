# Normalization proof — Sample Hack 2026

Method `additive-shrink-v3`; rubric scale 1–5; weights functionality=1, quality=1, innovation=1.
122 submitted reviews over 40 eligible projects by 30 judges. Panel mean 3.546, panel spread 0.658. Judge shrinkage K = 1; jury-size adjustment J = 3. Solved in 139 rounds.

## Judges

Leniency is what is taken off each of the judge's scores. It is measured against what other judges gave the same projects, and believed in the proportion reviews / (reviews + K).

| judge | reviews | mean | from the panel | spread | believed | leniency | note |
|---|---:|---:|---:|---:|---:|---:|---|
| jdg_01 | 1 | 2.000 | -1.546 | 0.000 | 0.50 | -0.721 | few reviews: leniency believed little |
| jdg_02 | 6 | 4.222 | +0.676 | 0.807 | 0.86 | +0.428 |  |
| jdg_03 | 2 | 3.833 | +0.287 | 0.707 | 0.67 | +0.333 | few reviews: leniency believed little |
| jdg_04 | 4 | 3.583 | +0.037 | 0.739 | 0.80 | -0.074 |  |
| jdg_05 | 2 | 3.500 | -0.046 | 0.236 | 0.67 | +0.220 | few reviews: leniency believed little |
| jdg_06 | 3 | 3.444 | -0.102 | 0.509 | 0.75 | +0.249 |  |
| jdg_07 | 3 | 4.000 | +0.454 | 0.000 | 0.75 |  | every score the same: set aside, says nothing of any project |
| jdg_08 | 3 | 3.444 | -0.102 | 0.839 | 0.75 | -0.275 |  |
| jdg_09 | 5 | 3.467 | -0.080 | 0.558 | 0.83 | +0.064 |  |
| jdg_10 | 3 | 3.222 | -0.324 | 0.509 | 0.75 | -0.521 |  |
| jdg_11 | 6 | 3.611 | +0.065 | 0.772 | 0.86 | +0.169 |  |
| jdg_12 | 2 | 3.500 | -0.046 | 0.707 | 0.67 | +0.037 | few reviews: leniency believed little |
| jdg_13 | 3 | 4.000 | +0.454 | 0.667 | 0.75 | +0.320 |  |
| jdg_14 | 3 | 3.000 | -0.546 | 0.667 | 0.75 | -0.271 |  |
| jdg_15 | 6 | 4.056 | +0.509 | 0.828 | 0.86 | +0.509 |  |
| jdg_16 | 6 | 3.611 | +0.065 | 0.772 | 0.86 | -0.107 |  |
| jdg_17 | 2 | 3.500 | -0.046 | 0.236 | 0.67 | +0.261 | few reviews: leniency believed little |
| jdg_18 | 2 | 3.500 | -0.046 | 0.236 | 0.67 | +0.046 | few reviews: leniency believed little |
| jdg_19 | 3 | 3.222 | -0.324 | 0.770 | 0.75 | -0.154 |  |
| jdg_20 | 6 | 3.111 | -0.435 | 0.807 | 0.86 | -0.189 |  |
| jdg_21 | 3 | 3.333 | -0.213 | 0.577 | 0.75 | -0.044 |  |
| jdg_22 | 5 | 3.733 | +0.187 | 0.760 | 0.83 | +0.142 |  |
| jdg_23 | 1 | 3.333 | -0.213 | 0.000 | 0.50 | -0.265 | few reviews: leniency believed little |
| jdg_24 | 11 | 3.364 | -0.183 | 0.623 | 0.92 | -0.040 |  |
| jdg_25 | 5 | 3.467 | -0.080 | 0.447 | 0.83 | -0.299 |  |
| jdg_26 | 9 | 3.704 | +0.157 | 0.512 | 0.90 | +0.337 |  |
| jdg_27 | 2 | 3.000 | -0.546 | 0.471 | 0.67 | -0.296 | few reviews: leniency believed little |
| jdg_28 | 2 | 3.167 | -0.380 | 0.236 | 0.67 | -0.068 | few reviews: leniency believed little |
| jdg_29 | 9 | 3.519 | -0.028 | 0.412 | 0.90 | +0.075 |  |
| jdg_30 | 4 | 4.083 | +0.537 | 0.569 | 0.80 | +0.136 |  |

## Projects

Sorted by final rank. Δ is raw rank minus final rank: positive means the project moved up once judge bias was removed and the number of reviews was accounted for.

| rank | norm. rank | raw rank | Δ | project | track | reviews | that count | raw mean | normalized | weight | adjusted | judges |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 1 | 0 | Salt Ledger | Data and analytics | 4 | 4 | 4.333 | 4.294 | 0.57 | 3.973 | jdg_02, jdg_16, jdg_25, jdg_30 |
| 2 | 2 | 1 | -1 | Iron Switch | Open hardware | 3 | 3 | 4.333 | 4.202 | 0.50 | 3.874 | jdg_15, jdg_20, jdg_29 |
| 3 | 3 | 4 | +1 | Dry Relay | Data and analytics | 3 | 3 | 4.111 | 4.201 | 0.50 | 3.874 | jdg_16, jdg_25, jdg_30 |
| 4 | 4 | 6 | +2 | Salt Kiln | Security | 3 | 3 | 4.000 | 4.123 | 0.50 | 3.834 | jdg_02, jdg_08, jdg_10 |
| 5 | 5 | 5 | 0 | Salt Loom | Education | 4 | 4 | 4.083 | 4.034 | 0.57 | 3.825 | jdg_04, jdg_11, jdg_22, jdg_24 |
| 6 | 10 | 9 | +3 | North Drift | Security | 5 | 5 | 3.800 | 3.863 | 0.62 | 3.744 | jdg_02, jdg_08, jdg_10, jdg_13, jdg_23 |
| 7 | 6 | 8 | +1 | Copper Kiln | Data and analytics | 3 | 3 | 3.889 | 3.881 | 0.50 | 3.714 | jdg_02, jdg_16, jdg_25 |
| 8 | 7 | 6 | -2 | Slow Trail | Developer tools | 3 | 3 | 4.000 | 3.880 | 0.50 | 3.713 | jdg_09, jdg_24, jdg_26 |
| 9 | 9 | 10 | +1 | Deep Beacon | Data and analytics | 3 | 3 | 3.778 | 3.868 | 0.50 | 3.707 | jdg_16, jdg_25, jdg_30 |
| 10 | 8 | 3 | -7 | Still Beacon | Open hardware | 2 | 2 | 4.167 | 3.875 | 0.40 | 3.678 | jdg_15, jdg_29 |
| 11 | 11 | 12 | +1 | Copper Orbit | Security | 2 | 2 | 3.667 | 3.767 | 0.40 | 3.635 | jdg_10, jdg_13 |
| 12 | 12 | 10 | -2 | Green Switch | Education | 3 | 3 | 3.778 | 3.699 | 0.50 | 3.623 | jdg_04, jdg_11, jdg_22 |
| 13 | 13 | 19 | +6 | Open Kiln | Education | 2 | 2 | 3.500 | 3.557 | 0.40 | 3.551 | jdg_04, jdg_24 |
| 14 | 14 | 19 | +5 | Glass Beacon | Accessibility | 2 | 2 | 3.500 | 3.554 | 0.40 | 3.549 | jdg_18, jdg_19 |
| 15 | 15 | 12 | -3 | Salt Drift | Open hardware | 3 | 3 | 3.667 | 3.535 | 0.50 | 3.541 | jdg_15, jdg_20, jdg_29 |
| 16 | 16 | 23 | +7 | Open Beacon | Education | 3 | 3 | 3.444 | 3.501 | 0.50 | 3.523 | jdg_14, jdg_22, jdg_24 |
| 17 | 19 | 15 | -2 | Small Loom | Health | 3 | 2 | 3.556 | 3.444 | 0.40 | 3.505 | jdg_07, jdg_27, jdg_29 |
| 17 | 19 | 15 | -2 | Hollow Signal | Health | 3 | 2 | 3.556 | 3.444 | 0.40 | 3.505 | jdg_07, jdg_27, jdg_29 |
| 19 | 17 | 23 | +4 | Glass Signal | Security | 3 | 3 | 3.444 | 3.448 | 0.50 | 3.497 | jdg_03, jdg_08, jdg_28 |
| 20 | 18 | 15 | -5 | Small Meadow | Accessibility | 3 | 3 | 3.556 | 3.445 | 0.50 | 3.496 | jdg_12, jdg_21, jdg_26 |
| 21 | 22 | 15 | -6 | Salt Ferry | Developer tools | 3 | 3 | 3.556 | 3.435 | 0.50 | 3.491 | jdg_09, jdg_24, jdg_26 |
| 22 | 21 | 29 | +7 | Dry Harbour | Accessibility | 5 | 5 | 3.333 | 3.442 | 0.62 | 3.481 | jdg_01, jdg_12, jdg_19, jdg_21, jdg_26 |
| 23 | 30 | 12 | -11 | Small Relay | Health | 2 | 1 | 3.667 | 3.259 | 0.25 | 3.474 | jdg_07, jdg_29 |
| 24 | 23 | 28 | +4 | Green Lantern | Education | 5 | 5 | 3.400 | 3.415 | 0.62 | 3.464 | jdg_04, jdg_11, jdg_14, jdg_22, jdg_24 |
| 25 | 25 | 29 | +4 | Flat Relay | Developer tools | 2 | 2 | 3.333 | 3.321 | 0.40 | 3.456 | jdg_09, jdg_24 |
| 26 | 27 | 19 | -7 | Paper Anchor | Accessibility | 2 | 2 | 3.500 | 3.309 | 0.40 | 3.451 | jdg_18, jdg_26 |
| 27 | 24 | 23 | -4 | Loud Ledger | Developer tools | 3 | 3 | 3.444 | 3.324 | 0.50 | 3.435 | jdg_09, jdg_24, jdg_26 |
| 28 | 26 | 23 | -5 | Flat Thread | Open hardware | 3 | 3 | 3.444 | 3.313 | 0.50 | 3.430 | jdg_15, jdg_20, jdg_29 |
| 29 | 28 | 23 | -6 | Flat Meadow | Data and analytics | 3 | 3 | 3.444 | 3.292 | 0.50 | 3.419 | jdg_02, jdg_16, jdg_30 |
| 30 | 29 | 29 | -1 | Deep Compass | Accessibility | 3 | 3 | 3.333 | 3.287 | 0.50 | 3.417 | jdg_19, jdg_21, jdg_26 |
| 31 | 32 | 35 | +4 | Paper Harbour | Education | 3 | 3 | 3.111 | 3.167 | 0.50 | 3.357 | jdg_14, jdg_22, jdg_24 |
| 32 | 31 | 22 | -10 | Warm Beacon | Climate | 5 | 5 | 3.467 | 3.220 | 0.62 | 3.343 | jdg_03, jdg_05, jdg_06, jdg_11, jdg_17 |
| 33 | 33 | 29 | -4 | Quiet Anchor | Climate | 3 | 3 | 3.333 | 3.121 | 0.50 | 3.334 | jdg_05, jdg_06, jdg_11 |
| 34 | 34 | 33 | -1 | Paper Thread | Open hardware | 3 | 3 | 3.222 | 3.091 | 0.50 | 3.318 | jdg_15, jdg_20, jdg_29 |
| 35 | 35 | 35 | 0 | Dry Bridge | Security | 3 | 3 | 3.111 | 3.090 | 0.50 | 3.318 | jdg_13, jdg_20, jdg_28 |
| 36 | 36 | 33 | -3 | Amber Hours | Climate | 3 | 3 | 3.222 | 2.996 | 0.50 | 3.271 | jdg_06, jdg_11, jdg_17 |
| 37 | 37 | 35 | -2 | Dry Compass | Developer tools | 3 | 3 | 3.111 | 2.991 | 0.50 | 3.269 | jdg_09, jdg_24, jdg_26 |
| 38 | 39 | 38 | 0 | Slow Loom | Developer tools | 2 | 2 | 3.000 | 2.851 | 0.40 | 3.268 | jdg_24, jdg_26 |
| 39 | 38 | 39 | 0 | North Compass | Data and analytics | 3 | 3 | 2.889 | 2.881 | 0.50 | 3.214 | jdg_02, jdg_16, jdg_25 |
| 40 | 40 | 39 | -1 | Slow Quarry | Open hardware | 3 | 3 | 2.889 | 2.757 | 0.50 | 3.152 | jdg_15, jdg_20, jdg_29 |

35 of 40 projects changed rank. Largest move: Small Relay from raw #12 to final #23.
