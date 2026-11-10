# Resultados del experimento T0 frente a T1

Corridas analizadas: 72. Excluidas por error de entorno: 0. Filas duplicadas descartadas: 0.

## Tabla 5.1: Rendimiento global

| Métrica | T0 | T1 |
| :--- | :---: | :---: |
| Corridas | 36 | 36 |
| Éxito de validate (S_v) | 97.2 % (35/36) | 100.0 % (36/36) |
| Éxito de plan (S_p) | 97.2 % (35/36) | 100.0 % (36/36) |
| Cobertura efectiva de recursos | 88.5 % | 100.0 % |
| Hallazgos de Checkov | 8.44 | 9.22 |
| Reparaciones (N_i) | 0.00 | 0.25 |
| Tiempo (T_e) | 36.8 s | 59.9 s |
| Costo (C_i) | 0.0151 USD | 0.0311 USD |
| Tokens (entrada + salida) | 4545.86 | 13182.03 |
| Éxito de validate (S_v), basico | 100.0 % (12/12) | 100.0 % (12/12) |
| Éxito de validate (S_v), intermedio | 100.0 % (15/15) | 100.0 % (15/15) |
| Éxito de validate (S_v), avanzado | 88.9 % (8/9) | 100.0 % (9/9) |
| Éxito de plan (S_p), basico | 100.0 % (12/12) | 100.0 % (12/12) |
| Éxito de plan (S_p), intermedio | 100.0 % (15/15) | 100.0 % (15/15) |
| Éxito de plan (S_p), avanzado | 88.9 % (8/9) | 100.0 % (9/9) |

La cobertura efectiva vale 0 cuando la corrida no llega a un plan válido.

## Tabla 5.2: Wilcoxon de rangos con signo, pareado por escenario

Cada escenario aporta un par: el promedio de sus repeticiones en T0 y en T1. Se descartan los escenarios con diferencia cero. p bilateral; p Holm ajusta por las métricas de la tabla.

| Métrica | Escenarios | Pares ≠ 0 | Mediana Δ (T1 − T0) | W+ | W− | W | p | p Holm | r biserial |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Éxito de validate (S_v) | 12 | 1 | 0.000 | 1.0 | 0.0 | 0.0 | 1.0000 | 1.0000 | 1.00 |
| Éxito de plan (S_p) | 12 | 1 | 0.000 | 1.0 | 0.0 | 0.0 | 1.0000 | 1.0000 | 1.00 |
| Cobertura efectiva de recursos | 12 | 6 | 0.033 | 21.0 | 0.0 | 0.0 | 0.0312 | 0.1562 | 1.00 |
| Hallazgos de Checkov | 12 | 7 | 0.000 | 15.5 | 12.5 | 12.5 | 0.8281 | 1.0000 | 0.11 |
| Reparaciones (N_i) | 12 | 5 | 0.000 | 15.0 | 0.0 | 0.0 | 0.0625 | 0.2500 | 1.00 |
| Tiempo (T_e) | 12 | 12 | 17.457 | 77.0 | 1.0 | 1.0 | 0.0010 | 0.0068 | 0.97 |
| Costo (C_i) | 12 | 12 | 0.012 | 76.0 | 2.0 | 2.0 | 0.0015 | 0.0088 | 0.95 |
| Tokens (entrada + salida) | 12 | 12 | 7193.833 | 78.0 | 0.0 | 0.0 | 0.0005 | 0.0039 | 1.00 |

### Detalle por escenario (descriptivo)

| Escenario | Nivel | S_v T0 | S_v T1 | S_p T0 | S_p T1 | Cobertura T0 | Cobertura T1 | Checkov T0 | Checkov T1 | Reparaciones T1 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| AVA-01 | avanzado | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 15.0 | 15.3 | 0.3 |
| AVA-02 | avanzado | 67 % | 100 % | 67 % | 100 % | 64.1 % | 100.0 % | 10.0 | 14.3 | 0.3 |
| AVA-03 | avanzado | 100 % | 100 % | 100 % | 100 % | 93.3 % | 100.0 % | 21.7 | 29.0 | 0.7 |
| BAS-01 | basico | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 4.0 | 4.0 | 0.0 |
| BAS-02 | basico | 100 % | 100 % | 100 % | 100 % | 75.0 % | 100.0 % | 0.0 | 0.0 | 0.0 |
| BAS-03 | basico | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 0.0 | 0.0 | 0.0 |
| BAS-04 | basico | 100 % | 100 % | 100 % | 100 % | 66.7 % | 100.0 % | 1.3 | 1.0 | 0.0 |
| INT-01 | intermedio | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 14.3 | 13.3 | 0.0 |
| INT-02 | intermedio | 100 % | 100 % | 100 % | 100 % | 80.0 % | 100.0 % | 0.0 | 0.0 | 1.0 |
| INT-03 | intermedio | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 14.3 | 14.0 | 0.7 |
| INT-04 | intermedio | 100 % | 100 % | 100 % | 100 % | 83.3 % | 100.0 % | 7.0 | 6.0 | 0.0 |
| INT-05 | intermedio | 100 % | 100 % | 100 % | 100 % | 100.0 % | 100.0 % | 13.7 | 13.7 | 0.0 |

## Tabla 5.3: McNemar (pares T0/T1 de la misma repetición)

**Éxito de validate (S_v)**

| | T1 éxito | T1 fracaso | Total |
| :--- | :---: | :---: | :---: |
| T0 éxito | 35 | 0 | 35 |
| T0 fracaso | 1 | 0 | 1 |
| Total | 36 | 0 | 36 |

Discordantes: b = 0, c = 1. p exacto (binomial) = 1.0000; χ² con corrección de continuidad = 0.00 (gl = 1), p = 1.0000.

**Éxito de plan (S_p)**

| | T1 éxito | T1 fracaso | Total |
| :--- | :---: | :---: | :---: |
| T0 éxito | 35 | 0 | 35 |
| T0 fracaso | 1 | 0 | 1 |
| Total | 36 | 0 | 36 |

Discordantes: b = 0, c = 1. p exacto (binomial) = 1.0000; χ² con corrección de continuidad = 0.00 (gl = 1), p = 1.0000.

## Tabla 5.4: IC 95 % por bootstrap (B = 10.000, por escenario, semilla 20261105)

| Métrica | T0 [IC 95 %] | T1 [IC 95 %] | Δ (T1 − T0) [IC 95 %] |
| :--- | :---: | :---: | :---: |
| Éxito de validate (S_v) | 97.2 % [91.7 %, 100.0 %] | 100.0 % [100.0 %, 100.0 %] | 2.8 % [0.0 %, 8.3 %] |
| Éxito de plan (S_p) | 97.2 % [91.7 %, 100.0 %] | 100.0 % [100.0 %, 100.0 %] | 2.8 % [0.0 %, 8.3 %] |
| Cobertura efectiva de recursos | 88.5 % [80.6 %, 95.8 %] | 100.0 % [100.0 %, 100.0 %] | 11.5 % [4.2 %, 19.4 %] |
| Hallazgos de Checkov | 8.44 [4.44, 12.42] | 9.22 [4.61, 14.20] | 0.78 [-0.33, 2.28] |
| Reparaciones (N_i) | 0.00 [0.00, 0.00] | 0.25 [0.08, 0.44] | 0.25 [0.08, 0.44] |
| Tiempo (T_e) | 36.8 s [32.1 s, 41.7 s] | 59.9 s [46.4 s, 77.0 s] | 23.1 s [11.2 s, 39.2 s] |
| Costo (C_i) | 0.0151 USD [0.0128 USD, 0.0177 USD] | 0.0311 USD [0.0224 USD, 0.0415 USD] | 0.0160 USD [0.0084 USD, 0.0257 USD] |
