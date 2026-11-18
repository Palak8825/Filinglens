# FilingLens eval

Answering model: `gemini-3.1-flash-lite` · judge: `gemini-3-flash-preview` · embeddings `BAAI/bge-small-en-v1.5` · 30 questions: 9 factual, 4 reasoning, 5 cross_doc, 2 flag, 10 unanswerable

## Headline (hybrid+rerank, verifier on)

| Metric | Value |
|---|---|
| Answer accuracy (20 answerable) | 100% (20/20) |
| · factual | 100% (9/9) |
| · reasoning | 4/4 |
| · cross_doc | 100% (5/5) |
| · flag | 2/2 |
| Citation precision (verified citations on a gold page) | 100% (30/30) |
| Citation precision if unverified citations were shown | 100% (30/30) |
| Model citations rejected by the verifier | 0% (0/30) |
| Correct abstention (10 unanswerable) | 100% (10/10) |
| · of which by the period guard (rule, not the model) | 1 |
| False abstention on answerable | 0% (0/20) |
| Answers with no verified citation | 0% (0/20) |
| Median latency | 6.0s |

## Retrieval ablation — gold page in the 8 excerpts the LLM sees (hit@8 / MRR)

| Config | all | factual | reasoning | cross_doc | flag | cross_doc: all docs |
|---|---|---|---|---|---|---|
| bm25 | 90% (18/20) / 0.75 | 89% (8/9) / 0.74 | 4/4 / 0.79 | 80% (4/5) / 0.65 | 2/2 / 1.00 | 60% (3/5) |
| vector | 85% (17/20) / 0.64 | 89% (8/9) / 0.63 | 3/4 / 0.62 | 80% (4/5) / 0.51 | 2/2 / 1.00 | 40% (2/5) |
| hybrid | 100% (20/20) / 0.82 | 100% (9/9) / 0.84 | 4/4 / 0.80 | 100% (5/5) / 0.75 | 2/2 / 1.00 | 100% (5/5) |
| hybrid+rerank | 100% (20/20) / 0.85 | 100% (9/9) / 0.89 | 4/4 / 0.49 | 100% (5/5) / 1.00 | 2/2 / 1.00 | 100% (5/5) |

## End-to-end by config

| Config | Prompt | Model | Accuracy | Reasoning acc. | Citation precision | Verifier rejected | Abstention | False abstention |
|---|---|---|---|---|---|---|---|---|
| hybrid+rerank | v1 | gemini-3.1-flash-lite | 100% (20/20) | 4/4 | 100% (30/30) | 0% (0/30) | 100% (10/10) | 0% (0/20) |
| hybrid+rerank@3.5lite | v1 | gemini-3.5-flash-lite | 80% (16/20) | 3/4 | 100% (23/23) | 0% (0/23) | 100% (10/10) | 10% (2/20) |
| hybrid@3.5lite | v1 | gemini-3.5-flash-lite | 80% (16/20) | 2/4 | 100% (26/26) | 0% (0/26) | 100% (10/10) | 10% (2/20) |
