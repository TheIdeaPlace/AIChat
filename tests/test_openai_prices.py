"""Standard prices and maximum request bounds, without wx (#685)."""
from datetime import date
from decimal import Decimal

import pytest

from thechatplace import openai_prices as prices


def test_every_exact_price_has_its_own_limits_and_source():
    assert prices.PRICES.keys() == prices.TOKEN_LIMITS.keys() == prices.SOURCES.keys()
    for model in prices.PRICES:
        assert prices.request_ceiling(model) > 0
        assert prices.request_ceiling(model, 1_050_000) == prices.request_ceiling(model)
    assert prices.TOKEN_LIMITS['gpt-6-luna'] == (922_000, 128_000)
    assert prices.TOKEN_LIMITS['gpt-5-nano'] == (272_000, 128_000)
    assert prices.request_ceiling('gpt-5-nano') == pytest.approx(.0648)


def test_luna_cache_writes_and_long_context_are_conservative():
    counters = dict(inputTokens=300000, cachedInputTokens=10000,
                    cacheWriteInputTokens=20000, outputTokens=1000, reasoningOutputTokens=400)
    # All input/cache rates doubled, output times 1.5. Reasoning not added again.
    expected = (270000 * .2 + 10000 * .02 + 20000 * .25 + 1000 * .75) / 1e6
    assert prices.estimate('gpt-6-luna', counters) == pytest.approx(expected)
    assert prices.request_ceiling('gpt-6-luna') == pytest.approx((922000*.25 + 128000*.75)/1e6)
    with pytest.raises(ValueError):
        prices.estimate('gpt-6-luna', dict(counters, cacheWriteInputTokens=300000))


def test_promotional_expiry_uses_clock_and_labels_unknown(monkeypatch):
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 30)
    monkeypatch.setattr(prices, 'date', Clock)
    assert prices.require_price('gpt-5.6-sol') == (Decimal('4'), Decimal('.4'), Decimal('20'))
    class Expired(Clock):
        @classmethod
        def today(cls):
            return cls(2026, 12, 1)
    monkeypatch.setattr(prices, 'date', Expired)
    assert not prices.is_priced('gpt-5.6-sol')
    assert 'price unknown' in next(m.label for m in prices.model_choices() if m.model == 'gpt-5.6-sol')
    with pytest.raises(prices.Unpriced):
        prices.request_ceiling('gpt-5.6-sol')


def test_unknown_catalog_label_is_decorated_once():
    from thechatplace.providers.codex_models import CodexModel
    raw = (CodexModel('future', 'Future', ('low', 'ultra'), 'low', False),)
    api_catalog = prices.model_choices(raw)
    dialog_catalog = prices.model_choices(api_catalog)
    model = next(m for m in dialog_catalog if m.model == 'future')
    assert model.label == 'Future (price unknown)'
    assert model.efforts == ('low',)
