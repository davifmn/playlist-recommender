"""End-to-end check against a running API: python scripts/smoke_test.py http://localhost:8080

Uses the planted anchor tracks of the synthetic dataset, so it knows what the right answer is:
two anchors of a genre must bring back the third one.
"""

import json
import sys
import time
import urllib.request

sys.path.insert(0, "src")
from recommender.trainer.sample_data import GENRES, anchors, catalogue  # noqa: E402


def call(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - test URL
        return json.loads(response.read())


def main(base: str) -> int:
    for _ in range(60):
        try:
            print("ready:", call(f"{base}/readyz"))
            break
        except Exception as exc:  # noqa: BLE001 - keep polling until ready
            print("waiting for API:", exc)
            time.sleep(5)
    else:
        return 1

    print("model:", json.dumps(call(f"{base}/v1/model")["stats"]))
    tracks = catalogue()
    failures = 0
    for genre in GENRES:
        a, b, expected = anchors(genre, tracks)
        body = call(f"{base}/v1/recommendations", {"tracks": [a, b], "limit": 5})
        got = [t["track"] for t in body["tracks"]]
        ok = expected in got
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {genre}: {len(got)} recommendations")
    legacy = call(f"{base}/api/recommend", {"songs": [anchors("rock", tracks)[0]]})
    print("legacy contract:", sorted(legacy))
    return 1 if failures or set(legacy) != {"songs", "version", "model_date"} else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/")))
