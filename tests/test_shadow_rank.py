from datetime import date, timedelta

from core.shadow_rank import rank_checkpoint


def test_shadow_checkpoint_stays_not_ready_until_sample_and_cluster_floor():
    rows = [
        {
            "status": "settled",
            "run_id": "run-one",
            "candidate_id": f"c{i}",
            "ticker": f"T{i}",
            "expiration": "20261016",
            "dte_bucket": "14-30",
            "rank_position": i + 1,
            "alternative_score": 100 - i,
            "capital_return_per_day": 0.001 * i,
        }
        for i in range(3)
    ]
    result = rank_checkpoint(rows, "alternative_score")
    assert result["ready"] is False
    assert result["by_dte_bucket"]["14-30"]["settled_candidates"] == 3
    assert result["by_dte_bucket"]["14-30"]["block_bootstrap_95_ci"] == [None, None]


def test_shadow_checkpoint_derives_bucket_from_archived_dte():
    result = rank_checkpoint(
        [
            {
                "status": "settled",
                "run_id": "run-one",
                "candidate_id": "candidate-one",
                "ticker": "XYZ",
                "expiration": "20261016",
                "dte": 32,
                "rank_position": 1,
                "alternative_score": 1,
                "capital_return_per_day": 0.001,
            }
        ],
        "alternative_score",
    )
    assert result["by_dte_bucket"]["31-45"]["settled_candidates"] == 1


def test_shadow_checkpoint_requires_eight_weeks_and_forty_ticker_week_clusters():
    rows = []
    base = date(2026, 1, 2)
    for week in range(8):
        expiry = (base + timedelta(days=7 * week)).strftime("%Y%m%d")
        for ticker_index in range(6):
            for contract_index in range(3):
                rows.append(
                    {
                        "status": "settled",
                        "run_id": f"run-{week}",
                        "candidate_id": f"{week}-{ticker_index}-{contract_index}",
                        "ticker": f"T{ticker_index}",
                        "expiration": expiry,
                        "dte_bucket": "14-30",
                        "rank_position": ticker_index * 3 + contract_index + 1,
                        "alternative_score": ticker_index * 10 + contract_index,
                        "capital_return_per_day": 0.001 * (ticker_index + contract_index + 1),
                    }
                )

    result = rank_checkpoint(rows, "alternative_score", bootstrap_samples=300)
    bucket = result["by_dte_bucket"]["14-30"]
    assert result["ready"] is True
    assert bucket["settled_candidates"] == 144
    assert bucket["expiry_weeks"] == 8
    assert bucket["ticker_expiry_week_clusters"] == 48
    assert bucket["alternative_minus_frozen"] is not None
    assert len(bucket["block_bootstrap_95_ci"]) == 2
