# Cost-scheduler weight tuning

Workload **C**; 31 candidates (including the current defaults); training seeds [101, 102, 103], held-out test seeds [201, 202, 203, 204, 205]; 300 s.

Objective per seed = fraction of experiments late + (makespan / FIFO makespan - 1). Lower is better. FIFO scores exactly its own late fraction.

| Weights | Training objective | Held-out objective |
|---|---|---|
| current defaults | 0.371 | 0.422 |
| best on training | 0.347 | 0.333 |
| FIFO (reference) | - | 0.641 |

The tuned weights **also beat the defaults on the held-out seeds**, so the improvement generalises to unseen workloads of this kind.

Best weights: `travel=4.3854, switching=11.1095, delay=0.0183, idle=0.003, deadline=23.9836`

## Top five candidates on the training seeds

| Objective | Weights |
|---|---|
| 0.347 | `travel=4.3854, switching=11.1095, delay=0.0183, idle=0.003, deadline=23.9836` |
| 0.359 | `travel=3.4795, switching=33.7949, delay=0.0012, idle=0.0095, deadline=1.8791` |
| 0.367 | `travel=5.0344, switching=0.3279, delay=0.0504, idle=0.0026, deadline=408.66` |
| 0.367 | `travel=7.0057, switching=0.1969, delay=0.0451, idle=0.0659, deadline=30.028` |
| 0.369 | `travel=5.8303, switching=31.0007, delay=0.0419, idle=0.0075, deadline=80.0827` |
