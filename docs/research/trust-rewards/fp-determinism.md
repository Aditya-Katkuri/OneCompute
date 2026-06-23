# Floating-point determinism and verifier comparators

## Why this matters

NightShift wants heterogeneous workers. Heterogeneous hardware is exactly where bitwise floating-point comparison fails: CPU, GPU, compiler, library, and framework choices can all change low-order bits while still producing a legitimate result.

NVIDIA's floating-point guide is the clearest primary source: IEEE 754 defines formats and operations, but operation order affects rounded results; `(A+B)+C` and `A+(B+C)` can differ after rounding [9]. It also shows FMA computes `rn(x*y+z)` with one rounding, while separate multiply/add computes `rn(rn(x*y)+z)` with two; NVIDIA notes CPU and GPU examples where both results are IEEE-correct but different [9]. MSVC documents that `/fp:contract` can generate fused operations and `/fp:fast` can reorder/combine/simplify FP operations with observably different rounding [11]. GCC exposes the same issue through `-ffp-contract` [10]. PyTorch warns reproducibility is not guaranteed across releases, platforms, or CPU vs GPU even with identical seeds [13].

## PoC rule

For T4's 1-2 day PoC, do **not** validate FP workloads. The challenge task should remain integer and exact:

```text
input:  {"x": int}
output: {"y": x*x + 1}
```

This keeps the cheater demo crisp: wrong answer means blacklist/forfeit, with no numerical ambiguity.

## Roadmap comparator policy

Add a manifest/result field like:

```json
"verifier": {
  "type": "float_close",
  "rel_tol": 1e-6,
  "abs_tol": 1e-9,
  "nan": "reject",
  "inf": "exact"
}
```

Suggested policies:

| Workload | Comparator | Notes |
|---|---|---|
| `challenge` | exact JSON/int | PoC path. |
| `data.transform: sha256/upper/square` | exact JSON | Deterministic non-FP. |
| CPU/GPU numeric arrays | per-element close + aggregate failure count | Use PEP 485-style absolute/relative tolerance [14]. |
| Render/image | perceptual or pixel tolerance | Avoid bytewise PNG differences from encoders. |
| Embeddings/logits | cosine/L2/top-k overlap | Validate semantic/numeric closeness, not exact bytes. |
| LLM text | rubric/hash of deterministic prompt only | Free-form text is not a proof of compute. |

## Implementation notes

- Use a symmetric close test for scalar floats: `abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)` [14].
- Always include absolute tolerance for comparisons near zero; pure relative tolerance fails for nonzero-vs-zero cases [14].
- Define NaN/Inf policy explicitly. Python's `math.isclose` treats NaN as close to nothing and infinities as close only to themselves [14].
- Store comparator parameters in the signed manifest so the worker cannot choose a looser verifier after seeing output.
- Pin compiler/runtime flags for deterministic challenge/reference generation. If a numerical job requires reproducibility, document `/fp`/`-ffp-contract`/CUDA/PyTorch settings in the manifest.

## Sources

- [9] NVIDIA, Floating Point and IEEE 754 / FMA guide. https://docs.nvidia.com/cuda/floating-point/index.html
- [10] GCC optimize options, `-ffp-contract`. https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html
- [11] Microsoft MSVC `/fp` floating-point behavior. https://learn.microsoft.com/en-us/cpp/build/reference/fp-specify-floating-point-behavior?view=msvc-170
- [13] PyTorch reproducibility notes. https://docs.pytorch.org/docs/2.12/notes/randomness.html
- [14] Python `math.isclose`; PEP 485. https://docs.python.org/3/library/math.html#math.isclose ; https://peps.python.org/pep-0485/
