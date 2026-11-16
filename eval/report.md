# FilingLens eval

Answering model: `gemini-3.5-flash-lite` · judge: `gemini-3.6-flash` · embeddings `BAAI/bge-small-en-v1.5` · 9 questions: 4 factual, 1 reasoning, 1 cross_doc, 1 flag, 2 unanswerable

## Headline (hybrid+rerank, verifier on)

| Metric | Value |
|---|---|
| Answer accuracy (7 answerable) | 86% (6/7) |
| · factual | 4/4 |
| · reasoning | 1/1 |
| · cross_doc | 0/1 |
| · flag | 1/1 |
| Citation precision (verified citations on a gold page) | 100% (6/6) |
| Citation precision if unverified citations were shown | 100% (6/6) |
| Model citations rejected by the verifier | 0% (0/6) |
| Correct abstention (2 unanswerable) | 2/2 |
| False abstention on answerable | 14% (1/7) |
| Answers with no verified citation | 0% (0/7) |
| Median latency | 4.2s |

## Retrieval ablation — gold page in the 8 excerpts the LLM sees (hit@8 / MRR)

| Config | all | factual | reasoning | cross_doc | flag | cross_doc: all docs |
|---|---|---|---|---|---|---|
| bm25 | 100% (7/7) / 0.93 | 4/4 / 0.88 | 1/1 / 1.00 | 1/1 / 1.00 | 1/1 / 1.00 | 1/1 |
| vector | 86% (6/7) / 0.43 | 4/4 / 0.47 | 0/1 / 0.00 | 1/1 / 0.12 | 1/1 / 1.00 | 0/1 |
| hybrid | 86% (6/7) / 0.60 | 4/4 / 0.71 | 0/1 / 0.00 | 1/1 / 0.33 | 1/1 / 1.00 | 1/1 |
| hybrid+rerank | 100% (7/7) / 0.81 | 4/4 / 0.88 | 1/1 / 0.17 | 1/1 / 1.00 | 1/1 / 1.00 | 1/1 |

## End-to-end by config

| Config | Prompt | Model | Accuracy | Reasoning acc. | Citation precision | Verifier rejected | Abstention | False abstention |
|---|---|---|---|---|---|---|---|---|
| hybrid+rerank | v1 | gemini-3.5-flash-lite | 86% (6/7) | 1/1 | 100% (6/6) | 0% (0/6) | 2/2 | 14% (1/7) |
| hybrid | v1 | gemini-3.5-flash-lite | 86% (6/7) | 0/1 | 100% (7/7) | 0% (0/7) | 2/2 | 14% (1/7) |
