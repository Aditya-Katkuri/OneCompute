import asyncio

import httpx
from fastapi import FastAPI

from workloads.submit import submit_all


def test_submit_all_with_asgi_transport() -> None:
    app = FastAPI()
    seen: list[dict] = []

    @app.post("/jobs")
    def submit(job: dict) -> dict:
        seen.append(job)
        return {"job_id": f"job-{len(seen)}"}

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        job_ids = submit_all("http://testserver", [{"kind": "data.transform"}], client=client)
    finally:
        asyncio.run(client.aclose())

    assert job_ids == ["job-1"]
    assert seen == [{"kind": "data.transform"}]

