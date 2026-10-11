"""Base-rate model: P(term said >= k times) from comparable past transcripts."""

DECAY = 0.85  # weight of each older transcript relative to the next newer one
PRIOR = 0.5   # Jeffreys Beta(0.5, 0.5) pseudo-counts on each side


def p_hit(counts, threshold):
    """`counts`: mentions per comparable transcript, newest first."""
    w = [DECAY ** i for i in range(len(counts))]
    hits = sum(wi for wi, c in zip(w, counts) if c >= threshold)
    return (hits + PRIOR) / (sum(w) + 2 * PRIOR)


def shrink(market, model, k):
    """Blend toward the market: market + k * (model - market)."""
    return market + k * (model - market)


def taker_fee(price, rate):
    """Polymarket taker fee per share: rate * p * (1 - p) (docs.polymarket.com/trading/fees)."""
    return rate * price * (1 - price)


def demo():
    assert abs(p_hit([], 1) - 0.5) < 1e-9
    assert p_hit([3, 2, 5, 4, 1, 2, 3, 4], 1) > 0.9
    assert p_hit([0] * 10, 1) < 0.1
    assert p_hit([5, 0, 0, 0], 1) > p_hit([0, 0, 0, 5], 1)  # recency matters
    assert abs(taker_fee(0.5, 0.04) - 0.01) < 1e-12


if __name__ == "__main__":
    demo()
    print("ok")
