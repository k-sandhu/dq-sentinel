## Summary

<!-- What does this PR change, and why? Link the issue: Closes #N -->

## Checklist

- [ ] `cd backend && pytest -q` passes (if backend touched)
- [ ] `cd backend && ruff check app tests` passes (if backend touched)
- [ ] `cd frontend && npm run build` passes (if frontend touched)
- [ ] Changed `backend/app/schemas.py`? → `frontend/src/api/types.ts` updated in this PR
- [ ] Changed `backend/app/models.py`? → Alembic migration included (`alembic revision --autogenerate`, then reviewed)
- [ ] Changed an LLM prompt? → its parser in `backend/app/llm/` still matches
- [ ] UI change? → screenshot/GIF below
- [ ] No secrets, DSNs, `.env`, or data files (`*.sqlite`, `*.duckdb`) committed

## Screenshots

<!-- For UI changes: before/after. Delete this section otherwise. -->
