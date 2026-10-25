"""Typed tabular inputs and sourced model probabilities for fictional transactions."""

from dataclasses import dataclass
from math import isfinite, log1p

from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result

FEATURES = (
    "log_amount_ratio",
    "new_counterparty",
    "cross_border",
    "night",
    "log_transactions_24h",
    "kyc_age_years",
)


@dataclass(frozen=True, slots=True, init=False)
class RiskInput(FactoryOnly):
    amount_ratio: float
    new_counterparty: bool
    cross_border: bool
    night: bool
    transactions_24h: int
    kyc_age_days: int

    @classmethod
    def create(
        cls,
        amount_ratio: float,
        new_counterparty: bool,
        cross_border: bool,
        night: bool,
        transactions_24h: int,
        kyc_age_days: int,
    ) -> Result["RiskInput", str]:
        if (
            not isfinite(amount_ratio)
            or not 0 <= amount_ratio <= 100_000
            or not 0 <= transactions_24h <= 100_000
            or not 0 <= kyc_age_days <= 36500
        ):
            return Err("Risk features must be finite and within documented bounds.")
        return Ok(
            cls._new(
                amount_ratio=amount_ratio,
                new_counterparty=new_counterparty,
                cross_border=cross_border,
                night=night,
                transactions_24h=transactions_24h,
                kyc_age_days=kyc_age_days,
            )
        )

    def vector(self) -> tuple[float, ...]:
        return (
            log1p(self.amount_ratio),
            float(self.new_counterparty),
            float(self.cross_border),
            float(self.night),
            log1p(self.transactions_24h),
            self.kyc_age_days / 365,
        )


@dataclass(frozen=True, slots=True, init=False)
class RiskPrediction(FactoryOnly):
    probability: float
    version: str

    @classmethod
    def create(cls, probability: float, version: str) -> Result["RiskPrediction", str]:
        if not isfinite(probability) or not 0 <= probability <= 1 or not version.strip():
            return Err("A risk probability needs a finite value in [0, 1] and a version.")
        return Ok(cls._new(probability=probability, version=version))

    @property
    def source(self) -> str:
        return f"risk_model@{self.version}"
