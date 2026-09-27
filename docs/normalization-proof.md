# Normalization proof — Sample Hack 2026

Method `zscore-shrink-v2`; rubric scale 1–5; weights functionality=1, quality=1, innovation=1.
122 submitted reviews over 40 eligible projects by 30 judges. Panel mean 3.557, panel spread 0.653. Judge shrinkage K = 3; jury-size adjustment J = 3.

## Judges

| judge | reviews | mean | spread | shrink weight | shrunk mean | shrunk spread | note |
|---|---:|---:|---:|---:|---:|---:|---|
| jdg_01 | 1 | 2.000 | 0.000 | 0.25 | 3.168 | 0.490 | few reviews: pulled toward panel |
| jdg_02 | 6 | 4.222 | 0.807 | 0.67 | 4.001 | 0.756 |  |
| jdg_03 | 2 | 3.833 | 0.707 | 0.40 | 3.668 | 0.675 | few reviews: pulled toward panel |
| jdg_04 | 4 | 3.583 | 0.739 | 0.57 | 3.572 | 0.702 |  |
| jdg_05 | 2 | 3.500 | 0.236 | 0.40 | 3.534 | 0.486 | few reviews: pulled toward panel |
| jdg_06 | 3 | 3.444 | 0.509 | 0.50 | 3.501 | 0.581 |  |
| jdg_07 | 3 | 4.000 | 0.000 | 0.50 | 3.779 | 0.327 | flat: every score identical, rank-neutral |
| jdg_08 | 3 | 3.444 | 0.839 | 0.50 | 3.501 | 0.746 |  |
| jdg_09 | 5 | 3.467 | 0.558 | 0.62 | 3.501 | 0.594 |  |
| jdg_10 | 3 | 3.222 | 0.509 | 0.50 | 3.390 | 0.581 |  |
| jdg_11 | 6 | 3.611 | 0.772 | 0.67 | 3.593 | 0.733 |  |
| jdg_12 | 2 | 3.500 | 0.707 | 0.40 | 3.534 | 0.675 | few reviews: pulled toward panel |
| jdg_13 | 3 | 4.000 | 0.667 | 0.50 | 3.779 | 0.660 |  |
| jdg_14 | 3 | 3.000 | 0.667 | 0.50 | 3.279 | 0.660 |  |
| jdg_15 | 6 | 4.056 | 0.828 | 0.67 | 3.889 | 0.770 |  |
| jdg_16 | 6 | 3.611 | 0.772 | 0.67 | 3.593 | 0.733 |  |
| jdg_17 | 2 | 3.500 | 0.236 | 0.40 | 3.534 | 0.486 | few reviews: pulled toward panel |
| jdg_18 | 2 | 3.500 | 0.236 | 0.40 | 3.534 | 0.486 | few reviews: pulled toward panel |
| jdg_19 | 3 | 3.222 | 0.770 | 0.50 | 3.390 | 0.712 |  |
| jdg_20 | 6 | 3.111 | 0.807 | 0.67 | 3.260 | 0.756 |  |
| jdg_21 | 3 | 3.333 | 0.577 | 0.50 | 3.445 | 0.615 |  |
| jdg_22 | 5 | 3.733 | 0.760 | 0.62 | 3.667 | 0.720 |  |
| jdg_23 | 1 | 3.333 | 0.000 | 0.25 | 3.501 | 0.490 | few reviews: pulled toward panel |
| jdg_24 | 11 | 3.364 | 0.623 | 0.79 | 3.405 | 0.629 |  |
| jdg_25 | 5 | 3.467 | 0.447 | 0.62 | 3.501 | 0.525 |  |
| jdg_26 | 9 | 3.704 | 0.512 | 0.75 | 3.667 | 0.547 |  |
| jdg_27 | 2 | 3.000 | 0.471 | 0.40 | 3.334 | 0.581 | few reviews: pulled toward panel |
| jdg_28 | 2 | 3.167 | 0.236 | 0.40 | 3.401 | 0.486 | few reviews: pulled toward panel |
| jdg_29 | 9 | 3.519 | 0.412 | 0.75 | 3.528 | 0.472 |  |
| jdg_30 | 4 | 4.083 | 0.569 | 0.57 | 3.858 | 0.605 |  |

## Projects

Sorted by final rank. Δ is raw rank minus final rank: positive means the project moved up once judge bias was removed and the number of reviews was accounted for.

| rank | norm. rank | raw rank | Δ | project | track | reviews | raw mean | normalized | weight | adjusted | judges |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | 1 | 1 | 0 | Iron Switch | Open hardware | 3 | 4.333 | 4.302 | 0.50 | 3.930 | jdg_15, jdg_20, jdg_29 |
| 2 | 2 | 1 | -1 | Salt Ledger | Data and analytics | 4 | 4.333 | 4.150 | 0.57 | 3.896 | jdg_02, jdg_16, jdg_25, jdg_30 |
| 3 | 4 | 5 | +2 | Salt Loom | Education | 4 | 4.083 | 4.054 | 0.57 | 3.841 | jdg_04, jdg_11, jdg_22, jdg_24 |
| 4 | 3 | 6 | +2 | Slow Trail | Developer tools | 3 | 4.000 | 4.079 | 0.50 | 3.818 | jdg_09, jdg_24, jdg_26 |
| 5 | 5 | 4 | -1 | Dry Relay | Data and analytics | 3 | 4.111 | 3.997 | 0.50 | 3.777 | jdg_16, jdg_25, jdg_30 |
| 6 | 7 | 6 | 0 | Salt Kiln | Security | 3 | 4.000 | 3.904 | 0.50 | 3.731 | jdg_02, jdg_08, jdg_10 |
| 7 | 6 | 3 | -4 | Still Beacon | Open hardware | 2 | 4.167 | 3.983 | 0.40 | 3.728 | jdg_15, jdg_29 |
| 8 | 8 | 8 | 0 | Copper Kiln | Data and analytics | 3 | 3.889 | 3.738 | 0.50 | 3.648 | jdg_02, jdg_16, jdg_25 |
| 9 | 9 | 10 | +1 | Green Switch | Education | 3 | 3.778 | 3.703 | 0.50 | 3.630 | jdg_04, jdg_11, jdg_22 |
| 10 | 10 | 9 | -1 | North Drift | Security | 5 | 3.800 | 3.669 | 0.62 | 3.627 | jdg_02, jdg_08, jdg_10, jdg_13, jdg_23 |
| 11 | 11 | 10 | -1 | Deep Beacon | Data and analytics | 3 | 3.778 | 3.662 | 0.50 | 3.610 | jdg_16, jdg_25, jdg_30 |
| 12 | 12 | 12 | 0 | Copper Orbit | Security | 2 | 3.667 | 3.635 | 0.40 | 3.588 | jdg_10, jdg_13 |
| 13 | 13 | 12 | -1 | Salt Drift | Open hardware | 3 | 3.667 | 3.616 | 0.50 | 3.587 | jdg_15, jdg_20, jdg_29 |
| 14 | 14 | 15 | +1 | Salt Ferry | Developer tools | 3 | 3.556 | 3.604 | 0.50 | 3.580 | jdg_09, jdg_24, jdg_26 |
| 15 | 15 | 15 | 0 | Small Meadow | Accessibility | 3 | 3.556 | 3.596 | 0.50 | 3.577 | jdg_12, jdg_21, jdg_26 |
| 16 | 16 | 19 | +3 | Glass Beacon | Accessibility | 2 | 3.500 | 3.549 | 0.40 | 3.554 | jdg_18, jdg_19 |
| 17 | 17 | 23 | +6 | Flat Thread | Open hardware | 3 | 3.444 | 3.546 | 0.50 | 3.552 | jdg_15, jdg_20, jdg_29 |
| 18 | 18 | 23 | +5 | Open Beacon | Education | 3 | 3.444 | 3.537 | 0.50 | 3.547 | jdg_14, jdg_22, jdg_24 |
| 19 | 19 | 19 | 0 | Open Kiln | Education | 2 | 3.500 | 3.528 | 0.40 | 3.546 | jdg_04, jdg_24 |
| 20 | 20 | 15 | -5 | Hollow Signal | Health | 3 | 3.556 | 3.524 | 0.50 | 3.541 | jdg_07, jdg_27, jdg_29 |
| 21 | 21 | 23 | +2 | Glass Signal | Security | 3 | 3.444 | 3.498 | 0.50 | 3.528 | jdg_03, jdg_08, jdg_28 |
| 22 | 25 | 29 | +7 | Flat Relay | Developer tools | 2 | 3.333 | 3.449 | 0.40 | 3.514 | jdg_09, jdg_24 |
| 23 | 26 | 19 | -4 | Paper Anchor | Accessibility | 2 | 3.500 | 3.447 | 0.40 | 3.513 | jdg_18, jdg_26 |
| 24 | 24 | 15 | -9 | Small Loom | Health | 3 | 3.556 | 3.467 | 0.50 | 3.512 | jdg_07, jdg_27, jdg_29 |
| 25 | 22 | 22 | -3 | Warm Beacon | Climate | 5 | 3.467 | 3.481 | 0.62 | 3.509 | jdg_03, jdg_05, jdg_06, jdg_11, jdg_17 |
| 26 | 29 | 12 | -14 | Small Relay | Health | 2 | 3.667 | 3.423 | 0.40 | 3.503 | jdg_07, jdg_29 |
| 27 | 23 | 28 | +1 | Green Lantern | Education | 5 | 3.400 | 3.469 | 0.62 | 3.502 | jdg_04, jdg_11, jdg_14, jdg_22, jdg_24 |
| 28 | 27 | 23 | -5 | Loud Ledger | Developer tools | 3 | 3.444 | 3.436 | 0.50 | 3.497 | jdg_09, jdg_24, jdg_26 |
| 29 | 28 | 29 | 0 | Dry Harbour | Accessibility | 5 | 3.333 | 3.428 | 0.62 | 3.476 | jdg_01, jdg_12, jdg_19, jdg_21, jdg_26 |
| 30 | 30 | 29 | -1 | Deep Compass | Accessibility | 3 | 3.333 | 3.366 | 0.50 | 3.462 | jdg_19, jdg_21, jdg_26 |
| 31 | 31 | 29 | -2 | Quiet Anchor | Climate | 3 | 3.333 | 3.301 | 0.50 | 3.429 | jdg_05, jdg_06, jdg_11 |
| 32 | 32 | 33 | +1 | Amber Hours | Climate | 3 | 3.222 | 3.278 | 0.50 | 3.418 | jdg_06, jdg_11, jdg_17 |
| 33 | 33 | 35 | +2 | Paper Harbour | Education | 3 | 3.111 | 3.228 | 0.50 | 3.393 | jdg_14, jdg_22, jdg_24 |
| 34 | 34 | 23 | -11 | Flat Meadow | Data and analytics | 3 | 3.444 | 3.195 | 0.50 | 3.376 | jdg_02, jdg_16, jdg_30 |
| 35 | 35 | 33 | -2 | Paper Thread | Open hardware | 3 | 3.222 | 3.173 | 0.50 | 3.365 | jdg_15, jdg_20, jdg_29 |
| 36 | 36 | 35 | -1 | Dry Bridge | Security | 3 | 3.111 | 3.156 | 0.50 | 3.357 | jdg_13, jdg_20, jdg_28 |
| 37 | 37 | 35 | -2 | Dry Compass | Developer tools | 3 | 3.111 | 3.094 | 0.50 | 3.326 | jdg_09, jdg_24, jdg_26 |
| 38 | 38 | 38 | 0 | Slow Loom | Developer tools | 2 | 3.000 | 2.975 | 0.40 | 3.324 | jdg_24, jdg_26 |
| 39 | 39 | 39 | 0 | Slow Quarry | Open hardware | 3 | 2.889 | 2.888 | 0.50 | 3.223 | jdg_15, jdg_20, jdg_29 |
| 40 | 40 | 39 | -1 | North Compass | Data and analytics | 3 | 2.889 | 2.786 | 0.50 | 3.172 | jdg_02, jdg_16, jdg_25 |

31 of 40 projects changed rank. Largest move: Small Relay from raw #12 to final #26.
