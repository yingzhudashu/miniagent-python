---
name: stackexchange-tools
description: Search Stack Overflow and related Stack Exchange sites for practical troubleshooting experience.
keywords: [stackoverflow, stackexchange, troubleshooting, error, hardware, compatibility]
---

# Stack Exchange troubleshooting workflow

Use `stack_exchange_search` proactively when the user is diagnosing an error, crash, unexpected
behavior, installation/build failure, dependency or version incompatibility, performance problem,
driver/network issue, or hardware/electronics fault. Do not call it merely for conceptual
explanations, routine code generation, or tasks already resolved by direct local evidence.

## Site routing

- Programming, libraries, APIs, builds: `stackoverflow`
- Desktop operating systems, PC hardware, drivers: `superuser`
- Professional server/network administration: `serverfault`
- General Unix/Linux: `unix`; Ubuntu-specific: `askubuntu`
- Electronics: `electronics`; Arduino: `arduino`; Raspberry Pi: `raspberrypi`
- Apple products: `apple`; Android: `android`

Select the one most relevant site first and at most two useful alternatives. Other valid Stack
Exchange API site parameters may be used when clearly more appropriate.

## Required behavior

1. Establish the exact error or symptom, component versions, operating environment, and available
   local evidence before searching.
2. Build a compact public-safe query from the error signature, component, version, and environment.
   Never send credentials, private URLs/hosts, email addresses, project names, or local paths.
3. Treat accepted answers and votes as quality signals, not proof. Check the post date, affected
   versions, comments or limitations quoted in the answer, and consistency with local evidence and
   current official documentation.
4. Distinguish local facts, official guidance, and community experience in the final response.
   Cite every materially used post with its title, author, and link. Prefer paraphrase over long
   quotation and preserve the returned content-license attribution.
5. Do not execute community commands automatically. Explain risks before suggesting destructive,
   privileged, firmware, registry, partition, or networking changes.
6. If all Stack Exchange searches fail or no relevant result is found, use another configured web
   source when appropriate or state that the advice could not be externally verified.

## Tool

```python
stack_exchange_search(
    query: str,
    sites: list[str] = ["stackoverflow"],
    tags: list[str] = [],
    maxResults: int = 3,
)
```

`maxResults` applies per site. At most three sites and five results per site are accepted.
