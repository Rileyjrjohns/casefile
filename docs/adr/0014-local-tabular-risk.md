# ADR 014: Reproducible local tabular risk

Status: Accepted.

A small CPU PyTorch MLP is trained on seeded fictional transactions, compared
with logistic regression on the same split. Train-only normalization and
validation-only epoch/temperature selection avoid test-label tuning. Exported
weights, metrics, dataset hash and model card are versioned.

Runtime validates and evaluates portable JSON weights without PyTorch or pickle.
Bootstrap bridges the risk port into decisioning; neither feature imports the
other. Each prediction becomes an observed `risk_model@version` source with exact
inputs. It is a synthetic risk signal, never an automatic human decision.
