"""kvleak.driver — the HTTP client that was missing, so `kvleak scan` can actually scan.

WHY THIS EXISTS
---------------
`probes.py` holds four control-aware classifiers and `residency.py` refuses to report an all-clear
when the probe set cannot stay resident. Both are sound. **Nothing fed them.** `_cmd_scan` printed
"live probing is not performed in this build" and returned 2 unconditionally, while the package
README marketed a black-box scanner. A published package whose headline verb does not work is a
credibility problem before it is a missing feature.

This is the missing half: an OpenAI-compatible client that turns an endpoint into the five numbers
`evaluate_reuse` needs.

STDLIB ONLY
-----------
`urllib.request`, deliberately. The package advertises zero runtime dependencies and that promise
is worth more than the ergonomics of `httpx`.

HOW A CACHED-TOKEN COUNT IS OBTAINED, AND WHEN IT REFUSES
---------------------------------------------------------
OpenAI-compatible servers report reuse in `usage.prompt_tokens_details.cached_tokens`. vLLM and
SGLang both populate it when prefix caching is on. If a server does not, this returns None and the
scan ABSTAINS rather than substituting a timing estimate: a latency difference is evidence about a
cache only once you have excluded queueing, batching and thermal effects, and a scanner that
silently swapped one for the other would be inventing its own headline.

THE ORDER OF THE PROBES IS THE EXPERIMENT
------------------------------------------
C1 cold baseline    an unsent prefix, before anything is warm  -> must read 0
   victim warm      the victim sends its prefix                -> creates the residency
C2 same-caller      the victim repeats it                      -> must be > 0, or reuse is off
   ATTACKER         a DIFFERENT identity sends the same bytes  -> the measurement
C3 anti-carryover   a prefix only the harness has ever sent    -> must read 0
C4 length-matched   same length, never sent                    -> isolates length from content

C3 is the one that catches the harness fooling itself. If the anti-carryover probe reads non-zero,
this harness created the residency it would then have "discovered", and the run is INCONCLUSIVE no
matter how dramatic the attacker number looks. That failure mode has bitten this estate before.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["Endpoint", "ProbeReading", "run_reuse_probe", "DriverError"]


class DriverError(Exception):
    """The endpoint could not be probed. Always ABSTAIN on this, never report CLEAN."""


@dataclass
class ProbeReading:
    label: str
    cached_tokens: Optional[int]
    prompt_tokens: int
    latency_ms: float
    raw_usage: dict = field(default_factory=dict)


@dataclass
class Endpoint:
    """An OpenAI-compatible chat/completions endpoint.

    `identity_header` is how a caller's identity reaches the server. Providers differ -- some key on
    the API key, some on a tenant header. It is a parameter because guessing wrong would make two
    DIFFERENT callers look like one, which manufactures a clean result out of a broken test.
    """

    base_url: str
    model: str
    api_key: Optional[str] = None
    identity_header: Optional[str] = None
    timeout_s: float = 120.0
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def _post(self, path: str, payload: dict, identity: Optional[str]) -> dict:
        url = self.base_url.rstrip("/") + path
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if identity and self.identity_header:
            headers[self.identity_header] = identity
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:400].decode("utf-8", "replace")
            raise DriverError(f"HTTP {e.code} from {url}: {detail}")
        except OSError as e:
            raise DriverError(f"cannot reach {url}: {e}")

    def probe(self, label: str, prompt: str, identity: Optional[str]) -> ProbeReading:
        """One request. Returns the cached-token reading, or None if the server does not report."""
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 1, "temperature": 0.0}
        t0 = time.perf_counter()
        out = self._post("/v1/chat/completions", payload, identity)
        dt = (time.perf_counter() - t0) * 1000.0
        usage = out.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        return ProbeReading(label, cached if isinstance(cached, int) else None,
                            int(usage.get("prompt_tokens") or 0), dt, usage)


def run_reuse_probe(ep: Endpoint, *, tokens: int = 1800,
                    victim: str = "tenant-victim",
                    attacker: str = "tenant-attacker") -> Dict[str, ProbeReading]:
    """Drive the six requests the cross-tenant reuse probe needs, in the order that makes it valid.

    Returns the readings; classification is `probes.evaluate_reuse`'s job and stays there, so the
    thing that decides LEAK vs CLEAN remains a pure function that can be tested without a network.
    """
    from .probes import tenant_prefix

    victim_prefix = tenant_prefix(victim, 0, tokens)
    unsent_prefix = tenant_prefix("never-sent", 1, tokens)
    carryover_prefix = tenant_prefix("harness-only", 2, tokens)
    matched_prefix = tenant_prefix("length-matched", 3, tokens)

    readings: Dict[str, ProbeReading] = {}
    # C1 FIRST, before anything is warm. Run later it would measure our own residency.
    readings["c1_cold_baseline"] = ep.probe("c1_cold_baseline", unsent_prefix, victim)
    readings["victim_warm"] = ep.probe("victim_warm", victim_prefix, victim)
    readings["c2_same_caller"] = ep.probe("c2_same_caller", victim_prefix, victim)
    readings["attacker"] = ep.probe("attacker", victim_prefix, attacker)
    readings["c3_anti_carryover"] = ep.probe("c3_anti_carryover", carryover_prefix, attacker)
    readings["c4_length_matched"] = ep.probe("c4_length_matched", matched_prefix, attacker)

    missing = [k for k, v in readings.items() if v.cached_tokens is None]
    if missing:
        raise DriverError(
            f"the endpoint did not report usage.prompt_tokens_details.cached_tokens on "
            f"{len(missing)} of {len(readings)} probes ({', '.join(missing)}). Without it there is "
            f"no cached-token count to classify. This ABSTAINS rather than falling back to a "
            f"timing estimate: a latency difference is evidence about a cache only once queueing, "
            f"batching and thermal effects are excluded, and substituting one for the other would "
            f"be inventing the headline.")
    return readings


def readings_to_counts(readings: Dict[str, ProbeReading]) -> Dict[str, int]:
    """Map the drive order onto `evaluate_reuse`'s five arguments."""
    return {
        "cross_tenant_cached": readings["attacker"].cached_tokens or 0,
        "same_tenant_cached": readings["c2_same_caller"].cached_tokens or 0,
        "cold_baseline_cached": readings["c1_cold_baseline"].cached_tokens or 0,
        "anti_carryover_cached": readings["c3_anti_carryover"].cached_tokens or 0,
        "length_matched_cached": readings["c4_length_matched"].cached_tokens or 0,
    }
