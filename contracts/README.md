# Baraq API contracts

These are generated, machine-readable client projections of the current Django backend. Django URL routing, views, serializers, permissions, models where applicable, and DRF Spectacular output remain the source of truth. The JSON files are not a parallel hand-maintained API specification.

## Artifacts

- `openapi.json` is the generated canonical OpenAPI view of canonical `/api/v1/` plus the internal AI service surface. It strips the legacy `/api/` aliases and environment-specific server URL.
- `mobile_api_contract.json` contains only routes appropriate for the Expo/React Native client. It excludes `/api/v1/admin/`, `/api/internal/v1/ai/`, and the staff-only AI service health route.
- `dashboard_api_contract.json` contains dashboard RBAC routes plus only the shared JWT routes required for dashboard authentication and the staff-only AI health check.

Each endpoint traces to a resolved Django view in `backend_source`, uses the exact renderer envelope, and references resources whose fields carry type, nullability, required/read/write and constraint metadata. Endpoint response schemas are the inner serializer data; `response_envelope` describes the HTTP body after `EnvelopeJSONRenderer` runs.

## Base URL and authentication

Clients combine their `PUBLIC_API_BASE_URL` deployment configuration with an endpoint `path`; no production host is embedded in the contracts. The default authenticated API mechanism is `Authorization: Bearer <access_token>` using SimpleJWT. Dashboard routes also require the current server-side RBAC checks described per endpoint.

## Versioning

The client-contract version is semantic (`1.0.0`) and is deliberately independent from `APP_VERSION`. Increment it intentionally when the client projection makes a compatible or breaking API-contract change. `generated_from_backend_commit` anchors the backend revision and `generated_at` records generation time. CI ignores only those two audit metadata fields when comparing artifacts, so unrelated commits do not force a rewrite while API-content drift still fails. Legacy `/api/` aliases remain deprecated in code and are not client-contract paths.

## Regenerate and validate

From the backend root:

```powershell
python scripts/generate_api_contracts.py
python scripts/generate_api_contracts.py --check
python scripts/validate_api_contracts.py
python -m json.tool contracts/mobile_api_contract.json > $null
python -m json.tool contracts/dashboard_api_contract.json > $null
```

`generate_api_contracts.py` runs Django/DRF schema generation, applies only documented code-truth corrections for dynamic serializer behavior, and generates all three JSON files. `validate_api_contracts.py` validates JSON structure, semantic version metadata, duplicate endpoint IDs/method-path pairs, actual Django route/method availability, resolved source view traceability, resource references, audience boundaries, and likely secret leakage.

CI runs both the deterministic freshness check and the route/audience validator. Any API view, serializer, permission, router, or schema change that affects a contract must regenerate these files in the same backend-reviewed change.

## Rules for client teams

- Mobile calls Django only. It must not call the Baraq AI service, provider APIs, webhooks, or internal source-manifest routes.
- Dashboard must use `/api/v1/admin/` routes only where its server-side role and permission codes allow it; shared JWT endpoints do not grant dashboard authority.
- Treat `missing_expected_capabilities` as proposals, never as implemented routes.
- Do not manually edit generated JSON, invent fields/statuses, or copy environment secrets into an artifact. Change backend code first, then regenerate and review the resulting diff.
