# FilingLens eval

Model: `gemini-3.5-flash-lite` (judge: `gemini-3.5-flash-lite`) · embeddings `BAAI/bge-small-en-v1.5` · 9 questions: 4 factual, 1 reasoning, 1 cross_doc, 1 flag, 2 unanswerable

## Headline (hybrid retrieval, verifier on)

| Metric | Value |
|---|---|
| Answer accuracy (7 answerable) | 86% |
| · factual | 100% |
| · reasoning | 0% |
| · cross_doc | 100% |
| · flag | 100% |
| Citation precision (verified citations on a gold page) | 100% |
| Citation precision if unverified citations were shown | 100% |
| Model citations rejected by the verifier | 0% of 7 |
| Correct abstention (2 unanswerable) | 100% |
| False abstention on answerable | 14% |
| Answers with no verified citation | 0% |
| Median latency | 1.8s |

## End-to-end by config

| Config | Prompt | Model | Accuracy | Reasoning acc. | Citation precision | Verifier rejected | Abstention | False abstention |
|---|---|---|---|---|---|---|---|---|
| hybrid | v1 | gemini-3.5-flash-lite | 86% | 0% | 100% | 0% | 100% | 14% |
| hybrid+rerank | v1 | gemini-3.5-flash-lite | 86% | 100% | 100% | 0% | 100% | 14% |
| hybrid@v2 | v2 | gemini-3.5-flash-lite | 86% | 0% | 90% | 0% | 100% | 14% |
| hybrid+rerank@v2 | v2 | gemini-3.5-flash-lite | 86% | 100% | 88% | 0% | 100% | 14% |
