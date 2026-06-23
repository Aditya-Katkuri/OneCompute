from contracts import Limits
from isolation import docker_available, isolation_proof, run_in_isolation
from isolation.jobobject import close, create_job_object


def test_run_challenge():
    assert run_in_isolation("challenge", {"x": 6}) == {"y": 37}


def test_run_data_transform():
    output = run_in_isolation("data.transform", {"items": [1, 2, 3], "op": "square"})
    assert output["results"] == [1, 4, 9]


def test_yield_kills():
    output = run_in_isolation(
        "data.transform",
        {"items": list(range(100000)), "op": "square"},
        should_yield=lambda: True,
    )
    assert output == {"yielded": True, "results": []}


def test_isolation_proof_shape():
    proof = isolation_proof()
    assert isinstance(proof["isolated"], bool)
    assert isinstance(proof["method"], str)


def test_jobobject_import():
    handle = create_job_object(Limits(mem_gb=0.1))
    close(handle)


def test_docker_available_shape():
    assert isinstance(docker_available(), bool)
