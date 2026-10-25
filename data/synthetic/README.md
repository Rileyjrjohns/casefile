# Synthetic transaction dataset

All rows are fictional and generated locally by
`casefile.features.risk_scoring.adapters.outbound.synthetic.generate`.
Reproduce with `make train-risk`. Default seed: `20261024`; rows: 6,000.

The CSV contains a synthetic identifier, six transformed features, binary label
and fixed split. Amount ratio follows a log-normal distribution; independent
Bernoulli flags represent new counterparties, foreign countries and night hours.
Activity counts and KYC ages are sampled from bounded ranges. The target is a
Bernoulli draw from the generator's explicit nonlinear logit, not a model output.

The training split alone determines normalization. Validation selects epoch and
temperature; the test split is held out for final comparison. Dataset hashes and
export provenance are recorded alongside the model. See the
[model card](../../models/MODEL_CARD.md) for exact metrics and limitations.
The dataset contains no customer names, account numbers or real observations.
