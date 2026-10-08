"""Parent pins of DeepSeek public packets built before 2026-10-05.

That public form of the captured parent manifest still stated the digest of
the compressed native file. The ranked packets (preparation v8) name these
pins in their root evidence, so the repository verifier must accept them to
replay those packets. This module is not part of any packet runtime: a new
packet neither holds these values nor accepts them. Remove it when no ranked
release uses a packet built before preparation v11.
"""

EARLIER_PUBLIC_PARENTS = {
    "dsh-cal-20260929-2": "d12c7783607234730cfd97fb1dfacf15a14372974b0bb46caf7359e8ea811da2",
    "dsh-eval-20260929-1": "21b1d50c7f8795ed58703dfc904bcf890977f93bc8ea443c6cdf8201a83b6a4c",
    "dsh-eval-20260929-2": "14ba755cf73242b487e50d4f877eb201570fe8724cb72e6419d25a8bb634895a",
}
