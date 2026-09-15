# Live Consultation tab

This is a real meeting of people, heard in the browser. It is NOT
`agents/consultation.py` (that is the product-pipeline team consult). They
share only the word. Nothing in live_consultation_* imports consultation.py.

Rules: docs/rules/live-consultation.md (73–110, 121–130).
Constitution: docs/consultation-constitution.md.
Verify: python scripts/test_live_consultation.py

Backend: agents/live_consultation*.py. Private store is
private/consultation.db ONLY. The browser never sees OPENAI_API_KEY.
In a room, Abigail carries none of Sheraj's private data (rule 88).

Related, different subsystem: gatherings/Home live in
docs/rules/gatherings.md and currently share the same test file. Don't
"clean that up" in this session.
