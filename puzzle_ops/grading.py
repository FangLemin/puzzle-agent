COUNTRY_THRESHOLDS = {
    "日本": {
        "open_rate": {"high": 0.1378, "low": 0.0789},
        "completion_rate": {"high": 0.9198, "low": 0.8673},
        "avg_finish_time": {"high": 19.73, "low": 15.06},
    },
    "法国": {
        "open_rate": {"high": 0.1078, "low": 0.0589},
        "completion_rate": {"high": 0.9189, "low": 0.8573},
        "avg_finish_time": {"high": 18.73, "low": 15.00},
    },
}


def classify_dimension(country: str, metric: str, value: float) -> str:
    try:
        limits = COUNTRY_THRESHOLDS[country][metric]
    except KeyError as exc:
        raise ValueError(f"未知国家或指标：{country}/{metric}") from exc
    if value > limits["high"]:
        return "高"
    if value < limits["low"]:
        return "低"
    return "中"


def dimension_grade(country: str, open_rate: float, completion_rate: float, avg_finish_time: float) -> str:
    return "".join(
        (
            classify_dimension(country, "open_rate", open_rate),
            classify_dimension(country, "completion_rate", completion_rate),
            classify_dimension(country, "avg_finish_time", avg_finish_time),
        )
    )


def expected_grade(dimensions: str) -> str:
    high = dimensions.count("高")
    mid = dimensions.count("中")
    low = dimensions.count("低")
    if dimensions == "高高高":
        return "S"
    if high == 2 and mid == 1:
        return "A"
    if mid == 3 or (high == 2 and low == 1):
        return "B"
    if (low == 1 and mid == 2) or (low == 1 and high == 1 and mid == 1):
        return "C"
    if low == 3 or (low == 2 and mid == 1):
        return "D"
    return "B"
