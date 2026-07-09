# Parking Lot

Ideas that came up during coding sessions but weren't acted on.
Review only at phase boundaries.

| Date | Idea | Came up in |
|------|------|------------|
| 2026-07-06 | **Semantic Washout / Ingestion Chunking**: Large blocks of text (like a massive clipboard copy) get washed out into a single average embedding, causing them to fail on specific keyword/sub-topic queries. The temporary hybrid search boost patches this, but the durable fix for Phase 4's Context Assembly is to chunk long text at the ingestion layer (sentences/paragraphs) before embedding. | Phase 3 Verification |
## Deferrals from Phase 2
- **Dashboard 'Last Copied Text' UI**: Held back to avoid scope creep. Revisit if we find ourselves checking the clipboard history often in daily use.

