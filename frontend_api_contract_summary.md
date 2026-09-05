# Frontend API Contract Summary

- Canonical base URL: `https://api.barraq.xn--mgbaab0cxheq.tech/api/v1`
- Authentication: Bearer JWT with rotating refresh tokens.
- Long-running AI operations: asynchronous `AIJob` flow.
- Canonical field-level source: generated OpenAPI schema at `/api/schema/`.
- Human documentation: `/api/docs/` (admin-only by default).
- Stable envelope: `success`, `message`, `data`, `meta`, `request_id`.
- Detailed groups: `frontend_api_contract.json` and `docs/API_CONTRACT.md`.
