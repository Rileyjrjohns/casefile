# risk-v1 model card

## Intended use

An educational tabular risk signal for fictional transactions in Casefile.
It estimates the synthetic generator's adverse-label probability. It is not a
fraud probability for a real person, a compliance decision or a credit score.
Human review is mandatory. No real customer information is used.

## Data and training

`data/synthetic/risk-v1.csv` has 6,000 rows from seed `20261024`. Its SHA-256 is
`d2159c1a6633623529a93c5b8f35bdea9719ca1504e022e74d6dc9eafdeeb460`.
A seeded shuffle assigns 3,600 training, 1,200 validation and 1,200 test examples.
The Bernoulli target follows a declared nonlinear logistic generator, including a
new-counterparty/cross-border interaction. No case-evaluation label is used.

Inputs: log1p(amount / declared monthly volume), new counterparty, cross-border,
night (UTC before 06:00), log1p(transactions in the previous 24 hours), KYC age in
years. Mean and standard deviation are fitted on training data only.

The double-precision CPU PyTorch MLP is Linear(6,12), ReLU, Linear(12,1).
Adam uses learning rate 0.02, weight decay 0.002 and up to 300 full-batch epochs.
Lowest validation BCE selects epoch 78. Validation BCE selects temperature 1.0
from {0.75, 1, 1.25, 1.5, 2}. Test labels do not select epochs or temperature.
LogisticRegression(max_iter=500, random_state=seed) uses identical standardized
features and splits. Deterministic algorithms and one CPU thread are enabled.

```sh
make train-risk
```

Training dependencies are isolated in the `training` uv group. Linux uses the
CPU-only PyTorch wheel index. Exact versions are in `uv.lock`; metrics retain
training parameters. Reproduction within the same pinned environment is intended;
floating-point differences across platforms are possible.

## Held-out results

Test prevalence is 0.2183. AUC-PR below means sklearn average precision, not
trapezoidal integration. ECE uses ten equal-width probability bins.

| Metric | PyTorch | Logistic regression |
| --- | ---: | ---: |
| AUC-PR | 0.586163 | 0.579005 |
| ROC-AUC | 0.780945 | 0.781307 |
| Brier score | 0.130165 | 0.131469 |
| ECE | 0.016995 | 0.035366 |
| Accuracy at 0.5 | 0.826667 | 0.820833 |

Full precision is in `risk-v1.metrics.json`. A small difference on one synthetic
split does not demonstrate statistical superiority or external validity.

## Artifacts and runtime

`risk-v1.pt` is the training state dictionary. Runtime never loads a pickle:
`risk-v1.json` contains bounded, validated dimensions, feature order, normalization,
weights, temperature and dataset hash. The wheel embeds this JSON. Pure Python
inference is checked against the PyTorch checkpoint to 1e-12 on 100 seeded rows.
Checkpoint tests use `torch.load(..., weights_only=True)`.

The investigation derives features only from observed evidence, with transaction
time as reference. New-counterparty means absent from the earlier returned
transaction window, not absent from the customer's full history. Counts cover
only returned rows, KYC age is clamped to nonnegative days, amount ratio to
100,000, and age to 36,500 days. Missing KYC or nonpositive declared volume skips
scoring. Cross-border compares transaction and KYC country; no currency conversion
is performed. These proxies can differ substantially from training assumptions.

Predictions are recorded as `risk_model@risk-v1`, with the transaction ID and
exact feature input in the run trace. Risk at least 0.5 adds a concern to demo
recommendation policy; every score remains visible for human interpretation.

## Limitations

Only synthetic data, one seed, a small model and no external validation. No
fairness, subgroup, adversarial, temporal-drift or population-shift assessment.
The generator is known and simplified; train/test distributions match by design.
Calibration describes this generator, not real fraud. The model cannot detect
prompt injection, identify sanctioned persons or decide a human review.
