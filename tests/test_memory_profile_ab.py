from evals.run_memory_profile_ab import NODES, run_benchmark


def test_node_memory_profiles_reduce_deterministic_prompt_input() -> None:
    report = run_benchmark(repeats=2)

    assert len(report["cases"]) == len(NODES)
    assert report["aggregate"]["after_input_tokens"] < report["aggregate"]["before_input_tokens"]
    assert report["aggregate"]["token_reduction_percent"] > 50
    assert all(case["after"]["input_tokens"] < case["before"]["input_tokens"] for case in report["cases"])

    search_case = next(case for case in report["cases"] if case["node"] == "searchAgent")
    assert search_case["token_reduction_percent"] > 90
